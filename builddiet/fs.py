"""Filesystem primitives that never follow links.

On Windows, Python < 3.12 does not treat directory junctions as links:
`os.walk`, `shutil.copytree` and `shutil.rmtree` descend into them, so a
junction inside a project could make BuildDiet read, copy or even empty a
directory elsewhere. Every walk, copy and removal in BuildDiet goes through
this module instead, and treats any symlink, junction or other reparse point
as an opaque entry that is never followed.

When the kind of an entry cannot be determined, it is treated as a link
(fail closed: not followed, not read).
"""

from __future__ import annotations

import os
import stat
from typing import Iterator

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def is_link(entry) -> bool:
    """True for symlinks, junctions and other reparse points (and for anything
    whose kind cannot be determined). ``entry`` is a path or an os.DirEntry."""
    try:
        st = entry.stat(follow_symlinks=False) if isinstance(entry, os.DirEntry) else os.lstat(entry)
    except OSError:
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def walk(top) -> Iterator[tuple]:
    """Like os.walk (top-down; callers may prune ``dirs`` in place) but links are
    never descended: they are reported among ``files``."""
    stack = [os.fspath(top)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue
        dirs, files = [], []
        for e in entries:
            if is_link(e):
                files.append(e.name)
                continue
            try:
                (dirs if e.is_dir(follow_symlinks=False) else files).append(e.name)
            except OSError:
                files.append(e.name)
        yield current, dirs, files
        for name in reversed(dirs):
            stack.append(os.path.join(current, name))


def _chmod_retry(func, path) -> None:
    try:
        func(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        func(path)


def remove(path) -> None:
    """Delete ``path``. A link (at the top or anywhere below) is removed as a
    link: its target is never entered."""
    path = os.fspath(path)
    if is_link(path):
        try:
            _chmod_retry(os.unlink, path)
        except (IsADirectoryError, PermissionError, OSError):
            os.rmdir(path)  # directory symlinks and junctions on Windows
        return
    if not os.path.isdir(path):
        _chmod_retry(os.unlink, path)
        return
    with os.scandir(path) as it:
        children = [e.path for e in it]
    for child in children:
        remove(child)
    _chmod_retry(os.rmdir, path)


def copytree(src, dst, ignore=None) -> None:
    """Copy a tree without following links; links are skipped (not copied)."""
    import shutil

    src, dst = os.fspath(src), os.fspath(dst)
    os.makedirs(dst)
    with os.scandir(src) as it:
        entries = list(it)
    skip = set(ignore(src, [e.name for e in entries])) if ignore else set()
    for e in entries:
        if e.name in skip or is_link(e):
            continue
        target = os.path.join(dst, e.name)
        if e.is_dir(follow_symlinks=False):
            copytree(e.path, target, ignore)
        else:
            shutil.copy2(e.path, target, follow_symlinks=False)
    shutil.copystat(src, dst, follow_symlinks=False)
