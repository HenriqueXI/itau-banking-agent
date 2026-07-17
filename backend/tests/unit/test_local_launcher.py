"""Safety rules for the repository-level local launcher."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from pytest import MonkeyPatch


def _launcher() -> ModuleType:
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("local_launcher", root / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_local_langfuse_updates_preserve_supported_keys() -> None:
    launcher = _launcher()
    current = {
        "LANGFUSE_PUBLIC_KEY": "lf_pk_existing",
        "LANGFUSE_SECRET_KEY": "lf_sk_existing",
        "LANGFUSE_ENCRYPTION_KEY": "a" * 64,
    }

    assert launcher._local_langfuse_updates(current) == current


def test_local_langfuse_updates_replace_legacy_keys_and_invalid_encryption() -> None:
    launcher = _launcher()

    updates = launcher._local_langfuse_updates(
        {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-legacy",
            "LANGFUSE_SECRET_KEY": "sk-lf-legacy",
            "LANGFUSE_ENCRYPTION_KEY": "not-a-key",
        }
    )

    assert updates["LANGFUSE_PUBLIC_KEY"].startswith("lf_pk_")
    assert updates["LANGFUSE_SECRET_KEY"].startswith("lf_sk_")
    assert len(updates["LANGFUSE_ENCRYPTION_KEY"]) == 64
    assert int(updates["LANGFUSE_ENCRYPTION_KEY"], 16) >= 0


def test_reset_langfuse_removes_only_the_dedicated_volume(monkeypatch: MonkeyPatch) -> None:
    launcher = _launcher()
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(launcher, "_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(launcher, "_langfuse_volume_exists", lambda: True)
    monkeypatch.setattr(launcher, "run", lambda *args: calls.append(args))

    launcher.reset_langfuse()

    assert calls == [
        (*launcher.COMPOSE, "stop", "langfuse", "langfuse-db"),
        (*launcher.COMPOSE, "rm", "-f", "langfuse", "langfuse-db"),
        ("docker", "volume", "rm", launcher.LANGFUSE_VOLUME),
    ]


def test_reset_langfuse_requires_confirmation(monkeypatch: MonkeyPatch) -> None:
    launcher = _launcher()
    monkeypatch.setattr(launcher, "_yes_no", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="cancelado"):
        launcher.reset_langfuse()


def test_ollama_profiles_are_curated_and_stable() -> None:
    launcher = _launcher()

    assert [(profile.key, profile.model) for profile in launcher.OLLAMA_PROFILES] == [
        ("leve", "gemma3:4b"),
        ("equilibrado", "qwen2.5:7b"),
        ("qualidade", "llama3.1:8b"),
    ]


def test_install_ollama_requires_explicit_confirmation(monkeypatch: MonkeyPatch) -> None:
    launcher = _launcher()
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(
        launcher.shutil,
        "which",
        lambda command: "winget.exe" if command == "winget" else None,
    )
    monkeypatch.setattr(launcher, "_yes_no", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="cancelada"):
        launcher._install_ollama()


def test_install_ollama_uses_winget_and_rediscovers_executable(monkeypatch: MonkeyPatch) -> None:
    launcher = _launcher()
    calls: list[tuple[str, ...]] = []
    paths = iter([r"C:\\Users\\demo\\AppData\\Local\\Programs\\Ollama\\ollama.exe"])
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        launcher.shutil,
        "which",
        lambda command: "winget.exe" if command == "winget" else None,
    )
    monkeypatch.setattr(launcher, "_find_ollama", lambda: next(paths))
    monkeypatch.setattr(launcher, "run", lambda *args: calls.append(args))

    executable = launcher._install_ollama()

    assert executable.endswith("ollama.exe")
    assert calls == [
        (
            "winget",
            "install",
            "--id",
            "Ollama.Ollama",
            "--exact",
            "--accept-source-agreements",
            "--accept-package-agreements",
        )
    ]


def test_ollama_model_provisioning_pulls_only_missing_models(monkeypatch: MonkeyPatch) -> None:
    launcher = _launcher()
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(launcher, "_installed_ollama_models", lambda: {"gemma3:4b"})
    monkeypatch.setattr(launcher, "run", lambda *args: calls.append(args))

    launcher._ensure_ollama_models("ollama.exe", "gemma3:4b")

    assert calls == [("ollama.exe", "pull", "nomic-embed-text")]


def test_embedding_signature_tracks_the_vector_space() -> None:
    launcher = _launcher()

    assert launcher._embedding_signature("ollama", {}) == "ollama:nomic-embed-text"
    assert (
        launcher._embedding_signature(
            "gemini",
            {"GEMINI_EMBEDDING_MODEL": "custom", "GEMINI_EMBEDDING_DIMENSION": "512"},
        )
        == "gemini:custom:512"
    )
