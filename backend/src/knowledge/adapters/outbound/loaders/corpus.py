"""Scan a KB directory into SourceFile descriptors.

Layout convention: `<root>/<source_type>/<file>` — the parent directory names
the source type (tariff|faq|regulation), the filename stem is the document_id,
and the first markdown heading (or the stem) is the title. Keeps ingestion
config-free for the demo corpus.
"""

import re
from pathlib import Path

from knowledge.application.dto import SourceFile
from knowledge.domain.values import SourceType

_SUPPORTED = {".pdf", ".md", ".markdown", ".txt"}
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")


def _title_of(path: Path) -> str:
    if path.suffix.lower() in (".md", ".markdown", ".txt"):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _HEADING.match(line)
            if match:
                return match.group(1).strip()
    return path.stem.replace("_", " ").replace("-", " ").title()


def scan_corpus(root: Path) -> tuple[SourceFile, ...]:
    files: list[SourceFile] = []
    for source_type in SourceType:
        directory = root / source_type.value
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in _SUPPORTED:
                continue
            files.append(
                SourceFile(
                    path=path,
                    source_type=source_type,
                    document_id=path.stem,
                    title=_title_of(path),
                )
            )
    return tuple(files)
