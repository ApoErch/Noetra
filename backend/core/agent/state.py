"""Graph state for one chat turn.

`messages` uses LangGraph's `add_messages` reducer (append, keyed by id); the list-typed
fields use `operator.add` so each node returns only what it adds. Everything else is
overwritten by the last node that sets it.
"""

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from core.retrieval import RetrievalHit


class ToolTrace(TypedDict):
    """One tool call as shown in the UI and stored with the message: what ran and a one-line outcome."""

    name: str
    args: dict[str, Any]
    summary: str


class AgentState(TypedDict, total=False):
    """Everything the loop accumulates while answering one user message."""

    messages: Annotated[list[AnyMessage], add_messages]
    # Every range a tool returned this turn — the only ranges the answer may cite.
    hits: Annotated[list[RetrievalHit], operator.add]
    tool_calls_made: int
    # `call_key`s already executed, for the no-progress guard.
    seen_calls: Annotated[list[str], operator.add]
    tool_trace: Annotated[list[ToolTrace], operator.add]
    # Set by the final node.
    answer: str
    citations: list[RetrievalHit]
    stripped: list[str]
