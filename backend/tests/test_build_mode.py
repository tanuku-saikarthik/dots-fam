"""Build mode: a Dot codes on its own branch, runs commands in a sandbox, and only asks to push."""
# ruff: noqa: ASYNC240, ASYNC221

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dotsfam import workspace as ws_module
from dotsfam.workspace import github_compare_url, sandbox_command

from .conftest import call, finish


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Owner", "-c", "user.email=o@x.example", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    project = tmp_path / "project"
    project.mkdir()
    sh(project, "init", "-q", "-b", "main")
    (project / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (project / ".gitignore").write_text(".env\n")
    sh(project, "add", "-A")
    sh(project, "commit", "-qm", "start")
    sh(project, "remote", "add", "origin", str(remote))
    sh(project, "push", "-q", "origin", "main")
    (project / ".env").write_text("API_KEY=owner-secret\n")  # untracked, like real life
    (project / "notes.txt").write_text("owner's uncommitted work\n")
    return project


@pytest.fixture
def builder(runtime, repo):
    owen = runtime.store.dot_by_name("Owen")
    return runtime.store.update_dot(
        owen["id"], local={"enabled": True, "project_dir": str(repo), "mode": "build"}
    )


@pytest.fixture
def fake_sandbox(monkeypatch):
    """Run sandbox commands on the host, but record that they went to the sandbox."""
    calls: list[tuple[Path, str]] = []

    async def run(settings, dot_id, workdir, command, seconds):
        calls.append((workdir, command))
        out = await asyncio.to_thread(
            subprocess.run, command, shell=True, cwd=workdir, capture_output=True, text=True
        )
        return f"{out.stdout}{out.stderr}(exit {out.returncode})"

    monkeypatch.setattr(ws_module, "sandbox_available", lambda: True)
    monkeypatch.setattr(ws_module, "run_in_sandbox", run)
    return calls


async def test_coding_on_a_branch_needs_no_approvals_until_the_push(
    runtime, script, client, repo, builder, fake_sandbox
):
    thread = runtime.store.create_thread(builder["id"], "Fix add")
    script.add(
        "Owen",
        call("read_file", {"path": "app.py"}, "r1"),
        call("edit_file", {"path": "app.py", "old_string": "a - b", "new_string": "a + b"}, "e1"),
        call(
            "write_file",
            {"path": "test_app.py", "content": "from app import add\nassert add(2, 3) == 5\n"},
            "w1",
        ),
        call("run_command", {"command": "python3 test_app.py && echo PASS"}, "t1"),
        call("glob_files", {"pattern": "*"}, "g1"),
        call("commit_work", {"message": "Fix add and test it"}, "c1"),
        call("open_pull_request", {"title": "Fix add", "body": "Tested."}, "p1"),
        "Opened the pull request.",
    )
    await runtime.runs.send(thread["id"], "Fix add() and open a PR")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "waiting_approval"
    # Only the pull request asked.
    [approval] = runtime.store.approvals(thread_id=thread["id"])
    assert approval["tool"] == "open_pull_request"
    [ws] = runtime.store.workspaces(builder["id"])
    assert approval["reason"] == f"pushes {ws['branch']} and opens a pull request: Fix add"
    assert ws["branch"].startswith("dots/fix-add-") and ws["base"] == "main"

    work = Path(ws["path"])
    assert work.parent.parent == runtime.settings.data_dir.resolve() / "workspaces"
    assert (work / "app.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert not (work / ".env").exists() and not (work / "notes.txt").exists()
    assert fake_sandbox == [(work, "python3 test_app.py && echo PASS")]
    assert "PASS" in str(script.calls["owen"][4][-1].content)  # the test ran and passed

    # The owner's checkout is untouched: same branch, same files, uncommitted work intact.
    assert sh(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert (repo / "app.py").read_text().endswith("a - b\n")
    assert not (repo / "test_app.py").exists()
    assert (repo / "notes.txt").exists() and (repo / ".env").exists()
    log = sh(repo, "log", "--format=%an|%s", ws["branch"])
    assert log.splitlines()[0] == "Owen (Dots Fam)|Fix add and test it"

    # Nothing left the machine yet.
    remote = repo.parent / "remote.git"
    assert ws["branch"] not in sh(remote, "branch", "--list")
    response = await client.post(f"/api/approvals/{approval['id']}", json={"decision": "approved"})
    assert response.json()["resumed"] is True
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "completed"
    assert ws["branch"] in sh(remote, "branch", "--list")
    assert sh(remote, "show", f"{ws['branch']}:app.py").endswith("a + b")
    assert runtime.store.workspaces(builder["id"])[0]["pushed_at"]


async def test_secret_files_ask_and_git_internals_are_refused(
    runtime, script, repo, builder, fake_sandbox
):
    thread = runtime.store.create_thread(builder["id"], "Config")
    script.add(
        "Owen",
        call("write_file", {"path": ".git/config", "content": "[core]"}, "w1"),
        call("write_file", {"path": "../escape.txt", "content": "x"}, "w2"),
        call("write_file", {"path": "config/.env", "content": "KEY=1"}, "w3"),
        "Done.",
    )
    await runtime.runs.send(thread["id"], "Set up config")
    outcome = await finish(runtime, thread["id"])
    assert outcome.status == "waiting_approval"
    [approval] = runtime.store.approvals(thread_id=thread["id"])
    assert approval["tool"] == "write_file" and approval["args"]["path"] == "config/.env"
    assert "may hold secrets" in approval["reason"]
    [ws] = runtime.store.workspaces(builder["id"])
    git_file = Path(ws["path"]) / ".git"
    assert git_file.is_file() and git_file.read_text().startswith("gitdir:")  # not overwritten
    assert not (repo.parent / "escape.txt").exists()
    assert not (Path(ws["path"]).parent / "escape.txt").exists()


async def test_commit_leaves_out_secret_files_made_in_the_sandbox(runtime, repo, builder):
    workspaces = ws_module.Workspaces(runtime.store, runtime.settings)
    thread = runtime.store.create_thread(builder["id"], "Keys")
    ws = await workspaces.ensure(builder, thread["id"])
    (Path(ws["path"]) / ".env.local").write_text("TOKEN=abc")
    (Path(ws["path"]) / "id_rsa").write_text("key")
    (Path(ws["path"]) / "readme.md").write_text("hi")
    out = await workspaces.commit(ws, "Add readme", "Owen")
    assert "readme.md" in out and "Left out secret-looking files" in out
    files = sh(repo, "ls-tree", "-r", "--name-only", ws["branch"]).splitlines()
    assert "readme.md" in files and ".env.local" not in files and "id_rsa" not in files
    assert await workspaces.commit(ws, "Again", "Owen") == (
        "Nothing new to commit (left out secret-looking files: .env.local, id_rsa)."
    )


async def test_hooks_never_run(runtime, repo, builder):
    hook = repo / ".git" / "hooks" / "pre-commit"
    marker = repo.parent / "hook-ran"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    workspaces = ws_module.Workspaces(runtime.store, runtime.settings)
    thread = runtime.store.create_thread(builder["id"], "Hooks")
    ws = await workspaces.ensure(builder, thread["id"])
    (Path(ws["path"]) / "a.txt").write_text("a")
    await workspaces.commit(ws, "Add a", "Owen")
    assert not marker.exists()


async def test_specialists_share_the_owner_conversations_branch(
    runtime, script, repo, builder, fake_sandbox
):
    vance = runtime.store.dot_by_name("Vance")
    thread = runtime.store.create_thread(vance["id"], "Ship it")
    script.add(
        "Vance",
        call(
            "delegate_tasks",
            {"assignments": [{"dot": "Owen", "brief": "Add a VERSION file."}], "wait_seconds": 20},
        ),
        "Owen added it.",
    )
    script.add(
        "Owen",
        call("write_file", {"path": "VERSION", "content": "1.0\n"}, "w1"),
        call("commit_work", {"message": "Add VERSION"}, "c1"),
        "Added VERSION on the branch.",
    )
    await runtime.runs.send(thread["id"], "Have Owen add a VERSION file")
    from .test_slack import settle

    await settle(runtime)
    [ws] = runtime.store.workspaces(builder["id"])
    assert ws["thread_id"] == thread["id"]  # keyed to the owner's conversation
    assert sh(repo, "show", f"{ws['branch']}:VERSION") == "1.0"
    assert runtime.store.approvals() == []
    system = script.calls["vance"][0][0].content
    assert f"Codes in {repo}" in system


async def test_ask_mode_is_unchanged(runtime, script, repo):
    owen = runtime.store.dot_by_name("Owen")
    owen = runtime.store.update_dot(owen["id"], local={"enabled": True, "project_dir": str(repo)})
    thread = runtime.store.create_thread(owen["id"], "Edit")
    script.add("Owen", call("write_file", {"path": "x.txt", "content": "x"}, "w1"), "ok")
    await runtime.runs.send(thread["id"], "write x")
    assert (await finish(runtime, thread["id"])).status == "waiting_approval"
    assert runtime.store.workspaces(owen["id"]) == []


async def test_build_mode_api(runtime, client, repo, builder, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    owen = builder["id"]
    bad = await client.patch(f"/api/local/{owen}", json={"project_dir": str(plain)})
    assert bad.status_code == 400 and "git repository" in bad.json()["detail"]
    ok = await client.patch(f"/api/local/{owen}", json={"project_dir": str(repo), "mode": "build"})
    assert ok.json()["local"]["mode"] == "build"
    assert (await client.get("/api/state")).json()["setup"]["build_sandbox"] in (True, False)

    workspaces = ws_module.Workspaces(runtime.store, runtime.settings)
    ws = await workspaces.ensure(builder, runtime.store.create_thread(owen, "Docs")["id"])
    (Path(ws["path"]) / "doc.md").write_text("doc")
    await workspaces.commit(ws, "Add doc", "Owen")
    (Path(ws["path"]) / "draft.md").write_text("draft")
    [row] = (await client.get(f"/api/local/{owen}/workspaces")).json()
    assert row["changes"] == {"exists": True, "commits": 1, "files": 1, "uncommitted": 1}
    gone = await client.delete(f"/api/local/{owen}/workspaces/{ws['id']}")
    assert gone.json() == {"ok": True}
    assert not Path(ws["path"]).exists()
    assert ws["branch"] in sh(repo, "branch", "--list")  # the commits are kept
    assert (await client.get(f"/api/local/{owen}/workspaces")).json() == []


def test_sandbox_command_mounts_only_the_branch(settings, tmp_path):
    args = sandbox_command(settings, "dot123", tmp_path / "work", "npm test", "dots-build-x")
    joined = " ".join(args)
    assert args[:3] == ["docker", "run", "--rm"]
    assert f"{tmp_path / 'work'}:/work" in joined
    assert "--cap-drop ALL" in joined and "no-new-privileges" in joined
    assert "--pids-limit 512" in joined and "--memory 2g" in joined
    assert args[-4:] == ["dotsfam-build", "sh", "-c", "npm test"]
    volumes = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
    assert len(volumes) == 2 and volumes[1].endswith("/build-cache/dot123:/cache")


def test_compare_url():
    assert (
        github_compare_url("git@github.com:me/app.git", "main", "dots/x-1")
        == "https://github.com/me/app/compare/main...dots/x-1?expand=1"
    )
    assert github_compare_url("https://gitlab.com/me/app.git", "main", "b") is None


def _docker_image(name: str) -> bool:
    if not shutil.which("docker"):
        return False
    result = subprocess.run(["docker", "image", "inspect", name], capture_output=True)
    return result.returncode == 0


@pytest.mark.skipif(
    not _docker_image("dotsfam-build-test"), reason="needs Docker and the dotsfam-build-test image"
)
async def test_real_sandbox_sees_only_the_branch(runtime, repo, builder, tmp_path):
    runtime.settings.build_image = "dotsfam-build-test"
    workspaces = ws_module.Workspaces(runtime.store, runtime.settings)
    ws = await workspaces.ensure(builder, runtime.store.create_thread(builder["id"], "Sbx")["id"])
    work = Path(ws["path"])
    out = await ws_module.run_in_sandbox(
        runtime.settings,
        builder["id"],
        work,
        "ls -a /work; cat /work/app.py; touch /work/made.txt; ls /home 2>&1; id -u; "
        "cat /work/.env 2>&1; echo done",
        60,
    )
    assert "app.py" in out and "a - b" in out and "done" in out and out.endswith("(exit 0)")
    assert "cat: /work/.env: No such file" in out  # untracked secrets aren't on the branch
    assert (work / "made.txt").stat().st_uid == os.getuid()
    with pytest.raises(TimeoutError):
        await ws_module.run_in_sandbox(runtime.settings, builder["id"], work, "sleep 30", 2)
    runtime.settings.build_image = "no-such-image-dots"
    with pytest.raises(ws_module.WorkspaceError, match="couldn't start"):
        await ws_module.run_in_sandbox(runtime.settings, builder["id"], work, "true", 30)


async def test_a_planted_git_pointer_cannot_run_code_on_the_host(runtime, repo, builder, tmp_path):
    """The sandbox can rewrite the worktree's .git file. Git on the host must ignore it."""
    workspaces = ws_module.Workspaces(runtime.store, runtime.settings)
    thread = runtime.store.create_thread(builder["id"], "Evil")
    ws = await workspaces.ensure(builder, thread["id"])
    work = Path(ws["work_root"])
    marker = tmp_path / "pwned"
    evil = work / "evil"  # a hand-made git dir, as the sandbox could write it
    for sub in ("objects", "refs/heads"):
        (evil / sub).mkdir(parents=True)
    (evil / "HEAD").write_text("ref: refs/heads/main\n")
    (evil / "config").write_text(
        f"[core]\n\trepositoryformatversion = 0\n\tbare = false\n"
        f'\tfsmonitor = "touch {marker}; false"\n\tsshCommand = "touch {marker}"\n'
    )
    (work / ".git").write_text(f"gitdir: {evil}\n")
    # The planted config is live: plain git discovery in the worktree would run it.
    subprocess.run(["git", "status"], cwd=work, capture_output=True)
    assert marker.exists()
    marker.unlink()
    (work / "feature.py").write_text("x = 1\n")
    await workspaces.commit(ws, "Add feature", "Owen")
    await workspaces.summary(ws)
    await ws_module.workspace_changes(ws)
    assert not marker.exists()
    # The commit landed on the real branch in the owner's repo, not in the planted one.
    assert "feature.py" in sh(repo, "ls-tree", "-r", "--name-only", ws["branch"])
    await ws_module.discard_workspace(ws, runtime.settings.data_dir)
    assert not work.exists() and not marker.exists()
