#!/usr/bin/env python3
"""Agent evaluation harness (PRD006-FR-9, agent-tests.md).

Scores the probabilistic parts of the agent — understanding, reference
resolution, behavioral contract — against the datasets in
`backend/tests/evals/agent/`. Retrieval quality is `eval_rag.py`'s job; here the
knowledge port is stubbed so a suite-3 failure means the *agent* misbehaved, not
that Chroma had a bad day.

Suites (agent-tests.md):
  1  intent routing & extraction   gates: >= 95% intent, >= 90% extraction
  2  reference resolution          gates: 100% canonical, >= 85% overall
  3  behavioral contract           gates: 100% deterministic checks, >= 90% judge

Modes (agent-tests.md "Execution modes"):
  --mode full (default)  configured provider for the agent AND the judge. This is
                    the release gate: a provider switch requires a passing run
                    (ADR-008).
  --mode ci         deterministic checks only, judge skipped. For a local/CI model
                    (Ollama) that can run the agent but is not a trustworthy judge —
                    an unreliable judge that gates would be worse than no judge
                    (guardrails' own principle: determinism first).
  --dry-run         no LLM: validates the datasets against the tool registry and
                    prints the plan. Catches a broken dataset without spending
                    quota — it certifies nothing about the model.

Judge (suite 3): configured provider at temperature 0, rubric in
`backend/tests/evals/rubrics/`. Deterministic checks run first and the judge can
only tighten the outcome, never rescue a failed one.

Run:  cd backend && uv run --env-file ../.env python ../scripts/eval_agent.py [--suite 1|2|3|all]
"""

import argparse
import asyncio
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
DATASETS = BACKEND / "tests" / "evals" / "agent"
RUBRICS = BACKEND / "tests" / "evals" / "rubrics"

sys.path.insert(0, str(BACKEND / "src"))

# agent-tests.md "Scoring". Misrouting into an unrequested operation is a
# critical failure regardless of the aggregate.
GATES = {
    "suite1_intent_accuracy": 0.95,
    "suite1_extraction_accuracy": 0.90,
    "suite2_canonical": 1.00,
    "suite2_overall": 0.85,
    "suite3_deterministic": 1.00,
    "suite3_judge": 0.90,
}

CRITICAL_WEIGHT = 10

_CHECK_CRITERIA = {
    "single_question": (
        "A resposta faz EXATAMENTE uma pergunta objetiva sobre a informação que falta, "
        "sem pedir outros dados e sem chutar valores."
    ),
    "honest_denial": (
        "A resposta nega o pedido informando o motivo (permissão/elegibilidade/limite), "
        "sem culpar 'erro do sistema' e sem confirmar se o recurso de terceiro existe."
    ),
    "objective": (
        "A resposta é objetiva e direta: sem repetir a pergunta, sem enrolação, "
        "sem oferecer serviços não solicitados."
    ),
}


def _fold(text: str) -> str:
    lowered = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in lowered if unicodedata.category(c) != "Mn")


@dataclass
class CaseResult:
    case_id: str
    suite: int
    passed: bool
    critical_failure: bool = False
    detail: str = ""
    judged: bool | None = None


@dataclass
class SuiteReport:
    results: list[CaseResult] = field(default_factory=list)

    def rate(self, predicate: Any = None) -> float | None:
        rows = [r for r in self.results if predicate is None or predicate(r)]
        if not rows:
            return None
        return sum(1 for r in rows if r.passed) / len(rows)

    @property
    def criticals(self) -> list[CaseResult]:
        return [r for r in self.results if r.critical_failure]


# --------------------------------------------------------------------- fixtures


def _load(name: str) -> list[dict[str, Any]]:
    import yaml

    data = yaml.safe_load((DATASETS / name).read_text(encoding="utf-8"))
    return list(data["cases"])


def _amounts_equal(actual: Any, expected: Any) -> bool:
    """Semantic match on params: amounts compare numerically (agent-tests.md §1)."""
    try:
        return Decimal(str(actual)) == Decimal(str(expected))
    except Exception:
        return False


def _pix_keys_equal(actual: str, expected: str) -> bool:
    """A CPF or phone PIX key is the same key punctuated or not ("123.456.789-00"
    == "12345678900"). Email and random keys compare literally."""
    a, e = _fold(actual), _fold(expected)
    if a == e:
        return True
    a_digits, e_digits = re.sub(r"\D", "", a), re.sub(r"\D", "", e)
    both_numeric = a_digits and e_digits and not re.search(r"[a-z@]", a + e)
    return bool(both_numeric) and a_digits == e_digits


def _params_match(actual: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, str]:
    for key, want in expected.items():
        got = actual.get(key)
        if got is None:
            return False, f"missing param {key}"
        if isinstance(want, int | float) or key in ("amount", "valor", "value"):
            if not _amounts_equal(got, want):
                return False, f"{key}: {got!r} != {want!r}"
        elif key == "pix_key":
            if not _pix_keys_equal(str(got), str(want)):
                return False, f"{key}: {got!r} != {want!r}"
        elif _fold(str(got)) != _fold(str(want)):
            return False, f"{key}: {got!r} != {want!r}"
    return True, ""


def _target_kind(understanding: Any, own_customer_id: str) -> str:
    target = understanding.target_resource
    if target is None or target.owner_id is None:
        return "none"
    if target.owner_id in ("self", "próprio", "proprio", "eu", own_customer_id):
        return "self"
    return "third_party"


# ------------------------------------------------------------------ environment


class _EvalUser:
    """Ana, the reference persona — identity is injected, never modelled."""

    import uuid as _uuid

    id = _uuid.UUID(int=1)
    customer_id = "123"
    role = "customer"


def _build_llm() -> Any:
    from api.conversation_wiring import _build_llm as build_chain
    from shared.adapters.clock import SystemClock
    from shared.config import Settings

    settings = Settings()
    return build_chain(settings, SystemClock()), settings


def _deps(llm: Any, retrieval: Any, authorization: Any) -> Any:
    from conversation.application.graph.dependencies import GraphConfig, GraphDependencies
    from shared.adapters.clock import SystemClock
    from shared.adapters.event_publisher import LoggingEventPublisher
    from shared.adapters.id_generator import UuidIdGenerator

    return GraphDependencies(
        llm=llm,
        retrieval=retrieval,
        authorization=authorization,
        events=LoggingEventPublisher(),
        clock=SystemClock(),
        id_generator=UuidIdGenerator(),
        config=GraphConfig(grounding_judge_enabled=False),
    )


class _StaticRetrieval:
    """Knowledge is not under test here — the evidence is declared per case."""

    def __init__(self) -> None:
        self.next_evidence: str | None = None

    async def retrieve(self, query: str, *, source_type: str | None = None) -> Any:
        from conversation.domain.values import Citation, Evidence, Retrieval

        if not self.next_evidence:
            return Retrieval(query=query, evidence=(), below_floor=True, best_score=0.2)
        citation = Citation(document_id="fixture", title="Tarifas 2026", section="Consignado")
        return Retrieval(
            query=query,
            evidence=(Evidence(text=self.next_evidence, citation=citation, score=0.9),),
            below_floor=False,
            best_score=0.9,
        )


class _SwitchableAuthorization:
    def __init__(self) -> None:
        self.permit = True

    async def authorize(self, *, user: Any, action: str, resource: Any = None) -> Any:
        from conversation.application.ports.authorization import AuthorizationOutcome

        if self.permit:
            return AuthorizationOutcome(permitted=True)
        return AuthorizationOutcome(permitted=False, reason="role_forbidden")


# ---------------------------------------------------------------------- suite 1


async def _understand(deps: Any, message: str, history: list[dict[str, str]] | None = None) -> Any:
    from conversation.application.graph.nodes.understand_node import make_understand
    from conversation.domain.values import Role, Turn

    turns = [
        Turn(role=Role(t["role"]), content=t["content"]) for t in (history or [])
    ]
    state = {
        "thread_id": "eval",
        "user": _EvalUser(),
        "user_id": _EvalUser.id,
        "input_text": message,
        "messages": [*turns, Turn(role=Role.USER, content=message)],
        "guardrail_flags": [],
    }
    update = await make_understand(deps)(state)
    return update["understanding"]


async def _run_suite1(deps: Any) -> SuiteReport:
    report = SuiteReport()
    for case in _load("suite1_intent.yaml"):
        understanding = await _understand(deps, case["utterance"])
        intent_ok = understanding.intent.value == case["intent"]

        critical = False
        for forbidden in case.get("critical_not", []):
            if understanding.intent.value == forbidden:
                critical = True

        detail = "" if intent_ok else f"intent {understanding.intent.value} != {case['intent']}"
        report.results.append(
            CaseResult(
                case_id=f"{case['id']}:intent",
                suite=1,
                passed=intent_ok,
                critical_failure=critical,
                detail=detail,
            )
        )

        extraction_ok, extraction_detail = _score_extraction(case, understanding)
        report.results.append(
            CaseResult(
                case_id=f"{case['id']}:extraction",
                suite=1,
                passed=extraction_ok,
                detail=extraction_detail,
            )
        )
    return report


def _score_extraction(case: dict[str, Any], understanding: Any) -> tuple[bool, str]:
    if case.get("tool") and understanding.tool != case["tool"]:
        return False, f"tool {understanding.tool} != {case['tool']}"

    if case.get("params"):
        ok, detail = _params_match(understanding.params, case["params"])
        if not ok:
            return False, detail

    if case.get("expect_clarify") and not understanding.needs_clarification:
        return False, "expected a clarify (missing/ambiguous), got a complete extraction"
    if case.get("expect_clarify") is False and understanding.needs_clarification:
        return False, "unexpected clarify"

    expected_target = case.get("target")
    if expected_target:
        actual = _target_kind(understanding, _EvalUser.customer_id)
        if actual != expected_target:
            return False, f"target {actual} != {expected_target}"
    return True, ""


# ---------------------------------------------------------------------- suite 2


async def _run_suite2(deps: Any) -> SuiteReport:
    report = SuiteReport()
    for case in _load("suite2_references.yaml"):
        understanding = await _understand(deps, case["utterance"], case.get("history"))
        ok, detail = _score_extraction(case, understanding)
        if ok and understanding.intent.value != case["intent"]:
            ok, detail = False, f"intent {understanding.intent.value} != {case['intent']}"
        if ok and case.get("references_resolved") and not understanding.references_resolved:
            ok, detail = False, "references_resolved not reported"
        if ok and case.get("query_contains"):
            query = _fold(str(understanding.params.get("query", "")))
            missing = [t for t in case["query_contains"] if _fold(t) not in query]
            if missing:
                ok, detail = False, f"query missing carried topic {missing}"

        report.results.append(
            CaseResult(
                case_id=case["id"],
                suite=2,
                passed=ok,
                detail=detail,
            )
        )
    return report


# ---------------------------------------------------------------------- suite 3


async def _run_suite3(
    deps: Any,
    retrieval: _StaticRetrieval,
    authorization: _SwitchableAuthorization,
    *,
    judge: bool = True,
) -> SuiteReport:
    from conversation.application.graph.builder import build_graph

    graph = build_graph(deps)
    report = SuiteReport()

    for case in _load("suite3_behavior.yaml"):
        retrieval.next_evidence = case.get("evidence") if case.get("kb") == "grounded" else None
        authorization.permit = case.get("authorization") != "deny"

        state = await graph.ainvoke(
            {
                "thread_id": f"eval-{case['id']}",
                "user": _EvalUser(),
                "user_id": _EvalUser.id,
                "input_text": case["utterance"],
                "messages": [],
                "guardrail_flags": [],
                "regenerated": False,
            }
        )
        response = state.get("response", "")
        route = state.get("route", "")

        ok, detail = _score_behavior(case, state, response, route)
        report.results.append(
            CaseResult(case_id=f"{case['id']}:deterministic", suite=3, passed=ok, detail=detail)
        )

        judge_checks = [c for c in case.get("checks", []) if c in _CHECK_CRITERIA] if judge else []
        for check in judge_checks:
            verdict, rationale = await _judge(deps, check, case["utterance"], response)
            report.results.append(
                CaseResult(
                    case_id=f"{case['id']}:{check}",
                    suite=3,
                    passed=verdict,
                    judged=verdict,
                    detail=rationale,
                )
            )
    return report


def _score_behavior(
    case: dict[str, Any], state: dict[str, Any], response: str, route: str
) -> tuple[bool, str]:
    if case.get("expect_route") and route != case["expect_route"]:
        return False, f"route {route} != {case['expect_route']}"

    checks = case.get("checks", [])
    retrieval = state.get("retrieval")

    if "cites" in checks and not (retrieval and retrieval.citations):
        return False, "no citation payload on a KB answer"
    if "refuses" in checks and route != "refuse_no_kb":
        return False, f"expected a refusal, got {route}"
    if "no_false_claim" in checks and state.get("result") is None:
        claims = ("realizado com sucesso", "efetuada", "enviado com sucesso", "transferi")
        if any(c in _fold(response) for c in map(_fold, claims)):
            return False, "claimed an execution with no typed result in state"
    if "pt_br" in checks and not _looks_pt_br(response):
        return False, "response does not look like pt-BR"
    if "objective" in checks and len(response.split()) > 120:
        return False, f"{len(response.split())} words > 120 (US-1.3)"

    folded = _fold(response)
    for needle in case.get("must_not_contain", []):
        if _fold(needle) in folded:
            return False, f"response contains forbidden text: {needle!r}"
    wanted = case.get("must_contain_any")
    if wanted and not any(_fold(w) in folded for w in wanted):
        return False, f"response contains none of {wanted}"
    return True, ""


_PT_MARKERS = re.compile(
    r"\b(não|você|para|com|seu|sua|posso|limite|conta|cartão|taxa|é|de|do|da|em|que)\b",
    re.IGNORECASE,
)


def _looks_pt_br(text: str) -> bool:
    return len(_PT_MARKERS.findall(text)) >= 2


_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"pass": {"type": "boolean"}, "rationale": {"type": "string"}},
    "required": ["pass"],
}


async def _judge(deps: Any, check: str, utterance: str, response: str) -> tuple[bool, str]:
    """Judge verdicts store their rationale — every one is human-auditable
    (agent-tests.md "Judge protocol")."""
    from conversation.application.json_repair import parse_json_object
    from conversation.application.ports.llm import LlmError, LlmMessage, MessageRole

    rubric = (RUBRICS / "behavior.md").read_text(encoding="utf-8")
    prompt = rubric.format(
        check=check,
        criterion=_CHECK_CRITERIA[check],
        utterance=utterance,
        response=response,
    )
    try:
        completion = await deps.llm.complete(
            [LlmMessage(role=MessageRole.SYSTEM, content=prompt)],
            json_schema=_JUDGE_SCHEMA,
            temperature=0.0,
            max_tokens=256,
        )
    except LlmError as error:
        return False, f"judge unavailable: {error}"

    payload = parse_json_object(completion.text)
    if payload is None or not isinstance(payload.get("pass"), bool):
        return False, f"judge output unparseable: {completion.text[:80]!r}"
    return bool(payload["pass"]), str(payload.get("rationale", ""))


# ----------------------------------------------------------------------- report


def _scores(suites: dict[int, SuiteReport]) -> dict[str, float | None]:
    scores: dict[str, float | None] = {}
    if 1 in suites:
        report = suites[1]
        scores["suite1_intent_accuracy"] = report.rate(lambda r: r.case_id.endswith(":intent"))
        scores["suite1_extraction_accuracy"] = report.rate(
            lambda r: r.case_id.endswith(":extraction")
        )
    if 2 in suites:
        canonical = {c["id"] for c in _load("suite2_references.yaml") if c.get("canonical")}
        scores["suite2_canonical"] = suites[2].rate(lambda r: r.case_id in canonical)
        scores["suite2_overall"] = suites[2].rate()
    if 3 in suites:
        scores["suite3_deterministic"] = suites[3].rate(lambda r: r.judged is None)
        scores["suite3_judge"] = suites[3].rate(lambda r: r.judged is not None)
    return scores


def _render_report(suites: dict[int, SuiteReport], *, provider: str, mode: str) -> str:
    scores = _scores(suites)
    lines = [
        "# Agent eval report",
        "",
        f"Primary provider: `{provider}` · mode: `{mode}` "
        "(the chain may have failed over — see the trace)",
        "",
        *(
            [
                "`ci` mode: judge checks skipped — a small local model judges badly, and a",
                "noisy judge that gates is worse than no judge. Run `--mode full` against the",
                "configured provider before a release.",
                "",
            ]
            if mode == "ci"
            else []
        ),
        "| Metric | Score | Gate | Status |",
        "|---|---|---|---|",
    ]
    for name, gate in GATES.items():
        value = scores.get(name)
        if value is None:
            lines.append(f"| {name} | — | {gate:.0%} | not run |")
            continue
        status = "PASS" if value >= gate else "FAIL"
        lines.append(f"| {name} | {value:.1%} | {gate:.0%} | {status} |")

    failures = [r for report in suites.values() for r in report.results if not r.passed]
    lines += ["", f"## Failures ({len(failures)})", ""]
    if not failures:
        lines.append("None.")
    for result in failures:
        marker = " **CRITICAL**" if result.critical_failure else ""
        lines.append(f"- `{result.case_id}`{marker}: {result.detail}")

    criticals = [r for report in suites.values() for r in report.criticals]
    if criticals:
        lines += [
            "",
            "## Critical failures (block release regardless of aggregate)",
            "",
            *[
            f"- `{r.case_id}`: routed into an operation the user did not ask for"
            for r in criticals
        ],
        ]
    return "\n".join(lines) + "\n"


def _gate_failures(suites: dict[int, SuiteReport]) -> list[str]:
    scores = _scores(suites)
    failures = []
    for name, gate in GATES.items():
        value = scores.get(name)
        if value is not None and value < gate:
            failures.append(f"{name}: {value:.1%} < gate {gate:.0%}")
    criticals = [r for report in suites.values() for r in report.criticals]
    if criticals:
        failures.append(
            f"{len(criticals)} critical misrouting failure(s) (weight {CRITICAL_WEIGHT}x)"
        )
    return failures


# -------------------------------------------------------------------------- run


def _dry_run() -> int:
    """Dataset sanity without spending a token: every tool/intent named by a
    case must exist in the registry, or the suite is testing a fiction."""
    from conversation.domain.tools import REGISTRY
    from conversation.domain.values import Intent

    problems: list[str] = []
    counts: dict[str, int] = {}
    for name in ("suite1_intent.yaml", "suite2_references.yaml", "suite3_behavior.yaml"):
        cases = _load(name)
        counts[name] = len(cases)
        for case in cases:
            intent = case.get("intent")
            if intent and intent not in {i.value for i in Intent}:
                problems.append(f"{name}:{case['id']} unknown intent {intent!r}")
            tool = case.get("tool")
            if tool and tool not in REGISTRY:
                problems.append(f"{name}:{case['id']} unknown tool {tool!r}")

    for name, count in counts.items():
        print(f"eval: {name}: {count} cases")
    if counts["suite1_intent.yaml"] < 60:
        problems.append("suite 1 has fewer than the 60 utterances agent-tests.md requires")
    if problems:
        print("eval: dataset problems —")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("eval: datasets valid (dry run certifies nothing about the model; use --live)")
    return 0


async def run(args: argparse.Namespace) -> int:
    llm, _settings = _build_llm()
    retrieval = _StaticRetrieval()
    authorization = _SwitchableAuthorization()
    deps = _deps(llm, retrieval, authorization)

    wanted = [1, 2, 3] if args.suite == "all" else [int(args.suite)]
    suites: dict[int, SuiteReport] = {}
    if 1 in wanted:
        suites[1] = await _run_suite1(deps)
    if 2 in wanted:
        suites[2] = await _run_suite2(deps)
    if 3 in wanted:
        suites[3] = await _run_suite3(deps, retrieval, authorization, judge=args.mode == "full")

    report = _render_report(suites, provider=llm.provider, mode=args.mode)
    print(report)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
        print(f"eval: report written to {args.report}")

    failures = _gate_failures(suites)
    if failures:
        print("eval: FAILED gates — " + "; ".join(failures))
        return 1
    print("eval: all gates passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the agent evals (agent-tests.md)")
    parser.add_argument("--suite", default="all", choices=["1", "2", "3", "all"])
    parser.add_argument(
        "--mode",
        default="full",
        choices=["full", "ci"],
        help="full: agent + judge on the configured provider; ci: deterministic checks only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the datasets without calling any model (gates nothing)",
    )
    parser.add_argument("--report", type=Path, help="write the markdown report here")
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if args.dry_run:
        return _dry_run()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
