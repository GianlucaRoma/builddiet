"""BD-ZERO gate: compare `builddiet analyze` verdicts with EXPECTED.json.

    python benchmarks/bd_zero/check.py <dir>

Exit code 0 only if every expected verdict matches and no PROVEN item is
missing from the manifest.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    base = Path(sys.argv[1])
    ws = base / "workspace"
    expected = json.loads((base / "EXPECTED.json").read_text(encoding="utf-8"))
    manifest = json.loads((ws / ".builddiet" / "manifest.json").read_text(encoding="utf-8"))
    got = {e["path"]: e for e in manifest["entries"]}
    failures = 0
    for path, verdict in expected.items():
        entry = got.get(path)
        actual = entry["verdict"] if entry else "missing"
        ok = actual == verdict
        failures += not ok
        extra = ""
        if entry and entry.get("rebuild_seconds") is not None:
            extra = f"  rebuild {entry['rebuild_seconds']:.2f}s"
        print(f"{'PASS' if ok else 'FAIL'}  {path:28} expected {verdict:16} got {actual}{extra}")
    for path, entry in got.items():
        if path not in expected and entry["verdict"] == "proven":
            print(f"FAIL  {path:28} unexpectedly PROVEN")
            failures += 1
    print(f"\n{failures} mismatches over {len(expected)} expected verdicts")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
