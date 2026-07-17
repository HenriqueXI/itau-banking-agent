#!/usr/bin/env python3
"""Interactive local launcher for the Itaú Banking Agent demo.

Run ``python main.py`` from the repository root. The script uses only the
standard library, never prints secret values, and keeps unknown ``.env`` keys
untouched. It is intentionally a local/demo helper, not a deployment tool.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE = ("docker", "compose", "--env-file", ".env", "-f", "infra/docker-compose.yml")
HEALTH_URL = "http://localhost:8000/health"
LANGFUSE_VOLUME = "itau-agent_langfuse_pg_data"
LANGFUSE_PUBLIC_KEY_PREFIX = "lf_pk_"
LANGFUSE_SECRET_KEY_PREFIX = "lf_sk_"
OLLAMA_API_URL = "http://localhost:11434"
OLLAMA_DOCKER_URL = "http://host.docker.internal:11434"
OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"
KB_INDEX_EMBEDDING_KEY = "KB_INDEX_EMBEDDING"


@dataclass(frozen=True)
class OllamaProfile:
    """A curated local model option supported by the demo launcher."""

    key: str
    label: str
    model: str


OLLAMA_PROFILES: tuple[OllamaProfile, ...] = (
    OllamaProfile(key="leve", label="Leve", model="gemma3:4b"),
    OllamaProfile(key="equilibrado", label="Equilibrado", model="qwen2.5:7b"),
    OllamaProfile(key="qualidade", label="Qualidade", model="llama3.1:8b"),
)


@dataclass(frozen=True)
class ConfigurationResult:
    """Configuration values that affect work after the Compose stack starts."""

    embedding_signature: str
    requires_kb_refresh: bool


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value
    return values


def _write_env(updates: dict[str, str]) -> None:
    if not ENV_FILE.exists():
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    rendered: list[str] = []
    for line in lines:
        if line and not line.lstrip().startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in updates:
                rendered.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        rendered.append(line)
    rendered.extend(f"{key}={value}" for key, value in updates.items() if key not in seen)
    ENV_FILE.write_text("\n".join(rendered) + "\n", encoding="utf-8")


def _yes_no(prompt: str, *, default: bool = True) -> bool:
    suffix = "[S/n]" if default else "[s/N]"
    answer = input(f"{prompt} {suffix}: ").strip().lower()
    return default if not answer else answer in {"s", "sim", "y", "yes"}


def _is_langfuse_key(value: str, *, prefix: str) -> bool:
    """Accept the project-key format required by the pinned Langfuse v2 image."""
    return value.startswith(prefix) and len(value) > len(prefix)


def _new_langfuse_key(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(16)}"


def _is_encryption_key(value: str) -> bool:
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _local_langfuse_updates(current: dict[str, str]) -> dict[str, str]:
    """Keep valid local keys and replace missing/legacy keys without printing them."""
    public = current.get("LANGFUSE_PUBLIC_KEY", "")
    secret = current.get("LANGFUSE_SECRET_KEY", "")
    encryption = current.get("LANGFUSE_ENCRYPTION_KEY", "")
    return {
        "LANGFUSE_PUBLIC_KEY": public
        if _is_langfuse_key(public, prefix=LANGFUSE_PUBLIC_KEY_PREFIX)
        else _new_langfuse_key(LANGFUSE_PUBLIC_KEY_PREFIX),
        "LANGFUSE_SECRET_KEY": secret
        if _is_langfuse_key(secret, prefix=LANGFUSE_SECRET_KEY_PREFIX)
        else _new_langfuse_key(LANGFUSE_SECRET_KEY_PREFIX),
        "LANGFUSE_ENCRYPTION_KEY": encryption
        if _is_encryption_key(encryption)
        else secrets.token_hex(32),
    }


def _embedding_signature(provider: str, current: dict[str, str]) -> str:
    """Identify the vector space that is currently expected in Chroma."""
    if provider == "ollama":
        return f"ollama:{OLLAMA_EMBEDDING_MODEL}"
    return (
        "gemini:"
        f"{current.get('GEMINI_EMBEDDING_MODEL', 'gemini-embedding-001')}:"
        f"{current.get('GEMINI_EMBEDDING_DIMENSION', '768')}"
    )


def _find_ollama() -> str | None:
    """Find Ollama even when the installer has not refreshed this process's PATH yet."""
    if executable := shutil.which("ollama"):
        return executable
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            installed = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
            if installed.is_file():
                return str(installed)
        return None
    for candidate in (
        Path("/usr/local/bin/ollama"),
        Path("/opt/homebrew/bin/ollama"),
        Path("/usr/bin/ollama"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _installed_ollama_models() -> set[str]:
    """Return local model tags, or an empty set when Ollama is not serving yet."""
    try:
        with urlopen(f"{OLLAMA_API_URL}/api/tags", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, OSError, ValueError, json.JSONDecodeError):
        return set()
    return {
        str(model["name"])
        for model in payload.get("models", [])
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    }


def _ollama_tags_endpoint_responds() -> bool:
    try:
        with urlopen(f"{OLLAMA_API_URL}/api/tags", timeout=3) as response:
            return response.status == 200
    except (URLError, OSError):
        return False


def _install_ollama() -> str:
    """Install Ollama after an explicit confirmation (winget on Windows; guided elsewhere)."""
    if sys.platform != "win32":
        instructions = (
            "  macOS:  brew install ollama  (ou baixe em https://ollama.com/download)"
            if sys.platform == "darwin"
            else "  Linux:  curl -fsSL https://ollama.com/install.sh | sh"
        )
        raise RuntimeError(
            "O Ollama não foi encontrado. Instale-o e execute o launcher novamente:\n"
            f"{instructions}"
        )
    if not shutil.which("winget"):
        raise RuntimeError(
            "O winget não foi encontrado. Instale o Ollama manualmente pelo site oficial "
            "e execute o launcher novamente."
        )
    if not _yes_no("O Ollama não foi encontrado. Deseja instalá-lo com winget?", default=False):
        raise RuntimeError("Instalação do Ollama cancelada.")

    run(
        "winget",
        "install",
        "--id",
        "Ollama.Ollama",
        "--exact",
        "--accept-source-agreements",
        "--accept-package-agreements",
    )
    if executable := _find_ollama():
        return executable
    raise RuntimeError(
        "O Ollama foi instalado, mas ainda não está disponível neste terminal. "
        "Feche e abra o terminal e execute o launcher novamente."
    )


def _wait_for_ollama(timeout_seconds: int = 30) -> None:
    print("Aguardando o serviço local do Ollama", end="", flush=True)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _ollama_tags_endpoint_responds():
            print(" pronto.")
            return
        print(".", end="", flush=True)
        time.sleep(1)
    print()
    raise RuntimeError(
        "O Ollama não respondeu em http://localhost:11434. "
        "Abra o aplicativo Ollama ou execute 'ollama serve' e tente novamente."
    )


def _ensure_ollama_server() -> str:
    executable = _find_ollama() or _install_ollama()
    if _ollama_tags_endpoint_responds():
        return executable

    print("Iniciando o serviço local do Ollama.")
    options: dict[str, object] = {
        "cwd": ROOT,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen((executable, "serve"), **options)
    _wait_for_ollama()
    return executable


def _choose_ollama_profile(installed_models: set[str]) -> OllamaProfile:
    print("\nModelos Ollama disponíveis:")
    for index, profile in enumerate(OLLAMA_PROFILES, start=1):
        status = "instalado" if profile.model in installed_models else "será baixado"
        print(f"  {index}. {profile.label}: {profile.model} ({status})")

    by_key = {profile.key: profile for profile in OLLAMA_PROFILES}
    while True:
        choice = input("Escolha o perfil [3]: ").strip().lower() or "3"
        if choice.isdigit() and 1 <= int(choice) <= len(OLLAMA_PROFILES):
            return OLLAMA_PROFILES[int(choice) - 1]
        if profile := by_key.get(choice):
            return profile
        print("Escolha inválida. Informe 1, 2, 3, leve, equilibrado ou qualidade.")


def _ensure_ollama_models(executable: str, model: str) -> None:
    installed_models = _installed_ollama_models()
    for required_model in (model, OLLAMA_EMBEDDING_MODEL):
        if required_model in installed_models:
            continue
        print(f"Baixando modelo Ollama: {required_model}")
        run(executable, "pull", required_model)
        installed_models.add(required_model)


def _configure_ollama(current: dict[str, str], updates: dict[str, str]) -> str:
    executable = _ensure_ollama_server()
    profile = _choose_ollama_profile(_installed_ollama_models())
    _ensure_ollama_models(executable, profile.model)
    updates.update(
        {
            "LLM_PROVIDER": "ollama",
            "LLM_FALLBACK_ORDER": "ollama",
            "OLLAMA_URL": OLLAMA_DOCKER_URL,
            "OLLAMA_MODEL": profile.model,
            "EMBEDDING_PROVIDER": "ollama",
            "OLLAMA_EMBEDDING_MODEL": OLLAMA_EMBEDDING_MODEL,
        }
    )
    return _embedding_signature("ollama", current)


def configure() -> ConfigurationResult:
    current = _read_env(ENV_FILE)
    updates: dict[str, str] = {}
    print("\nConfiguração local — valores secretos não serão exibidos.")
    use_ollama = _yes_no(
        "Usar o Ollama instalado nesta máquina como provedor principal?",
        default=current.get("LLM_PROVIDER") == "ollama",
    )
    if use_ollama:
        embedding_signature = _configure_ollama(current, updates)
    else:
        # Do not leave a previous Ollama choice active when the user declines it.
        if _yes_no("Configurar uma chave Gemini para RAG e respostas reais?"):
            key = getpass.getpass("GEMINI_API_KEY (deixe vazio para manter a atual): ").strip()
            if key:
                updates["GEMINI_API_KEY"] = key
        updates.update(
            {
                "LLM_PROVIDER": "gemini",
                "LLM_FALLBACK_ORDER": "gemini,openrouter",
                "EMBEDDING_PROVIDER": "gemini",
            }
        )
        embedding_signature = _embedding_signature("gemini", current)

    requires_kb_refresh = current.get(KB_INDEX_EMBEDDING_KEY) != embedding_signature
    if requires_kb_refresh:
        # The marker is written only after a successful forced ingestion.
        updates[KB_INDEX_EMBEDDING_KEY] = ""
    if _yes_no("Configurar chaves locais do Langfuse?"):
        updates.update(_local_langfuse_updates(current))
    else:
        public = getpass.getpass("LANGFUSE_PUBLIC_KEY (lf_pk_...): ").strip()
        secret = getpass.getpass("LANGFUSE_SECRET_KEY (lf_sk_...): ").strip()
        if not _is_langfuse_key(public, prefix=LANGFUSE_PUBLIC_KEY_PREFIX) or not _is_langfuse_key(
            secret, prefix=LANGFUSE_SECRET_KEY_PREFIX
        ):
            raise ValueError("As chaves do Langfuse devem começar com lf_pk_ e lf_sk_.")
        updates.update(
            {
                "LANGFUSE_PUBLIC_KEY": public,
                "LANGFUSE_SECRET_KEY": secret,
                "LANGFUSE_ENCRYPTION_KEY": current.get("LANGFUSE_ENCRYPTION_KEY", "")
                if _is_encryption_key(current.get("LANGFUSE_ENCRYPTION_KEY", ""))
                else secrets.token_hex(32),
            }
        )
    _write_env(updates)
    print(f".env preparado em {ENV_FILE}")
    return ConfigurationResult(
        embedding_signature=embedding_signature,
        requires_kb_refresh=requires_kb_refresh,
    )


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def run_compose_up(compose: tuple[str, ...], *, build: bool) -> None:
    """Start the stack, retrying a transient Docker Desktop/BuildKit EOF once."""
    command = (*compose, "up", "-d", *([] if not build else ["--build"]))
    for attempt in range(1, 3):
        try:
            run(*command)
            return
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            print("O Docker Desktop interrompeu o build; tentando novamente com o cache local.")
            time.sleep(5)


def _langfuse_volume_exists() -> bool:
    check = subprocess.run(
        ("docker", "volume", "inspect", LANGFUSE_VOLUME),
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return check.returncode == 0


def reset_langfuse() -> None:
    """Remove only Langfuse's local state after explicit confirmation."""
    if not _yes_no(
        "Isso removerá somente os traces, projetos e chaves locais do Langfuse. Continuar?",
        default=False,
    ):
        raise RuntimeError("Reset do Langfuse cancelado.")
    run(*COMPOSE, "stop", "langfuse", "langfuse-db")
    run(*COMPOSE, "rm", "-f", "langfuse", "langfuse-db")
    if _langfuse_volume_exists():
        run("docker", "volume", "rm", LANGFUSE_VOLUME)
    print("Volume local do Langfuse removido. Os dados da aplicação foram preservados.")


def wait_for_health(timeout_seconds: int = 120) -> None:
    print("Aguardando backend ficar saudável", end="", flush=True)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(HEALTH_URL, timeout=3) as response:
                if response.status == 200:
                    print(" pronto.")
                    return
        except URLError:
            pass
        print(".", end="", flush=True)
        time.sleep(3)
    print()
    raise RuntimeError("backend não ficou saudável; execute o comando de logs abaixo")


def print_urls() -> None:
    print(
        """
Projeto disponível:
  Frontend:  http://localhost:3000
  Backend:   http://localhost:8000/docs
  Health:    http://localhost:8000/health
  Langfuse:  http://localhost:3001  (admin@demo.local / langfuse123)
  Traces:    http://localhost:3001/project/itau-agent/traces

Personas demo (senha configurada em SEED_PASSWORD, padrão demo123):
  Ana:   ana@demo
  Bruno: bruno@demo
  Carla: carla@demo

Comandos úteis:
  docker compose --env-file .env -f infra/docker-compose.yml logs -f backend
  docker compose --env-file .env -f infra/docker-compose.yml down
  python main.py --configure-only
  python main.py --refresh-kb
  python main.py --reset-langfuse
""".strip()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicializa a demo local do Itaú Banking Agent.")
    parser.add_argument("--configure-only", action="store_true", help="apenas prepara o .env")
    parser.add_argument("--no-build", action="store_true", help="não reconstrói as imagens Docker")
    parser.add_argument("--skip-seed", action="store_true", help="não cria as personas demo")
    parser.add_argument(
        "--refresh-kb",
        action="store_true",
        help="força a reingestão da KB após iniciar a stack",
    )
    parser.add_argument(
        "--reset-langfuse",
        action="store_true",
        help="remove apenas o volume local do Langfuse antes de iniciar a stack",
    )
    args = parser.parse_args()

    if not shutil.which("docker"):
        print("Docker Desktop não foi encontrado no PATH.", file=sys.stderr)
        return 1
    try:
        configuration = configure()
    except ValueError as error:
        print(f"Configuração inválida: {error}", file=sys.stderr)
        return 1
    if args.configure_only:
        return 0
    try:
        if args.reset_langfuse:
            reset_langfuse()
        run(*COMPOSE, "config", "-q")
        run_compose_up(COMPOSE, build=not args.no_build)
        wait_for_health()
        if not args.skip_seed:
            run(*COMPOSE, "exec", "-T", "backend", "python", "scripts/seed.py")
        if configuration.requires_kb_refresh or args.refresh_kb:
            print("Reindexando a base de conhecimento para o provedor de embeddings atual.")
            run(
                *COMPOSE,
                "exec",
                "-T",
                "backend",
                "python",
                "scripts/ingest_kb.py",
                "--force",
                "--prune",
            )
            _write_env({KB_INDEX_EMBEDDING_KEY: configuration.embedding_signature})
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(
            "\n"
            f"Falha: {error}\n"
            "Veja: docker compose --env-file .env -f infra/docker-compose.yml logs -f",
            file=sys.stderr,
        )
        return 1
    print_urls()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
