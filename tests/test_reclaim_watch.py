"""Deleting for real: reclaim, restore, watch.

The fixture project has a recipe-made output, two identical directories, an
extracted zip and canonical data. Everything runs in temporary folders.
"""

import io
import os
import tempfile
import unittest
import zipfile
from collections import namedtuple
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from builddiet import cli, manifest, reclaim, workspaces
from builddiet.config import Config
from builddiet.experiment import analyze
from builddiet.planner import PlanItem
from builddiet.service import affordable_target, load_manifests, verified_plan
from builddiet.units import parse_duration
from builddiet.verifier import fingerprint, signature
from builddiet.watch import AGGRESSIVE, OK, PREPARE, RECLAIM, WatchSettings, cycle, level, value, DiskStatus

MAKE_OUT = "import os\nos.makedirs('out', exist_ok=True)\nopen('out/a.bin', 'wb').write(bytes(range(256)) * 40)\n"
Usage = namedtuple("Usage", "total used free")
GB = 10**9


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data) if isinstance(data, bytes) else path.write_text(data)


def make_project(base: Path) -> Path:
    root = base / "projects" / "demo"
    write(root / "pyproject.toml", "[project]\nname='demo'\n")
    write(root / "tools" / "make_out.py", MAKE_OUT)
    write(root / "out" / "a.bin", bytes(range(256)) * 40)
    blob = os.urandom(6000)
    write(root / "models" / "w.bin", blob)
    write(root / "models_bak" / "w.bin", blob)
    write(root / "assets" / "logo.bin", os.urandom(4000))
    with zipfile.ZipFile(root / "assets.zip", "w") as zf:
        zf.write(root / "assets" / "logo.bin", "assets/logo.bin")
    write(root / "data" / "raw.csv", "precious\n" * 500)
    return root


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = make_project(self.base).resolve()
        self.sb = self.base / "sandboxes"
        m = analyze(self.root, Config(min_size=1000, timeout=60), sandbox_dir=self.sb,
                    confirm=lambda *_: True)
        manifest.save(self.root, m)
        self.proven = {e["path"] for e in m["entries"] if e["verdict"] == "proven"}

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, target):
        manifests, _ = load_manifests([self.root])
        search, _items = verified_plan(manifests, target, sandbox_dir=self.sb)
        return search, manifests


class ReclaimRestoreTest(Base):
    def test_fixture(self):
        self.assertEqual(self.proven, {"out", "models", "assets"})

    def test_reclaim_and_restore_roundtrip(self):
        before = {p: signature(fingerprint(self.root / p)) for p in self.proven}
        search, manifests = self.plan(sum(e["bytes"] for e in manifests_entries(self.root)
                                          if e["path"] in self.proven))
        self.assertIsNotNone(search.verified)
        done = reclaim.reclaim(search, manifests)
        self.assertEqual({p for _, p, _ in done.deleted}, self.proven)
        for p in self.proven:
            self.assertFalse((self.root / p).exists(), p)
        self.assertTrue((self.root / "data" / "raw.csv").exists())
        self.assertTrue((self.root / "models_bak").exists())  # source of models/ is kept
        self.assertEqual(len(reclaim.read_log(self.root)), 3)

        result = reclaim.restore(self.root)
        self.assertEqual(set(result.restored), self.proven, result.failed)
        for p in self.proven:
            self.assertEqual(signature(fingerprint(self.root / p)), before[p], p)
        self.assertEqual(reclaim.read_log(self.root), [])

    def test_changed_item_is_never_deleted(self):
        search, manifests = self.plan(10_000)
        planned = [i.path for i in search.verified.plan.items]
        write(self.root / planned[0] / "extra.txt", "edited after the analysis")
        done = reclaim.reclaim(search, manifests)
        self.assertEqual(done.deleted, [])
        self.assertIn("changed since it was proven", done.refused[0][1])
        for p in planned:
            self.assertTrue((self.root / p).exists())

    def test_cli_reclaim_needs_confirmation(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["reclaim", str(self.root), "--free", "1KB", "--sandbox-dir", str(self.sb)])
        self.assertEqual(code, 5)
        self.assertIn("Nothing deleted", out.getvalue())
        for p in self.proven:
            self.assertTrue((self.root / p).exists())

    def test_cli_reclaim_yes_and_restore(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["reclaim", str(self.root), "--free", "1KB", "--yes",
                                       "--sandbox-dir", str(self.sb)]), 0)
            self.assertEqual(len(reclaim.read_log(self.root)), 1)
            self.assertEqual(cli.main(["restore", str(self.root)]), 0)
        self.assertEqual(reclaim.read_log(self.root), [])


def manifests_entries(root):
    return manifest.load(root)["entries"]


class WatchTest(Base):
    def settings(self, **kw):
        s = WatchSettings(min_size=1000, sandbox_dir=self.sb, dialog=False, max_penalty=60,
                          aggressive_max_penalty=600)
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    def run_cycle(self, free_pct, **kw):
        usage = lambda _p: Usage(100 * GB, int((100 - free_pct) * GB), int(free_pct * GB))
        return cycle(self.base / "projects", self.settings(**kw), usage=usage,
                     ask=lambda *a: None, log=lambda _m: None, interactive=False)

    def test_healthy_disk_does_nothing(self):
        r = self.run_cycle(50)
        self.assertEqual(r.level, OK)
        self.assertFalse(r.verified)

    def test_prepare_verifies_but_never_deletes(self):
        r = self.run_cycle(12)
        self.assertEqual(r.level, PREPARE)
        self.assertTrue(r.verified)
        self.assertEqual(r.reclaimed, 0)
        for p in self.proven:
            self.assertTrue((self.root / p).exists())

    def test_reclaim_level_asks_and_does_nothing_without_approval(self):
        r = self.run_cycle(8)
        self.assertEqual(r.level, RECLAIM)
        self.assertEqual(r.reclaimed, 0)
        self.assertIn("Joint verification: PASS", r.message)

    def test_auto_reclaims_within_limits_and_can_be_restored(self):
        r = self.run_cycle(3, auto=True)
        self.assertEqual(r.level, AGGRESSIVE)
        self.assertGreater(r.reclaimed, 0)
        self.assertEqual(r.refused, [])
        self.assertTrue((self.root / "data" / "raw.csv").exists())
        restored = reclaim.restore(self.root)
        self.assertEqual(restored.failed, [])

    def test_zero_budget_reclaims_only_free_items(self):
        r = self.run_cycle(8, auto=True, max_penalty=0.0)
        # only items whose measured cost is 0 fit (copies / archives), never the recipe
        remaining = {p for p in self.proven if (self.root / p).exists()}
        self.assertIn("out", remaining)
        self.assertLessEqual(r.plan_cost, 0.0 + 1e-9)


class WorkflowRestoreTest(unittest.TestCase):
    """Restoring through a declared workflow runs the user's build in the project;
    anything else it rewrites must be reported, never hidden."""

    def test_side_effects_of_the_build_are_reported(self):
        from tests.helpers import corrupt_derived, fixture_config
        from tests.helpers import make_project as make_workflow_project
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = make_workflow_project(Path(tmp) / "wf").resolve()
            cfg = fixture_config()
            for cmd in (cfg.regenerate, cfg.verify):
                subprocess.run(cmd, shell=True, cwd=root, check=True)
            corrupt_derived(root)  # always.txt is now stale; the build rewrites it
            from builddiet.config import write_config
            write_config(root, cfg)
            m = analyze(root, cfg, sandbox_dir=Path(tmp) / "sb")
            manifest.save(root, m)
            manifests, _ = load_manifests([root])
            search, _ = verified_plan(manifests, 500, sandbox_dir=Path(tmp) / "sb", strict=True)
            done = reclaim.reclaim(search, manifests)
            self.assertTrue(done.deleted)
            result = reclaim.restore(root)
            self.assertEqual(result.failed, [])
            self.assertIn("always.txt", result.also_changed)


class UnitsTest(unittest.TestCase):
    def test_levels(self):
        s = WatchSettings()
        self.assertEqual(level(DiskStatus(100, 30), s), OK)
        self.assertEqual(level(DiskStatus(100, 14), s), PREPARE)
        self.assertEqual(level(DiskStatus(100, 9), s), RECLAIM)
        self.assertEqual(level(DiskStatus(100, 4), s), AGGRESSIVE)

    def test_value(self):
        item = lambda gb, seconds: PlanItem("p", "/p", "x", int(gb * GB), seconds)
        self.assertEqual(value(item(31, 12)), "LOW")
        self.assertEqual(value(item(8, 7 * 60)), "MEDIUM")
        self.assertEqual(value(item(18, 3 * 3600)), "HIGH")

    def test_affordable_target(self):
        items = [PlanItem("p", "/p", "a", 30 * GB, 3 * 3600), PlanItem("p", "/p", "b", 12 * GB, 10),
                 PlanItem("p", "/p", "c", 9 * GB, 15)]
        self.assertEqual(affordable_target(items, 20 * GB, 300), 20 * GB)  # b + c: 25s
        self.assertEqual(affordable_target(items, 40 * GB, 300), 21 * GB)  # a does not fit
        self.assertEqual(affordable_target(items, 40 * GB, None), 40 * GB)

    def test_parse_duration(self):
        self.assertEqual(parse_duration("5m"), 300)
        self.assertEqual(parse_duration("1.5h"), 5400)
        self.assertEqual(parse_duration(90), 90)

    def test_discover_workspaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write(base / "a" / "package.json", "{}")
            write(base / "a" / "sub" / "Cargo.toml", "")  # nested in a: not listed twice
            write(base / "group" / "b" / ".git" / "HEAD", "ref")
            write(base / "group" / "c" / "Game.uproject", "{}")
            write(base / "node_modules" / "x" / "package.json", "{}")
            found = {p.relative_to(base.resolve()).as_posix() for p in workspaces.discover(base)}
            self.assertEqual(found, {"a", "group/b", "group/c"})


if __name__ == "__main__":
    unittest.main()
