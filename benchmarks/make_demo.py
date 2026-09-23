"""Create a demo workspace that shows what BuildDiet finds.

    python benchmarks/make_demo.py <dir>
    builddiet analyze <dir>/demo --min-size 100KB
    builddiet plan <dir>/demo --free 3MB

The workspace mimics a project with canonical inputs, cheap and expensive
derived data, a project-specific directory no cleaner catalog knows
(weird_stuff/foo_2026), and data that is neither needed nor derived.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BUILD = r'''
import hashlib, os, sys, time

def derive(src_path, out_path, seconds, size):
    if os.path.exists(out_path):
        return
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    seed = hashlib.sha256(open(src_path, "rb").read()).digest()
    time.sleep(seconds)  # stands in for real work
    with open(out_path, "wb") as f:
        block = seed * (size // len(seed))
        f.write(block)

derive("models/canonical.bin", "build/model.o", 0.2, 3_000_000)
derive("models/canonical.bin", "weird_stuff/foo_2026/layout.tmp", 0.5, 4_000_000)
derive("models/canonical.bin", "arena/derived/tables.bin", 3.0, 2_500_000)
os.makedirs("logs", exist_ok=True)
with open("logs/last.log", "w") as f:
    f.write(time.ctime())
'''

TEST = r'''
import os, sys
need = ["build/model.o", "weird_stuff/foo_2026/layout.tmp", "arena/derived/tables.bin"]
missing = [p for p in need if not os.path.exists(p)]
sys.exit(1 if missing else 0)
'''


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]) / "demo"
    if root.exists():
        print(f"{root} already exists")
        return 1
    (root / "models").mkdir(parents=True)
    (root / "models" / "canonical.bin").write_bytes(os.urandom(2_000_000))
    (root / "scripts").mkdir()
    (root / "scripts" / "build.py").write_text(BUILD)
    (root / "scripts" / "test.py").write_text(TEST)
    (root / "old-results").mkdir()
    (root / "old-results" / "run-2025.csv").write_bytes(os.urandom(1_500_000))
    (root / "Makefile").write_text(
        f"all:\n\t{sys.executable} scripts/build.py\n\ntest:\n\t{sys.executable} scripts/test.py\n"
    )
    subprocess.run([sys.executable, "scripts/build.py"], cwd=root, check=True)
    print(f"demo workspace created at {root}")
    print("next:")
    print(f'  builddiet analyze "{root}" --min-size 100KB')
    print(f'  builddiet plan "{root}" --free 3MB')
    return 0


if __name__ == "__main__":
    sys.exit(main())
