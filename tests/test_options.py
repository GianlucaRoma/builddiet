"""LIGHT / NORMAL / EXTREME, and explicit out-of-space handling."""

import errno
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from builddiet import cli, manifest, protect, reclaim
from builddiet.config import Config
from builddiet.experiment import analyze, verify_joint
from builddiet.options import EXTREME, LIGHT, NORMAL, TierRule, build_options, pick_for_need
from builddiet.planner import JointCheck, PlanItem
from builddiet.sandbox import OutOfSpace, SandboxError
from builddiet.service import load_manifests, project_config, reclaim_options

from tests.test_reclaim_watch import make_project, write

GB = 10**9


def item(path, gb, seconds):
    return PlanItem("p", "/p", path, int(gb * GB), seconds)


def manifest_for(methods: dict) -> dict:
    return {"project": "/p", "name": "p", "reuse": {}, "entries": [
        {"path": p, "verdict": "proven", "bytes": int(gb * GB), "rebuild_seconds": s, "identity": "identical",
         "method": m, "kind": "dir"} for p, (gb, s, m) in methods.items()]}


class TierRuleTest(unittest.TestCase):
    def test_placement(self):
        rule = TierRule()
        self.assertEqual(rule.tier(item("a", 1, 9999), "duplicate"), LIGHT)  # a copy, whatever it costs
        self.assertEqual(rule.tier(item("b", 1, 0.5), "recipe"), LIGHT)
        self.assertEqual(rule.tier(item("c", 1, 30), "recipe"), NORMAL)
        self.assertEqual(rule.tier(item("d", 1, 300), "workflow"), NORMAL)
        self.assertEqual(rule.tier(item("e", 1, 301), "workflow"), EXTREME)
        strict = TierRule(light_max=0, normal_max=10)
        self.assertEqual(strict.tier(item("b", 1, 0.5), "recipe"), NORMAL)
        self.assertEqual(strict.tier(item("c", 1, 30), "recipe"), EXTREME)


class BuildOptionsTest(unittest.TestCase):
    def setUp(self):
        self.m = manifest_for({"copy": (5, 0.0, "duplicate"), "fast": (3, 0.4, "recipe"),
                               "mid": (8, 90, "recipe"), "slow": (20, 7200, "workflow")})
        self.calls = []

    def build(self, verify=None, **kw):
        def ok(root, group):
            self.calls.append(sorted(i.path for i in group))
            return JointCheck(True, "ok")

        with mock.patch("builddiet.planner.Path.exists", return_value=True):
            return build_options([self.m], verify or ok, **kw)

    def test_cumulative_and_recommended(self):
        light, normal, extreme = self.build()
        self.assertEqual({i.path for i in light.items}, {"copy", "fast"})
        self.assertEqual({i.path for i in normal.items}, {"copy", "fast", "mid"})
        self.assertEqual({i.path for i in extreme.items}, {"copy", "fast", "mid", "slow"})
        self.assertTrue(normal.recommended)
        self.assertFalse(light.recommended or extreme.recommended)
        self.assertEqual(light.freed, 8 * GB)
        self.assertEqual(extreme.rebuild_seconds, 7290.4)
        self.assertEqual(light.breakdown(), {"copies": 1, "recipes": 1})

    def test_each_option_is_verified_as_a_whole(self):
        self.build()
        self.assertEqual(self.calls, [["copy", "fast"], ["copy", "fast", "mid"], ["copy", "fast", "mid", "slow"]])

    def test_failed_joint_check_leaves_an_item_out(self):
        def verify(root, group):
            names = {i.path for i in group}
            return JointCheck(not {"fast", "mid"} <= names, "fast and mid need each other")

        light, normal, extreme = self.build(verify)
        self.assertTrue(light.verified)
        self.assertTrue(normal.verified)
        self.assertEqual(len(normal.dropped), 1)
        self.assertNotEqual({i.path for i in normal.items} >= {"fast", "mid"}, True)

    def test_fatal_check_verifies_nothing(self):
        light, normal, extreme = self.build(lambda r, g: JointCheck(False, "no space", fatal=True))
        self.assertFalse(any(o.verified for o in (light, normal, extreme)))
        self.assertTrue(all(o.dropped for o in (light, normal, extreme)))

    def test_pick_for_need(self):
        options = self.build()
        self.assertEqual(pick_for_need(options, 1 * GB, (LIGHT, NORMAL)).name, LIGHT)
        self.assertEqual(pick_for_need(options, 10 * GB, (LIGHT, NORMAL)).name, NORMAL)
        self.assertEqual(pick_for_need(options, 30 * GB, (LIGHT, NORMAL)).name, NORMAL)  # the most allowed
        self.assertEqual(pick_for_need(options, 30 * GB, (LIGHT, NORMAL, EXTREME)).name, EXTREME)


class OptionsEndToEnd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = make_project(self.base).resolve()
        self.sb = self.base / "sb"
        write(self.root / "tools" / "slow.py",
              "import os, time\ntime.sleep(1.5)\nos.makedirs('heavy', exist_ok=True)\n"
              "open('heavy/h.bin', 'wb').write(b'h' * 3000)\n")
        write(self.root / "heavy" / "h.bin", b"h" * 3000)
        m = analyze(self.root, Config(min_size=1000, timeout=60), sandbox_dir=self.sb, confirm=lambda *_: True)
        manifest.save(self.root, m)

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_plan_shows_three_options_without_free(self):
        code, text = self.cli("plan", str(self.root), "--sandbox-dir", str(self.sb), "--normal-max", "1s",
                              "--light-max", "0.5s")
        self.assertEqual(code, 0, text)
        for word in ("LIGHT", "NORMAL", "EXTREME", "<- recommended", "joint verification", "PASS",
                     "How an item is placed", "rebuild <= 1s"):
            self.assertIn(word, text)
        code, js = self.cli("plan", str(self.root), "--sandbox-dir", str(self.sb), "--json",
                            "--normal-max", "1s", "--light-max", "0.5s")
        options = {o["name"]: o for o in json.loads(js)["options"]}
        self.assertIn("heavy", {i["path"] for i in options["EXTREME"]["items"]})  # slow.py: ~1.5s
        self.assertNotIn("heavy", {i["path"] for i in options["NORMAL"]["items"]})

    def test_reclaim_requires_a_choice(self):
        with mock.patch("builddiet.cli.sys.stdin", io.StringIO()):  # non-interactive, whatever runs the tests
            code, text = self.cli("reclaim", str(self.root), "--sandbox-dir", str(self.sb))
        self.assertEqual(code, 5)
        self.assertIn("Nothing deleted", text)
        self.assertTrue((self.root / "out").exists())

    def test_reclaim_option_and_restore(self):
        code, text = self.cli("reclaim", str(self.root), "--option", "light", "--yes",
                              "--sandbox-dir", str(self.sb), "--light-max", "0")
        self.assertEqual(code, 0, text)
        self.assertIn("LIGHT: freed", text)
        self.assertFalse((self.root / "models").exists())  # a copy: LIGHT
        self.assertTrue((self.root / "out").exists())  # a recipe: not LIGHT with --light-max 0
        self.assertEqual(reclaim.restore(self.root).failed, [])
        self.assertTrue((self.root / "models").exists())

    def test_analyze_folder_of_projects_shows_options(self):
        second = self.base / "projects" / "other"
        write(second / "package.json", "{}")
        blob = os.urandom(5000)
        write(second / "a" / "x.bin", blob)
        write(second / "b" / "x.bin", blob)
        code, text = self.cli("analyze", str(self.base / "projects"), "--min-size", "1KB", "--yes",
                              "--sandbox-dir", str(self.sb))
        self.assertEqual(code, 0, text)
        self.assertIn("2 projects analyzed", text)
        self.assertIn("LIGHT", text)
        self.assertIn("builddiet reclaim", text)

    def test_protected_path_is_listed_and_left_out(self):
        with mock.patch.dict(os.environ, {"BUILDDIET_HOME": str(self.base / "home")}):
            protect.protect(self.root / "models")
            code, text = self.cli("plan", str(self.root), "--sandbox-dir", str(self.sb))
        self.assertIn("1 protected path left out", text)
        self.assertIn("protected:", text)
        self.assertNotIn("models ", text.split("How an item")[0].replace("models_bak", ""))


class OutOfSpaceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = make_project(self.base).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def full_disk(self, *a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")

    def test_analyze_fails_explicitly(self):
        with mock.patch("builddiet.fs.copytree", side_effect=self.full_disk):
            with self.assertRaises(OutOfSpace) as ctx:
                analyze(self.root, Config(min_size=1000), sandbox_dir=self.base / "sb", confirm=lambda *_: True)
        self.assertIn("out of disk space", str(ctx.exception))
        self.assertIn("Nothing in your project was changed", str(ctx.exception))
        self.assertTrue(isinstance(ctx.exception, SandboxError))

    def test_cli_reports_it(self):
        err = io.StringIO()
        with mock.patch("builddiet.fs.copytree", side_effect=self.full_disk):
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = cli.main(["analyze", str(self.root), "--min-size", "1KB", "--yes",
                                 "--sandbox-dir", str(self.base / "sb")])
        self.assertEqual(code, 1)
        self.assertIn("out of disk space", err.getvalue())

    def test_joint_verification_fails_explicitly(self):
        m = analyze(self.root, Config(min_size=1000), sandbox_dir=self.base / "sb", confirm=lambda *_: True)
        entry = next(e for e in m["entries"] if e["method"] == "recipe")
        with mock.patch("builddiet.fs.copytree", side_effect=self.full_disk):
            check = verify_joint(self.root, project_config(m), [entry], total_bytes=m["total_bytes"],
                                 sandbox_dir=self.base / "sb")
        self.assertFalse(check.ok)
        self.assertTrue(check.fatal)
        self.assertIn("out of disk space", check.detail)

    def test_reclaim_deletes_nothing_if_the_log_cannot_be_written(self):
        m = analyze(self.root, Config(min_size=1000), sandbox_dir=self.base / "sb", confirm=lambda *_: True)
        manifest.save(self.root, m)
        manifests, _ = load_manifests([self.root])
        options = reclaim_options(manifests, sandbox_dir=self.base / "sb")
        chosen = next(o for o in options if o.verified)
        with mock.patch("builddiet.reclaim._write_log", side_effect=self.full_disk):
            done = reclaim.reclaim(chosen.as_search(), manifests)
        self.assertEqual(done.deleted, [])
        self.assertIn("could not record", done.refused[0][1])
        for i in chosen.items:
            self.assertTrue((self.root / i.path).exists(), i.path)


if __name__ == "__main__":
    unittest.main()
