from shared.adapters.outbox_relay import OutboxRelay


class _Unused:
    pass


def _relay() -> OutboxRelay:
    return OutboxRelay(
        session_factory=_Unused(),  # type: ignore[arg-type]
        event_bus=_Unused(),  # type: ignore[arg-type]
        clock=_Unused(),  # type: ignore[arg-type]
        batch_size=100,
        max_attempts=5,
        max_backoff_seconds=60,
    )


def test_retry_delay_is_exponential_and_capped() -> None:
    relay = _relay()
    assert [relay.retry_delay_seconds(i) for i in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
