"""Graph assembly.

    START → supervisor ⇄ {data_agent ∥ news_agent} → writer → verify → gate → deliver → END
                │                                        ↑        │
                └──── abort (budget / iteration cap) ─────┘   one regeneration

The supervisor's conditional edge returns a *list* of node names, which is what
makes the two workers execute in the same LangGraph superstep. Their concurrent
writes are merged by the reducers declared on :class:`~app.graph.state.RunState`.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph.context import RunContext
from app.graph.data_agent import data_agent_node
from app.graph.deliver import deliver_node
from app.graph.gate import gate_node, route_after_gate
from app.graph.news_agent import news_agent_node
from app.graph.state import RunState
from app.graph.supervisor import route_from_supervisor, supervisor_node
from app.graph.verify import route_after_verify, verify_node
from app.graph.writer import writer_node

logger = logging.getLogger(__name__)

NODE_NAMES = (
    "supervisor",
    "data_agent",
    "news_agent",
    "writer",
    "verify",
    "gate",
    "deliver",
)


def build_state_graph() -> StateGraph[RunState, RunContext, RunState, RunState]:
    """The uncompiled graph — useful for diagrams and structural tests.

    ``context_schema=RunContext`` declares the per-run dependency channel. Unlike
    ``config["configurable"]``, runtime context is not checkpoint state, so live
    handles (the open MCP session, the event bus) survive a run with a
    checkpointer attached.
    """
    graph: StateGraph[RunState, RunContext, RunState, RunState] = StateGraph(
        RunState, context_schema=RunContext
    )

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("data_agent", data_agent_node)
    graph.add_node("news_agent", news_agent_node)
    graph.add_node("writer", writer_node)
    graph.add_node("verify", verify_node)
    graph.add_node("gate", gate_node)
    graph.add_node("deliver", deliver_node)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        ["data_agent", "news_agent", "writer", END],
    )
    # Workers rejoin the supervisor, which re-plans from merged state.
    graph.add_edge("data_agent", "supervisor")
    graph.add_edge("news_agent", "supervisor")

    graph.add_edge("writer", "verify")
    graph.add_conditional_edges("verify", route_after_verify, ["writer", "gate"])
    graph.add_conditional_edges("gate", route_after_gate, ["deliver", END])
    graph.add_edge("deliver", END)
    return graph


def build_graph(checkpointer: Any | None = None) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile the graph with a checkpointer (required for the human gate)."""
    return build_state_graph().compile(checkpointer=checkpointer)


def mermaid_diagram() -> str:
    """Mermaid source for the README, generated from the real graph."""
    return build_state_graph().compile().get_graph().draw_mermaid()
