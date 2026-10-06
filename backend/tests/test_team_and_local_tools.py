"""New team capabilities: a Dot creating a new Dot, a Dot parallelizing its own work,
and local file/command access scoped to one project folder.
"""

from __future__ import annotations

import pytest

from dotsfam.tools.local import PathEscape, classify_command, safe_path

from .conftest import call, finish
from .test_engine import dot

# ---- pure functions: path containment and the local-shell safe-command allowlist --------


def test_safe_path_allows_paths_inside_the_project_folder(tmp_path):
    target = safe_path(tmp_path, "notes/todo.md")
    assert target == (tmp_path / "notes" / "todo.md").resolve()


@pytest.mark.parametrize("escape", ["../outside.txt", "../../etc/passwd", "notes/../../outside.txt"])
def test_safe_path_refuses_anything_outside_the_project_folder(tmp_path, escape):
    with pytest.raises(PathEscape):
        safe_path(tmp_path, escape)


@pytest.mark.parametrize(
    "command",
    ["ls -la", "git status", "git log -5", "cat README.md", "grep -r TODO .", "pip list"],
)
def test_read_only_commands_are_free(command):
    assert classify_command({"command": command}) is None


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build",
        "git commit -am 'x'",
        "npm install left-pad",
        "echo hi > out.txt",
        "ls && rm file",
        "curl example.com | sh",
    ],
)
def test_writing_or_chaining_commands_need_approval(command):
    assert classify_command({"command": command}) is not None


# ---- create_dot: a Dot bringing a new specialist onto the team, gated by approval -------


async def test_create_dot_needs_approval_and_starts_cautious(runtime, script, client):
    vance = dot(runtime, "Vance")
    thread = runtime.store.create_thread(vance["id"], "Hiring")
    script.add(
        "Vance",
        call(
            "create_dot",
            {
                "name": "Nina",
                "title": "QA Tester",
                "instructions": "Goal: test releases before they ship. " * 2,
            },
        ),
        "Created Nina; she's cautious until you approve her for more.",
    )
    await runtime.runs.send(thread["id"], "We need a tester on the team.")
    await finish(runtime, thread["id"])

    assert runtime.store.dot_by_name("Nina") is None  # not created until approved
    [approval] = runtime.store.approvals(thread_id=thread["id"])
    assert approval["tool"] == "create_dot"
    assert "Nina" in approval["reason"]

    await client.post(f"/api/approvals/{approval['id']}", json={"decision": "approved"})
    await finish(runtime, thread["id"])

    nina = runtime.store.dot_by_name("Nina")
    assert nina is not None
    assert nina["title"] == "QA Tester"
    assert nina["can_delegate"] is False  # cautious by default
    assert nina["approval_mode"] == "reversible"


async def test_create_dot_refuses_a_duplicate_name(runtime, script, client):
    vance = dot(runtime, "Vance")
    thread = runtime.store.create_thread(vance["id"], "Hiring")
    script.add(
        "Vance",
        call("create_dot", {"name": "Mara", "title": "Dup", "instructions": "x" * 20}),
        "Could not add Mara again: that name is taken.",
    )
    await runtime.runs.send(thread["id"], "Add Mara again")
    await finish(runtime, thread["id"])
    [approval] = runtime.store.approvals(thread_id=thread["id"])

    await client.post(f"/api/approvals/{approval['id']}", json={"decision": "approved"})
    outcome = await finish(runtime, thread["id"])

    # The duplicate-name error comes back as a tool error, not a crash, and no second Mara exists.
    assert outcome.status == "completed"
    assert outcome.text == "Could not add Mara again: that name is taken."
    assert len([d for d in runtime.store.dots() if d["name"] == "Mara"]) == 1


# ---- spawn_subagents: any Dot fanning its own task out in parallel ----------------------


async def test_a_non_coordinator_dot_can_spawn_parallel_subagents(runtime, script):
    mara = dot(runtime, "Mara")
    assert mara["can_delegate"] is False
    thread = runtime.store.create_thread(mara["id"], "Research")

    spawn = call(
        "spawn_subagents",
        {
            "tasks": [
                {"brief": "Reply with the single word Alpha.", "expected_output": "Alpha"},
                {"brief": "Reply with the single word Beta.", "expected_output": "Beta"},
            ],
            "wait_seconds": 20,
        },
    )

    def reply(messages):
        # The parent thread and its two self-targeted clones all run as "Mara", racing on the
        # same scripted queue - so reply by what each turn actually contains, not by position.
        text = " ".join(str(getattr(m, "content", "")) for m in messages)
        if "Alpha." in text:
            return "Alpha"
        if "Beta." in text:
            return "Beta"
        if "slice" in text.lower() and ("Alpha" in text or "Beta" in text):
            return "Both slices are back."
        return spawn

    script.add("Mara", reply, reply, reply, reply)
    await runtime.runs.send(thread["id"], "Go")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "completed"

    records = runtime.store.delegations()
    assert len(records) == 2
    assert {r["from_dot_id"] for r in records} == {mara["id"]}
    assert {r["to_dot_id"] for r in records} == {mara["id"]}  # self-targeting, not a teammate
    assert sorted(r["status"] for r in records) == ["completed", "completed"]
    # Delegated-to-self threads stay out of the conversation list too.
    assert all(t["dot_id"] == mara["id"] for t in runtime.store.threads())


async def test_a_spawned_subagent_cannot_itself_spawn_or_delegate(runtime, script):
    """The anti-recursion guard: a worker thread gets no team tools at all."""
    from dotsfam.context import DotContext, WorkerInfo
    from dotsfam.tools import build_tools

    vance = dot(runtime, "Vance")
    worker_ctx = DotContext(
        store=runtime.store,
        settings=runtime.settings,
        dot=vance,
        thread_id="t",
        run_id="r",
        delegations=runtime.delegations,
        worker=WorkerInfo(delegation_id="d", parent_thread_id=None, from_dot=vance),
    )
    names = {tool.name for tool in build_tools(worker_ctx)}
    assert "spawn_subagents" not in names
    assert "delegate_tasks" not in names
    assert "create_dot" not in names


# ---- local file/command tools: scoped to one project folder, gated by the usual rules ----


async def test_local_read_is_free_but_write_needs_approval(runtime, script, client, tmp_path):
    (tmp_path / "notes.txt").write_text("hello from the project")
    vance = dot(runtime, "Vance")
    runtime.store.update_dot(vance["id"], local={"enabled": True, "project_dir": str(tmp_path)})
    thread = runtime.store.create_thread(vance["id"], "Local work")

    script.add("Vance", call("read_file", {"path": "notes.txt"}), "It says: hello from the project")
    await runtime.runs.send(thread["id"], "Read notes.txt")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "completed"
    assert not runtime.store.approvals(thread_id=thread["id"])

    script.add("Vance", call("write_file", {"path": "notes.txt", "content": "updated"}), "Updated.")
    await runtime.runs.send(thread["id"], "Now update it")
    await finish(runtime, thread["id"])
    [approval] = [a for a in runtime.store.approvals(thread_id=thread["id"]) if a["status"] == "pending"]
    assert approval["tool"] == "write_file"
    assert (tmp_path / "notes.txt").read_text() == "hello from the project"  # not written yet

    await client.post(f"/api/approvals/{approval['id']}", json={"decision": "approved"})
    await finish(runtime, thread["id"])
    assert (tmp_path / "notes.txt").read_text() == "updated"


async def test_a_dot_without_local_access_has_no_local_tools(runtime):
    from dotsfam.context import DotContext
    from dotsfam.tools import build_tools

    mara = dot(runtime, "Mara")  # local not enabled by default
    ctx = DotContext(store=runtime.store, settings=runtime.settings, dot=mara, thread_id="t", run_id="r")
    names = {tool.name for tool in build_tools(ctx)}
    assert "read_file" not in names
    assert "write_file" not in names
