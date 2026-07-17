#!/usr/bin/env python3
"""Ingest the knowledge base into Chroma (PRD002-FR-5).

Idempotent by default: documents whose content_hash is unchanged are skipped
(visible in the logs as `kb.ingest.skipped_unchanged`). `--force` re-embeds all
documents, which is required after changing embedding providers or models.
Changed documents get a new version and their previous chunks are dropped.

Run through the backend environment so chromadb/langchain are available:
    cd backend && uv run --env-file ../.env python ../scripts/ingest_kb.py \
        [--kb DIR] [--prune] [--force]

`--prune` deletes chunks of documents no longer present in the source directory
(v1 stale handling — full sync is future work).
"""

import argparse
import asyncio
import sys
from pathlib import Path

DEFAULT_KB = Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures" / "kb"


async def ingest(kb_dir: Path, prune: bool, force: bool) -> int:
    from api.knowledge_wiring import KnowledgeProviders
    from knowledge.adapters.outbound.loaders.corpus import scan_corpus
    from knowledge.application.dto import IngestCommand
    from shared.adapters.clock import SystemClock
    from shared.adapters.event_publisher import event_transaction
    from shared.adapters.id_generator import UuidIdGenerator
    from shared.config import Settings
    from shared.container import Container
    from shared.domain.result import is_err
    from shared.logging.setup import configure_logging

    settings = Settings()  # type: ignore[call-arg] — env-provided fields
    configure_logging(settings.log_level)

    files = scan_corpus(kb_dir)
    if not files:
        print(f"ingest: no documents found under {kb_dir}")
        return 1

    providers = KnowledgeProviders.build(settings)
    container = Container.build(settings)
    try:
        async with (
            container.session_factory() as session,
            session.begin(),
            event_transaction(session),
        ):
            use_case = providers.ingest_use_case(
                clock=SystemClock(), id_generator=UuidIdGenerator()
            )
            result = await use_case.execute(
                IngestCommand(files=files, prune_missing=prune, force=force)
            )
    finally:
        await container.aclose()

    if is_err(result):
        print(f"ingest: failed — {result.error.code}: {result.error.message}")
        return 1

    report = result.value
    for doc in report.documents:
        detail = f" ({doc.detail})" if doc.detail else ""
        print(
            f"ingest: {doc.status:<8} {doc.document_id} v{doc.version} "
            f"chunks={doc.chunk_count}{detail}"
        )
    for document_id in report.pruned_document_ids:
        print(f"ingest: pruned   {document_id}")
    print(
        f"ingest: done — {report.ingested} ingested, {report.skipped} skipped, "
        f"{report.failed} failed"
    )
    return 1 if report.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest the demo knowledge base into Chroma")
    parser.add_argument("--kb", type=Path, default=DEFAULT_KB, help="KB source directory")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="delete chunks of documents missing from --kb",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-embed unchanged documents (required after changing embedding providers)",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(ingest(args.kb, args.prune, args.force))


if __name__ == "__main__":
    sys.exit(main())
