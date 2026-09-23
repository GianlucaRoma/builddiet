"""End-to-end: `builddiet analyze <dir>` with no configuration at all.

    data/                canonical input                       -> NOT PROVEN (keep)
    tools/               the scripts                           -> NOT PROVEN (keep)
    out/                 written by tools/make_report.py       -> PROVEN by a discovered script
    index/               stale copy; make_index.py rewrites it -> STALE
    cache/               timestamped by tools/stamp.py         -> INCONCLUSIVE (not planned)
    gen2/                written by tools/bad_gen.py, which also
                         appends to data/input.csv             -> NOT PROVEN (recipe unsafe)
    models/, models_bak/ identical directories                 -> one is PROVEN (duplicate)
    assets/              extracted from assets.zip             -> PROVEN (archive)
    inline_out/          written by an inline command found
                         only in a (fabricated) agent log      -> PROVEN with --agent-logs
    notes/               irreproducible                        -> NOT PROVEN (keep)
    README.md            documents `git push` and `rm -rf out` -> never run
"""

import io
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from builddiet import cli, manifest
from builddiet.config import Config
from builddiet.experiment import analyze
from builddiet.model import INCONCLUSIVE, NOT_REGENERATED, PROVEN, STALE
from builddiet.verifier import fingerprint

REPORT = r'''
import hashlib, os
data = open(os.path.join("data", "input.csv"), "rb").read()
os.makedirs("out", exist_ok=True)
digest = data
with open(os.path.join("out", "report.bin"), "wb") as f:
    for _ in range(2000):
        digest = hashlib.sha256(digest).digest()
        f.write(digest)
'''
INDEX = r'''
import os
words = sorted(set(open(os.path.join("data", "input.csv")).read().split(",")))
os.makedirs("index", exist_ok=True)
with open(os.path.join("index", "idx.txt"), "w") as f:
    f.write("\n".join(words))
'''
STAMP = r'''
import os, time
os.makedirs("cache", exist_ok=True)
with open(os.path.join("cache", "stamp.txt"), "w") as f:
    f.write(repr(time.time()))
'''
BAD = r'''
import os
os.makedirs("gen2", exist_ok=True)
with open(os.path.join("gen2", "x.txt"), "w") as f:
    f.write("generated")
with open(os.path.join("data", "input.csv"), "a") as f:
    f.write(",tampered")
'''
README = """# demo

```bash
python tools/make_report.py
python tools/publish.py && git push
rm -rf out
```
"""


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def run(root: Path, script: str) -> None:
    import subprocess
    subprocess.run([sys.executable, script], cwd=root, check=True)


class ZeroConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.root = (base / "proj").resolve()
        cls.sandboxes = base / "sandboxes"
        root = cls.root
        words = [f"w{int.from_bytes(os.urandom(2), 'big')}" for _ in range(3000)]
        write(root / "data" / "input.csv", ",".join(words))
        write(root / "tools" / "make_report.py", REPORT)
        write(root / "tools" / "make_index.py", INDEX)
        write(root / "tools" / "stamp.py", STAMP)
        write(root / "tools" / "bad_gen.py", BAD)
        write(root / "README.md", README)
        write(root / "notes" / "ideas.txt", os.urandom(3000))
        blob = os.urandom(8000)
        write(root / "models" / "w.bin", blob)
        write(root / "models_bak" / "w.bin", blob)
        write(root / "assets" / "tex.png", os.urandom(6000))
        with zipfile.ZipFile(root / "assets.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(root / "assets" / "tex.png", "assets/tex.png")
        for script in ("tools/make_report.py", "tools/make_index.py", "tools/stamp.py"):
            run(root, script)
        write(root / "gen2" / "x.txt", "generated")
        write(root / "index" / "idx.txt", "an older index")  # stale copy
        # a command an agent ran; nothing else in the project mentions it
        write(root / "inline_out" / "data.txt", "made inline\n")
        t0 = time.time() - 600
        os.utime(root / "inline_out" / "data.txt", (t0 + 5, t0 + 5))
        cls.logs = base / "agent-logs"
        inline = ("python -c \"import os; os.makedirs('inline_out', exist_ok=True); "
                  "open('inline_out/data.txt', 'w').write('made inline\\n')\"")
        records = [
            {"timestamp": t0, "type": "session_meta", "payload": {"cwd": str(root)}},
            {"timestamp": t0 + 1, "type": "response_item",
             "payload": {"type": "function_call", "name": "exec_command",
                         "arguments": json.dumps({"cmd": inline, "workdir": str(root)})}},
            {"timestamp": t0 + 60, "type": "response_item",
             "payload": {"type": "function_call", "name": "exec_command",
                         "arguments": json.dumps({"cmd": "git push", "workdir": str(root)})}},
        ]
        write(cls.logs / "rollout.jsonl", "\n".join(json.dumps(r) for r in records))

        cls.original = fingerprint(root, "full")
        cls.asked = []

        def confirm(found, rejected):
            cls.asked.append(([r.command for r in found], rejected))
            return True

        cls.manifest = analyze(root, Config(min_size=0, timeout=120), sandbox_dir=cls.sandboxes,
                               agent_log_dirs=[cls.logs], confirm=confirm)
        cls.by_path = {e["path"]: e for e in cls.manifest["entries"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def verdict(self, path):
        return self.by_path[path]["verdict"]

    def test_mode_and_confirmation(self):
        self.assertEqual(self.manifest["mode"], "recipes")
        self.assertEqual(len(self.asked), 1)
        commands, rejected = self.asked[0]
        self.assertIn("{python} tools/make_report.py", commands)
        self.assertTrue(any("git push" in c for c, _ in rejected))

    def test_script_recipe_proves_output(self):
        e = self.by_path["out"]
        self.assertEqual(e["verdict"], PROVEN)
        self.assertEqual(e["method"], "recipe")
        self.assertEqual(e["recipe"], "{python} tools/make_report.py")

    def test_hash_proofs(self):
        self.assertEqual(self.by_path["models"]["method"], "duplicate")
        self.assertEqual(self.verdict("models"), PROVEN)
        self.assertNotEqual(self.verdict("models_bak"), PROVEN)  # the copy that is kept
        self.assertEqual(self.by_path["assets"]["method"], "archive")

    def test_agent_log_recipe(self):
        e = self.by_path["inline_out"]
        self.assertEqual(e["verdict"], PROVEN, e["detail"])
        self.assertIn("agent log", e["recipe_origin"])

    def test_stale_and_nondeterministic(self):
        self.assertEqual(self.verdict("index"), STALE)
        self.assertEqual(self.verdict("cache"), INCONCLUSIVE)

    def test_recipe_that_damages_data_is_never_trusted(self):
        e = self.by_path["gen2"]
        self.assertEqual(e["verdict"], NOT_REGENERATED)
        self.assertIn("data", e["detail"])

    def test_keep_irreproducible(self):
        for path in ("data", "tools", "notes"):
            self.assertEqual(self.verdict(path), NOT_REGENERATED, path)

    def test_original_untouched_and_sandboxes_removed(self):
        self.assertEqual(fingerprint(self.root, "full").entries, self.original.entries)
        self.assertEqual(list(self.sandboxes.iterdir()), [])

    def test_jointly_verified_plan(self):
        manifest.save(self.root, self.manifest)
        try:
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = cli.main(["plan", str(self.root), "--free", "1B", "--json",
                                 "--sandbox-dir", str(self.sandboxes)])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertTrue(result["jointly_verified"])
            out = io.StringIO()
            total = sum(e["bytes"] for e in self.manifest["entries"] if e["verdict"] == PROVEN)
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = cli.main(["plan", str(self.root), "--free", f"{total}B", "--json",
                                 "--sandbox-dir", str(self.sandboxes)])
            result = json.loads(out.getvalue())
            self.assertEqual(code, 0, result)
            planned = {i["path"] for i in result["verified_plan"]["plan"]["items"]}
            self.assertEqual(planned, {"out", "models", "assets", "inline_out"})
        finally:
            (self.root / ".builddiet" / "manifest.json").unlink()
            (self.root / ".builddiet").rmdir()
        self.assertEqual(fingerprint(self.root, "full").entries, self.original.entries)


class SideEffectTest(unittest.TestCase):
    """A recipe that also rewrites another existing file proves nothing: from the
    outside, refreshing a stale output and overwriting user data look the same."""

    def test_side_effects_block_proofs(self):
        both = ("import os\nfor d, t in (('fresh', 'A'), ('old', 'B')):\n"
                "    os.makedirs(d, exist_ok=True)\n    open(os.path.join(d, 'f.txt'), 'w').write(t)\n")
        reset = ("import os\nos.makedirs('gen3', exist_ok=True)\nopen('gen3/z.txt', 'w').write('z')\n"
                 "open(os.path.join('data', 'raw.csv'), 'w').write('')\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            write(root / "tools" / "both.py", both)
            write(root / "tools" / "reset.py", reset)
            write(root / "fresh" / "f.txt", "A")
            write(root / "old" / "f.txt", "stale B")
            write(root / "gen3" / "z.txt", "z")
            write(root / "data" / "raw.csv", "precious,rows\n" * 100)
            m = analyze(root, Config(min_size=0, timeout=60), sandbox_dir=Path(tmp) / "sb",
                        confirm=lambda *_: True)
            by = {e["path"]: e for e in m["entries"]}
            self.assertEqual(by["old"]["verdict"], STALE)
            self.assertEqual(by["fresh"]["verdict"], NOT_REGENERATED)
            self.assertIn("also changes old/f.txt", by["fresh"]["detail"])
            self.assertEqual(by["gen3"]["verdict"], NOT_REGENERATED)
            self.assertIn("data/raw.csv", by["gen3"]["detail"])
            self.assertNotEqual(by["data"]["verdict"], PROVEN)
            self.assertEqual((root / "data" / "raw.csv").read_text(), "precious,rows\n" * 100)


class VolatileSideEffectTest(unittest.TestCase):
    """Rewriting a log file is harmless and very common; it must not block a proof."""

    def test_log_rewrite_is_tolerated(self):
        build = ("import os, time\nos.makedirs('out', exist_ok=True)\n"
                 "open('out/a.bin', 'wb').write(b'x' * 5000)\n"
                 "os.makedirs('logs', exist_ok=True)\nopen('logs/build.log', 'w').write(str(time.time()))\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            write(root / "tools" / "build_out.py", build)
            write(root / "out" / "a.bin", b"x" * 5000)
            write(root / "logs" / "build.log", "old")
            m = analyze(root, Config(min_size=1000, timeout=60), sandbox_dir=Path(tmp) / "sb",
                        confirm=lambda *_: True)
            by = {e["path"]: e for e in m["entries"]}
            self.assertEqual(by["out"]["verdict"], PROVEN, by["out"]["detail"])
            self.assertEqual((root / "logs" / "build.log").read_text(), "old")


class NoRecipesTest(unittest.TestCase):
    def test_hash_only_mode_runs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            blob = os.urandom(4000)
            write(root / "a" / "x.bin", blob)
            write(root / "b" / "x.bin", blob)
            write(root / "gen.py", "open('marker','w')\n")
            m = analyze(root, Config(min_size=0), recipes_enabled=False)
            self.assertEqual(m["mode"], "hash-only")
            self.assertFalse((root / "marker").exists())
            self.assertEqual({e["path"] for e in m["entries"] if e["verdict"] == PROVEN}, {"a"})


if __name__ == "__main__":
    unittest.main()
