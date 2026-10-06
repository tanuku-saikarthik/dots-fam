"""Local Claude-Code-style file and command tools, scoped to one project folder per Dot.

Unlike the sandboxed per-Dot "computer" (a disposable Docker container), these tools run
against a real folder on this machine - the project the owner points the Dot at. That's a
materially weaker boundary than a container, so the rules here are deliberately stricter:

- Every path is resolved and must stay inside the Dot's project_dir (see `safe_path`).
  No `..`, no symlink escape, no absolute paths outside the folder.
- Reading (read_file, glob_files, grep_files) and read-only commands are free.
- Writing, editing, and any shell command that isn't clearly read-only always needs owner
  approval under the Reversibility Law (metadata["external"] / metadata["classify"] below) -
  this is a default-deny allowlist, the opposite of the computer shell's default-allow
  denylist in reversibility.py, because this runs unattended against real project files.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ..context import DotContext

MAX_READ_BYTES = 256_000
MAX_OUTPUT_CHARS = 20_000
MAX_MATCHES = 200

# Read-only command *starts* that never need approval. Conservative on purpose: anything
# not matched here is treated as a possible write and needs the owner's OK.
SAFE_COMMANDS = re.compile(
    r"^\s*(ls|dir|pwd|cat|type|head|tail|wc|echo|grep|rg|find|file|stat|du|df|which|where|"
    r"env|date|whoami|tree|git\s+(status|log|diff|show|branch|remote|rev-parse|blame)|"
    r"npm\s+(list|ls|view|outdated)|pip\s+(list|show|freeze)|node\s+--version|"
    r"python3?\s+--version|java\s+-version)\b",
    re.IGNORECASE,
)
# Even a "safe" command is not safe if it contains one of these - redirects/pipes to a
# write, or command chaining that could smuggle in something else.
UNSAFE_MARKERS = re.compile(r"(>>|>|\|\s*(rm|mv|tee|sh|bash)|&&|;|\$\(|`)")


class ReadFile(BaseModel):
    path: str = Field(description="Path relative to the project folder.")
    offset: int = Field(0, ge=0, description="0-based line to start from, for long files.")
    limit: int = Field(2000, ge=1, le=5000, description="Max lines to return.")


class WriteFile(BaseModel):
    path: str = Field(description="Path relative to the project folder. Created if it doesn't exist.")
    content: str = Field(description="Full file contents to write.")


class EditFile(BaseModel):
    path: str = Field(description="Path relative to the project folder.")
    old_string: str = Field(min_length=1, description="Exact text to replace. Must appear exactly once.")
    new_string: str = Field(description="Replacement text.")


class GlobFiles(BaseModel):
    pattern: str = Field(description="Glob pattern, e.g. '**/*.py' or 'src/*.ts'.")


class GrepFiles(BaseModel):
    pattern: str = Field(description="Regular expression to search for.")
    glob: str = Field("**/*", description="Limit the search to files matching this glob.")


class RunCommand(BaseModel):
    command: str = Field(description="Shell command to run inside the project folder.")
    timeout_seconds: int = Field(30, ge=1, le=120)


class PathEscape(ValueError):
    pass


def safe_path(project_dir: Path, raw: str) -> Path:
    """Resolve `raw` relative to project_dir and refuse anything that escapes it."""
    target = (project_dir / raw).resolve()
    root = project_dir.resolve()
    if root != target and root not in target.parents:
        raise PathEscape(f"'{raw}' is outside this Dot's project folder.")
    return target


def classify_command(args: dict[str, Any]) -> str | None:
    command = str(args.get("command", ""))
    if UNSAFE_MARKERS.search(command):
        return f"runs a command that writes or chains: {command[:120]}"
    if SAFE_COMMANDS.match(command):
        return None
    return f"runs a command on your computer: {command[:120]}"


def local_tools(ctx: DotContext) -> list[BaseTool]:
    settings = ctx.dot.get("local") or {}
    if not settings.get("enabled") or ctx.worker:
        return []
    raw_dir = settings.get("project_dir")
    if not raw_dir:
        return []
    project_dir = Path(raw_dir).expanduser()
    if not project_dir.is_dir():
        return []

    async def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
        ctx.check()
        target = safe_path(project_dir, path)
        if not target.is_file():
            raise FileNotFoundError(f"No file at {path}")
        if target.stat().st_size > MAX_READ_BYTES and offset == 0 and limit >= 2000:
            raise ValueError(f"{path} is large; pass offset/limit to read it in parts.")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        chunk = lines[offset : offset + limit]
        return "\n".join(f"{i + offset + 1}\t{line}" for i, line in enumerate(chunk)) or "(empty)"

    async def write_file(path: str, content: str) -> str:
        ctx.check()
        target = safe_path(project_dir, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path}"

    async def edit_file(path: str, old_string: str, new_string: str) -> str:
        ctx.check()
        target = safe_path(project_dir, path)
        if not target.is_file():
            raise FileNotFoundError(f"No file at {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        count = text.count(old_string)
        if count == 0:
            raise ValueError("old_string was not found in the file.")
        if count > 1:
            raise ValueError(f"old_string appears {count} times; make it unique before editing.")
        target.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return f"Edited {path}"

    async def glob_files(pattern: str) -> str:
        ctx.check()
        matches = sorted(str(p.relative_to(project_dir)) for p in project_dir.glob(pattern) if p.is_file())
        if not matches:
            return "No files matched."
        return "\n".join(matches[:MAX_MATCHES])

    async def grep_files(pattern: str, glob: str = "**/*") -> str:
        ctx.check()
        try:
            regex = re.compile(pattern)
        except re.error as error:
            raise ValueError(f"Bad pattern: {error}") from error
        hits: list[str] = []
        for file in project_dir.glob(glob):
            if not file.is_file() or len(hits) >= MAX_MATCHES:
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{file.relative_to(project_dir)}:{lineno}:{line.strip()[:200]}")
                    if len(hits) >= MAX_MATCHES:
                        break
        return "\n".join(hits) if hits else "No matches."

    async def run_command(command: str, timeout_seconds: int = 30) -> str:
        ctx.check()
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            raw, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            process.kill()
            raise TimeoutError(f"Command timed out after {timeout_seconds}s.") from None
        output = raw.decode(errors="replace")
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n...(truncated)"
        return output or f"(exit {process.returncode}, no output)"

    return [
        StructuredTool.from_function(
            coroutine=read_file,
            name="read_file",
            args_schema=ReadFile,
            description=f"Read a text file from the project folder ({project_dir}). Free, no approval needed.",
        ),
        StructuredTool.from_function(
            coroutine=write_file,
            name="write_file",
            args_schema=WriteFile,
            description="Create or overwrite a file in the project folder. Always needs owner approval.",
            metadata={"external": True, "reason": "writes a file in the project folder on your computer"},
        ),
        StructuredTool.from_function(
            coroutine=edit_file,
            name="edit_file",
            args_schema=EditFile,
            description="Replace one exact, unique snippet of text in a file. Always needs owner approval.",
            metadata={"external": True, "reason": "edits a file in the project folder on your computer"},
        ),
        StructuredTool.from_function(
            coroutine=glob_files,
            name="glob_files",
            args_schema=GlobFiles,
            description="List files in the project folder matching a glob pattern. Free, no approval needed.",
        ),
        StructuredTool.from_function(
            coroutine=grep_files,
            name="grep_files",
            args_schema=GrepFiles,
            description="Search file contents with a regular expression. Free, no approval needed.",
        ),
        StructuredTool.from_function(
            coroutine=run_command,
            name="run_command",
            args_schema=RunCommand,
            description=(
                "Run a shell command inside the project folder (e.g. run tests, check git status, "
                "install a dependency). Read-only commands run immediately; anything that could change "
                "something needs owner approval."
            ),
            metadata={"classify": classify_command},
        ),
    ]
