"""Find the projects under a folder, so `watch` and `market` need no list."""

from __future__ import annotations

import os
from pathlib import Path

from .config import CONFIG_DIR

MARKERS = (
    ".git", CONFIG_DIR, "package.json", "pyproject.toml", "setup.py", "requirements.txt",
    "Cargo.toml", "CMakeLists.txt", "Makefile", "go.mod", "pom.xml", "build.gradle",
    "build.gradle.kts", "Package.swift", "pubspec.yaml", "composer.json", "Gemfile",
)
MARKER_SUFFIXES = (".sln", ".csproj", ".uproject", ".xcodeproj")
SKIP = {"node_modules", "__pycache__", ".venv", "venv", "target", "build", "dist", "Library",
        "$RECYCLE.BIN", "System Volume Information", "Windows", "Program Files",
        "Program Files (x86)", "ProgramData", "AppData"}


def is_workspace(path: Path) -> bool:
    try:
        names = os.listdir(path)
    except OSError:
        return False
    return any(n in MARKERS for n in names) or any(n.endswith(MARKER_SUFFIXES) for n in names)


def discover(root: Path, max_depth: int = 3) -> list:
    """Project roots at or below ``root``; nested projects are not listed twice."""
    root = Path(root).resolve()
    found = []

    def walk(path: Path, depth: int) -> None:
        if is_workspace(path):
            found.append(path)
            return
        if depth >= max_depth:
            return
        try:
            children = sorted(
                e.path for e in os.scandir(path)
                if e.is_dir(follow_symlinks=False) and e.name not in SKIP and not e.name.startswith(".")
            )
        except OSError:
            return
        for child in children:
            walk(Path(child), depth + 1)

    walk(root, 0)
    return found
