from shared.config import Settings
from shared.container import Container
from tests.fakes import FakeEngine, FixedClock, SequentialIdGenerator

SETTINGS = Settings(
    _env_file=None,
    database_url="postgresql+asyncpg://app:app@localhost:5432/app",
    jwt_secret="test-secret",
)


def test_build_wires_system_defaults() -> None:
    container = Container.build(SETTINGS, engine=FakeEngine())
    assert container.clock.now().tzinfo is not None
    assert container.id_generator.new_id() != container.id_generator.new_id()


def test_build_accepts_overrides() -> None:
    clock = FixedClock()
    container = Container.build(SETTINGS, engine=FakeEngine(), clock=clock)
    assert container.clock is clock


def test_with_overrides_replaces_provider_only_in_copy() -> None:
    container = Container.build(SETTINGS, engine=FakeEngine())
    ids = SequentialIdGenerator()

    overridden = container.with_overrides(id_generator=ids)

    assert overridden.id_generator is ids
    assert container.id_generator is not ids
    assert overridden.settings is container.settings


async def test_aclose_disposes_engine() -> None:
    engine = FakeEngine()
    container = Container.build(SETTINGS, engine=engine)
    await container.aclose()
    assert engine.disposed
