"""Protected paths: absolute precedence, no bypass, fail closed."""

import io
import os
import tempfile
import unittest
from collections import namedtuple
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from builddiet import cli, fs, level0, manifest, protect, reclaim, recipes
from builddiet.config import Config
from builddiet.experiment import AnalysisError, analyze
from builddiet.planner import collect_items
from builddiet.protect import CLEAR, EXCLUDED, PROTECTED, UNKNOWN, Guard, ProtectionError
from builddiet.scanner import LINK, USER_EXCLUDED, scan
from builddiet.sandbox import Sandbox
from builddiet.service import load_manifests, verified_plan
from builddiet.watch import WatchSettings, cycle

from tests.test_reclaim_watch import make_project, write

WINDOWS = os.name == "nt"
Usage = namedtuple("Usage", "total used free")


def make_junction(link: Path, target: Path) -> bool:
    if not WINDOWS:
        return False
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return True
    except (ImportError, AttributeError, OSError):
        return False


def make_symlink(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
        return True
    except (OSError, NotImplementedError):
        return False


class Isolated(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self._home = mock.patch.dict(os.environ, {"BUILDDIET_HOME": str(self.base / "home")})
        self._home.start()

    def tearDown(self):
        self._home.stop()
        # junctions/symlinks first, so cleanup never walks into a target
        for dirpath, _dirs, files in fs.walk(self.base):
            for name in files:
                full = os.path.join(dirpath, name)
                if fs.is_link(full):
                    fs.remove(full)
        self._tmp.cleanup()


class CanonicalTest(Isolated):
    def test_case_dots_and_trailing_separators(self):
        write(self.base / "Data" / "sub" / "x.bin", b"x")
        protect.protect(self.base / "Data")
        g = Guard()
        spellings = [self.base / "Data" / "sub" / "x.bin",
                     str(self.base / "Data" / "sub" / ".." / "sub") + os.sep,
                     str(self.base / "Other" / ".." / "Data")]
        if WINDOWS:
            spellings += [str(self.base / "data").upper(), str(self.base / "DATA" / "SUB")]
        for s in spellings:
            self.assertEqual(g.status(s), PROTECTED, s)
        self.assertEqual(g.status(self.base / "Database"), CLEAR)  # prefix of a name is not "inside"

    @unittest.skipUnless(WINDOWS, "8.3 short names are a Windows feature")
    def test_short_names(self):
        import ctypes
        long_dir = self.base / "averylongfoldername"
        long_dir.mkdir()
        buf = ctypes.create_unicode_buffer(1024)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(long_dir), buf, 1024) or \
                buf.value.lower() == str(long_dir).lower():
            self.skipTest("8.3 names disabled on this volume")
        protect.protect(long_dir)
        self.assertEqual(Guard().status(buf.value), PROTECTED)

    def test_parent_and_child(self):
        write(self.base / "p" / "keep" / "a.bin", b"a")
        write(self.base / "p" / "other" / "b.bin", b"b")
        protect.protect(self.base / "p" / "keep")
        g = Guard()
        self.assertIsNotNone(g.delete_verdict(self.base / "p"))  # would delete the child
        self.assertIsNone(g.delete_verdict(self.base / "p" / "other"))
        protect.protect(self.base / "p")
        self.assertEqual(Guard().status(self.base / "p" / "other" / "b.bin"), PROTECTED)

    def test_fail_closed_when_unresolvable(self):
        g = Guard()
        with mock.patch.object(protect, "canonical", side_effect=OSError("cannot resolve")):
            self.assertEqual(g.status(self.base / "anything"), UNKNOWN)
            self.assertTrue(g.contains_guarded(self.base / "anything"))
            self.assertIsNotNone(g.delete_verdict(self.base / "anything"))

    def test_fail_closed_on_corrupt_store(self):
        protect.store_path().parent.mkdir(parents=True, exist_ok=True)
        protect.store_path().write_text("{not json")
        with self.assertRaises(ProtectionError):
            Guard()
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(cli.main(["analyze", str(self.base), "--no-recipes"]), 1)
        self.assertIn("fail closed", err.getvalue())

    def test_cli_roundtrip(self):
        target = self.base / "Keep"
        target.mkdir()
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["protect", str(target)]), 0)
            self.assertEqual(cli.main(["protected"]), 0)
            spelled = str(target).lower() if WINDOWS else str(target)
            self.assertEqual(cli.main(["unprotect", spelled]), 0)
            self.assertEqual(cli.main(["unprotect", str(target)]), 1)  # nothing left
        self.assertIn(protect.canonical(target), out.getvalue())
        self.assertEqual(protect.load(), [])


class ScanTest(Isolated):
    def test_protected_area_is_never_read_or_offered(self):
        root = self.base / "proj"
        write(root / "secret" / "big.bin", b"s" * 5000)
        write(root / "models" / "private" / "w.bin", b"p" * 5000)
        write(root / "models" / "public" / "w.bin", b"q" * 5000)
        write(root / "out" / "a.bin", b"o" * 5000)
        protect.protect(root / "secret")
        protect.protect(root / "models" / "private")
        cfg = Config(min_size=0, include=["secret"])  # an include cannot override a protection
        with mock.patch("builddiet.scanner.tree_size", wraps=__import__("builddiet.scanner", fromlist=["x"]).tree_size) as ts:
            regions = {r.path: r for r in scan(root, cfg, guard=Guard())}
            sized = {Path(c.args[0]).relative_to(root).as_posix() for c in ts.call_args_list}
        self.assertEqual(regions["secret"].status, "protected")
        self.assertEqual(regions["secret"].bytes, 0)
        self.assertEqual(regions["models/private"].status, "protected")
        self.assertEqual(regions["models/public"].status, "candidate")
        self.assertNotIn("models", regions)  # split around the protected child
        self.assertFalse({"secret", "models", "models/private"} & sized)

    def test_exclude_is_temporary_and_protection_wins(self):
        root = self.base / "proj"
        write(root / "tmpdata" / "a.bin", b"a" * 5000)
        write(root / "keep" / "b.bin", b"b" * 5000)
        protect.protect(root / "keep")
        g = Guard(excludes=[root / "tmpdata", root / "keep"])
        self.assertEqual(g.status(root / "tmpdata"), EXCLUDED)
        self.assertEqual(g.status(root / "keep"), PROTECTED)  # protection has precedence
        regions = {r.path: r for r in scan(root, Config(min_size=0), guard=g)}
        self.assertEqual(regions["tmpdata"].status, USER_EXCLUDED)
        self.assertEqual(Guard().status(root / "tmpdata"), CLEAR)  # not persistent

    def test_analyze_refuses_protected_root(self):
        root = self.base / "proj"
        write(root / "a.bin", b"a")
        protect.protect(root)
        with self.assertRaises(AnalysisError):
            analyze(root, Config(min_size=0), recipes_enabled=False)

    def test_protected_file_is_neither_copied_nor_used_as_a_duplicate(self):
        root = self.base / "proj"
        write(root / "out.bin", b"same" * 1000)
        write(root / "secret.bin", b"same" * 1000)
        protect.protect(root / "secret.bin")
        guard = Guard()
        regions = scan(root, Config(min_size=0), guard=guard)
        self.assertNotIn("out.bin", level0.find_recoverable(root, regions, guard=guard))
        with Sandbox(root, base=self.base / "sandboxes", guard=guard) as sb:
            sb.populate()
            self.assertFalse((sb.project / "secret.bin").exists())
            self.assertEqual((sb.project / "out.bin").read_bytes(), b"same" * 1000)

    def test_protected_recipe_and_input_file_are_not_read(self):
        root = self.base / "proj"
        write(root / "out.bin", b"out")
        write(root / "Makefile", b"out.bin:\n\tpython build.py\n")
        write(root / "build.py", b"print('out.bin')\n")
        protect.protect(root / "Makefile")
        protect.protect(root / "build.py")
        guard = Guard()
        self.assertEqual(recipes.discover(root, ["out.bin"], guard=guard).recipes, [])
        before = manifest.inputs_hash(root)
        write(root / "Makefile", b"changed content of protected file")
        self.assertEqual(manifest.inputs_hash(root), before)


class LinkTest(Isolated):
    """A link inside a project that points into a protected area."""

    def setUp(self):
        super().setUp()
        self.vault = self.base / "vault"
        write(self.vault / "precious.bin", b"v" * 4000)
        protect.protect(self.vault)
        self.root = self.base / "proj"
        write(self.root / "out" / "a.bin", b"o" * 4000)

    def check_link(self, make):
        if not make(self.root / "shortcut", self.vault):
            self.skipTest("cannot create this kind of link here")
        g = Guard()
        with self.assertRaises(reclaim.ReclaimError):
            reclaim._target(self.root, "shortcut/precious.bin")
        regions = {r.path: r for r in scan(self.root, Config(min_size=0), guard=g)}
        self.assertIn(regions["shortcut"].status, ("protected", LINK))
        self.assertNotEqual(regions["shortcut"].status, "candidate")
        # deleting a folder that contains such a link is refused
        nested = self.root / "out" / "link_inside"
        self.assertTrue(make(nested, self.vault))
        self.assertIsNotNone(g.delete_verdict(self.root / "out"))
        # and even the low-level remove never enters the link
        fs.remove(self.root / "out")
        self.assertTrue((self.vault / "precious.bin").exists())
        # analysis copies nothing from the vault into the sandbox
        m = analyze(self.root, Config(min_size=0), sandbox_dir=self.base / "sb",
                    recipes_enabled=False, keep_sandbox=True)
        copied = [p for p in (self.base / "sb").rglob("precious.bin")]
        self.assertEqual(copied, [])
        self.assertNotIn("shortcut", {e["path"] for e in m["entries"] if e["verdict"] == "proven"})

    def test_junction(self):
        self.check_link(make_junction)

    def test_symlink(self):
        self.check_link(make_symlink)

    def test_junction_to_unprotected_folder_is_not_followed(self):
        other = self.base / "elsewhere"
        write(other / "data.bin", b"d" * 4000)
        if not make_junction(self.root / "j", other):
            self.skipTest("junctions not available")
        regions = {r.path: r for r in scan(self.root, Config(min_size=0), guard=Guard())}
        self.assertEqual(regions["j"].status, LINK)
        fs.remove(self.root / "j")
        self.assertTrue((other / "data.bin").exists())


class DeletionTest(Isolated):
    """Protections added after an analysis still win at plan, reclaim and --auto time."""

    def setUp(self):
        super().setUp()
        self.root = make_project(self.base).resolve()
        self.sb = self.base / "sandboxes"
        m = analyze(self.root, Config(min_size=1000, timeout=60), sandbox_dir=self.sb,
                    confirm=lambda *_: True)
        manifest.save(self.root, m)

    def test_plan_leaves_out_protected_items(self):
        protect.protect(self.root / "out")
        manifests, _ = load_manifests([self.root])
        self.assertNotIn("out", {i.path for i in collect_items(manifests, guard=Guard())})

    def test_reclaim_rechecks_protection_right_before_deleting(self):
        manifests, _ = load_manifests([self.root])
        search, _ = verified_plan(manifests, 10**9, sandbox_dir=self.sb)  # everything proven
        planned = {i.path for i in search.verified.plan.items} if search.verified else set()
        if not planned:
            search, _ = verified_plan(manifests, 1, sandbox_dir=self.sb)
            planned = {i.path for i in search.verified.plan.items}
        victim = sorted(planned)[0]
        before = {p: (self.root / p).exists() for p in planned}
        protect.protect(self.root / victim)  # added after the plan was verified
        done = reclaim.reclaim(search, manifests)
        self.assertEqual(done.deleted, [])
        self.assertTrue(done.refused)
        for p in planned:
            self.assertEqual((self.root / p).exists(), before[p], p)

    def test_reclaim_respects_exclude(self):
        manifests, _ = load_manifests([self.root])
        search, _ = verified_plan(manifests, 1, sandbox_dir=self.sb)
        victim = search.verified.plan.items[0].path
        done = reclaim.reclaim(search, manifests, excludes=[self.root / victim])
        self.assertEqual(done.deleted, [])
        self.assertTrue((self.root / victim).exists())

    def test_reclaim_refuses_a_newly_protected_log(self):
        manifests, _ = load_manifests([self.root])
        search, _ = verified_plan(manifests, 1, sandbox_dir=self.sb)
        victim = search.verified.plan.items[0].path
        protect.protect(reclaim.log_path(self.root))
        done = reclaim.reclaim(search, manifests)
        self.assertEqual(done.deleted, [])
        self.assertIn("log is protected", done.refused[0][1])
        self.assertTrue((self.root / victim).exists())

    def run_auto(self, **kw):
        s = WatchSettings(min_size=1000, sandbox_dir=self.sb, dialog=False, auto=True, **kw)
        usage = lambda _p: Usage(100 * 10**9, 97 * 10**9, 3 * 10**9)  # 3% free: aggressive
        return cycle(self.base / "projects", s, usage=usage, ask=lambda *a: None,
                     log=lambda _m: None, interactive=False)

    def test_auto_never_touches_protected_item(self):
        protect.protect(self.root / "out")
        before = protect.canonical(self.root / "out")
        self.run_auto()
        self.assertTrue((self.root / "out" / "a.bin").exists())
        self.assertEqual(protect.canonical(self.root / "out"), before)

    def test_auto_skips_a_protected_project_entirely(self):
        protect.protect(self.root)
        r = self.run_auto()
        self.assertEqual(r.reclaimed, 0)
        for p in ("out", "models", "assets"):
            self.assertTrue((self.root / p).exists(), p)

    def test_auto_refuses_protected_watch_root(self):
        protect.protect(self.base / "projects")
        r = self.run_auto()
        self.assertEqual(r.reclaimed, 0)
        self.assertIn("protected", r.message)

    def test_auto_respects_exclude(self):
        r = self.run_auto(excludes=(self.root / "out",))
        self.assertTrue((self.root / "out" / "a.bin").exists())
        self.assertGreater(r.reclaimed, 0)  # other items were still reclaimed

    def test_restore_never_writes_into_a_protected_path(self):
        manifests, _ = load_manifests([self.root])
        search, _ = verified_plan(manifests, 1, sandbox_dir=self.sb)
        done = reclaim.reclaim(search, manifests)
        victim = done.deleted[0][1]
        protect.protect(self.root / victim)
        result = reclaim.restore(self.root)
        self.assertIn(victim, [p for p, _ in result.failed])
        self.assertFalse((self.root / victim).exists())


if __name__ == "__main__":
    unittest.main()
