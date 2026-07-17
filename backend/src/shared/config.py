"""Application settings — the only place environment variables are read.

Loads every variable documented in `.env.example` (the contract). Missing
required variables make startup fail loudly with the variable named in the
pydantic ValidationError; optional ones fall back to safe defaults.
"""

from decimal import Decimal
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "demo", "prod"]
LlmProvider = Literal["gemini", "openrouter", "ollama"]
EmbeddingProvider = Literal["gemini", "ollama"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Environment = "local"
    log_level: str = "INFO"

    # Required — no safe default exists.
    database_url: str
    jwt_secret: str

    chroma_url: str = "http://chromadb:8000"
    # Chroma rejects names shorter than 3 chars ([a-zA-Z0-9._-], alnum at both ends).
    chroma_collection: str = "knowledge_base"
    mcp_server_url: str = "http://mcp-server:8080/mcp"

    llm_provider: LlmProvider = "gemini"
    llm_fallback_order: str = "gemini,openrouter,ollama"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1:8b"
    embedding_provider: EmbeddingProvider = "gemini"
    gemini_embedding_model: str = "gemini-embedding-001"
    # gemini-embedding-001 emits 3072 dims by default; 768 (a supported MRL size)
    # keeps parity with Ollama's nomic-embed-text so providers stay swappable.
    gemini_embedding_dimension: int = 768
    ollama_embedding_model: str = "nomic-embed-text"

    # RAG retrieval — floor calibrated on the golden set (rag-tests.md), see eval-report.md.
    # 0.66 sits midway between the answerable minimum (0.724) and the unanswerable
    # maximum (0.595) measured with gemini-embedding-001@768. Re-calibrate on any
    # embedding-model or corpus change: the value is model-specific, not universal.
    rag_top_k: int = 6
    rag_relevance_floor: float = 0.66
    rag_context_token_cap: int = 2000
    rag_dedupe_similarity: float = 0.97

    # Agent (PRD-006). History window is verbatim turns, no summarization
    # the token budget assumes the smallest context in the
    # fallback chain (Ollama 8k), so both caps are hard limits, not hints.
    agent_history_window_turns: int = 20
    agent_max_input_chars: int = 4000
    agent_answer_max_tokens: int = 512
    agent_generation_temperature: float = 0.3
    # O2 grounding judge: an extra LLM call per KB answer. Disabling it trades
    # a hallucination control for latency/quota — deliberate, never accidental.
    agent_grounding_judge_enabled: bool = True

    # Telemetry (ADR-010). Keys have no default — a secret with a default is a
    # secret in git. Unkeyed ⇒ the no-op tracer, which is a supported way to run
    # (tests, evals), not a degraded one.
    langfuse_host: str = "http://langfuse:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    jwt_ttl_minutes: int = 60
    step_up_ttl_minutes: int = 5
    confirmation_ttl_minutes: int = 5
    default_card_id: str = "card-1"

    # Transactional outbox relay (PRD-014). The relay is in-process today;
    # its repository/bus boundaries keep a future broker extraction localized.
    outbox_relay_interval_seconds: float = 1.0
    outbox_relay_batch_size: int = 100
    outbox_max_attempts: int = 5
    outbox_max_backoff_seconds: int = 60

    # Business-rule overrides — defaults match the documented business rules.
    pix_daily_limit: Decimal = Decimal("5000")
    pix_stepup_threshold: Decimal = Decimal("1000")
    # BR-2.2 card-limit maximums per segment as (score ≥800, ≥600, <600).
    # Env override is JSON: {"Personnalité": ["50000","25000","10000"], ...}.
    card_limit_maximums: dict[str, tuple[Decimal, Decimal, Decimal]] = {
        "Personnalité": (Decimal("50000"), Decimal("25000"), Decimal("10000")),
        "Uniclass": (Decimal("30000"), Decimal("15000"), Decimal("8000")),
        "Varejo": (Decimal("15000"), Decimal("8000"), Decimal("4000")),
    }

    rate_limit_per_minute: int = 30

    @property
    def docs_enabled(self) -> bool:
        """OpenAPI docs are exposed only in local development (environments.md)."""
        return self.env == "local"

    @property
    def tracing_enabled(self) -> bool:
        """Both keys or nothing: a half-configured tracer would fail on every
        export and warn forever instead of saying so once, at wiring time."""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)
