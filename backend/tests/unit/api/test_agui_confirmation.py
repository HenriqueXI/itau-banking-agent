from api.routers.agui import _encode
from conversation.application.dto import ConfirmationRequired


def test_confirmation_event_uses_server_owned_typed_payload() -> None:
    encoded = _encode(
        ConfirmationRequired(
            operation_hash="hash",
            operation="alterar_limite",
            current_amount="5000.00",
            requested_amount="15000.00",
            expires_at="2026-07-15T12:05:00+00:00",
            issued_at="2026-07-15T12:00:00+00:00",
        )
    )

    assert encoded is not None
    assert "event: confirmation_required" in encoded
    assert '"operationHash": "hash"' in encoded
    assert '"requestedAmount": "15000.00"' in encoded
    assert '"issuedAt": "2026-07-15T12:00:00+00:00"' in encoded
