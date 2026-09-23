"""Level 1: find the recipes that may regenerate a candidate, without asking.

A recipe is only a *hypothesis*. Nothing found here is trusted: every recipe
is later run in the sandbox, and a candidate is PROVEN only if the recipe
recreates it byte-for-byte without changing anything else (autoprove.py).

Sources, strongest evidence first:

    agent logs   a command an AI agent ran in the project, finished right
                 before the candidate was last written (opt-in)
    Makefile     a target named like the candidate
    scripts      a project script whose text mentions the candidate's name
    ecosystem    npm ci for node_modules, cargo build for target/, ...
    docs / CI    build commands in AGENTS.md, CLAUDE.md, README, workflows
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import fs
from .scanner import METADATA_DIRS

ROOT_TOKEN = "{project}"
PYTHON_TOKEN = "{python}"

SCRIPT_RUNNERS = {
    ".py": PYTHON_TOKEN + " {path}",
    ".sh": "bash {path}",
    ".ps1": "powershell -NoProfile -ExecutionPolicy Bypass -File {path}",
    ".bat": "cmd /c {path}",
    ".cmd": "cmd /c {path}",
    ".js": "node {path}",
    ".mjs": "node {path}",
}
DOC_FILES = ("AGENTS.md", "CLAUDE.md", "README.md", "README", "CONTRIBUTING.md", "BUILDING.md", "docs/BUILD.md")
BUILD_TOOLS = {
    "python", "python3", "py", PYTHON_TOKEN, "node", "npm", "npx", "pnpm", "yarn", "cargo", "cmake",
    "make", "ninja", "gradle", "./gradlew", "gradlew", "gradlew.bat", "dotnet", "msbuild", "go",
    "mvn", "bazel", "just", "task", "bash", "sh", "powershell", "pwsh", "cmd", "meson", "scons",
}
READ_ONLY = {
    "ls", "dir", "cat", "type", "head", "tail", "less", "more", "grep", "rg", "find", "findstr",
    "echo", "pwd", "cd", "tree", "du", "df", "wc", "which", "where", "whoami", "date", "jq",
    "get-childitem", "gci", "get-content", "gc", "select-string", "sls", "get-item", "test-path",
    "measure-object", "get-location", "set-location", "code", "explorer", "start", "open",
    "notepad", "clear", "cls", "exit", "history", "git", "sleep", "start-sleep", "write-host",
}
DENY_FIRST = {  # never allowed as the program of any command segment
    "rm": "deletes files", "rmdir": "deletes files", "del": "deletes files", "erase": "deletes files",
    "rd": "deletes files", "remove-item": "deletes files", "shred": "deletes files",
    "format": "formats disks", "mkfs": "formats disks", "diskpart": "formats disks",
    "curl": "network transfer", "wget": "network transfer", "invoke-webrequest": "network transfer",
    "invoke-restmethod": "network transfer", "iwr": "network transfer", "irm": "network transfer",
    "scp": "network transfer", "sftp": "network transfer", "ssh": "network transfer",
    "rsync": "network transfer", "ftp": "network transfer", "telnet": "network transfer",
    "docker": "containers / remote infrastructure", "podman": "containers / remote infrastructure",
    "kubectl": "remote infrastructure", "helm": "remote infrastructure", "terraform": "remote infrastructure",
    "pulumi": "remote infrastructure", "aws": "remote infrastructure", "gcloud": "remote infrastructure",
    "az": "remote infrastructure", "gh": "remote infrastructure",
    "sudo": "system administration", "runas": "system administration", "shutdown": "system administration",
    "reboot": "system administration", "taskkill": "system administration", "kill": "system administration",
    "pkill": "system administration", "stop-process": "system administration", "reg": "system administration",
    "setx": "system administration", "set-executionpolicy": "system administration",
}
DENY_ANYWHERE = [
    (r"\bgit\s+(push|commit|reset|clean|checkout|switch|rebase|merge|pull|fetch|stash|tag|branch|"
     r"restore|rm|mv|am|apply|cherry-pick|revert|gc|prune|remote|config|submodule|lfs)\b", "changes git state"),
    (r"\b(npm|yarn|pnpm|twine|cargo|dotnet|gem|poetry|flit|hatch)\s+(publish|upload|login|adduser|owner|yank)\b",
     "publishes"),
    (r"\b(pip|pip3|conda|mamba|choco|winget|scoop|brew|apt|apt-get|yum|dnf|pacman)\s+"
     r"(install|uninstall|remove|upgrade|update)\b", "changes the environment outside the project"),
]
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\|")
_WIN_ABS = re.compile(r"(?<![\w{])[A-Za-z]:[\\/]")
_MAX_SCRIPT_BYTES = 1 << 20


@dataclass
class Recipe:
    command: str  # template: may contain {project} and {python}
    origin: str  # human-readable evidence
    strength: int  # higher is stronger evidence
    cwd: str = ""  # project-relative working directory
    targets: set = field(default_factory=set)  # candidate paths it is linked to

    @property
    def key(self) -> tuple:
        return (self.command, self.cwd)


def check_safe(command: str) -> Optional[str]:
    """Reason why ``command`` must never run, or None."""
    lowered = command.lower()
    for pattern, reason in DENY_ANYWHERE:
        if re.search(pattern, lowered):
            return reason
    for segment in _SEGMENT_SPLIT.split(command):
        first = _first_token(segment)
        if first in DENY_FIRST:
            return DENY_FIRST[first]
    if _WIN_ABS.search(command.replace(ROOT_TOKEN, "")):
        return "uses an absolute path outside the project"
    if re.search(r"(^|\s)(/home|/Users|/mnt|/tmp|/var|/etc|~)/", command.replace(ROOT_TOKEN, "")):
        return "uses an absolute path outside the project"
    return None


def _first_token(segment: str) -> str:
    segment = segment.strip()
    if not segment:
        return ""
    try:
        token = shlex.split(segment, posix=False)[0]
    except ValueError:
        token = segment.split()[0]
    return os.path.basename(token.strip("\"'")).lower()


def is_read_only(command: str) -> bool:
    segments = [s for s in _SEGMENT_SPLIT.split(command) if s.strip()]
    return bool(segments) and all(_first_token(s) in READ_ONLY for s in segments)


def looks_like_build(command: str) -> bool:
    segments = [s for s in _SEGMENT_SPLIT.split(command) if s.strip()]
    return any(
        _first_token(s) in BUILD_TOOLS or _first_token(s).endswith((".py", ".sh", ".ps1", ".bat", ".cmd"))
        for s in segments
    )


def relativize(command: str, root: Path) -> str:
    """Replace the project's absolute path with {project}, in any slash style."""
    root_str = str(Path(root).resolve())
    variants = {root_str, root_str.replace("\\", "/"), root_str.replace("/", "\\")}
    out = command
    for v in sorted(variants, key=len, reverse=True):
        out = re.sub(re.escape(v), lambda _m: ROOT_TOKEN, out, flags=re.IGNORECASE)
    return out


def render(command: str, project: Path, python: str) -> str:
    quoted_project = f'"{project}"' if " " in str(project) else str(project)
    return command.replace(ROOT_TOKEN, quoted_project).replace(PYTHON_TOKEN, python)


def project_python(project: Path) -> str:
    for rel in (".venv/Scripts/python.exe", "venv/Scripts/python.exe", ".venv/bin/python", "venv/bin/python"):
        candidate = Path(project) / rel
        if candidate.exists():
            return f'"{candidate}"'
    return f'"{sys.executable}"'


# ------------------------------------------------------------------- sources

def _name_pattern(name: str) -> re.Pattern:
    # "data" must match 'data/', "data" or data\x, but not data.txt or metadata
    return re.compile(r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])")


def normalize(command: str) -> str:
    """Run Python commands with the project's interpreter ({python})."""
    return re.sub(r"^(python3?|py)(?=\s)", PYTHON_TOKEN, command.strip())


def _latest_mtime(path: Path) -> float:
    if path.is_file():
        return path.stat().st_mtime
    latest = 0.0
    for dirpath, _dirs, files in fs.walk(path):
        for f in files:
            try:
                latest = max(latest, os.stat(os.path.join(dirpath, f)).st_mtime)
            except OSError:
                pass
    return latest


def _walk_text_files(root: Path, suffixes: tuple, skip: set):
    for dirpath, dirnames, filenames in fs.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [
            d for d in dirnames
            if d not in METADATA_DIRS and d not in ("node_modules", "__pycache__", ".venv", "venv")
            and f"{rel_dir}/{d}".lstrip("/") not in skip
        ]
        for name in filenames:
            if name.lower().endswith(suffixes) and f"{rel_dir}/{name}".lstrip("/") not in skip:
                full = os.path.join(dirpath, name)
                try:
                    if os.path.getsize(full) <= _MAX_SCRIPT_BYTES:
                        yield f"{rel_dir}/{name}".lstrip("/"), full
                except OSError:
                    pass


def _from_scripts(root: Path, candidates: list, excluded: set) -> list:
    recipes = []
    patterns = {c: _name_pattern(c.rsplit("/", 1)[-1]) for c in candidates}
    for rel, full in _walk_text_files(root, tuple(SCRIPT_RUNNERS), excluded):
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        linked = {c for c, pat in patterns.items() if pat.search(text) and not (rel == c or rel.startswith(c + "/"))}
        if linked:
            suffix = os.path.splitext(rel)[1].lower()
            path = rel if " " not in rel else f'"{rel}"'
            recipes.append(Recipe(SCRIPT_RUNNERS[suffix].replace("{path}", path),
                                  f"script {rel} mentions it", 3, "", linked))
    return recipes


def _from_makefile(root: Path, candidates: list, skip: set = frozenset()) -> list:
    makefile = root / "Makefile"
    if "Makefile" in skip or not makefile.is_file():
        return []
    text = makefile.read_text(encoding="utf-8", errors="replace")
    targets = {m.group(1) for m in re.finditer(r"^([A-Za-z0-9_./-]+)\s*:(?!=)", text, re.MULTILINE)}
    recipes = []
    for c in candidates:
        for t in (c, c + "/", c.rsplit("/", 1)[-1]):
            if t in targets:
                recipes.append(Recipe(f"make {t}", f"Makefile target '{t}'", 4, "", {c}))
                break
    recipes.append(Recipe("make", "Makefile default target", 1))
    return recipes


def _from_ecosystem(root: Path, candidates: list, skip: set = frozenset()) -> list:
    recipes = []
    names = {c: c.rsplit("/", 1)[-1] for c in candidates}
    package = root / "package.json"
    if "package.json" not in skip and package.is_file():
        for c, n in names.items():
            if n == "node_modules" and c == "node_modules":
                if (root / "package-lock.json").exists():
                    recipes.append(Recipe("npm ci", "package-lock.json", 4, "", {c}))
                elif (root / "pnpm-lock.yaml").exists():
                    recipes.append(Recipe("pnpm install --frozen-lockfile", "pnpm-lock.yaml", 4, "", {c}))
                elif (root / "yarn.lock").exists():
                    recipes.append(Recipe("yarn install --frozen-lockfile", "yarn.lock", 4, "", {c}))
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        except (ValueError, OSError):
            scripts = {}
        if "build" in scripts:
            recipes.append(Recipe("npm run build", "package.json script 'build'", 2))
    if (root / "Cargo.toml").is_file():
        linked = {c for c, n in names.items() if c == "target"}
        recipes.append(Recipe("cargo build", "Cargo.toml", 4 if linked else 1, "", linked))
    if (root / "CMakeLists.txt").is_file():
        linked = {c for c, n in names.items() if c == "build"}
        recipes.append(Recipe("cmake -S . -B build && cmake --build build", "CMakeLists.txt",
                              4 if linked else 1, "", linked))
    return recipes


def _commands_in_markdown(text: str) -> list:
    out = []
    for block in re.findall(r"```(?:bash|sh|shell|console|powershell|ps1|pwsh|cmd|bat|text)?\s*\n(.*?)```",
                            text, re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            for prompt in ("$ ", "> ", "PS> ", "% "):
                if line.startswith(prompt):
                    line = line[len(prompt):]
            if line and not line.startswith("#"):
                out.append(line)
    return out


def _commands_in_workflow(text: str) -> list:
    out, block_indent = [], None
    for raw in text.splitlines():
        m = re.match(r"^(\s*)-?\s*run:\s*(.*)$", raw)
        if m:
            value = m.group(2).strip()
            if value in ("|", ">", "|-", ">-"):
                block_indent = len(m.group(1)) + 1
            elif value:
                out.append(value.strip("'\""))
                block_indent = None
            continue
        if block_indent is not None:
            if raw.strip() and (len(raw) - len(raw.lstrip())) > block_indent:
                out.append(raw.strip())
            elif raw.strip():
                block_indent = None
    return out


def _from_docs(root: Path, candidates: list, skip: set = frozenset()) -> list:
    commands = []
    for name in DOC_FILES:
        path = root / name
        if name not in skip and not any(name.startswith(s + "/") for s in skip) and path.is_file():
            commands += [(c, name) for c in _commands_in_markdown(path.read_text(encoding="utf-8", errors="replace"))]
    workflows = root / ".github" / "workflows"
    if ".github" not in skip and workflows.is_dir() and not fs.is_link(workflows):
        for wf in sorted(workflows.glob("*.y*ml")):
            commands += [(c, f".github/workflows/{wf.name}")
                         for c in _commands_in_workflow(wf.read_text(encoding="utf-8", errors="replace"))]
    recipes = []
    patterns = {c: _name_pattern(c.rsplit("/", 1)[-1]) for c in candidates}
    for command, origin in commands:
        if not looks_like_build(command):
            continue
        linked = {c for c, pat in patterns.items() if pat.search(command)}
        recipes.append(Recipe(normalize(command), f"command in {origin}", 3 if linked else 1, "", linked))
    return recipes


def _from_agent_logs(root: Path, candidates: list, log_dirs: Optional[list], guard=None) -> list:
    from . import agentlogs

    logged = agentlogs.commands_for(root, log_dirs, guard)
    if not logged:
        return []
    mtimes = {c: _latest_mtime(root / c) for c in candidates}
    patterns = {c: _name_pattern(c.rsplit("/", 1)[-1]) for c in candidates}
    recipes = []
    for i, item in enumerate(logged):
        command = normalize(relativize(item.command, root))
        if is_read_only(command):
            continue
        nxt = next((l.timestamp for l in logged[i + 1:] if l.source == item.source and l.timestamp), None)
        window_end = (nxt or (item.timestamp or 0) + 6 * 3600) + 0.5
        linked = set()
        for c in candidates:
            # written after the command started (0.5s clock slack) and before the next one
            if item.timestamp and item.timestamp - 0.5 <= mtimes[c] <= window_end:
                linked.add(c)
        mentioned = {c for c, pat in patterns.items() if pat.search(command)}
        if not linked and not mentioned:
            continue
        cwd = os.path.relpath(item.cwd, root).replace(os.sep, "/")
        cwd = "" if cwd == "." else cwd
        strength = 5 if linked else 4
        origin = "agent log: ran right before it was written" if linked else "agent log: command mentions it"
        recipes.append(Recipe(command, origin, strength, cwd, linked | mentioned))
    return recipes


# ---------------------------------------------------------------------- entry

@dataclass
class Discovery:
    recipes: list  # safe, de-duplicated
    rejected: list  # (command, reason)

    def ranked_for(self, candidate: str, limit: int) -> list:
        linked = sorted((r for r in self.recipes if candidate in r.targets), key=lambda r: -r.strength)
        generic = sorted((r for r in self.recipes if not r.targets and r.strength >= 1), key=lambda r: -r.strength)
        out, seen = [], set()
        for r in linked + generic:
            if r.key not in seen:
                seen.add(r.key)
                out.append(r)
        return out[:limit]


def discover(root: Path, candidates: list, excluded: Optional[set] = None,
             agent_log_dirs: Optional[list] = None, guard=None) -> Discovery:
    """Collect recipe hypotheses for ``candidates`` (project-relative paths)."""
    root = Path(root).resolve()
    found = []
    if agent_log_dirs is not None:
        found += _from_agent_logs(root, candidates, agent_log_dirs, guard)
    skip = set(excluded or ())
    found += _from_makefile(root, candidates, skip)
    found += _from_scripts(root, candidates, skip)
    found += _from_ecosystem(root, candidates, skip)
    found += _from_docs(root, candidates, skip)

    merged: dict = {}
    rejected = []
    for r in found:
        reason = check_safe(r.command)
        if reason:
            rejected.append((r.command, reason))
            continue
        if r.key in merged:
            existing = merged[r.key]
            existing.targets |= r.targets
            if r.strength > existing.strength:
                existing.strength, existing.origin = r.strength, r.origin
        else:
            merged[r.key] = r
    return Discovery(list(merged.values()), sorted(set(rejected)))
