"""PRD001-FR-9: AsyncPostgresSaver setup + two-node graph checkpoint/resume
round-trip — de-risks phase 2 (roadmap risk register)."""

from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

pytestmark = pytest.mark.integration


class State(TypedDict):
    count: int


def _build_graph(checkpointer: AsyncPostgresSaver) -> Any:
    def first(state: State) -> State:
        return {"count": state["count"] + 1}

    def second(state: State) -> State:
        return {"count": state["count"] + 1}

    builder = StateGraph(State)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    return builder.compile(checkpointer=checkpointer, interrupt_before=["second"])


async def test_checkpointer_round_trip(database_urls: dict[str, str]) -> None:
    url = database_urls["psycopg"]
    thread = {"configurable": {"thread_id": "smoke-1"}}

    # First process: setup tables, run until the interrupt before node 2.
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()
        graph = _build_graph(saver)
        partial = await graph.ainvoke({"count": 0}, thread)
        assert partial["count"] == 1

    # Fresh saver instance (fresh connection): resume from the checkpoint.
    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        graph = _build_graph(saver)
        state = await graph.aget_state(thread)
        assert state.next == ("second",)

        final = await graph.ainvoke(None, thread)
        assert final["count"] == 2

        resumed = await graph.aget_state(thread)
        assert resumed.next == ()
        assert resumed.values["count"] == 2
