"""structlog processors: PII masking over every emitted event."""

from collections.abc import MutableMapping
from typing import Any

from shared.logging.masking import mask_mapping


def mask_pii_processor(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    return mask_mapping(event_dict)
