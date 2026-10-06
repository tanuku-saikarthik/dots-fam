"""Local Claude-Code-style file and command tools, scoped to one project folder per Dot.

Unlike the sandboxed per-Dot "computer" (a disposable Docker container), these tools run
against a real folder on this machine - the project the owner points the Dot at. That's a
materially weaker boundary than a container, so the rules here are deliberately strict:

- Every path a free tool touches must stay inside the Dot's project_dir: no `..`, no
  absolute or home paths, no symlink that resolves outside (see `safe_path`, `_inside`).
- Reading files is free, except files that look like they hold secrets (.env, keys,
  credentials). Those need the owner's OK, and grep_files skips them. Free web tools could
  otherwise carry a secret out of the machine.
- Writing, editing, and any shell command that is not provably read-only and inside the
  folder needs owner approval under the Reversibility Law. Commands are judged default-deny:
  a short list of read-only programs, checked flag by flag and path by path, after refusing
  anything that chains, substitutes or redirects.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import signal
import sys
import time
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ..context import DotContext

MAX_READ_BYTES = 256_000
MAX_OUTPUT_CHARS = 20_000
MAX_MATCHES = 200
MAX_GREP_FILE_BYTES = 2_000_000

# ---- secrets --------------------------------------------------------------------------
SECRET_FILE = re.compile(
    r"(^\.env(\.(?!example$|sample$|template$|dist$)[^.]+)*$|^\.envrc$|^\.netrc$|^\.npmrc$|^\.pypirc$|"
    r"^\.git-credentials$|^id_(rsa|dsa|ecdsa|ed25519)$|^credentials(\.(json|ya?ml|toml|ini|txt|csv|env))?$|"
    r"^secrets?\.(json|ya?ml|toml|env)$|\.(pem|key|p12|pfx|jks|keystore|kdbx|secret)$)",
    re.IGNORECASE,
)
SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure", ".config/gcloud"}
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".cache",
}


def is_secret(relative: str) -> bool:
    """True for paths that commonly hold credentials (by file or folder name)."""
    parts = [p for p in re.split(r"[\\/]+", relative) if p and p != "."]
    if not parts:
        return False
    joined = "/".join(parts)
    if any(part in SECRET_DIRS for part in parts) or any(
        f"{d}/" in f"{joined}/" for d in SECRET_DIRS if "/" in d
    ):
        return True
    return bool(SECRET_FILE.search(parts[-1]))


_SCAN_CACHE: dict[str, tuple[float, list[str]]] = {}


def secret_files(root: Path, limit: int = 20_000, ttl: float = 30.0) -> list[str]:
    """Secret-looking files in a project (cached briefly). Unknown counts as present."""
    key = str(root)
    cached = _SCAN_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < ttl:
        return cached[1]
    found: list[str] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            seen += 1
            relative = os.path.relpath(os.path.join(dirpath, name), root)
            if is_secret(relative):
                found.append(relative)
        if seen > limit:
            found.append("(too many files to check)")
            break
    _SCAN_CACHE[key] = (time.monotonic(), found)
    return found


# ---- paths ------------------------------------------------------------------------------
class PathEscape(ValueError):
    pass


def safe_path(project_dir: Path, raw: str) -> Path:
    """Resolve `raw` relative to project_dir and refuse anything that escapes it."""
    target = (project_dir / raw).resolve()
    root = project_dir.resolve()
    if root != target and root not in target.parents:
        raise PathEscape(f"'{raw}' is outside this Dot's project folder.")
    return target


def _inside(root: Path, path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _leaves_folder(token: str) -> bool:
    """Lexically points outside the folder: absolute, home, drive letter, or a `..` step."""
    if token.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", token):
        return True
    return ".." in re.split(r"[\\/]+", token)


def check_pattern(pattern: str) -> None:
    if not pattern.strip() or _leaves_folder(pattern):
        raise PathEscape(f"'{pattern}' reaches outside this Dot's project folder.")


# ---- commands ---------------------------------------------------------------------------
# Anything containing these needs approval: chaining, substitution, redirection, subshells,
# and cmd.exe's variable/escape characters.
SHELL_SPECIAL = set("\n\r;&`$<>()%^")
READ_PROGRAMS = {
    "ls", "dir", "pwd", "cat", "type", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "file",
    "stat", "du", "tree", "which", "where", "echo", "date", "whoami", "sort", "uniq", "cut", "diff",
    "basename", "dirname", "nl", "find",
}  # fmt: skip
# Flags that make an otherwise read-only program write files or run other programs.
FORBIDDEN_FLAGS = {
    "find": {
        "-delete",
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-fprint",
        "-fprint0",
        "-fprintf",
        "-fls",
    },
    "rg": {"--pre", "--pre-glob"},
    "sort": {"-o", "--output"},
    "tail": {"-f", "-F", "--follow"},
    "date": {"-s", "--set"},
    "tree": {"-o"},
}
FORBIDDEN_LETTERS = {"sort": "o", "tail": "fF", "date": "s", "tree": "o"}
# Programs that read file contents (as opposed to only listing names).
CONTENT_READERS = {
    "cat",
    "type",
    "head",
    "tail",
    "wc",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "sort",
    "uniq",
    "cut",
    "diff",
    "nl",
}
GIT_READ = {
    "status",
    "log",
    "diff",
    "show",
    "blame",
    "rev-parse",
    "ls-files",
    "shortlog",
    "describe",
}
GIT_FORBIDDEN = {"--output", "-o", "--ext-diff", "--textconv"}
GIT_BRANCH_FLAGS = {
    "-a",
    "-r",
    "-v",
    "-vv",
    "--list",
    "--show-current",
    "--all",
    "--remotes",
    "--verbose",
}
TOOL_SUBCOMMANDS = {"npm": {"list", "ls", "view", "outdated"}, "pip": {"list", "show", "freeze"}}
VERSION_ONLY = {"python", "python3", "node", "java"}


def _program(token: str) -> str:
    name = re.split(r"[\\/]", token)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _segment_reason(tokens: list[str], project_dir: Path | None) -> str | None:
    """None when this one pipeline segment is read-only and stays in the folder."""
    if not tokens:
        return "has an empty command"
    program, args = _program(tokens[0]), tokens[1:]
    if program == "git":
        if not args or args[0].startswith("-") or args[0] not in GIT_READ | {"branch", "remote"}:
            return "runs a git command that may change the repository"
        if args[0] == "branch" and not set(args[1:]) <= GIT_BRANCH_FLAGS:
            return "changes git branches"
        if args[0] == "remote" and not set(args[1:]) <= {"-v", "--verbose"}:
            return "changes git remotes"
        if any(a.split("=", 1)[0] in GIT_FORBIDDEN for a in args):
            return "makes git write a file or run a configured program"
    elif program in TOOL_SUBCOMMANDS:
        if not args or args[0] not in TOOL_SUBCOMMANDS[program]:
            return f"runs {program} in a way that may change something"
    elif program in VERSION_ONLY:
        if args not in (["--version"], ["-version"], ["-V"]):
            return f"runs {program} code"
    elif program in READ_PROGRAMS:
        forbidden = FORBIDDEN_FLAGS.get(program, set())
        letters = FORBIDDEN_LETTERS.get(program, "")
        for arg in args:
            if arg.split("=", 1)[0] in forbidden:
                return (
                    f"uses {program} {arg.split('=', 1)[0]}, which can write files or run programs"
                )
            if (
                letters
                and arg.startswith("-")
                and not arg.startswith("--")
                and set(arg[1:]) & set(letters)
            ):
                return f"uses {program} {arg}, which can write files or run programs"
    else:
        return "runs a program that isn't on the read-only list"

    # Every path-like argument must stay inside the folder and away from secrets.
    recursive = program == "rg" or (
        program in {"grep", "egrep", "fgrep"} and any(a in ("-r", "-R", "--recursive") or
        (a.startswith("-") and not a.startswith("--") and set(a[1:]) & {"r", "R"}) for a in args)
    )  # fmt: skip
    for arg in args:
        value = arg.split("=", 1)[1] if arg.startswith("-") and "=" in arg else arg
        if arg.startswith("-") and "=" not in arg:
            continue
        if _leaves_folder(value):
            return f"reaches outside the project folder ({value[:60]})"
        if value.startswith(".") and any(c in value for c in "*?["):
            return "uses a dot-glob that can match the parent folder"
        if program in CONTENT_READERS and any(c in value for c in "*?["):
            recursive = True
        if program in CONTENT_READERS and is_secret(value):
            return f"reads a file that may hold secrets ({value[:60]})"
        if (
            project_dir is not None
            and value
            and not _inside(project_dir.resolve(), project_dir / value)
        ):
            if (project_dir / value).exists():
                return f"follows a link out of the project folder ({value[:60]})"
    if recursive and project_dir is not None:
        secrets = secret_files(project_dir.resolve())
        if secrets:
            return f"searches files in a project that has secret-looking files ({', '.join(secrets[:3])})"
    return None


def classify_command(args: dict[str, Any], project_dir: Path | None = None) -> str | None:
    """None for a provably read-only command that stays inside the folder; else a reason."""
    command = str(args.get("command", ""))
    if not command.strip():
        return "runs an empty command"
    if SHELL_SPECIAL & set(command):
        return f"runs a command that chains, substitutes or redirects: {command[:120]}"
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return f"runs a command that couldn't be checked: {command[:120]}"
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            segments.append([])
        elif set(token) == {"|"}:
            return f"runs a command that chains: {command[:120]}"
        else:
            segments[-1].append(token)
    for segment in segments:
        reason = _segment_reason(segment, project_dir)
        if reason:
            return f"{reason}: {command[:120]}"
    return None


# ---- tool schemas -------------------------------------------------------------------------
class ReadFile(BaseModel):
    path: str = Field(description="Path relative to the project folder.")
    offset: int = Field(0, ge=0, description="0-based line to start from, for long files.")
    limit: int = Field(2000, ge=1, le=5000, description="Max lines to return.")


class WriteFile(BaseModel):
    path: str = Field(
        description="Path relative to the project folder. Created if it doesn't exist."
    )
    content: str = Field(description="Full file contents to write.")


class EditFile(BaseModel):
    path: str = Field(description="Path relative to the project folder.")
    old_string: str = Field(
        min_length=1, description="Exact text to replace. Must appear exactly once."
    )
    new_string: str = Field(description="Replacement text.")


class GlobFiles(BaseModel):
    pattern: str = Field(
        description="Glob pattern inside the project, e.g. '**/*.py' or 'src/*.ts'."
    )


class GrepFiles(BaseModel):
    pattern: str = Field(description="Regular expression to search for.")
    glob: str = Field(
        "**/*", description="Limit the search to files matching this glob (inside the project)."
    )


class RunCommand(BaseModel):
    command: str = Field(description="Shell command to run inside the project folder.")
    timeout_seconds: int = Field(30, ge=1, le=120)


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
    root = project_dir.resolve()

    def relative(path: str) -> str:
        return str(safe_path(project_dir, path).relative_to(root))

    def classify_read(args: dict[str, Any]) -> str | None:
        try:
            rel = relative(str(args.get("path", "")))
        except PathEscape:
            return None  # read_file itself refuses paths outside the folder
        return f"reads a file that may hold secrets: {rel}" if is_secret(rel) else None

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
        check_pattern(pattern)
        matches = sorted(
            str(p.relative_to(project_dir))
            for p in project_dir.glob(pattern)
            if p.is_file() and _inside(root, p)
        )
        if not matches:
            return "No files matched."
        return "\n".join(matches[:MAX_MATCHES])

    async def grep_files(pattern: str, glob: str = "**/*") -> str:
        ctx.check()
        check_pattern(glob)
        try:
            regex = re.compile(pattern)
        except re.error as error:
            raise ValueError(f"Bad pattern: {error}") from error
        hits: list[str] = []
        skipped: list[str] = []
        for file in project_dir.glob(glob):
            if len(hits) >= MAX_MATCHES:
                break
            if not file.is_file() or not _inside(root, file):
                continue
            rel = str(file.relative_to(project_dir))
            if is_secret(rel):
                skipped.append(rel)
                continue
            try:
                if file.stat().st_size > MAX_GREP_FILE_BYTES:
                    continue
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{rel}:{lineno}:{line.strip()[:200]}")
                    if len(hits) >= MAX_MATCHES:
                        break
        out = "\n".join(hits) if hits else "No matches."
        if skipped:
            out += (
                f"\n(Skipped {len(skipped)} file(s) that may hold secrets, e.g. {skipped[0]}. "
                "Read one with read_file if you need it; the owner will be asked.)"
            )
        return out

    async def run_command(command: str, timeout_seconds: int = 30) -> str:
        ctx.check()
        posix = sys.platform != "win32"
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=posix,  # its own process group, so a timeout kills children too
        )
        try:
            raw, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            if posix:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
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
            description=f"Read a text file from the project folder ({project_dir}). Free, except files "
            "that may hold secrets (.env, keys, credentials), which ask the owner first.",
            metadata={"classify": classify_read},
        ),
        StructuredTool.from_function(
            coroutine=write_file,
            name="write_file",
            args_schema=WriteFile,
            description="Create or overwrite a file in the project folder. Always needs owner approval.",
            metadata={
                "external": True,
                "reason": "writes a file in the project folder on your computer",
            },
        ),
        StructuredTool.from_function(
            coroutine=edit_file,
            name="edit_file",
            args_schema=EditFile,
            description="Replace one exact, unique snippet of text in a file. Always needs owner approval.",
            metadata={
                "external": True,
                "reason": "edits a file in the project folder on your computer",
            },
        ),
        StructuredTool.from_function(
            coroutine=glob_files,
            name="glob_files",
            args_schema=GlobFiles,
            description="List files in the project folder matching a glob pattern. Free.",
        ),
        StructuredTool.from_function(
            coroutine=grep_files,
            name="grep_files",
            args_schema=GrepFiles,
            description="Search file contents in the project with a regular expression. Free; skips "
            "files that may hold secrets. Prefer this over grep in run_command.",
        ),
        StructuredTool.from_function(
            coroutine=run_command,
            name="run_command",
            args_schema=RunCommand,
            description=(
                "Run a shell command inside the project folder (run tests, git status, install a "
                "dependency). Simple read-only commands that stay inside the folder run immediately "
                "(ls, cat, git status/log/diff, ...); anything else asks the owner first. To read or "
                "search files, prefer read_file / grep_files."
            ),
            metadata={"classify": lambda args: classify_command(args, project_dir)},
        ),
    ]
