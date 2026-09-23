"""Joint-plan verification against two artifacts that regenerate each other.

    mirror_a.bin, mirror_b.bin   the same irreproducible asset cached twice;
                                 the build restores either one from the other
    c.bin                        independently derived, slower (0.3s)

Removed one at a time, both mirrors come back byte-for-byte, so both are
PROVEN. Removed together, nothing can recreate them, so any plan containing
both must be rejected.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from builddiet import cli, manifest
from builddiet.config import Config, write_config
from builddiet.experiment import analyze
from builddiet.model import PROVEN
from builddiet.verifier import IDENTICAL, fingerprint

GEN = r'''
import os, shutil, time
a, b = "mirror_a.bin", "mirror_b.bin"
if not os.path.exists(a) and os.path.exists(b):
    shutil.copyfile(b, a)
if not os.path.exists(b) and os.path.exists(a):
    shutil.copyfile(a, b)
if not os.path.exists("c.bin"):
    time.sleep(0.3)
    with open("c.bin", "wb") as f:
        f.write(b"c" * 12000)
'''

CHECK = r'''
import os, sys
ok = all(os.path.exists(p) for p in ("mirror_a.bin", "mirror_b.bin", "c.bin"))
ok = ok and open("mirror_a.bin", "rb").read() == open("mirror_b.bin", "rb").read()
sys.exit(0 if ok else 1)
'''

MIRRORS = {"mirror_a.bin", "mirror_b.bin"}


class JointPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.root = base / "proj"
        cls.sandboxes = base / "sandboxes"
        cls.root.mkdir()
        (cls.root / "gen.py").write_text(GEN)
        (cls.root / "check.py").write_text(CHECK)
        asset = os.urandom(10000)
        (cls.root / "mirror_a.bin").write_bytes(asset)
        (cls.root / "mirror_b.bin").write_bytes(asset)
        (cls.root / "c.bin").write_bytes(b"c" * 12000)
        py = f'"{sys.executable}"'
        cls.cfg = Config(regenerate=f"{py} gen.py", verify=f"{py} check.py", min_size=1000, timeout=120)
        write_config(cls.root, cls.cfg)
        cls.manifest = analyze(cls.root, cls.cfg, sandbox_dir=cls.sandboxes)
        manifest.save(cls.root, cls.manifest)
        cls.original = fingerprint(cls.root / "mirror_a.bin")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def plan(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["plan", str(self.root), "--sandbox-dir", str(self.sandboxes), *extra])
        return code, out.getvalue()

    def test_both_mirrors_are_individually_proven(self):
        by_path = {e["path"]: e for e in self.manifest["entries"]}
        for p in MIRRORS | {"c.bin"}:
            self.assertEqual(by_path[p]["verdict"], PROVEN, p)
            self.assertEqual(by_path[p]["identity"], IDENTICAL, p)

    def test_plan_removing_both_mirrors_is_rejected(self):
        code, text = self.plan("--free", "15KB", "--json")
        self.assertEqual(code, 0)
        result = json.loads(text)
        first = result["candidates"][0]
        self.assertEqual({i["path"] for i in first["plan"]["items"]}, MIRRORS)
        self.assertFalse(first["jointly_verified"])
        verified = {i["path"] for i in result["verified_plan"]["plan"]["items"]}
        self.assertIn("c.bin", verified)
        self.assertFalse(MIRRORS <= verified)
        self.assertTrue(result["jointly_verified"])

    def test_no_jointly_verified_plan_when_target_needs_both(self):
        code, text = self.plan("--free", "30KB")
        self.assertEqual(code, 4)
        self.assertIn("CANDIDATE PLAN #1", text)
        self.assertIn("joint check FAILED", text)
        self.assertIn("NO JOINTLY VERIFIED PLAN", text)
        self.assertNotIn("\nJOINTLY VERIFIED PLAN", text)

    def test_output_distinguishes_candidate_from_verified(self):
        code, text = self.plan("--free", "15KB")
        self.assertEqual(code, 0)
        candidate, verified = text.split("\nJOINTLY VERIFIED PLAN\n")
        self.assertIn("CANDIDATE PLAN #1", candidate)
        self.assertIn("joint check FAILED", candidate)
        self.assertIn("c.bin", verified)
        self.assertFalse(all(m in verified for m in MIRRORS))

    def test_no_verify_is_labelled_unverified(self):
        code, text = self.plan("--free", "15KB", "--no-verify")
        self.assertEqual(code, 0)
        self.assertIn("NOT JOINTLY VERIFIED", text)
        self.assertNotIn("\nJOINTLY VERIFIED PLAN", text)

    def test_original_untouched(self):
        self.plan("--free", "15KB")
        self.assertEqual(fingerprint(self.root / "mirror_a.bin").entries, self.original.entries)
        self.assertTrue((self.root / "mirror_b.bin").exists())
        self.assertEqual(list(self.sandboxes.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
