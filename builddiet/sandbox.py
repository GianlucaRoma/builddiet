"""The sandbox: a full copy of the project where destructive experiments happen.

Every destructive operation BuildDiet performs goes through :class:`Sandbox`
and is checked to resolve inside the sandbox directory. The original project
is only ever read.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from . import fs
from .config import CONFIG_DIR, normalize_rel

PREFIX = "builddiet-"


class SandboxError(RuntimeError):
    pass


class OutOfSpace(SandboxError):
    """A sandbox ran out of disk space. Nothing in the user's project was changed."""


def is_out_of_space(exc: BaseException) -> bool:
    import errno

    return isinstance(exc, OSError) and (
        exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in (39, 112)  # ERROR_(HANDLE_)DISK_FULL
    )


def out_of_space(where, doing: str) -> OutOfSpace:
    try:
        free = shutil.disk_usage(str(where)).free
        left = f"{free / 1e6:.0f} MB left"
    except OSError:
        left = "free space unknown"
    return OutOfSpace(
        f"out of disk space in {where} while {doing} ({left}). Nothing in your project was changed "
        "or deleted. Free some space there, or pass --sandbox-dir on a drive with room."
    )


def is_within(child: Path, parent: Path) -> bool:
    try:
        Path(child).relative_to(parent)
        return True
    except ValueError:
        return False


def has_link_ancestor(path: Path, root: Path) -> bool:
    """Whether an existing parent below ``root`` redirects through a link."""
    for parent in Path(path).parents:
        if parent == root:
            return False
        if os.path.lexists(parent) and fs.is_link(parent):
            return True
    return True  # root was not an ancestor


def force_remove(path: Path) -> None:
    """Delete a file or tree; links (symlinks, junctions) are removed, never followed."""
    fs.remove(path)


SANDBOX_ENV = "BUILDDIET_SANDBOX_DIR"


def _fixed_drives() -> list:
    """Local fixed drives on Windows (no network shares, no removable media)."""
    if os.name != "nt":
        return []
    try:
        import ctypes
        import string

        get_type = ctypes.windll.kernel32.GetDriveTypeW
        return [Path(f"{d}:\\") for d in string.ascii_uppercase if get_type(f"{d}:\\") == 3]
    except (AttributeError, OSError):
        return []


def default_sandbox_base(project: Optional[Path] = None) -> Path:
    """Where sandboxes go when --sandbox-dir is not given.

    $BUILDDIET_SANDBOX_DIR if set; otherwise the local drive with the most free
    space (``X:\\builddiet-sandboxes``), or the temp directory if that is best.
    A sandbox is a full copy of the project, so it should not compete for the
    space on the (often full) system drive.
    """
    env = os.environ.get(SANDBOX_ENV)
    if env:
        return Path(env)
    temp = Path(tempfile.gettempdir())
    best, best_free = temp, shutil.disk_usage(str(temp)).free
    temp_drive = os.path.splitdrive(str(temp.resolve()))[0].upper()
    for drive in _fixed_drives():
        if str(drive)[:2].upper() == temp_drive:
            continue
        try:
            free = shutil.disk_usage(str(drive)).free
        except OSError:
            continue
        if free > best_free:
            best, best_free = drive / "builddiet-sandboxes", free
    if project is not None and is_within(best.resolve() if best.exists() else best,
                                         Path(project).resolve()):
        return temp
    return best


class Sandbox:
    """``with Sandbox(project) as sb: sb.populate(); ...``"""

    def __init__(self, source: Path, base: Optional[Path] = None, keep: bool = False, guard=None):
        self.source = Path(source).resolve()
        self.guard = guard
        self.base = Path(base).resolve() if base else Path(default_sandbox_base(self.source)).resolve()
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
        """Copy the project. Links are never followed or copied, and protected /
        excluded areas are not even read."""
        source = str(self.source)
        guard = self.guard

        def ignore(directory, names):
            skipped = []
            top = os.path.normcase(os.path.abspath(directory)) == os.path.normcase(source)
            for n in names:
                full = os.path.join(directory, n)
                if top and n == CONFIG_DIR:
                    skipped.append(n)
                elif guard is not None and guard.status(full) != "clear":
                    skipped.append(n)
            return skipped

        try:
            fs.copytree(source, str(self.project), ignore=ignore)
        except OSError as exc:
            if is_out_of_space(exc):
                raise out_of_space(self.base, "copying the project into the sandbox") from exc
            raise

    def target(self, rel: str) -> Path:
        """Absolute path of ``rel`` inside the sandbox copy, with escape checks."""
        rel = normalize_rel(rel)
        path = Path(os.path.abspath(self.project / rel))
        if path == self.project or not is_within(path, self.project) \
                or has_link_ancestor(path, self.project) \
                or not is_within(path.resolve(strict=False), self.project):
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
