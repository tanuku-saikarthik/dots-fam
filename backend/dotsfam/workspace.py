"""Build mode: a Dot works on its own git branch and runs commands in a throwaway container.

Why this makes most coding steps reversible, so they don't need the owner:
- Each conversation gets a git worktree on a new branch (dots/<topic>-<id>) in the data
  folder. The owner's checkout, uncommitted work and untracked files (.env and friends)
  are never touched or even visible there.
- Commands run in `docker run --rm` with only that worktree mounted, as the owner's user,
  with no capabilities, a memory/CPU/process cap and a time limit. Nothing else on the
  machine is reachable from inside.
- Commits are local and made by Dots Fam itself, with hooks off.

What still asks: pushing the branch and opening a pull request (they leave the machine),
writing secret-looking files, and anything outside the worktree.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from .tools.local import MAX_OUTPUT_CHARS, is_secret

BRANCH_PREFIX = "dots/"
_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


class WorkspaceError(RuntimeError):
    pass


async def git(cwd: Path, *args: str, check: bool = True, seconds: float = 60) -> str:
    """Run git on the host without a shell. Hooks are always off."""
    process = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    try:
        raw, _ = await asyncio.wait_for(process.communicate(), seconds)
    except TimeoutError:
        process.kill()
        raise WorkspaceError(f"git {args[0]} timed out") from None
    out = raw.decode(errors="replace").strip()
    if check and process.returncode != 0:
        raise WorkspaceError(f"git {args[0]} failed: {out[-800:]}")
    return out


async def repo_root(folder: Path) -> Path | None:
    """The git repository containing `folder` (with at least one commit), or None."""
    try:
        top = await git(folder, "rev-parse", "--show-toplevel")
        await git(folder, "rev-parse", "--verify", "HEAD")
    except (WorkspaceError, FileNotFoundError, NotADirectoryError):
        return None
    return Path(top)


async def wsgit(ws: dict[str, Any], *args: str, check: bool = True, seconds: float = 60) -> str:
    """git on a workspace with its repository pinned. The sandbox can rewrite anything in the
    worktree, including the `.git` pointer file, so git must never discover the repository
    from there: a planted config could otherwise run commands here, with the owner's keys."""
    return await git(
        Path(ws["work_root"]),
        f"--git-dir={ws['git_dir']}",
        f"--work-tree={ws['work_root']}",
        *args,
        check=check,
        seconds=seconds,
    )


def _pinned_env(ws: dict[str, Any]) -> dict[str, str]:
    return {**os.environ, "GIT_DIR": ws["git_dir"], "GIT_WORK_TREE": ws["work_root"]}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:32].strip("-") or "work"


def root_thread(store: Any, thread_id: str) -> str:
    for _ in range(6):
        delegation = store.delegation_for_worker(thread_id)
        if not delegation or not delegation["parent_thread_id"]:
            break
        thread_id = delegation["parent_thread_id"]
    return thread_id


class Workspaces:
    """One worktree + branch per (Dot, owner conversation), created on first use."""

    def __init__(self, store: Any, settings: Any):
        self.store = store
        self.settings = settings

    def find(self, dot_id: str, thread_id: str) -> dict[str, Any] | None:
        return self.store.workspace(dot_id, root_thread(self.store, thread_id))

    async def ensure(self, dot: dict[str, Any], thread_id: str) -> dict[str, Any]:
        root = root_thread(self.store, thread_id)
        key = (dot["id"], root)
        lock = _LOCKS.setdefault(key, asyncio.Lock())
        async with lock:
            existing = self.store.workspace(dot["id"], root)
            if existing and Path(existing["path"]).is_dir():  # noqa: ASYNC240 - one stat
                return existing
            if existing:  # its folder was removed by hand: start over cleanly
                self.store.remove_workspace(existing["id"])
            project = Path((dot.get("local") or {}).get("project_dir") or "").expanduser()  # noqa: ASYNC240
            repo = await repo_root(project) if project.is_dir() else None  # noqa: ASYNC240
            if repo is None:
                raise WorkspaceError(
                    "Build mode needs the project folder to be a git repository with at least one "
                    "commit."
                )
            base = await git(repo, "rev-parse", "--abbrev-ref", "HEAD")
            base_commit = await git(repo, "rev-parse", "HEAD")
            title = self.store.thread(root).get("title") or "work"
            short = uuid.uuid4().hex[:6]
            branch = f"{BRANCH_PREFIX}{_slug(title)}-{short}"
            path = Path(self.settings.data_dir).resolve() / "workspaces" / dot["id"][:12] / short  # noqa: ASYNC240
            path.parent.mkdir(parents=True, exist_ok=True)
            await git(repo, "worktree", "prune", check=False)
            await git(repo, "worktree", "add", "-b", branch, str(path), base_commit)
            # Read before any sandbox command has run, then pinned for every later git call.
            git_dir = await git(path, "rev-parse", "--absolute-git-dir")
            # A subfolder project (monorepo) maps to the same subfolder in the worktree.
            sub = project.resolve().relative_to(repo.resolve())
            return self.store.add_workspace(
                dot_id=dot["id"],
                thread_id=root,
                project_dir=str(project),
                path=str(path / sub) if str(sub) != "." else str(path),
                work_root=str(path),
                git_dir=git_dir,
                branch=branch,
                base=base if base != "HEAD" else base_commit[:12],
                base_commit=base_commit,
            )

    # ---- git steps the Dot takes through tools ----------------------------------------
    async def commit(self, ws: dict[str, Any], message: str, author: str) -> str:
        await wsgit(ws, "add", "-A")
        staged = (await wsgit(ws, "diff", "--cached", "--name-only", "-z")).split("\0")
        held = [name for name in staged if name and is_secret(name)]
        if held:
            await wsgit(ws, "reset", "-q", "--", *held)
        if not (await wsgit(ws, "diff", "--cached", "--name-only")).strip():
            note = f" (left out secret-looking files: {', '.join(held[:5])})" if held else ""
            return f"Nothing new to commit{note}."
        email = "dots@dotsfam.local"
        await wsgit(
            ws,
            "-c",
            f"user.name={author} (Dots Fam)",
            "-c",
            f"user.email={email}",
            "commit",
            "-q",
            "--no-verify",
            "-m",
            message,
        )
        stat = await wsgit(ws, "show", "--stat", "--format=%h %s", "HEAD")
        if held:
            stat += f"\nLeft out secret-looking files: {', '.join(held[:5])}"
        return stat[:4000]

    async def summary(self, ws: dict[str, Any]) -> str:
        log = await wsgit(ws, "log", "--format=%h %s", f"{ws['base_commit']}..HEAD")
        stat = await wsgit(ws, "diff", "--stat", ws["base_commit"])
        status = await wsgit(ws, "status", "--short")
        parts = [
            f"Branch {ws['branch']} (from {ws['base']}).",
            f"Commits:\n{log}" if log else "No commits yet.",
            f"Changed vs {ws['base']}:\n{stat}" if stat else "No changes yet.",
            f"Not committed yet:\n{status}" if status else "Everything is committed.",
        ]
        if ws.get("pr_url"):
            parts.append(f"Pull request: {ws['pr_url']}")
        return "\n\n".join(parts)[:MAX_OUTPUT_CHARS]

    async def push(self, ws: dict[str, Any]) -> str:
        if await wsgit(ws, "status", "--porcelain"):
            raise WorkspaceError("Commit your changes with commit_work before pushing.")
        out = await wsgit(
            ws, "push", "-u", "origin", f"HEAD:refs/heads/{ws['branch']}", seconds=120
        )
        self.store.update_workspace(ws["id"], pushed_at=_now())
        return out[-2000:] or f"Pushed {ws['branch']}."

    async def open_pr(self, ws: dict[str, Any], title: str, body: str) -> str:
        if not ws.get("pushed_at"):
            await self.push(ws)
        if shutil.which("gh"):
            out = await git_free_gh(
                ws,
                [
                    "pr",
                    "create",
                    "--head",
                    ws["branch"],
                    "--base",
                    ws["base"],
                    "--title",
                    title,
                    "--body",
                    body,
                ],
            )
            url = next((w for w in out.split() if w.startswith("https://")), "")
            if url:
                self.store.update_workspace(ws["id"], pr_url=url)
                return f"Opened {url}"
            return out[-1500:]
        remote = await wsgit(ws, "remote", "get-url", "origin", check=False)
        compare = github_compare_url(remote, ws["base"], ws["branch"])
        return (
            f"Pushed {ws['branch']}. The GitHub CLI (gh) isn't installed here, so open the pull "
            f"request yourself: {compare or 'from your git host'}"
        )


def _now() -> int:
    import time

    return int(time.time() * 1000)


def github_compare_url(remote: str, base: str, branch: str) -> str | None:
    match = re.match(
        r"^(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?/?$", remote
    )
    return (
        f"https://github.com/{match.group(1)}/compare/{base}...{branch}?expand=1" if match else None
    )


async def git_free_gh(ws: dict[str, Any], args: list[str]) -> str:
    process = await asyncio.create_subprocess_exec(
        "gh",
        *args,
        cwd=ws["work_root"],
        env=_pinned_env(ws),  # gh runs git too: keep it on the pinned repository
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    raw, _ = await asyncio.wait_for(process.communicate(), 120)
    out = raw.decode(errors="replace").strip()
    if process.returncode != 0:
        raise WorkspaceError(f"gh pr create failed: {out[-800:]}")
    return out


# ---- the sandbox ----------------------------------------------------------------------
def sandbox_available() -> bool:
    return shutil.which("docker") is not None


def sandbox_command(
    settings: Any, dot_id: str, workdir: Path, command: str, name: str
) -> list[str]:
    """`docker run` for one command: only the worktree (and a per-Dot cache) is mounted."""
    cache = Path(settings.data_dir).resolve() / "build-cache" / dot_id[:12]
    (cache / "home").mkdir(parents=True, exist_ok=True)
    user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else None
    args = [
        "docker", "run", "--rm", "--name", name,
        "--network", settings.build_network,
        "--memory", settings.build_memory, "--cpus", settings.build_cpus,
        "--pids-limit", "512", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "-v", f"{workdir}:/work", "-w", "/work",
        "-v", f"{cache}:/cache",
        "-e", "HOME=/cache/home", "-e", "npm_config_cache=/cache/npm",
        "-e", "PIP_CACHE_DIR=/cache/pip", "-e", "XDG_CACHE_HOME=/cache/xdg", "-e", "CI=1",
    ]  # fmt: skip
    if user:
        args += ["--user", user]
    return [*args, settings.build_image, "sh", "-c", command]


async def run_in_sandbox(
    settings: Any, dot_id: str, workdir: Path, command: str, seconds: int
) -> str:
    name = f"dots-build-{uuid.uuid4().hex[:10]}"
    process = await asyncio.create_subprocess_exec(
        *sandbox_command(settings, dot_id, workdir, command, name),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        raw, _ = await asyncio.wait_for(process.communicate(), seconds)
    except TimeoutError:
        kill = await asyncio.create_subprocess_exec(
            "docker",
            "kill",
            name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await kill.wait()
        process.kill()
        raise TimeoutError(
            f"Command timed out after {seconds}s (the sandbox was stopped)."
        ) from None
    output = raw.decode(errors="replace")
    if process.returncode == 125:  # docker itself failed (no image, daemon down, bad option)
        raise WorkspaceError(f"The build sandbox couldn't start: {output.strip()[-600:]}")
    if len(output) > MAX_OUTPUT_CHARS:
        output = (
            output[: MAX_OUTPUT_CHARS // 2]
            + "\n...(truncated)...\n"
            + output[-MAX_OUTPUT_CHARS // 2 :]
        )
    return f"{output.rstrip()}\n(exit {process.returncode})".lstrip()


# ---- for the owner's settings screen ----------------------------------------------------
async def workspace_changes(ws: dict[str, Any]) -> dict[str, Any]:
    """Commits and changed files on a workspace branch, or that its folder is gone."""
    path = Path(ws["path"])
    if not await asyncio.to_thread(path.is_dir):
        return {"exists": False}
    try:
        commits = await wsgit(ws, "rev-list", "--count", f"{ws['base_commit']}..HEAD")
        files = await wsgit(ws, "diff", "--name-only", ws["base_commit"])
        dirty = await wsgit(ws, "status", "--porcelain")
    except WorkspaceError:
        return {"exists": False}
    return {
        "exists": True,
        "commits": int(commits or 0),
        "files": len([f for f in files.splitlines() if f]),
        "uncommitted": len([f for f in dirty.splitlines() if f]),
    }


async def discard_workspace(ws: dict[str, Any], data_dir: Path) -> None:
    """Remove the worktree folder. The branch and its commits stay in the owner's repo."""
    root = Path(ws["work_root"])
    repo = await repo_root(Path(ws["project_dir"]))
    if repo is not None:
        await git(repo, "worktree", "remove", "--force", str(root), check=False)
    # If the sandbox mangled the worktree, git may refuse; it's our own folder, so remove it.
    ours = (Path(data_dir).resolve() / "workspaces") in root.resolve().parents  # noqa: ASYNC240
    if ours and await asyncio.to_thread(root.exists):
        await asyncio.to_thread(shutil.rmtree, root, True)
    if repo is not None:
        await git(repo, "worktree", "prune", check=False)
