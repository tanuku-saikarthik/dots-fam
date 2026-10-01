"""The Dot graph: agent → approval gate → tools → agent.

The approval gate is where the Reversibility Law is enforced. `gate` judges each tool
call and saves the ones that change the outside world; `ask` interrupts. The run
stops, the checkpoint keeps its place, and the owner's decisions later resume the
thread exactly there, applied to exactly the saved calls. Declined calls come back
to the model as tool errors, and `tools` refuses any call that needs approval but
was not approved in this step.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage, trim_messages
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from .context import DotContext
from .prompts import system_prompt
from .tools import approval_reason

HISTORY_MESSAGES = 80
TOOL_RESULT_CHARS = 30_000


class DotState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    gated: list[dict[str, Any]]  # calls waiting for the owner (saved before the interrupt)
    approved: list[str]  # tool_call_ids the owner approved in this step


def _pending_calls(messages: list[AnyMessage]) -> tuple[AIMessage | None, list[dict[str, Any]]]:
    """Tool calls in the latest AI message that have no tool result yet."""
    answered: set[str] = set()
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            answered.add(message.tool_call_id)
            continue
        if isinstance(message, AIMessage):
            calls = [call for call in message.tool_calls if call["id"] not in answered]
            return message, calls
        break
    return None, []


def _summary(name: str, args: dict[str, Any]) -> str:
    for key in ("url", "title", "page", "path", "dot", "channel"):
        if isinstance(args.get(key), str):
            return f"{name} → {args[key][:120]}"
    if name == "delegate_tasks":
        return (
            f"delegate_tasks → {', '.join(a.get('dot', '?') for a in args.get('assignments', []))}"
        )
    return name


def _content(result: Any) -> str:
    text = (
        result if isinstance(result, str) else json.dumps(result, default=str, ensure_ascii=False)
    )
    return text[:TOOL_RESULT_CHARS]


def build_graph(
    ctx: DotContext,
    model: BaseChatModel,
    tools: list[BaseTool],
    checkpointer: BaseCheckpointSaver | None,
):
    tool_map = {tool.name: tool for tool in tools}
    bound = model.bind_tools(tools) if tools else model

    async def agent(state: DotState) -> dict:
        ctx.check()
        history = trim_messages(
            state["messages"],
            max_tokens=HISTORY_MESSAGES,
            token_counter=len,
            strategy="last",
            start_on="human",
            include_system=False,
            allow_partial=False,
        )
        response = await bound.ainvoke([SystemMessage(system_prompt(ctx)), *history])
        return {"messages": [response]}

    async def gate(state: DotState) -> dict:
        """Decide which calls need the owner. Saved to state, so a resume never re-judges them."""
        _message, calls = _pending_calls(state["messages"])
        gated = []
        for call in calls:
            reason = approval_reason(ctx, tool_map.get(call["name"]), call.get("args", {}))
            if reason:
                gated.append(
                    {
                        "tool_call_id": call["id"],
                        "tool": call["name"],
                        "args": call.get("args", {}),
                        "reason": reason,
                    }
                )
        return {"gated": gated, "approved": []}

    async def ask(state: DotState) -> dict:
        """Pause for the owner. On resume, exactly the saved calls get exactly their decisions."""
        gated = state.get("gated") or []
        decisions = interrupt({"kind": "approval", "dot_id": ctx.dot["id"], "items": gated}) or {}
        approved, declined = [], []
        for item in gated:
            decision = decisions.get(item["tool_call_id"]) or {}
            if decision.get("approved") is True:
                approved.append(item["tool_call_id"])
                continue
            note = decision.get("note")
            declined.append(
                ToolMessage(
                    content="The owner declined this action"
                    + (f": {note}" if note else ".")
                    + " Do not retry it unless the owner asks.",
                    tool_call_id=item["tool_call_id"],
                    name=item["tool"],
                    status="error",
                )
            )
        return {"messages": declined, "approved": approved, "gated": []}

    async def run_tools(state: DotState) -> dict:
        writer = get_stream_writer()
        _message, calls = _pending_calls(state["messages"])
        approved = set(state.get("approved") or [])
        results = []
        for call in calls:
            ctx.check()
            name, args = call["name"], call.get("args", {})
            summary = _summary(name, args)
            writer({"type": "tool.started", "id": call["id"], "name": name, "summary": summary})
            tool = tool_map.get(name)
            try:
                if tool is None:
                    raise ValueError(f"Unknown tool {name}.")
                # Judge again right before acting: earlier calls in this batch can change what a
                # call means (a new snapshot, a different focused field).
                reason = approval_reason(ctx, tool, args)
                if reason and call["id"] not in approved:
                    raise PermissionError(
                        f"Not run: this {reason} and needs the owner's approval. Call it again "
                        "on its own so the owner can approve it."
                    )
                output = await tool.ainvoke(args)
                content, status = _content(output), "success"
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - tool errors go back to the model
                if type(error).__name__ == "Stopped":
                    raise
                content, status = f"Error: {error}", "error"
            ctx.log(f"{summary}{'' if status == 'success' else ' (failed)'}", "tool")
            writer(
                {
                    "type": "tool.finished",
                    "id": call["id"],
                    "name": name,
                    "summary": summary,
                    "ok": status == "success",
                    "preview": content[:400],
                }
            )
            results.append(
                ToolMessage(content=content, tool_call_id=call["id"], name=name, status=status)
            )
        return {"messages": results}

    def after_agent(state: DotState) -> str:
        last = state["messages"][-1]
        return "gate" if isinstance(last, AIMessage) and last.tool_calls else END

    def after_gate(state: DotState) -> str:
        return "ask" if state.get("gated") else "tools"

    graph = StateGraph(DotState)
    graph.add_node("agent", agent)
    graph.add_node("gate", gate)
    graph.add_node("ask", ask)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", after_agent, ["gate", END])
    graph.add_conditional_edges("gate", after_gate, ["ask", "tools"])
    graph.add_edge("ask", "tools")
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)


def final_text(messages: list[AnyMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            content = message.content
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            if str(content).strip():
                return str(content).strip()
    return ""
