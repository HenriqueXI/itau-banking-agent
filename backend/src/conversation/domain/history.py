"""History windowing: last N turns verbatim, no summarization.

Token budgets assume the smallest context window in the fallback chain (Ollama
8k, llm-providers.md §4), so the window is a hard cap, not a hint. Summarization
of older turns is future work — deliberately absent, not forgotten.
"""

from conversation.domain.values import Role, Turn

DEFAULT_WINDOW_TURNS = 20


def window(turns: list[Turn], size: int = DEFAULT_WINDOW_TURNS) -> list[Turn]:
    """Keep the most recent `size` turns, oldest first."""
    if size <= 0:
        return []
    return turns[-size:]


def render_history(turns: list[Turn]) -> str:
    """Flatten the window for a prompt slot. History is *data* about the
    conversation, so it renders as labeled lines, never as chat roles the model
    could confuse with its own instructions."""
    labels = {Role.USER: "Usuário", Role.ASSISTANT: "Assistente"}
    return "\n".join(f"{labels[t.role]}: {t.content}" for t in turns)


def last_user_message(turns: list[Turn]) -> str | None:
    for turn in reversed(turns):
        if turn.role is Role.USER:
            return turn.content
    return None
