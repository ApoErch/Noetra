"""Run one chat turn through the agent graph — the single entry point for the API and the eval.

`run_turn` blocks and returns the finished turn; `stream_turn` yields events (tool status,
tokens, the final result) for SSE. Both build the same config, so the eval measures exactly
what the chat endpoint serves.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.messages import AIMessage, AIMessageChunk, AnyMessage, HumanMessage
from sqlalchemy.orm import Session

from core.agent.graph import AGENT, AgentConfig
from core.agent.prompts import build_system_prompt
from core.agent.repo_map import get_repo_map
from core.agent.tools import GRAPH_TOOLS, TOOLS
from core.agent.state import AgentState, ToolTrace
from core.config import get_settings
from core.models import Repository
from core.retrieval import RetrievalHit


@dataclass
class TurnResult:
    """Everything one answered question produced, for persisting, rendering, and scoring."""

    answer: str
    citations: list[RetrievalHit]
    tool_trace: list[ToolTrace]
    hits: list[RetrievalHit]  # every range retrieved this turn (the eval scores on these)
    stripped: list[str]
    tool_calls_made: int
    input_tokens: int = 0
    output_tokens: int = 0


def _config(
    db: Session,
    repo: Repository,
    *,
    provider: str | None,
    budget: int | None,
    repo_map: bool,
    graph_tools: bool = True,
) -> AgentConfig:
    """Assemble the per-turn config: system prompt (with or without the map) and the budget."""
    settings = get_settings()
    budget = budget if budget is not None else settings.agent_tool_budget
    rendered = get_repo_map(db, repo, settings.agent_repo_map_tokens) if repo_map else ""
    # The M8 ablation drops the graph tool from both the bound tool list and the prompt, so a
    # tools-off run is a genuine "agent without the call graph", not one told to use a tool
    # it cannot call.
    tools = TOOLS if graph_tools else [t for t in TOOLS if t not in GRAPH_TOOLS]
    return AgentConfig(
        db=db,
        repository_id=repo.id,
        system_prompt=build_system_prompt(repo.name, rendered, budget, graph_tools=graph_tools),
        budget=budget,
        provider=provider,
        tools=tools,
    )


def _initial_state(history: list[AnyMessage], question: str) -> AgentState:
    """History (prior user/assistant messages only) plus the new question."""
    return {"messages": [*history, HumanMessage(content=question)], "tool_calls_made": 0}


def _result(state: AgentState) -> TurnResult:
    """Read the finished graph state into a TurnResult, summing token usage over model calls."""
    input_tokens = output_tokens = 0
    for message in state["messages"]:
        if isinstance(message, AIMessage) and message.usage_metadata:
            input_tokens += message.usage_metadata["input_tokens"]
            output_tokens += message.usage_metadata["output_tokens"]
    return TurnResult(
        answer=state.get("answer", ""),
        citations=state.get("citations", []),
        tool_trace=state.get("tool_trace", []),
        hits=state.get("hits", []),
        stripped=state.get("stripped", []),
        tool_calls_made=state.get("tool_calls_made", 0),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def run_turn(
    db: Session,
    repo: Repository,
    question: str,
    history: list[AnyMessage] | None = None,
    *,
    provider: str | None = None,
    budget: int | None = None,
    repo_map: bool = True,
    graph_tools: bool = True,
) -> TurnResult:
    """Answer one question and return the whole turn (no streaming)."""
    config = _config(db, repo, provider=provider, budget=budget, repo_map=repo_map, graph_tools=graph_tools)
    final = AGENT.invoke(_initial_state(history or [], question), config.runnable_config())
    return _result(cast(AgentState, final))


def stream_turn(
    db: Session,
    repo: Repository,
    question: str,
    history: list[AnyMessage] | None = None,
    *,
    provider: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Answer one question, yielding events as they happen; the last event is `{"type": "result", "result": TurnResult}`.

    Event types: `tool_call {name, args}`, `tool_result {name, summary}` (emitted by the tools
    node via `get_stream_writer`), `token {text}` (answer text as the model produces it).
    """
    config = _config(db, repo, provider=provider, budget=None, repo_map=True)
    final: AgentState | None = None

    stream: Iterator[Any] = AGENT.stream(
        _initial_state(history or [], question),
        config.runnable_config(),
        stream_mode=["custom", "messages", "values"],
    )
    for mode, chunk in stream:
        if mode == "custom":
            yield chunk
        elif mode == "messages":
            message, metadata = chunk
            # Only the model's own text, not tool-call argument fragments (those have
            # tool_call_chunks and empty content on OpenAI; Anthropic may narrate first,
            # which is fine to show).
            if (
                metadata.get("langgraph_node") == "call_model"
                and isinstance(message, AIMessageChunk)
                and isinstance(message.content, str)
                and message.content
            ):
                yield {"type": "token", "text": message.content}
        elif mode == "values":
            final = chunk

    assert final is not None
    yield {"type": "result", "result": _result(final)}
