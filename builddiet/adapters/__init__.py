"""Ecosystem adapters: workflow suggestions and KNOWN-artifact catalogs."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import Adapter, Suggestion
from .cargo import CargoAdapter
from .cmake import CMakeAdapter
from .generic import GenericAdapter
from .node import NodeAdapter
from .python import PythonAdapter

ADAPTERS = (NodeAdapter(), CargoAdapter(), CMakeAdapter(), PythonAdapter(), GenericAdapter())


def detect(root: Path) -> list:
    return [a for a in ADAPTERS if a.detect(Path(root))]


def suggestions(root: Path) -> list:
    found = []
    for adapter in detect(root):
        suggestion = adapter.suggest(Path(root))
        if suggestion:
            found.append(suggestion)
    return found


def known_reason(rel: str, adapters) -> Optional[str]:
    name = rel.rsplit("/", 1)[-1]
    for adapter in adapters:
        reason = adapter.known_reason(name)
        if reason:
            return f"{adapter.name}: {reason}"
    return None


__all__ = ["Adapter", "Suggestion", "ADAPTERS", "detect", "suggestions", "known_reason"]
