"""Commands that AI coding agents ran in a project, from their session logs.

Opt-in only (`--agent-logs`). Codex writes JSONL rollouts under
~/.codex/sessions and Claude Code under ~/.claude/projects. Both formats
change over time, so this reader does not depend on exact schemas: it walks
every JSON record looking for a working directory ("cwd", "workdir") and a
command ("command", "cmd"), decoding JSON-encoded tool arguments on the way.

Only commands whose working directory is inside the analyzed project are
returned. Nothing is sent anywhere.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

_SHELLS = {"bash", "sh", "zsh", "pwsh", "powershell", "powershell.exe", "pwsh.exe", "cmd", "cmd.exe"}
_SHELL_FLAGS = {"-lc", "-c", "/c", "-command", "-Command", "-NoProfile", "-noprofile", "-NonInteractive"}
_MAX_FILE_BYTES = 200 << 20


@dataclass
class LoggedCommand:
    command: str
    cwd: str
    timestamp: Optional[float]  # epoch seconds
    source: str  # log file it came from


def default_log_dirs() -> list:
    home = Path.home()
    return [home / ".codex" / "sessions", home / ".claude" / "projects"]


def _parse_time(value) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value) / (1000.0 if value > 1e11 else 1.0)
    if isinstance(value, str):
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _command_text(value) -> Optional[str]:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        parts = list(value)
        if os.path.basename(parts[0]).lower() in _SHELLS:
            rest = [p for p in parts[1:] if p not in _SHELL_FLAGS]
            return rest[-1].strip() if rest else None
        return " ".join(parts).strip() or None
    return None


def _walk(node, context: dict) -> Iterator[tuple]:
    """Yield (command, cwd, timestamp) found anywhere below ``node``."""
    if isinstance(node, dict):
        ctx = dict(context)
        for key in ("cwd", "workdir", "working_directory"):
            if isinstance(node.get(key), str):
                ctx["cwd"] = node[key]
        for key in ("timestamp", "time", "created_at"):
            if key in node and _parse_time(node[key]) is not None:
                ctx["ts"] = _parse_time(node[key])
        for key in ("command", "cmd"):
            if key in node:
                text = _command_text(node[key])
                if text:
                    yield text, ctx.get("cwd"), ctx.get("ts")
        for key, value in node.items():
            if key in ("arguments", "input") and isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    continue
            if isinstance(value, (dict, list)):
                yield from _walk(value, ctx)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, context)


def _inside(path: str, root: Path) -> bool:
    try:
        Path(os.path.abspath(path)).resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def commands_for(root: Path, log_dirs: Optional[list] = None) -> list:
    """Commands logged by agents with a working directory inside ``root``."""
    root = Path(root).resolve()
    found = []
    for base in log_dirs if log_dirs is not None else default_log_dirs():
        base = Path(base)
        if not base.is_dir():
            continue
        for dirpath, _dirs, filenames in os.walk(base):
            for name in filenames:
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    if os.path.getsize(path) > _MAX_FILE_BYTES:
                        continue
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        lines = fh.read().splitlines()
                except OSError:
                    continue
                session_cwd = None  # Codex: session_meta / turn_context carry the cwd
                for line in lines:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict):
                        meta = record.get("payload") if isinstance(record.get("payload"), dict) else record
                        if isinstance(meta.get("cwd"), str):
                            session_cwd = meta["cwd"]
                    for command, cwd, ts in _walk(record, {"cwd": session_cwd}):
                        if cwd and _inside(cwd, root):
                            found.append(LoggedCommand(command, os.path.abspath(cwd), ts, path))
    found.sort(key=lambda c: (c.timestamp or 0.0))
    return found
