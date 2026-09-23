"""BD-REAL: build a small, isolated, real workspace for the release gate.

    python benchmarks/bd_real/make_workspace.py <dir>

Creates <dir>/workspace with a make-like build (a target is rebuilt when it
is missing or older than its inputs) doing real work, and puts it in the
state a real working copy ends up in:

    canonical, irreproducible   data/corpus.txt, data/weights.f32
    code                        build.py, verify.py
    derived, regenerable        derived/corpus.idx.json, derived/shards/,
                                derived/features.bin, cache/ngrams.json
    derived, nondeterministic   derived/build-info.json (timestamp)
    not needed, not derived     notes/meeting-2025-11.md
    derived but STALE           derived/weights.q8: the weights were updated
                                afterwards and nobody rebuilt
    derived but CORRUPT         derived/corpus.z: truncated by an interrupted
                                write, newer than its input, so make-style
                                builds consider it up to date

EXPECTED (below) is what BuildDiet must conclude; check.py compares it with
the manifest produced by `builddiet analyze`. It is written next to the
workspace, not inside it.
"""

from __future__ import annotations

import json
import os
import random
import struct
import subprocess
import sys
import time
from pathlib import Path

EXPECTED = {
    "build.py": "required",
    "verify.py": "required",
    "data/corpus.txt": "required",
    "data/weights.f32": "required",
    "notes/meeting-2025-11.md": "not-regenerated",
    "derived/weights.q8": "stale",
    "derived/corpus.z": "stale",
    "derived/corpus.idx.json": "proven",
    "derived/shards": "proven",
    "derived/features.bin": "proven",
    "cache/ngrams.json": "proven",
    "derived/build-info.json": "proven",
}

CONFIG = """\
[commands]
regenerate = 'python build.py'
verify = 'python verify.py'
timeout = 600

[candidates]
auto = true
depth = 2
include = []
exclude = []
min_size = '100B'

[identity]
hash = 'full'

[reuse]
"""

BUILD = r'''"""Make-like build: a target is rebuilt when missing or older than its inputs."""
import hashlib, json, os, struct, sys, time, zlib

RULES = []


def rule(target, inputs):
    def register(fn):
        RULES.append((target, inputs, fn))
        return fn
    return register


def mtime(path):
    if not os.path.exists(path):
        return None
    if os.path.isdir(path):
        times = [os.stat(os.path.join(d, f)).st_mtime for d, _, fs in os.walk(path) for f in fs]
        return min(times) if times else None
    return os.stat(path).st_mtime


def read(path, mode="rb"):
    with open(path, mode) as f:
        return f.read()


def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


@rule("derived/weights.q8", ["data/weights.f32"])
def quantize(out):
    raw = read("data/weights.f32")
    vals = struct.unpack(f"<{len(raw) // 4}f", raw)
    scale = max(abs(v) for v in vals) / 127 or 1.0
    write(out, struct.pack("<f", scale) + bytes(round(v / scale) & 0xFF for v in vals))


@rule("derived/corpus.idx.json", ["data/corpus.txt"])
def index(out):
    counts = {}
    for w in read("data/corpus.txt", "r").split():
        counts[w] = counts.get(w, 0) + 1
    write(out, json.dumps(counts, sort_keys=True).encode())


@rule("derived/corpus.z", ["data/corpus.txt"])
def compress(out):
    write(out, zlib.compress(read("data/corpus.txt"), 9))


@rule("derived/shards", ["data/corpus.txt"])
def shard(out):
    data = read("data/corpus.txt")
    os.makedirs(out, exist_ok=True)
    size = -(-len(data) // 8)
    for i in range(8):
        write(f"{out}/shard-{i:02d}.txt", data[i * size:(i + 1) * size])


@rule("derived/features.bin", ["data/corpus.txt"])
def features(out):
    data = read("data/corpus.txt")
    parts = []
    for i in range(0, len(data) - 64, 32):
        h = data[i:i + 64]
        for _ in range(6):
            h = hashlib.sha256(h).digest()
        parts.append(h[:24])
    write(out, b"".join(parts))


@rule("cache/ngrams.json", ["data/corpus.txt"])
def ngrams(out):
    ws = read("data/corpus.txt", "r").split()
    counts = {}
    for a, b, c in zip(ws, ws[1:], ws[2:]):
        key = a + " " + b + " " + c
        counts[key] = counts.get(key, 0) + 1
    write(out, json.dumps(counts, sort_keys=True).encode())


@rule("derived/build-info.json", [])
def info(out):
    write(out, json.dumps({"built_at": time.time(), "python": sys.version}, indent=2).encode())


def main():
    for target, inputs, fn in RULES:
        missing = [i for i in inputs if not os.path.exists(i)]
        if missing:
            print(f"build: no rule to make '{missing[0]}', needed by '{target}'", file=sys.stderr)
            return 1
        built = mtime(target)
        if built is not None and all(mtime(i) <= built for i in inputs):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        start = time.perf_counter()
        fn(target)
        print(f"built {target} in {time.perf_counter() - start:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

VERIFY = r'''"""Checks the outputs the product depends on. corpus.z (a distribution
archive) and build-info.json are not covered, like in many real projects."""
import json, os, struct, sys


def fail(msg):
    print("verify: FAIL:", msg)
    sys.exit(1)


def read(path, mode="rb"):
    with open(path, mode) as f:
        return f.read()


corpus = read("data/corpus.txt")
words = corpus.decode().split()
if sum(json.loads(read("derived/corpus.idx.json")).values()) != len(words):
    fail("word index does not match the corpus")
shards = sorted(os.listdir("derived/shards"))
if len(shards) != 8 or b"".join(read("derived/shards/" + s) for s in shards) != corpus:
    fail("shards do not reassemble the corpus")
if sum(json.loads(read("cache/ngrams.json")).values()) != len(words) - 2:
    fail("n-gram counts do not match the corpus")
if os.path.getsize("derived/features.bin") != len(range(0, len(corpus) - 64, 32)) * 24:
    fail("feature file has the wrong size")
raw = read("data/weights.f32")
vals = struct.unpack(f"<{len(raw) // 4}f", raw)
q = read("derived/weights.q8")
scale = struct.unpack("<f", q[:4])[0]
for i in range(0, len(vals), max(1, len(vals) // 2000)):
    b = q[4 + i]
    if abs((b - 256 if b > 127 else b) * scale - vals[i]) > scale * 0.51 + 1e-6:
        fail(f"quantized weight {i} does not match data/weights.f32")
print("verify: ok")
'''

NOTES = """# Meeting 2025-11-14

Decisions that exist only here:
- keep the 3-gram model, drop the 5-gram experiment
- corpus v3 frozen; any change needs a new baseline
"""


def _weights(rng: random.Random, n: int) -> bytes:
    return struct.pack(f"<{n}f", *(rng.gauss(0, 1) for _ in range(n)))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    ws = Path(sys.argv[1]).resolve() / "workspace"
    if ws.exists():
        print(f"{ws} already exists")
        return 1
    rng = random.Random(os.urandom(16))  # irreproducible on purpose
    (ws / "data").mkdir(parents=True)
    (ws / "notes").mkdir()
    (ws / ".builddiet").mkdir()
    (ws / "build.py").write_text(BUILD, encoding="utf-8")
    (ws / "verify.py").write_text(VERIFY, encoding="utf-8")
    (ws / "notes" / "meeting-2025-11.md").write_text(NOTES * 20, encoding="utf-8")
    (ws / ".builddiet" / "config.toml").write_text(CONFIG, encoding="utf-8")

    letters = "abcdefghijklmnopqrstuvwxyz"
    vocab = ["".join(rng.choice(letters) for _ in range(rng.randint(3, 9))) for _ in range(3000)]
    weights = [1 / (i + 1) for i in range(len(vocab))]
    words = rng.choices(vocab, weights=weights, k=220_000)
    lines = [" ".join(words[i:i + 12]) for i in range(0, len(words), 12)]
    (ws / "data" / "corpus.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 1. build everything against the first version of the weights
    (ws / "data" / "weights.f32").write_bytes(_weights(rng, 400_000))
    subprocess.run([sys.executable, "build.py"], cwd=ws, check=True)
    time.sleep(0.1)
    # 2. the weights get updated and nobody rebuilds -> weights.q8 is stale
    (ws / "data" / "weights.f32").write_bytes(_weights(rng, 400_000))
    # 3. an interrupted write truncated the archive; it is newer than its input
    archive = ws / "derived" / "corpus.z"
    archive.write_bytes(archive.read_bytes()[: archive.stat().st_size // 2])

    (ws.parent / "EXPECTED.json").write_text(json.dumps(EXPECTED, indent=2), encoding="utf-8")
    print(f"workspace ready: {ws}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
