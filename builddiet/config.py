"""Project configuration stored in ``<project>/.builddiet/config.toml``."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from . import tomlmini
from .units import format_size, parse_size

CONFIG_DIR = ".builddiet"
CONFIG_FILE = "config.toml"
MANIFEST_FILE = "manifest.json"

HASH_MODES = ("full", "meta")


class ConfigError(ValueError):
    pass


def normalize_rel(path: str) -> str:
    """Normalise a project-relative path to 'a/b' form and reject escapes."""
    text = str(path).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    text = text.rstrip("/")
    pure = PurePosixPath(text)
    if not text or text == ".":
        raise ConfigError("path must not be the project root")
    if pure.is_absolute() or (len(text) > 1 and text[1] == ":"):
        raise ConfigError(f"path must be relative to the project: {path!r}")
    if ".." in pure.parts:
        raise ConfigError(f"path must stay inside the project: {path!r}")
    return str(pure)


@dataclass
class Config:
    regenerate: Optional[str] = None
    verify: Optional[str] = None
    timeout: int = 3600
    auto: bool = True
    depth: int = 1
    include: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    min_size: int = 10_000_000
    hash_mode: str = "full"
    reuse: dict = field(default_factory=dict)

    def validate(self) -> "Config":
        if not (self.regenerate or self.verify):
            raise ConfigError(
                "no workflow configured: set a regenerate and/or verify command "
                "(run `builddiet init`)"
            )
        if self.depth < 1:
            raise ConfigError("candidates.depth must be >= 1")
        if self.timeout <= 0:
            raise ConfigError("commands.timeout must be positive")
        if self.hash_mode not in HASH_MODES:
            raise ConfigError(f"identity.hash must be one of {HASH_MODES}")
        self.include = [normalize_rel(p) for p in self.include]
        for path, p in self.reuse.items():
            if not 0.0 <= float(p) <= 1.0:
                raise ConfigError(f"reuse probability for {path!r} must be in [0, 1]")
        return self

    def digest(self) -> str:
        """Hash of every setting that affects what an experiment proves."""
        relevant = {
            "regenerate": self.regenerate,
            "verify": self.verify,
            "auto": self.auto,
            "depth": self.depth,
            "include": sorted(self.include),
            "exclude": sorted(self.exclude),
            "min_size": self.min_size,
            "hash": self.hash_mode,
        }
        blob = json.dumps(relevant, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


def from_dict(data: dict) -> Config:
    commands = data.get("commands", {})
    candidates = data.get("candidates", {})
    identity = data.get("identity", {})
    try:
        cfg = Config(
            regenerate=commands.get("regenerate") or None,
            verify=commands.get("verify") or None,
            timeout=int(commands.get("timeout", 3600)),
            auto=bool(candidates.get("auto", True)),
            depth=int(candidates.get("depth", 1)),
            include=list(candidates.get("include", [])),
            exclude=list(candidates.get("exclude", [])),
            min_size=parse_size(candidates.get("min_size", "10MB")),
            hash_mode=str(identity.get("hash", "full")),
            reuse={str(k): float(v) for k, v in data.get("reuse", {}).items()},
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc
    return cfg


def config_path(root: Path) -> Path:
    return Path(root) / CONFIG_DIR / CONFIG_FILE


def load_config(root: Path) -> Optional[Config]:
    path = config_path(root)
    if not path.is_file():
        return None
    try:
        data = tomlmini.loads(path.read_text(encoding="utf-8"))
    except tomlmini.TomlError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return from_dict(data)


def render_config(cfg: Config) -> str:
    q = tomlmini.quote

    def arr(items):
        return "[" + ", ".join(q(i) for i in items) + "]"

    lines = [
        "# BuildDiet configuration. See docs/EXPERIMENT_MODEL.md.",
        "# Commands run ONLY inside a sandbox copy of the project, never in the original.",
        "",
        "[commands]",
        "# Recreates derived data (build, codegen, dataset preparation, ...).",
        f"regenerate = {q(cfg.regenerate or '')}",
        "# Defines 'the project still works'. Its exit code is the gate.",
        f"verify = {q(cfg.verify or '')}",
        "# Seconds allowed for one regenerate+verify run.",
        f"timeout = {cfg.timeout}",
        "",
        "[candidates]",
        "# auto: every directory at `depth` levels is a candidate for an experiment.",
        f"auto = {'true' if cfg.auto else 'false'}",
        f"depth = {cfg.depth}",
        "# Extra directories to test individually, e.g. ['experiments/cache'].",
        f"include = {arr(cfg.include)}",
        "# Glob patterns never touched, not even in the sandbox, e.g. ['family_photos'].",
        f"exclude = {arr(cfg.exclude)}",
        "# Smaller directories are reported but not experimented on.",
        f"min_size = {q(format_size(cfg.min_size).replace(' ', ''))}",
        "",
        "[identity]",
        "# full: SHA-256 of every file. meta: paths and sizes only (faster, weaker).",
        f"hash = {q(cfg.hash_mode)}",
        "",
        "# Optional: probability (0..1) that a directory will be needed again soon.",
        "# The planner minimises  probability x measured rebuild time.",
        "[reuse]",
    ]
    for path, p in sorted(cfg.reuse.items()):
        lines.append(f"{q(path)} = {p}")
    if not cfg.reuse:
        lines.append("# 'old-results' = 0.05")
    return "\n".join(lines) + "\n"


def write_config(root: Path, cfg: Config, overwrite: bool = False) -> Path:
    path = config_path(root)
    if path.exists() and not overwrite:
        raise ConfigError(f"{path} already exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_config(cfg), encoding="utf-8")
    return path
