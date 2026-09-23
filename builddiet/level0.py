"""Level 0: proofs that need no command at all.

A candidate is RECOVERABLE when its exact bytes provably exist somewhere
else on disk, so it can be restored without knowing how it was made:

    duplicate   an identical copy exists elsewhere in the project
    archive     a .zip/.tar* in the project contains exactly these files
    git         every file is tracked, clean, and `git cat-file --filters`
                reproduces it byte-for-byte

Every claim is checked with SHA-256 against the real bytes (archive members
are decompressed, git blobs are rendered through the checkout filters), so
nothing here is inferred from names or metadata alone.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from .scanner import CANDIDATE, METADATA_DIRS

DUPLICATE = "duplicate"
ARCHIVE = "archive"
GIT = "git"

ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
_CHUNK = 1 << 20
_DEFAULT_THROUGHPUT = 150e6  # bytes/s, used until something has been measured


@dataclass
class Recovery:
    method: str
    source: Optional[str]  # project-relative path of the copy / archive (None for git)
    detail: str
    seconds: float  # time to restore: measured where possible, else estimated
    estimated: bool
    prefix: str = ""  # archive member prefix that maps onto the candidate
    source_stat: Optional[list] = None  # [size, mtime_ns] of the source when proven
    signature: str = ""  # SHA-256 of the source file, or content signature of a source dir
    single_file: bool = False  # the candidate is one file (archive: one member)

    def to_dict(self) -> dict:
        return asdict(self)


class Hasher:
    """SHA-256 with a cache and a running throughput measurement."""

    def __init__(self) -> None:
        self._cache: dict = {}
        self.bytes = 0
        self.seconds = 0.0

    def file(self, path: str) -> str:
        key = os.path.normcase(os.path.abspath(path))
        if key not in self._cache:
            start = time.perf_counter()
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(_CHUNK), b""):
                    h.update(chunk)
                    self.bytes += len(chunk)
            self.seconds += time.perf_counter() - start
            self._cache[key] = h.hexdigest()
        return self._cache[key]

    @property
    def throughput(self) -> float:
        return self.bytes / self.seconds if self.seconds > 0.05 else _DEFAULT_THROUGHPUT


def _rel(root: Path, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _within(rel: str, container: str) -> bool:
    return rel == container or rel.startswith(container + "/")


def _files(path: Path) -> dict:
    """{relative posix path: (size, absolute path)} of regular files, links skipped."""
    out = {}
    if path.is_file() and not path.is_symlink():
        out[path.name] = (path.stat().st_size, str(path))
        return out
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            try:
                out[os.path.relpath(full, path).replace(os.sep, "/")] = (os.path.getsize(full), full)
            except OSError:
                pass
    return out


def _index(root: Path) -> tuple:
    """Size index of every file in the project, and the list of archives."""
    by_size: dict = {}
    archives = []
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.abspath(dirpath) == os.path.abspath(root):
            dirnames[:] = [d for d in dirnames if d not in METADATA_DIRS]
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            by_size.setdefault(size, []).append(full)
            if name.lower().endswith(ARCHIVE_SUFFIXES):
                archives.append(full)
    return by_size, archives


def _stat(path: str) -> list:
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns]


def source_unchanged(root: Path, recovery: dict) -> bool:
    """True if the recovery source still holds the bytes it held when proven."""
    method = recovery.get("method")
    if method == GIT:
        return _git(Path(root), "rev-parse", "--verify", "HEAD") is not None
    src = Path(root) / recovery["source"]
    try:
        if src.is_dir():
            return _dir_signature(_files(src), Hasher()) == recovery.get("signature")
        if _stat(str(src)) == recovery.get("source_stat"):
            return True
        return method == DUPLICATE and Hasher().file(str(src)) == recovery.get("signature")
    except OSError:
        return False


# ----------------------------------------------------------------- duplicates

def _dir_signature(files: dict, hasher: Hasher) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        size, full = files[rel]
        h.update(f"{rel}\0{size}\0{hasher.file(full)}\n".encode())
    return h.hexdigest()


def _find_duplicate(root: Path, rel: str, files: dict, by_size: dict, hasher: Hasher,
                    taken: set) -> Optional[Recovery]:
    if not files:
        return None
    anchor_rel, (anchor_size, anchor_full) = max(files.items(), key=lambda kv: kv[1][0])
    if anchor_size == 0:
        return None
    anchor_hash = hasher.file(anchor_full)
    single_file = (root / rel).is_file()
    for other in sorted(by_size.get(anchor_size, [])):
        other_rel = _rel(root, other)
        if _within(other_rel, rel) or hasher.file(other) != anchor_hash:
            continue
        if single_file:
            twin = other_rel
        else:
            if not other_rel.endswith("/" + anchor_rel) and other_rel != anchor_rel:
                continue
            twin = other_rel[: -len(anchor_rel)].rstrip("/")
            if not twin or _within(rel, twin) or _within(twin, rel):
                continue
            twin_files = _files(root / twin)
            if set(twin_files) != set(files) or any(
                twin_files[k][0] != files[k][0] for k in files
            ):
                continue
            if _dir_signature(twin_files, hasher) != _dir_signature(files, hasher):
                continue
        if twin in taken:  # never let two copies point at each other
            continue
        total = sum(size for size, _ in files.values())
        return Recovery(
            DUPLICATE, twin, f"identical copy at {twin}",
            seconds=total / hasher.throughput, estimated=True,
            source_stat=_stat(str(root / twin)),
            signature=anchor_hash if single_file else _dir_signature(files, hasher),
        )
    return None


# ------------------------------------------------------------------- archives

def _members(archive: str) -> Optional[dict]:
    """{member path: size} for regular members, or None if unreadable."""
    try:
        if archive.lower().endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                return {i.filename: i.file_size for i in zf.infolist() if not i.is_dir()}
        with tarfile.open(archive) as tf:
            return {m.name: m.size for m in tf.getmembers() if m.isreg()}
    except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError, ValueError):
        return None


def _member_hashes(archive: str, names: dict) -> dict:
    """SHA-256 of the requested members {member: rel}, by decompressing them."""
    out = {}

    def digest(stream) -> str:
        h = hashlib.sha256()
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            h.update(chunk)
        return h.hexdigest()

    if archive.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for member in names:
                with zf.open(member) as stream:
                    out[member] = digest(stream)
    else:
        with tarfile.open(archive) as tf:
            for m in tf:
                if m.name in names and m.isreg():
                    stream = tf.extractfile(m)
                    if stream is not None:
                        out[m.name] = digest(stream)
    return out


def _find_archive(root: Path, rel: str, files: dict, archives: list, hasher: Hasher,
                  member_cache: dict) -> Optional[Recovery]:
    if not files:
        return None
    single_file = (root / rel).is_file()
    for archive in sorted(archives):
        archive_rel = _rel(root, archive)
        if _within(archive_rel, rel):
            continue  # deleting the candidate would delete its own archive
        if archive not in member_cache:
            member_cache[archive] = _members(archive)
        members = member_cache[archive]
        if not members:
            continue
        anchor = next(iter(sorted(files)))
        prefixes = set()
        for m in members:
            if m == anchor:
                prefixes.add("")
            elif m.endswith("/" + anchor):
                prefixes.add(m[: -len(anchor)])
        for prefix in sorted(prefixes):
            wanted = {prefix + r: r for r in files}
            if not all(m in members and members[m] == files[r][0] for m, r in wanted.items()):
                continue
            start = time.perf_counter()
            try:
                digests = _member_hashes(archive, wanted)
            except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError, ValueError):
                break
            seconds = time.perf_counter() - start
            if all(digests.get(m) == hasher.file(files[r][1]) for m, r in wanted.items()):
                where = f"{archive_rel}:{prefix}" if prefix else archive_rel
                return Recovery(
                    ARCHIVE, archive_rel,
                    f"{'member' if single_file else 'contents'} of {where}",
                    seconds=seconds, estimated=False, prefix=prefix,
                    source_stat=_stat(archive), single_file=single_file,
                )
    return None


# ------------------------------------------------------------------------ git

def _git(root: Path, *args: str) -> Optional[bytes]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=600,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _git_clean_files(root: Path) -> Optional[set]:
    if _git(root, "rev-parse", "--verify", "HEAD") is None:
        return None
    tracked = _git(root, "ls-files", "-z")
    modified = _git(root, "ls-files", "-z", "-m", "-d")
    staged = _git(root, "diff", "--cached", "--name-only", "-z", "--relative")
    if tracked is None or modified is None or staged is None:
        return None

    def split(b: bytes) -> set:
        return {p for p in b.decode("utf-8", "surrogateescape").split("\0") if p}

    return split(tracked) - split(modified) - split(staged)


def _git_blob_hashes(root: Path, paths: list) -> dict:
    """SHA-256 of `git cat-file --filters HEAD:<path>`: the bytes a checkout writes."""
    out = {}
    prefix = (_git(root, "rev-parse", "--show-prefix") or b"").decode().strip()
    for rel in paths:
        try:
            proc = subprocess.Popen(
                ["git", "-C", str(root), "cat-file", "--filters", f"HEAD:{prefix}{rel}"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except OSError:
            return out
        h = hashlib.sha256()
        with proc.stdout:
            for chunk in iter(lambda: proc.stdout.read(_CHUNK), b""):
                h.update(chunk)
        if proc.wait() == 0:
            out[rel] = h.hexdigest()
    return out


def _find_git(root: Path, rel: str, files: dict, clean: set, hasher: Hasher) -> Optional[Recovery]:
    if not files:
        return None
    single_file = (root / rel).is_file()
    full = {rel if single_file else f"{rel}/{r}": v for r, v in files.items()}
    if len(full) > 5000 or not set(full) <= clean:
        return None  # untracked, ignored or modified files would be lost
    start = time.perf_counter()
    blobs = _git_blob_hashes(root, sorted(full))
    seconds = time.perf_counter() - start
    if all(blobs.get(p) == hasher.file(v[1]) for p, v in full.items()):
        return Recovery(GIT, None, "tracked and clean; git reproduces every byte",
                        seconds=seconds, estimated=False)
    return None


# -------------------------------------------------------------------- restore

def restore(recovery: dict, project: Path, target_rel: str) -> None:
    """Recreate ``target_rel`` inside ``project`` from its level-0 source.

    Used by joint verification in the sandbox, so a plan is checked by doing
    the actual restore, not by trusting the analysis.
    """
    import shutil

    project = Path(project)
    target = project / target_rel
    method = recovery["method"]
    if method == DUPLICATE:
        source = project / recovery["source"]
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    elif method == ARCHIVE:
        archive = str(project / recovery["source"])
        prefix = recovery.get("prefix", "")
        single_file = target_rel.rsplit("/", 1)[-1]

        def dest_for(member: str) -> Optional[Path]:
            if not member.startswith(prefix):
                return None
            rest = member[len(prefix):]
            if not rest or ".." in rest.split("/"):
                return None
            if recovery.get("single_file"):
                return target if rest == single_file else None
            return target / rest

        if archive.lower().endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    dest = None if info.is_dir() else dest_for(info.filename)
                    if dest is not None:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as src, open(dest, "wb") as out:
                            shutil.copyfileobj(src, out, _CHUNK)
        else:
            with tarfile.open(archive) as tf:
                for m in tf:
                    dest = dest_for(m.name) if m.isreg() else None
                    if dest is not None:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with tf.extractfile(m) as src, open(dest, "wb") as out:
                            shutil.copyfileobj(src, out, _CHUNK)
    elif method == GIT:
        subprocess.run(["git", "-C", str(project), "checkout", "HEAD", "--", target_rel],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    else:
        raise ValueError(f"unknown recovery method {method!r}")


# ---------------------------------------------------------------------- entry

def find_recoverable(root: Path, regions: list, log: Callable[[str], None] = lambda _m: None) -> dict:
    """{candidate path: Recovery} for every CANDIDATE region provably recoverable."""
    root = Path(root).resolve()
    targets = [r for r in regions if r.status == CANDIDATE]
    if not targets:
        return {}
    hasher = Hasher()
    by_size, archives = _index(root)
    clean = _git_clean_files(root) if (root / ".git").exists() else None
    member_cache: dict = {}
    found: dict = {}
    for region in sorted(targets, key=lambda r: r.path):
        files = _files(root / region.path)
        recovery = (
            _find_duplicate(root, region.path, files, by_size, hasher, set(found))
            or (_find_git(root, region.path, files, clean, hasher) if clean else None)
            or _find_archive(root, region.path, files, archives, hasher, member_cache)
        )
        if recovery:
            found[region.path] = recovery
            log(f"level 0   {region.display}: {recovery.detail}")
    return found
