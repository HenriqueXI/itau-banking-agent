#!/usr/bin/env python3
"""RAG evaluation harness (PRD002-FR-8, rag-tests.md).

Scores the retrieval stage against `backend/tests/evals/rag/golden.yaml` with
deterministic metrics only — no LLM judge, no network in the default mode. The
judge-scored metrics (faithfulness, answer relevance) need generated answers and
arrive with PRD-006; this harness gates what exists today: retrieval hit rate,
citation correctness, fact accuracy, refusal accuracy.

Modes:
  --offline (default)  in-process: deterministic lexical embedder + in-memory
                       store, fixtures ingested on the fly. Reproducible with no
                       network — but a hashed bag-of-words is a LEXICAL proxy, not
                       semantics, so it exercises the pipeline (chunking, floor
                       mechanics, metric computation) and does NOT gate retrieval
                       quality. Metrics print for diagnosis; gates stay off.
  --live               the configured embedding provider + the Chroma collection
                       (run scripts/ingest_kb.py first). This is the mode that
                       enforces the rag-tests.md gates and calibrates the floor.

Also prints the per-category best-score distribution — this is the calibration
input for the relevance floor (rag-tests.md "component-level evals"). A floor is
only meaningful when the answerable minimum sits above the unanswerable maximum;
in the lexical proxy space those ranges overlap, which is expected and is why
calibration requires --live.

Run:  cd backend && uv run python ../scripts/eval_rag.py [--live] [--report PATH]
"""

import argparse
import asyncio
import statistics
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
GOLDEN = BACKEND / "tests" / "evals" / "rag" / "golden.yaml"

# Gates (rag-tests.md "Metrics & gates"); judge metrics land with PRD-006.
GATES = {
    "retrieval_hit_rate": 0.90,
    "citation_correctness": 1.00,
    "fact_accuracy": 1.00,
    "refusal_accuracy_unanswerable": 1.00,
    "refusal_accuracy_answerable": 0.95,
}

ANSWERABLE = ("answerable_tariff", "answerable_faq", "answerable_regulation")


def _fold(text: str) -> str:
    """Case- and accent-insensitive form for fact matching."""
    lowered = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in lowered if unicodedata.category(c) != "Mn")


@dataclass
class CaseResult:
    case_id: str
    category: str
    question: str
    best_score: float | None
    below_floor: bool
    hit: bool | None = None
    citation_ok: bool | None = None
    facts_ok: bool | None = None
    refusal_ok: bool | None = None
    pending: bool = False
    missing_facts: list[str] = field(default_factory=list)


@dataclass
class Metrics:
    results: list[CaseResult]

    def _rate(self, attribute: str, categories: tuple[str, ...]) -> float | None:
        values = [
            getattr(r, attribute)
            for r in self.results
            if r.category in categories and getattr(r, attribute) is not None
        ]
        return (sum(values) / len(values)) if values else None

    def scores(self) -> dict[str, float | None]:
        return {
            "retrieval_hit_rate": self._rate("hit", ANSWERABLE),
            "citation_correctness": self._rate("citation_ok", ANSWERABLE),
            "fact_accuracy": self._rate("facts_ok", ANSWERABLE),
            "refusal_accuracy_unanswerable": self._rate("refusal_ok", ("unanswerable",)),
            "refusal_accuracy_answerable": self._rate("refusal_ok", ANSWERABLE),
        }

    def failures(self) -> list[str]:
        failed = []
        for name, gate in GATES.items():
            value = self.scores()[name]
            if value is not None and value < gate:
                failed.append(f"{name}: {value:.1%} < gate {gate:.0%}")
        return failed


def _score_case(case: dict[str, Any], outcome: Any) -> CaseResult:
    result = CaseResult(
        case_id=case["id"],
        category=case["category"],
        question=case["question"],
        best_score=outcome.best_score,
        below_floor=outcome.below_floor,
    )

    if case["category"] == "ambiguous":
        # The clarify path is the agent's job (PRD-006) — not scorable here.
        result.pending = True
        return result

    if case["category"] == "unanswerable":
        result.refusal_ok = outcome.below_floor
        return result

    result.refusal_ok = not outcome.below_floor
    expected = case["expected_source"]
    from_expected_doc = [
        sc for sc in outcome.chunks if sc.chunk.metadata.document_id == expected["document_id"]
    ]
    result.hit = bool(from_expected_doc)
    result.citation_ok = any(
        sc.chunk.metadata.section == expected["section"] for sc in from_expected_doc
    )

    evidence = _fold(" ".join(sc.chunk.text for sc in outcome.chunks))
    result.missing_facts = [f for f in case.get("expected_facts", []) if _fold(f) not in evidence]
    result.facts_ok = not result.missing_facts
    return result


async def _build_offline_retriever(floor: float, top_k: int):
    """Ingest the fixture corpus into an in-memory store with a lexical embedder."""
    sys.path.insert(0, str(BACKEND))
    from tests.fakes.knowledge import InMemoryVectorStore, LexicalEmbedder
    from tests.fakes.providers import FixedClock, RecordingEventPublisher, SequentialIdGenerator

    from knowledge.adapters.outbound.loaders.corpus import scan_corpus
    from knowledge.adapters.outbound.loaders.langchain_loader import LangChainDocumentLoader
    from knowledge.application.dto import IngestCommand
    from knowledge.application.use_cases.ingest_knowledge_base import IngestKnowledgeBase
    from knowledge.application.use_cases.retrieve_knowledge import RetrieveKnowledge

    embedder, store = LexicalEmbedder(), InMemoryVectorStore()
    await IngestKnowledgeBase(
        loader=LangChainDocumentLoader(),
        embedder=embedder,
        store=store,
        events=RecordingEventPublisher(),
        clock=FixedClock(),
        id_generator=SequentialIdGenerator(),
    ).execute(IngestCommand(files=scan_corpus(BACKEND / "tests" / "fixtures" / "kb")))

    return RetrieveKnowledge(
        embedder=embedder,
        store=store,
        top_k=top_k,
        relevance_floor=floor,
        context_token_cap=2000,
        dedupe_similarity=0.97,
    )


def _build_live_retriever(floor: float, top_k: int):
    from api.knowledge_wiring import KnowledgeProviders
    from shared.config import Settings

    settings = Settings()  # type: ignore[call-arg] — env-provided fields
    providers = KnowledgeProviders.build(settings)
    from knowledge.application.use_cases.retrieve_knowledge import RetrieveKnowledge

    return RetrieveKnowledge(
        embedder=providers.embedder,
        store=providers.store,
        top_k=top_k,
        relevance_floor=floor,
        context_token_cap=settings.rag_context_token_cap,
        dedupe_similarity=settings.rag_dedupe_similarity,
    )


def _distribution(results: list[CaseResult]) -> list[str]:
    lines = ["| Category | cases | min | mean | max |", "|---|---|---|---|---|"]
    categories = sorted({r.category for r in results})
    for category in categories:
        scores = [r.best_score for r in results if r.category == category and r.best_score]
        if not scores:
            lines.append(f"| {category} | 0 | — | — | — |")
            continue
        lines.append(
            f"| {category} | {len(scores)} | {min(scores):.3f} | "
            f"{statistics.mean(scores):.3f} | {max(scores):.3f} |"
        )
    return lines


def _render_report(metrics: Metrics, *, mode: str, floor: float, top_k: int) -> str:
    gated = mode == "live"
    lines = [
        "# RAG eval report",
        "",
        f"Mode: `{mode}` · top_k: `{top_k}` · relevance floor: `{floor}` · "
        f"cases: {len(metrics.results)}",
        "",
    ]
    if not gated:
        lines += [
            "> **Diagnostic run — gates not enforced.** The offline mode embeds with a hashed",
            "> bag-of-words (lexical overlap), not a semantic model, so these numbers measure the",
            "> pipeline, not retrieval quality. Run `--live` against the configured provider for",
            "> the gated numbers and to calibrate the relevance floor.",
            "",
        ]
    lines += [
        "## Deterministic metrics",
        "",
        "| Metric | Value | Gate | Status |",
        "|---|---|---|---|",
    ]
    scores = metrics.scores()
    for name, gate in GATES.items():
        value = scores[name]
        if value is None:
            lines.append(f"| {name} | — | {gate:.0%} | no cases |")
            continue
        if not gated:
            lines.append(f"| {name} | {value:.1%} | {gate:.0%} | not gated |")
            continue
        status = "PASS" if value >= gate else "FAIL"
        lines.append(f"| {name} | {value:.1%} | {gate:.0%} | {status} |")

    lines += [
        "",
        "## Best-score distribution (floor calibration input)",
        "",
        *_distribution(metrics.results),
        "",
        "Floor rationale: it must sit below the minimum answerable score and above the",
        "maximum unanswerable score — the gap between those two rows is the safety margin.",
        "",
    ]

    failures = [
        r for r in metrics.results if False in (r.hit, r.citation_ok, r.facts_ok, r.refusal_ok)
    ]
    if failures:
        lines += ["## Failing cases", "", "| Case | Question | Issue |", "|---|---|---|"]
        for r in failures:
            issues = []
            if r.hit is False:
                issues.append("expected source not in top-k")
            if r.citation_ok is False:
                issues.append("expected section not cited")
            if r.facts_ok is False:
                issues.append(f"missing facts: {', '.join(r.missing_facts)}")
            if r.refusal_ok is False:
                issues.append("refusal path wrong")
            lines.append(f"| {r.case_id} | {r.question} | {'; '.join(issues)} |")
        lines.append("")

    pending = [r for r in metrics.results if r.pending]
    if pending:
        lines += [
            "## Pending (not scored here)",
            "",
            f"{len(pending)} `ambiguous` cases assert the clarify path, which is the agent's "
            "job — scored once PRD-006 lands.",
            "",
        ]
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    import yaml

    dataset = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    cases = dataset["cases"]

    mode = "live" if args.live else "offline"
    retriever = (
        _build_live_retriever(args.floor, args.top_k)
        if args.live
        else await _build_offline_retriever(args.floor, args.top_k)
    )

    from knowledge.application.dto import RetrieveQuery
    from shared.domain.result import is_err

    results: list[CaseResult] = []
    for case in cases:
        result = await retriever.execute(RetrieveQuery(text=case["question"]))
        if is_err(result):
            print(f"eval: aborted — {result.error.code}: {result.error.message}")
            return 1
        results.append(_score_case(case, result.value))

    metrics = Metrics(results)
    report = _render_report(metrics, mode=mode, floor=args.floor, top_k=args.top_k)
    print(report)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
        print(f"eval: report written to {args.report}")

    if mode != "live":
        # A lexical proxy cannot certify semantic retrieval; refuse to imply it did.
        print("eval: diagnostic run complete (offline mode does not gate quality; use --live)")
        return 0

    failures = metrics.failures()
    if failures:
        print("eval: FAILED gates — " + "; ".join(failures))
        return 1
    print("eval: all deterministic gates passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic RAG eval")
    parser.add_argument(
        "--live",
        action="store_true",
        help="use the configured embedding provider + Chroma (default: offline, deterministic)",
    )
    parser.add_argument("--floor", type=float, default=0.35, help="relevance floor under test")
    parser.add_argument("--top-k", type=int, default=6, help="retrieval depth under test")
    parser.add_argument("--report", type=Path, help="write the markdown report here")
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
