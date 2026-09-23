"""BD-ZERO: release gate for `builddiet analyze <dir>` with NO configuration.

    python benchmarks/bd_zero/make_workspace.py <dir>

Creates <dir>/workspace: a git repository shaped like a real data/ML project,
plus <dir>/agent-logs with ONE fabricated Codex-style session log (clearly
synthetic, used to exercise --agent-logs-dir) and <dir>/EXPECTED.json.

    src/, scripts/, README.md    tracked in git                 -> IN GIT
    data/raw.csv                 canonical, irreproducible      -> NOT PROVEN (keep)
    notes/                       irreproducible                 -> NOT PROVEN (keep)
    features/                    made by scripts/build_features -> PROVEN (identical copy in backups/)
    backups/                     holds the copy of features/    -> keep (source)
    models/                      made by scripts/train.py       -> PROVEN by a discovered recipe
    exports/                     scripts/eval.py, on-disk copy
                                 is from an older run            -> STALE
    reports/                     timestamped by scripts/report  -> INCONCLUSIVE
    release/app-1.0/             extracted from dist/app-1.0.zip -> PROVEN (archive)
    dist/                        holds the zip                  -> keep (source)
    cache/                       made by an inline command seen
                                 only in the agent log          -> PROVEN (agent log)
    README.md also documents `python scripts/publish.py && git push`: never run.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

EXPECTED = {
    "src": "in-git",
    "scripts": "in-git",
    "data": "not-regenerated",
    "notes": "not-regenerated",
    "features": "proven",
    "backups": "not-regenerated",
    "models": "proven",
    "exports": "stale",
    "reports": "inconclusive",
    "release": "proven",
    "dist": "not-regenerated",
    "cache": "proven",
}

APP = '''"""Tiny app package (tracked source code)."""


def score(features: bytes) -> int:
    return sum(features[:1024]) % 997
'''

BUILD_FEATURES = r'''"""data/raw.csv -> features/ (one shard per 5000 rows)."""
import hashlib, os
rows = open(os.path.join("data", "raw.csv"), encoding="utf-8").read().splitlines()
os.makedirs("features", exist_ok=True)
for i in range(0, len(rows), 5000):
    h = b""
    with open(os.path.join("features", f"shard-{i // 5000:03d}.bin"), "wb") as f:
        for row in rows[i:i + 5000]:
            h = hashlib.sha256(h + row.encode()).digest()
            f.write(h)
'''

TRAIN = r'''"""features/ -> models/model.bin (slow on purpose: many hash rounds)."""
import hashlib, os
state = b""
for name in sorted(os.listdir("features")):
    state = hashlib.sha256(state + open(os.path.join("features", name), "rb").read()).digest()
os.makedirs("models", exist_ok=True)
with open(os.path.join("models", "model.bin"), "wb") as f:
    for _ in range(40):
        for _ in range(20000):
            state = hashlib.sha256(state).digest()
        f.write(state * 256)
'''

EVAL = r'''"""models/ -> exports/metrics.json (deterministic)."""
import hashlib, json, os
digest = hashlib.sha256(open(os.path.join("models", "model.bin"), "rb").read()).hexdigest()
os.makedirs("exports", exist_ok=True)
with open(os.path.join("exports", "metrics.json"), "w") as f:
    per_class = [int(digest[i:i + 2], 16) / 255 for i in range(0, 64, 2)] * 4
    json.dump({"model": digest, "accuracy": int(digest[:4], 16) / 65535, "per_class": per_class},
              f, indent=2, sort_keys=True)
'''

REPORT = r'''"""-> reports/summary.html (contains the build time: nondeterministic)."""
import os, time
os.makedirs("reports", exist_ok=True)
with open(os.path.join("reports", "summary.html"), "w") as f:
    f.write(f"<html><body>built {time.time()}</body></html>" + "<!-- pad -->" * 200)
'''

PUBLISH = '''print("would upload the release")\n'''

README = """# demo-ml

Rebuild everything:

```bash
python scripts/build_features.py
python scripts/train.py
python scripts/eval.py
```

Release:

```bash
python scripts/publish.py && git push
```
"""


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    base = Path(sys.argv[1]).resolve()
    ws = base / "workspace"
    if ws.exists():
        print(f"{ws} already exists")
        return 1
    rng = random.Random(os.urandom(16))  # irreproducible on purpose
    ws.mkdir(parents=True)
    (ws / "src" / "app").mkdir(parents=True)
    (ws / "src" / "app" / "__init__.py").write_text(APP * 20)
    (ws / "scripts").mkdir()
    for name, text in (("build_features.py", BUILD_FEATURES), ("train.py", TRAIN), ("eval.py", EVAL),
                       ("report.py", REPORT), ("publish.py", PUBLISH)):
        (ws / "scripts" / name).write_text(text)
    (ws / "README.md").write_text(README)
    (ws / ".gitignore").write_text("\n".join(
        ["data/", "notes/", "features/", "backups/", "models/", "exports/", "reports/",
         "release/", "dist/", "cache/", ".builddiet/"]) + "\n")
    (ws / "data").mkdir()
    rows = [",".join(str(rng.randint(0, 10**9)) for _ in range(6)) for _ in range(40000)]
    (ws / "data" / "raw.csv").write_text("\n".join(rows) + "\n")
    (ws / "notes").mkdir()
    (ws / "notes" / "experiments.md").write_text("".join(f"- run {i}: lr={rng.random():.5f}\n" for i in range(400)))

    py = [sys.executable]
    for script in ("build_features.py", "train.py", "eval.py", "report.py"):
        subprocess.run(py + [f"scripts/{script}"], cwd=ws, check=True)
    shutil.copytree(ws / "features", ws / "backups" / "features-2026-09-01")
    # metrics from an older model: the on-disk copy is stale
    (ws / "exports" / "metrics.json").write_text(
        json.dumps({"model": "old", "accuracy": 0.41, "per_class": [0.5] * 128}, indent=2))

    # a release zip and its extracted copy
    (ws / "dist").mkdir()
    with zipfile.ZipFile(ws / "dist" / "app-1.0.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted((ws / "src").rglob("*.py")):
            zf.write(f, "app-1.0/" + f.relative_to(ws / "src").as_posix())
        zf.write(ws / "README.md", "app-1.0/README.md")
        zf.writestr("app-1.0/assets/logo.bin", os.urandom(20000))
    with zipfile.ZipFile(ws / "dist" / "app-1.0.zip") as zf:
        zf.extractall(ws / "release")

    # an inline command an agent ran; no file in the project mentions it
    inline = ("python -c \"import hashlib, os; os.makedirs('cache', exist_ok=True); "
              "open('cache/tokens.bin', 'wb').write(hashlib.sha256(open('data/raw.csv', 'rb')"
              ".read()).digest() * 20000)\"")
    subprocess.run(inline.replace("python", f'"{sys.executable}"', 1), cwd=ws, check=True, shell=True)
    written = (ws / "cache" / "tokens.bin").stat().st_mtime
    log_dir = base / "agent-logs" / "2026" / "09" / "23"
    log_dir.mkdir(parents=True)
    records = [
        {"timestamp": written - 30, "type": "session_meta",
         "payload": {"id": "synthetic-session", "cwd": str(ws), "cli_version": "fixture"}},
        {"timestamp": written - 1, "type": "response_item",
         "payload": {"type": "function_call", "name": "exec_command",
                     "arguments": json.dumps({"cmd": inline, "workdir": str(ws)}), "call_id": "c1"}},
        {"timestamp": written + 5, "type": "response_item",
         "payload": {"type": "function_call", "name": "exec_command",
                     "arguments": json.dumps({"cmd": "git status", "workdir": str(ws)}), "call_id": "c2"}},
    ]
    (log_dir / "rollout-synthetic.jsonl").write_text("\n".join(json.dumps(r) for r in records))

    git(ws, "init", "-q")
    git(ws, "config", "user.email", "bd@example.com")
    git(ws, "config", "user.name", "bd")
    git(ws, "config", "core.autocrlf", "false")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "demo project")

    (base / "EXPECTED.json").write_text(json.dumps(EXPECTED, indent=2))
    print(f"workspace ready: {ws}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
