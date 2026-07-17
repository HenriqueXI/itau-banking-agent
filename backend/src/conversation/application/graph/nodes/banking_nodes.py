"""Deterministic banking graph nodes for PRD-007."""
# ruff: noqa: E501

import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Literal

from conversation.application.graph.dependencies import GraphDependencies
from conversation.application.graph.state import AgentState
from conversation.application.graph.types import GraphNode
from conversation.application.ports.banking_workflow import (
    BalanceView,
    HybridInvoiceGuidanceView,
    InvoiceView,
    LimitConfirmationView,
    LimitReceiptView,
    LimitRejectedView,
    LimitView,
    PixConfirmationView,
    PixReceiptView,
    PixRejectedView,
    PixStepUpView,
    ProfileView,
    StatementView,
)
from conversation.application.responses import denied, format_brl
from conversation.domain.tools import tool_for
from conversation.domain.values import Intent, OperationRef, ResourceRef, ResourceSubject
from shared.application.ports.tracer import annotate

_brl = format_brl


def _claimed_resource_for(state: AgentState) -> ResourceRef | None:
    """Turn the model's resource claim into an own-resource reference when safe.

    Named third-party references remain opaque here.  They become a canonical
    customer id only in ``resolve_customer_reference`` before authorization.
    """
    understanding = state.get("understanding")
    spec = tool_for(understanding.tool) if understanding else None
    if spec is None or spec.resource_kind is None:
        return None
    target = understanding.target_resource if understanding else None
    own_customer_id = getattr(state.get("user"), "customer_id", None)
    if target is None or target.owner_id in (None, "self", "proprio", "eu"):
        return ResourceRef(
            kind=spec.resource_kind,
            owner_id=own_customer_id,
            id=target.id if target else None,
        )
    return target


def _resource_for(state: AgentState) -> ResourceRef | None:
    """Return only the server-resolved resource used by downstream banking nodes."""
    return state.get("resolved_resource")


def _customer_id(state: AgentState) -> str | None:
    resource = _resource_for(state)
    return resource.owner_id if resource is not None else None


def _subject_for(state: AgentState, *, customer_id: str, name: str | None) -> ResourceSubject:
    """Build narration metadata from authenticated identity and an MCP profile."""
    return ResourceSubject(
        customer_id=customer_id,
        name=name,
        is_self=customer_id == getattr(state.get("user"), "customer_id", None),
    )


def _subject_name(state: AgentState) -> str:
    subject = state.get("resource_subject")
    if isinstance(subject, ResourceSubject) and subject.name:
        return subject.name
    return "o cliente informado"


def _is_own_subject(state: AgentState) -> bool:
    subject = state.get("resource_subject")
    return not isinstance(subject, ResourceSubject) or subject.is_self


def _resolve_card_reference(reference: object, profile: ProfileView) -> tuple[str | None, bool]:
    """Map a safe card reference to a server-authorized card id.

    The model never receives canonical card ids.  It can only suggest a
    masked final-four reference, which is matched against the profile returned
    by MCP.  The boolean reports whether the value looked like an explicit
    card reference, so an unrelated model value (for example, the owner's
    name copied into ``target_resource.id``) can be treated as no selection.
    """
    if reference is None:
        return None, False

    value = str(reference).strip()
    if not value:
        return None, False
    if value in profile.card_ids:
        return value, True

    # Accept only a four-digit masked reference, such as "8888", "final
    # 8888" or "**** 8888".  A PAN has more than four digits and is never a
    # valid input to this resolver.
    digits = re.sub(r"\D", "", value)
    if len(digits) != 4:
        return None, False
    matching_cards = [card for card in profile.cards if card.last4 == digits]
    if len(matching_cards) == 1:
        return matching_cards[0].card_id, True
    return None, True


def _selected_resource_reference(
    *,
    understanding: object,
    field: str,
    selected_from_ui: object,
) -> tuple[object | None, Literal["model", "ui"] | None]:
    """Return a candidate and its untrusted source without mixing origins."""
    params = getattr(understanding, "params", {})
    if isinstance(params, dict) and params.get(field) is not None:
        return params[field], "model"
    target = getattr(understanding, "target_resource", None)
    if target is not None and target.id is not None:
        return target.id, "model"
    if selected_from_ui is not None:
        return selected_from_ui, "ui"
    return None, None


def make_resolve_customer_reference(deps: GraphDependencies) -> GraphNode:
    """Resolve a demo persona before RBAC and before any banking access.

    This prevents a natural-language value such as ``Ana`` from being treated
    as a MCP ``customer_id``.  Unknown references stop here: neither the
    authorization service nor MCP gets to probe a non-existent resource.
    """

    async def resolve_customer_reference(state: AgentState) -> AgentState:
        claimed = _claimed_resource_for(state)
        if claimed is None or claimed.owner_id is None:
            return {
                "resolved_resource": None,
                "response": "Nao foi possivel identificar o cliente para esta consulta.",
                "route": "cancelled",
            }

        own_customer_id = getattr(state.get("user"), "customer_id", None)
        if claimed.owner_id == own_customer_id:
            return {"resolved_resource": claimed, "route": "customer_resolved"}

        customer_id = await deps.customer_references.resolve(claimed.owner_id)
        if customer_id is None:
            return {
                "resolved_resource": None,
                "response": "Nao foi possivel localizar o cliente informado neste ambiente de demonstracao.",
                "route": "cancelled",
            }
        return {
            "resolved_resource": ResourceRef(
                kind=claimed.kind,
                owner_id=customer_id,
                id=claimed.id,
            ),
            "route": "customer_resolved",
        }

    return resolve_customer_reference


def make_authorize(deps: GraphDependencies) -> GraphNode:
    async def authorize(state: AgentState) -> AgentState:
        understanding = state.get("understanding")
        spec = tool_for(understanding.tool) if understanding else None
        if spec is None:
            return {"response": denied(), "route": "denied_response"}
        outcome = await deps.authorization.authorize(
            user=state.get("user"), action=spec.action, resource=_resource_for(state)
        )
        annotate(
            action=spec.action,
            resource_kind=spec.resource_kind,
            permitted=outcome.permitted,
            reason=outcome.reason,
        )
        if not outcome.permitted:
            resource = _resource_for(state)
            return {
                "response": denied(
                    outcome.reason,
                    action=spec.action,
                    own_resource=(
                        resource is not None
                        and resource.owner_id == getattr(state.get("user"), "customer_id", None)
                    ),
                ),
                "route": "denied_response",
            }
        if understanding is not None and understanding.intent is Intent.HYBRID_INVOICE_GUIDANCE:
            transaction_outcome = await deps.authorization.authorize(
                user=state.get("user"),
                action="view_transactions",
                resource=ResourceRef(
                    kind="account",
                    owner_id=_customer_id(state),
                ),
            )
            annotate(action="view_transactions", permitted=transaction_outcome.permitted)
            if not transaction_outcome.permitted:
                return {
                    "response": denied(transaction_outcome.reason),
                    "route": "denied_response",
                }
        return {"route": "authorized"}

    return authorize


def make_denied_response(_: GraphDependencies) -> GraphNode:
    async def denied_response(state: AgentState) -> AgentState:
        return {"route": "denied_response"}

    return denied_response


def make_resolve_resource(deps: GraphDependencies) -> GraphNode:
    """Resolve a resource only after deterministic authorization.

    The model and browser can suggest an ID, but the authenticated customer's
    MCP profile is the authority for whether that ID may be used.  This keeps a
    forged UI ID from becoming a cross-customer lookup and avoids guessing when
    a customer has several cards.
    """

    async def resolve_resource(state: AgentState) -> AgentState:
        understanding = state.get("understanding")
        banking = deps.banking
        customer_id = _customer_id(state)
        spec = tool_for(understanding.tool) if understanding else None
        if understanding is None or spec is None or customer_id is None or banking is None:
            return {"route": "cancelled", "response": "Nao consegui identificar a operacao."}
        if spec.resource_kind in (None, "customer"):
            return {
                "resource_subject": _subject_for(state, customer_id=customer_id, name=None),
                "route": "resource_resolved",
            }

        profile = await banking.get_profile(customer_id=customer_id)
        field = "card_id" if spec.resource_kind == "card" else "account_id"
        candidates = profile.card_ids if field == "card_id" else profile.account_ids
        ui_context = state.get("ui_context") or {}
        # The panel summary only describes the authenticated user's resources.
        # An explicit third-party target must never inherit that local hint.
        selected_from_ui = (
            ui_context.get(f"selected_{field}")
            if _subject_for(state, customer_id=customer_id, name=profile.name).is_self
            else None
        )
        selected, source = _selected_resource_reference(
            understanding=understanding,
            field=field,
            selected_from_ui=selected_from_ui,
        )
        explicit_card_reference = False
        if field == "card_id" and selected is not None:
            selected, explicit_card_reference = _resolve_card_reference(selected, profile)

        if selected is not None and selected not in candidates:
            if field == "card_id" and state.get("pending_card_selection") is not None:
                finals = " ou ".join(f"final {card.last4}" for card in profile.cards)
                return {
                    "clarification_response": (
                        f"Nao reconheci esse final para {profile.name}. "
                        f"Informe os quatro ultimos digitos: {finals}."
                    ),
                    "resource_subject": _subject_for(
                        state, customer_id=customer_id, name=profile.name
                    ),
                    "route": "clarify",
                }
            return {
                "route": "cancelled",
                "response": "Nao encontrei esse recurso na conta autorizada.",
            }
        if field == "card_id" and source == "ui" and selected is None:
            # Browser context is only a temporary reference.  Unlike a model
            # claim, an invalid UI id must not silently fall through to a
            # different card or a clarification path.
            return {
                "route": "cancelled",
                "response": "Nao encontrei esse recurso na conta autorizada.",
            }
        if field == "card_id" and explicit_card_reference and selected is None:
            if state.get("pending_card_selection") is not None:
                finals = " ou ".join(f"final {card.last4}" for card in profile.cards)
                return {
                    "clarification_response": (
                        f"Nao reconheci esse final para {profile.name}. "
                        f"Informe os quatro ultimos digitos: {finals}."
                    ),
                    "resource_subject": _subject_for(
                        state, customer_id=customer_id, name=profile.name
                    ),
                    "route": "clarify",
                }
            return {
                "route": "cancelled",
                "response": "Nao encontrei esse recurso na conta autorizada.",
            }
        if selected is None:
            if len(candidates) == 1:
                selected = candidates[0]
            elif len(candidates) > 1:
                if field == "card_id" and profile.cards:
                    finals = " ou ".join(f"final {card.last4}" for card in profile.cards)
                    return {
                        "pending_card_selection": understanding,
                        "clarification_response": (
                            f"Encontrei mais de um cartao de {profile.name}. "
                            "Informe os quatro ultimos digitos: "
                            f"{finals}."
                        ),
                        "resource_subject": _subject_for(
                            state, customer_id=customer_id, name=profile.name
                        ),
                        "route": "clarify",
                    }
                kind = "cartoes" if field == "card_id" else "contas"
                return {
                    "understanding": replace(
                        understanding,
                        ambiguity=f"ha mais de um {kind}; informe qual deseja consultar",
                    ),
                    "route": "clarify",
                }
            else:
                return {"route": "cancelled", "response": "Nenhum recurso elegivel foi encontrado."}

        return {
            "understanding": replace(
                understanding,
                params={**understanding.params, field: selected},
                references_resolved=True,
            ),
            "pending_card_selection": None,
            "clarification_response": None,
            "resource_subject": _subject_for(state, customer_id=customer_id, name=profile.name),
            "route": "resource_resolved",
        }

    return resolve_resource


def make_validate_rules(_: GraphDependencies) -> GraphNode:
    async def validate_rules(state: AgentState) -> AgentState:
        if _customer_id(state) is None:
            return {
                "response": "Preciso identificar o cliente antes de continuar.",
                "route": "cancelled",
            }
        return {"route": "validated"}

    return validate_rules


def make_gate(_: GraphDependencies) -> GraphNode:
    async def gate(state: AgentState) -> AgentState:
        understanding = state.get("understanding")
        spec = tool_for(understanding.tool) if understanding else None
        if spec is None:
            return {"response": denied(), "route": "denied_response"}
        return {"route": "gated"}

    return gate


def make_execute(deps: GraphDependencies) -> GraphNode:
    async def execute(state: AgentState) -> AgentState:
        understanding = state.get("understanding")
        customer_id = _customer_id(state)
        banking = deps.banking
        if understanding is None or customer_id is None or banking is None:
            return {"route": "cancelled", "response": "Nao consegui identificar a operacao."}
        params = understanding.params
        # Resource resolution validates and stores these IDs before this node.
        # Never consult the browser hint again at execution time: it is not an
        # authorization source and must not become one in a future graph edit.
        card_id = params.get("card_id")
        account_id = params.get("account_id")
        if understanding.intent is Intent.VIEW_PROFILE:
            profile = await banking.get_profile(customer_id=customer_id)
            return {
                "result": profile,
                "resource_subject": _subject_for(state, customer_id=customer_id, name=profile.name),
                "route": "executed",
            }
        if understanding.intent is Intent.VIEW_LIMIT:
            return {
                "result": await banking.get_limit(customer_id=customer_id, card_id=card_id),
                "route": "executed",
            }
        if understanding.intent is Intent.VIEW_BALANCE:
            return {
                "result": await banking.get_balance(customer_id=customer_id, account_id=account_id),
                "route": "executed",
            }
        if understanding.intent is Intent.VIEW_INVOICE:
            return {
                "result": await banking.get_invoice(customer_id=customer_id, card_id=card_id),
                "route": "executed",
            }
        if understanding.intent is Intent.VIEW_TRANSACTIONS:
            return {
                "result": await banking.get_statement(
                    customer_id=customer_id, account_id=account_id
                ),
                "route": "executed",
            }
        if understanding.intent is Intent.HYBRID_INVOICE_GUIDANCE:
            invoice = await banking.get_invoice(customer_id=customer_id, card_id=card_id)
            statement = await banking.get_statement(customer_id=customer_id, account_id=account_id)
            return {
                "result": HybridInvoiceGuidanceView(invoice=invoice, statement=statement),
                "route": "executed",
            }
        if understanding.intent is Intent.UPDATE_CARD_LIMIT:
            try:
                amount = Decimal(str(params["amount"]))
            except (KeyError, InvalidOperation):
                return {"route": "cancelled", "response": "Informe um valor valido para o limite."}
            result = await banking.request_limit_change(
                user_id=state["user_id"],
                customer_id=customer_id,
                card_id=card_id,
                amount=amount,
            )
            if isinstance(result, LimitConfirmationView):
                return {
                    "pending_operation": OperationRef(
                        operation_hash=result.operation_hash,
                        tool="alterar_limite",
                        tier=2,
                    ),
                    "confirmation": result,
                    "route": "confirmation_required",
                }
            return {"result": result, "route": "executed"}
        if understanding.intent is Intent.CREATE_PIX:
            try:
                amount = Decimal(str(params["amount"]))
                recipient_key = str(params["pix_key"])
            except (KeyError, InvalidOperation):
                return {"route": "cancelled", "response": "Informe valor e chave PIX validos."}
            pix_result = await banking.request_pix(
                user_id=state["user_id"],
                customer_id=customer_id,
                recipient_key=recipient_key,
                amount=amount,
            )
            if isinstance(pix_result, PixStepUpView):
                return {
                    "pending_operation": OperationRef(
                        operation_hash=pix_result.operation_hash, tool="fazer_pix", tier=3
                    ),
                    "step_up": pix_result,
                    "route": "step_up_required",
                }
            if isinstance(pix_result, PixConfirmationView):
                return {
                    "pending_operation": OperationRef(
                        operation_hash=pix_result.operation_hash, tool="fazer_pix", tier=3
                    ),
                    "confirmation": pix_result,
                    "route": "confirmation_required",
                }
            return {"result": pix_result, "route": "executed"}
        return {
            "route": "cancelled",
            "response": "Essa consulta ainda nao esta disponivel por aqui.",
        }

    return execute


def make_await_confirmation(_: GraphDependencies) -> GraphNode:
    async def await_confirmation(state: AgentState) -> AgentState:
        confirmation = state.get("confirmation")
        if not isinstance(confirmation, (LimitConfirmationView, PixConfirmationView)):
            return {
                "route": "cancelled",
                "response": "Nao ha confirmacao valida para esta operacao.",
            }
        if isinstance(confirmation, PixConfirmationView):
            return {
                "response": (
                    f"Confirme o PIX de {_brl(confirmation.amount)} para "
                    f"{confirmation.recipient_key_masked}, saindo da conta "
                    f"{confirmation.account_id}."
                ),
                "route": "await_confirmation",
            }
        return {
            "response": (
                f"Confirme a alteracao do limite de {_brl(confirmation.current_limit)} "
                f"para {_brl(confirmation.requested_limit)}."
            ),
            "route": "await_confirmation",
        }

    return await_confirmation


def make_await_step_up(_: GraphDependencies) -> GraphNode:
    async def await_step_up(state: AgentState) -> AgentState:
        step_up = state.get("step_up")
        if not isinstance(step_up, PixStepUpView):
            return {
                "route": "cancelled",
                "response": "Nao ha validacao adicional ativa para esta operacao.",
            }
        return {
            "response": (
                "Para continuar, solicite e informe o codigo de verificacao desta operacao."
            ),
            "route": "await_step_up",
        }

    return await_step_up


def make_narrate(_: GraphDependencies) -> GraphNode:
    async def narrate(state: AgentState) -> AgentState:
        result = state.get("result")
        amounts: tuple[Decimal, ...] = ()
        own_subject = _is_own_subject(state)
        subject_name = _subject_name(state)
        if isinstance(result, ProfileView):
            text = (
                f"Seu perfil e {result.name}, do segmento {result.segment}."
                if own_subject
                else f"O perfil de {subject_name} e do segmento {result.segment}."
            )
        elif isinstance(result, LimitView):
            usage = (
                f"Voce ja utilizou {_brl(result.used_amount)} e tem "
                f"{_brl(result.available_amount)} disponivel."
                if own_subject
                else f"{subject_name} ja utilizou {_brl(result.used_amount)} e tem "
                f"{_brl(result.available_amount)} disponivel."
            )
            text = (
                f"O limite total do {'seu ' if own_subject else ''}cartao final {result.last4}"
                f"{' e' if own_subject else f' de {subject_name} e'} {_brl(result.current_limit)}. "
                f"{usage}"
            )
            amounts = (result.current_limit, result.used_amount, result.available_amount)
        elif isinstance(result, BalanceView):
            text = (
                f"Seu saldo disponivel e {_brl(result.available_balance)}."
                if own_subject
                else f"O saldo disponivel de {subject_name} e {_brl(result.available_balance)}."
            )
            amounts = (result.available_balance,)
        elif isinstance(result, InvoiceView):
            text = (
                f"A fatura do {'seu ' if own_subject else ''}cartao final {result.last4}"
                f"{' esta' if own_subject else f' de {subject_name} esta'} em {_brl(result.amount)}, "
                f"vence no dia {result.due_date} e esta {result.status.lower()}."
            )
            amounts = (result.amount,)
        elif isinstance(result, StatementView):
            if not result.entries:
                text = (
                    "Nao encontrei movimentacoes recentes para esta conta."
                    if own_subject
                    else f"Nao encontrei movimentacoes recentes para a conta de {subject_name}."
                )
            else:
                lines = "; ".join(
                    f"{description}: {_brl(amount)}" for description, amount in result.entries[:3]
                )
                text = (
                    f"Estas sao suas movimentacoes recentes: {lines}."
                    if own_subject
                    else f"Estas sao as movimentacoes recentes de {subject_name}: {lines}."
                )
                amounts = tuple(amount for _, amount in result.entries[:3])
        elif isinstance(result, LimitRejectedView):
            if result.reason == "non_positive":
                text = "Informe um novo limite maior que zero."
                amounts = ()
            elif result.reason == "not_multiple_of_100":
                text = "O novo limite deve ser multiplo de R$ 100,00."
                amounts = (Decimal("100"),)
            elif result.reason == "unchanged":
                text = "O novo limite deve ser diferente do limite atual."
                amounts = ()
            elif result.reason == "below_used_amount":
                text = "Nao posso reduzir o limite abaixo do valor ja utilizado no cartao."
                amounts = ()
            elif result.reason == "above_maximum":
                text = (
                    f"Nao posso alterar esse limite. O maximo disponivel e {_brl(result.maximum)}."
                )
                amounts = (result.maximum,)
            else:
                text = "Nao posso alterar esse limite no momento."
                amounts = ()
        elif isinstance(result, LimitReceiptView):
            text = (
                f"Limite do cartao final {result.last4} atualizado de "
                f"{_brl(result.old_limit)} para {_brl(result.new_limit)}."
            )
            amounts = (result.old_limit, result.new_limit)
        elif isinstance(result, PixRejectedView):
            text = "Nao foi possivel agendar esse PIX."
            if result.reason == "daily_limit_exceeded" and result.remaining_limit is not None:
                text = (
                    "Esse PIX ultrapassa seu limite diario. "
                    f"Restam {_brl(result.remaining_limit)} hoje."
                )
            amounts = (result.remaining_limit,) if result.remaining_limit is not None else ()
        elif isinstance(result, PixReceiptView):
            text = (
                f"PIX de {_brl(result.amount)} para {result.recipient_key_masked} realizado. "
                f"Comprovante {result.e2e_id}."
            )
            amounts = (result.amount,)
        else:
            text = state.get("response", "Nao foi possivel concluir a operacao.")
            amounts = ()
        return {"response": text, "route": "narrate", "narration_amounts": amounts}

    return narrate


def make_cancelled(_: GraphDependencies) -> GraphNode:
    async def cancelled(state: AgentState) -> AgentState:
        return {"route": "cancelled", "response": state.get("response", "Operacao cancelada.")}

    return cancelled
