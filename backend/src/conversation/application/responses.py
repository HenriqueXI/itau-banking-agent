"""Templated pt-BR responses for the paths that must not improvise.

A refusal, a block, or a denial is copy — not generation. Two reasons: an LLM
asked to explain a block tends to explain the *rule* (teaching the attacker,
guardrails.md §4), and a template can't hallucinate a capability we don't have.
"""

from decimal import Decimal

OFFICIAL_CHANNELS = "o app do Itaú, sua agência ou a central 4004-4828 (capitais)"


def format_brl(value: Decimal) -> str:
    """pt-BR money ("R$ 15.000,00") — the one format O5's integrity check reads."""
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


BLOCKED_INPUT = (
    "Não consigo seguir com essa mensagem. Posso ajudar com dúvidas sobre produtos e "
    "tarifas, consulta de perfil, limite e saldo, alteração de limite e PIX."
)

REFUSE_NO_KB = (
    "Não tenho essa informação na minha base de conhecimento e prefiro não arriscar um "
    f"palpite. Para confirmar com precisão, consulte {OFFICIAL_CHANNELS}."
)

KNOWLEDGE_UNAVAILABLE = (
    "Não consigo acessar a base de conhecimento agora, então não vou responder de memória. "
    f"Tente novamente em instantes ou consulte {OFFICIAL_CHANNELS}."
)

NOT_YET_AVAILABLE = (
    "Consigo entender seu pedido, mas essa operação ainda não está disponível por aqui. "
    f"Em breve! Por enquanto, use {OFFICIAL_CHANNELS}."
)

NO_PENDING_OPERATION = (
    "Não há nenhuma operação aguardando confirmação nesta conversa. "
    "Se ainda quiser seguir, é só me pedir de novo."
)

OUTPUT_BLOCKED = "Não consigo compartilhar isso. Posso ajudar com dúvidas sobre produtos e tarifas,"

SECRETS_WARNING = (
    "Por segurança, removi os dados sensíveis que você enviou (senhas, códigos ou números "
    "de cartão). Nunca compartilhe esses dados — nem comigo, nem com ninguém."
)


def denied(
    reason: str | None = None,
    *,
    action: str | None = None,
    own_resource: bool = False,
) -> str:
    """Honest denial: says no and why-ish, never confirms the resource exists
    and never blames a 'system error'."""
    if reason == "role_forbidden":
        if action == "update_card_limit" and own_resource:
            return (
                "Seu perfil de cliente pode consultar o limite, mas não pode alterá-lo "
                "neste canal. Alterações de limite são permitidas apenas para perfis "
                "manager e admin. Nenhuma solicitação foi criada."
            )
        return (
            "Seu perfil não tem permissão para essa consulta, então não vou realizá-la. "
            f"Se precisar desse acesso, fale com {OFFICIAL_CHANNELS}."
        )
    return (
        "Não posso realizar esse pedido com as permissões desta sessão. "
        f"Se precisar, confirme os detalhes com {OFFICIAL_CHANNELS}."
    )


def fallback_error(correlation_id: str) -> str:
    """The only path that admits an internal failure — with the id that makes it
    traceable, and no internals (langgraph.md §6)."""
    return (
        "Tive um problema técnico e não consegui concluir seu pedido. Nada foi executado. "
        f"Se precisar de suporte, informe o código {correlation_id}."
    )
