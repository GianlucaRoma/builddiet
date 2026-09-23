"""The sandbox: a full copy of the project where destructive experiments happen.

Every destructive operation BuildDiet performs goes through :class:`Sandbox`
and is checked to resolve inside the sandbox directory. The original project
is only ever read.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .config import CONFIG_DIR, normalize_rel

PREFIX = "builddiet-"


class SandboxError(RuntimeError):
    pass


def is_within(child: Path, parent: Path) -> bool:
    try:
        Path(child).relative_to(parent)
        return True
    except ValueError:
        return False


def _make_writable_and_retry(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def force_remove(path: Path) -> None:
    path = str(path)
    if os.path.islink(path) or not os.path.isdir(path):
        try:
            os.unlink(path)
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            os.unlink(path)
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_make_writable_and_retry)
    else:
        shutil.rmtree(path, onerror=_make_writable_and_retry)


class Sandbox:
    """``with Sandbox(project) as sb: sb.populate(); ...``"""

    def __init__(self, source: Path, base: Optional[Path] = None, keep: bool = False):
        self.source = Path(source).resolve()
        self.base = Path(base).resolve() if base else Path(tempfile.gettempdir()).resolve()
        if is_within(self.base, self.source):
            raise SandboxError(
                f"the sandbox directory {self.base} is inside the project; "
                "choose a location outside it (--sandbox-dir)"
            )
        self.keep = keep
        self.root: Optional[Path] = None
        self._counter = 0

    def __enter__(self) -> "Sandbox":
        self.base.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix=PREFIX, dir=str(self.base))).resolve()
        self.project = self.root / "project"
        self.aside = self.root / "aside"
        self.logs = self.root / "logs"
        self.aside.mkdir()
        self.logs.mkdir()
        return self

    def __exit__(self, *exc) -> None:
        if not self.keep:
            self.destroy()

    def populate(self) -> None:
        source = str(self.source)

        def ignore(directory, names):
            if os.path.normcase(os.path.abspath(directory)) == os.path.normcase(source):
                return [n for n in names if n == CONFIG_DIR]
            return []

        shutil.copytree(source, str(self.project), symlinks=True, ignore=ignore)

    def target(self, rel: str) -> Path:
        """Absolute path of ``rel`` inside the sandbox copy, with escape checks."""
        rel = normalize_rel(rel)
        path = Path(os.path.abspath(self.project / rel))
        if path == self.project or not is_within(path, self.project):
            raise SandboxError(f"refusing to touch {path}: not inside the sandbox copy")
        return path

    def set_aside(self, rel: str) -> Optional[Path]:
        """Move ``rel`` out of the sandbox copy. Returns a token for :meth:`restore`."""
        src = self.target(rel)
        if not os.path.lexists(src):
            return None
        self._counter += 1
        token = self.aside / str(self._counter)
        try:
            os.replace(src, token)
        except OSError as exc:
            raise SandboxError(f"could not move {rel} aside in the sandbox: {exc}") from exc
        return token

    def restore(self, rel: str, token: Path) -> None:
        """Delete whatever the workflow recreated and put the original bytes back."""
        dst = self.target(rel)
        if not is_within(Path(token), self.aside):
            raise SandboxError(f"invalid restore token {token}")
        if os.path.lexists(dst):
            force_remove(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(token, dst)
        except OSError as exc:
            raise SandboxError(f"could not restore {rel} in the sandbox: {exc}") from exc

    def destroy(self) -> None:
        if self.root is None or not self.root.name.startswith(PREFIX):
            return
        if not is_within(self.root, self.base) or is_within(self.root, self.source):
            raise SandboxError(f"refusing to delete unexpected path {self.root}")
        if self.root.exists():
            force_remove(self.root)
        self.root = None
