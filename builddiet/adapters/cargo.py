from __future__ import annotations

from pathlib import Path

from .base import Adapter, Suggestion


class CargoAdapter(Adapter):
    name = "cargo"
    markers = ("Cargo.toml",)
    known = {"target": "Cargo build output"}

    def suggest(self, root: Path):
        return Suggestion(self.name, "cargo build", "cargo test", "Cargo.toml found")
