from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from dotsfam.team import TEAM_SPACE

from .conftest import call, finish


def dot(runtime, name):
    return runtime.store.dot_by_name(name)


async def test_first_start_installs_team_hq_and_five_role_cards(runtime):
    names = [d["name"] for d in runtime.store.dots()]
    assert names == ["Vance", "Mara", "Cole", "Rina", "Owen"]
    vance = dot(runtime, "Vance")
    assert vance["can_delegate"] is True
    assert vance["model"] == "anthropic:claude-sonnet-4-5"
    assert dot(runtime, "Mara")["model"] == "openai:gpt-5-mini"
    assert all(d["approval_mode"] == "reversible" for d in runtime.store.dots())
    space = runtime.store.space_by_name(TEAM_SPACE)
    titles = {p["title"] for p in runtime.store.pages(space["id"])}
    assert titles == {"Launch Brief", "Approved Messaging", "Lead Staging"}


async def test_chat_turn_streams_text_and_keeps_history(runtime, script):
    vance = dot(runtime, "Vance")
    thread = runtime.store.create_thread(vance["id"], "New conversation")
    queue = runtime.runs.subscribe(thread["id"])
    script.add("Vance", "Morning! Two launches need you today.")
    await runtime.runs.send(thread["id"], "What needs me today?")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "completed"
    assert outcome.text == "Morning! Two launches need you today."
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    kinds = [e["type"] for e in events]
    assert kinds[0] == "run.started" and kinds[-1] == "run.finished"
    assert any(e["type"] == "message.delta" for e in events)
    history = await runtime.runs.history(thread["id"])
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert runtime.store.thread(thread["id"])["title"] == "What needs me today?"
    # The system prompt carries the role card and the team roster.
    system = script.calls["vance"][0][0].content
    assert "Chief of Staff" in system and "Mara" in system and "Reversibility Law" in system


async def test_tools_write_pages_with_revision_checks(runtime, script):
    rina = dot(runtime, "Rina")
    thread = runtime.store.create_thread(rina["id"], "Copy")
    script.add(
        "Rina",
        call("create_page", {"title": "Landing copy", "content": "# Hello"}),
        call(
            "update_page",
            {"page": "Landing copy", "expected_revision": 9, "content": "# Stale"},
            "c2",
        ),
        "Created the page.",
    )
    await runtime.runs.send(thread["id"], "Draft landing copy")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "completed"
    space = runtime.store.space_by_name(TEAM_SPACE)
    page = runtime.store.page_by_title(space["id"], "Landing copy")
    assert page["author"] == "Rina" and page["revision"] == 1
    tool_results = [m for m in script.calls["rina"][-1] if isinstance(m, ToolMessage)]
    assert "Page changed since revision 9" in tool_results[-1].content


async def test_chief_delegates_in_parallel_to_isolated_specialists(runtime, script):
    vance = dot(runtime, "Vance")
    thread = runtime.store.create_thread(vance["id"], "Launch")
    script.delay = 0.05
    script.add(
        "Vance",
        call(
            "delegate_tasks",
            {
                "assignments": [
                    {
                        "dot": "Mara",
                        "brief": "List two companies hiring AI engineers in Bengaluru.",
                        "expected_output": "Table with evidence URLs.",
                    },
                    {
                        "dot": "owen",
                        "brief": "Summarize pipeline risk for this week in three numbers.",
                    },
                ],
                "wait_seconds": 20,
            },
        ),
        lambda messages: AIMessage(content="Merged: " + messages[-1].content[:60]),
    )
    script.add("Mara", "| Acme | hiring | https://acme.example/jobs |")
    script.add("Owen", "Churn risk up 2 accounts.")
    await runtime.runs.send(thread["id"], "PRIVATE-CONTEXT: prep the launch.")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "completed"
    assert outcome.text.startswith("Merged:")
    # Isolation: specialists only see their brief, never the owner's message.
    mara_seen = " ".join(str(m.content) for m in script.calls["mara"][0])
    assert "List two companies hiring" in mara_seen
    assert "PRIVATE-CONTEXT" not in mara_seen
    assert "Expected output" in mara_seen
    assert script.models["mara"] == "openai:gpt-5-mini"
    assert script.models["vance"] == "anthropic:claude-sonnet-4-5"
    records = runtime.store.delegations()
    assert sorted(r["status"] for r in records) == ["completed", "completed"]
    assert {r["result"] for r in records} == {
        "| Acme | hiring | https://acme.example/jobs |",
        "Churn risk up 2 accounts.",
    }
    # Delegated threads stay out of the conversation list.
    assert all(t["dot_id"] == vance["id"] for t in runtime.store.threads())
    # The merge turn saw both deliverables.
    tool_message = [m for m in script.calls["vance"][-1] if isinstance(m, ToolMessage)][-1]
    assert "acme.example/jobs" in tool_message.content and "Churn risk" in tool_message.content


async def test_slow_work_is_delivered_later_as_a_follow_up(runtime, script):
    vance = dot(runtime, "Vance")
    thread = runtime.store.create_thread(vance["id"], "Leads")
    release = asyncio.Event()

    async def slow(messages):
        return AIMessage(content="Prospect table with 3 rows.")

    script.add(
        "Vance",
        call(
            "delegate_tasks",
            {
                "assignments": [{"dot": "Mara", "brief": "Scan career pages at Acme and Globex."}],
                "wait_seconds": 5,
            },
        ),
        "Mara is on it; results will follow.",
    )

    original = runtime.runs.model_factory

    def gated_factory(settings, ref, d):
        model = original(settings, ref, d)
        if d["name"] == "Mara":

            async def wait_then(*args, **kwargs):
                await release.wait()
                return AIMessage(content="Prospect table with 3 rows.")

            object.__setattr__(model, "ainvoke", wait_then)
        return model

    runtime.runs.model_factory = gated_factory
    await runtime.runs.send(thread["id"], "Find leads")
    outcome = await finish(runtime, thread["id"], seconds=20)
    assert outcome.status == "completed"
    tool_message = [m for m in script.calls["vance"][-1] if isinstance(m, ToolMessage)][-1]
    assert "still in progress" in tool_message.content
    assert runtime.store.tasks() == []
    release.set()
    for _ in range(100):
        if runtime.store.tasks():
            break
        await asyncio.sleep(0.05)
    [task] = runtime.store.tasks()
    assert task["thread_id"] == thread["id"] and task["origin"] == "team"
    assert "Prospect table with 3 rows." in task["prompt"]


def external_tool(executed: list):
    async def send_email(to: str, body: str) -> str:
        executed.append((to, body))
        return f"sent to {to}"

    return StructuredTool.from_function(
        coroutine=send_email,
        name="send_email",
        description="Send an email.",
        metadata={"external": True, "reason": "sends an email"},
    )


class FakeOutbox:
    def __init__(self):
        self.executed = []

    def tools(self, ctx):
        return [external_tool(self.executed)]


async def test_outside_world_actions_pause_for_approval_then_resume(runtime, script, client):
    outbox = FakeOutbox()
    runtime.runs.extra["slack"] = outbox
    cole = dot(runtime, "Cole")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    script.add(
        "Cole", call("send_email", {"to": "ana@acme.example", "body": "Hi Ana"}), "Sent to Ana."
    )
    await runtime.runs.send(thread["id"], "Email Ana")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "waiting_approval"
    assert outbox.executed == []
    [approval] = runtime.store.approvals(thread_id=thread["id"])
    assert approval["tool"] == "send_email" and approval["reason"] == "sends an email"
    assert approval["args"]["to"] == "ana@acme.example"
    # New owner messages wait until the approval is decided.
    response = await client.post(f"/api/threads/{thread['id']}/messages", json={"text": "hello?"})
    assert response.status_code == 409
    response = await client.post(f"/api/approvals/{approval['id']}", json={"decision": "approved"})
    assert response.json()["resumed"] is True
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "completed" and outcome.text == "Sent to Ana."
    assert outbox.executed == [("ana@acme.example", "Hi Ana")]


async def test_declined_actions_never_run(runtime, script, client):
    outbox = FakeOutbox()
    runtime.runs.extra["slack"] = outbox
    cole = dot(runtime, "Cole")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    script.add(
        "Cole", call("send_email", {"to": "bo@x.example", "body": "Hi"}), "Understood, not sent."
    )
    await runtime.runs.send(thread["id"], "Email Bo")
    await finish(runtime, thread["id"])
    [approval] = runtime.store.approvals(thread_id=thread["id"])
    await client.post(
        f"/api/approvals/{approval['id']}", json={"decision": "declined", "note": "wrong person"}
    )
    outcome = await finish(runtime, thread["id"])
    assert outcome.text == "Understood, not sent."
    assert outbox.executed == []
    declined = [m for m in script.calls["cole"][-1] if isinstance(m, ToolMessage)][-1]
    assert "declined" in declined.content and "wrong person" in declined.content


async def test_autonomous_dots_skip_the_gate(runtime, script):
    outbox = FakeOutbox()
    runtime.runs.extra["slack"] = outbox
    cole = runtime.store.update_dot(dot(runtime, "Cole")["id"], approval_mode="autonomous")
    thread = runtime.store.create_thread(cole["id"], "Outreach")
    script.add("Cole", call("send_email", {"to": "a@b.example", "body": "x"}), "Done.")
    await runtime.runs.send(thread["id"], "Email")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "completed" and len(outbox.executed) == 1


async def test_a_specialists_approval_routes_back_through_the_chief(runtime, script, client):
    outbox = FakeOutbox()
    runtime.runs.extra["slack"] = outbox
    vance = dot(runtime, "Vance")
    thread = runtime.store.create_thread(vance["id"], "Outreach run")
    script.add(
        "Vance",
        call(
            "delegate_tasks",
            {
                "assignments": [{"dot": "Cole", "brief": "Email the Acme CTO the approved intro."}],
                "wait_seconds": 10,
            },
        ),
        "Cole's email is waiting for your approval.",
    )
    script.add(
        "Cole",
        call("send_email", {"to": "cto@acme.example", "body": "Intro"}),
        "Email sent to the CTO.",
    )
    await runtime.runs.send(thread["id"], "Run outreach")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "completed"
    result = [m for m in script.calls["vance"][-1] if isinstance(m, ToolMessage)][-1].content
    assert "waiting_for_owner_approval" in result and "Cole" in result
    [record] = runtime.store.delegations()
    assert record["status"] == "waiting_approval"
    [approval] = [a for a in runtime.store.approvals() if a["status"] == "pending"]
    assert approval["dot_id"] == dot(runtime, "Cole")["id"]
    await client.post(f"/api/approvals/{approval['id']}", json={"decision": "approved"})
    await finish(runtime, record["worker_thread_id"])
    assert outbox.executed == [("cto@acme.example", "Intro")]
    assert runtime.store.delegation(record["id"])["status"] == "completed"
    [task] = runtime.store.tasks()
    assert task["thread_id"] == thread["id"] and "Email sent to the CTO." in task["prompt"]


async def test_pause_stops_running_work(runtime, script, client):
    script.delay = 2
    mara = dot(runtime, "Mara")
    thread = runtime.store.create_thread(mara["id"], "Research")
    await runtime.runs.send(thread["id"], "Research Acme")
    await asyncio.sleep(0.1)
    await client.patch("/api/settings", json={"paused": True})
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "cancelled"
    response = await client.post(f"/api/threads/{thread['id']}/messages", json={"text": "again"})
    assert response.status_code == 423


async def test_history_round_trips_tool_calls(runtime, script):
    rina = dot(runtime, "Rina")
    thread = runtime.store.create_thread(rina["id"], "x")
    script.add("Rina", call("list_spaces", {}), "You have Team HQ.")
    await runtime.runs.send(thread["id"], "Which spaces?")
    await finish(runtime, thread["id"])
    history = await runtime.runs.history(thread["id"])
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]
    assert history[1]["tool_calls"][0]["name"] == "list_spaces"
    assert history[2]["ok"] is True and "Team HQ" in history[2]["text"]
    assert isinstance(HumanMessage("x"), HumanMessage)
