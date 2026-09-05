"""The agent loop, hand-rolled as a LangGraph `StateGraph`:

    START → call_model ──(tool calls?)──► tools ──► call_model …
                │
                └──(no tool calls)──► verify_citations → END

`call_model` asks the chat model what to do next; `tools` executes what it asked for (our own
node, not the prebuilt ToolNode, so every result also records citable ranges, a UI trace and
the no-progress guard); `verify_citations` strips anything the model cited without evidence.
LangGraph only runs the graph — the loop logic is all in this file.
"""

import logging
import uuid
from typing import Any, Literal

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.orm import Session

from core.agent.citations import verify_citations
from core.agent.state import AgentState, ToolTrace
from core.agent.tools import TOOLS, TOOLS_BY_NAME, ToolResult, call_key
from core.ai.chat import get_chat_model

logger = logging.getLogger(__name__)

# Backstop only — the tool budget in state is what actually ends a runaway loop. Each tool
# round is two graph steps (call_model + tools), plus the final call_model and verify.
def recursion_limit(budget: int) -> int:
    """Graph-step ceiling that can only be hit if the budget check itself fails."""
    return 2 * budget + 4


class AgentConfig:
    """Per-turn resources handed to the nodes through LangGraph's `configurable` dict."""

    def __init__(self, db: Session, repository_id: uuid.UUID, system_prompt: str, budget: int, provider: str | None = None):
        self.db = db
        self.repository_id = repository_id
        self.system_prompt = system_prompt
        self.budget = budget
        self.provider = provider

    def runnable_config(self) -> RunnableConfig:
        """Wrap into the config object `graph.invoke`/`graph.stream` accept."""
        return {"configurable": {"agent": self}, "recursion_limit": recursion_limit(self.budget)}


def _agent_config(config: RunnableConfig) -> AgentConfig:
    """Pull our AgentConfig back out of the LangGraph config."""
    agent: AgentConfig = config["configurable"]["agent"]
    return agent


def call_model(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Ask the model for the next step: tool calls, or the final answer.

    The system prompt goes first and never changes within a repo, so the provider's automatic
    prompt cache hits on every turn. Once the tool budget is spent the tools are still declared
    (the history contains tool calls, which some providers reject otherwise) but the model is
    forbidden from calling them, so it must answer with what it has.
    """
    agent = _agent_config(config)
    over_budget = state.get("tool_calls_made", 0) >= agent.budget
    model = get_chat_model(agent.provider).bind_tools(TOOLS, tool_choice="none" if over_budget else None)
    messages: list[Any] = [SystemMessage(content=agent.system_prompt), *state["messages"]]
    if over_budget:
        messages.append(SystemMessage(content="Tool budget exhausted. Answer now from what you have, citing only what you saw."))
    response = model.invoke(messages)
    return {"messages": [response]}


def run_tools(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Execute every tool call in the last assistant message and collect what each produced."""
    agent = _agent_config(config)
    writer = get_stream_writer()
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)

    tool_messages: list[ToolMessage] = []
    hits = []
    seen: list[str] = []
    trace: list[ToolTrace] = []
    already = set(state.get("seen_calls", []))

    for call in last.tool_calls:
        name, args, call_id = call["name"], dict(call["args"]), call["id"] or ""
        key = call_key(name, args)
        writer({"type": "tool_call", "name": name, "args": args})

        if key in already or key in seen:
            result = ToolResult(
                text="You already ran this exact call. Change the arguments, or answer with what you have.",
                summary=f"{name} repeated — blocked",
            )
        else:
            result = _execute(name, args, agent)
        seen.append(key)

        tool_messages.append(ToolMessage(content=result.text, tool_call_id=call_id, name=name))
        hits.extend(result.hits)
        trace.append({"name": name, "args": args, "summary": result.summary})
        writer({"type": "tool_result", "name": name, "summary": result.summary})

    return {
        "messages": tool_messages,
        "hits": hits,
        "seen_calls": seen,
        "tool_trace": trace,
        "tool_calls_made": state.get("tool_calls_made", 0) + len(last.tool_calls),
    }


def _execute(name: str, args: dict[str, Any], agent: AgentConfig) -> ToolResult:
    """Run one tool with the injected DB/repo, turning any failure into text the model can act on."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return ToolResult(text=f"Unknown tool {name!r}.", summary=f"{name} — unknown tool")
    try:
        result: ToolResult = tool.invoke({**args, "db": agent.db, "repository_id": agent.repository_id})
        return result
    except Exception as exc:  # a bad argument or a DB error must not kill the turn
        logger.warning("tool %s failed: %s", name, exc)
        return ToolResult(text=f"{name} failed: {exc}", summary=f"{name} — error")


def should_continue(state: AgentState) -> Literal["tools", "verify_citations"]:
    """Route on whether the model asked for tools (loop) or answered (verify and finish)."""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "verify_citations"


def verify(state: AgentState) -> dict[str, Any]:
    """Final node: keep only citations backed by this turn's tool results."""
    last = state["messages"][-1]
    text = last.content if isinstance(last.content, str) else str(last.content)
    if not text.strip():
        # A content-filter refusal comes back with empty `content` and the reason in
        # `additional_kwargs["refusal"]`; without this the UI showed a blank bubble.
        refusal = last.additional_kwargs.get("refusal") if isinstance(last, AIMessage) else None
        text = refusal or "I can't respond to that. Ask me something about this repository's code."
    result = verify_citations(text, state.get("hits", []))
    if result.stripped:
        logger.info("stripped %d unbacked citations: %s", len(result.stripped), result.stripped)
    return {"answer": result.text, "citations": result.citations, "stripped": result.stripped}


def build_graph() -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    """Wire the nodes and edges above into a runnable graph."""
    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("tools", run_tools)
    graph.add_node("verify_citations", verify)
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", should_continue)
    graph.add_edge("tools", "call_model")
    graph.add_edge("verify_citations", END)
    return graph.compile()


AGENT = build_graph()
