"""Builds a small but realistic fixture workspace for end-to-end tests.

    src/input.txt          source of truth         -> REQUIRED
    tools/gen.py           the generator           -> REQUIRED
    checks/check.py        the verifier            -> REQUIRED
    build/out.txt          derived, deterministic  -> PROVEN identical
    arena/derived/*.bin    derived, slow (0.4s)    -> PROVEN identical
    cache/stamp.txt        derived, timestamped    -> PROVEN recreated
    family_photos/*        not needed, not derived -> NOT REGENERATED
    index.txt              derived single file     -> PROVEN identical
    summary.txt            derived, corrupted copy -> STALE (not rebuilt: exists)
    always.txt             derived, old copy       -> STALE (baseline refreshes it)
    README.txt             not needed, not derived -> NOT REGENERATED

summary.txt and always.txt are corrupted by the test after the derived data
has been materialised; see ``corrupt_derived``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from builddiet.config import Config

GEN = r'''
import hashlib, os, time
src = open(os.path.join("src", "input.txt")).read()
os.makedirs("build", exist_ok=True)
with open(os.path.join("build", "out.txt"), "w") as f:
    f.write(src.upper())
os.makedirs(os.path.join("arena", "derived"), exist_ok=True)
table = os.path.join("arena", "derived", "table.bin")
if not os.path.exists(table):
    time.sleep(0.4)  # an expensive derivation
    with open(table, "wb") as f:
        f.write(hashlib.sha256(src.encode()).digest() * 4000)
os.makedirs("cache", exist_ok=True)
with open(os.path.join("cache", "stamp.txt"), "w") as f:
    f.write(repr(time.time()))
for name, text in (("index.txt", src[::-1] * 3), ("summary.txt", str(len(src)))):
    if not os.path.exists(name):
        with open(name, "w") as f:
            f.write(text)
with open("always.txt", "w") as f:
    f.write(src.upper())
'''

CHECK = r'''
import sys
out = open("build/out.txt").read()
sys.exit(0 if out == open("src/input.txt").read().upper() else 1)
'''


def make_project(root: Path) -> Path:
    root = Path(root)
    (root / "src").mkdir(parents=True)
    (root / "src" / "input.txt").write_text("hello builddiet\n" * 50)
    (root / "tools").mkdir()
    (root / "tools" / "gen.py").write_text(GEN)
    (root / "checks").mkdir()
    (root / "checks" / "check.py").write_text(CHECK)
    (root / "family_photos").mkdir()
    (root / "family_photos" / "beach.jpg").write_bytes(os.urandom(20000))
    (root / "README.txt").write_text("fixture\n")
    return root


def corrupt_derived(root: Path) -> None:
    (Path(root) / "summary.txt").write_text("truncated")
    (Path(root) / "always.txt").write_text("output of an older version")


def fixture_config(**overrides) -> Config:
    py = f'"{sys.executable}"'
    cfg = Config(
        regenerate=f"{py} tools/gen.py",
        verify=f"{py} checks/check.py",
        min_size=0,
        timeout=120,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg
