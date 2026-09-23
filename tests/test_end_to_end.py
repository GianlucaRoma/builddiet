import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from builddiet import cli, manifest
from builddiet.config import write_config
from builddiet.experiment import AnalysisError, analyze
from builddiet.model import NOT_REGENERATED, PROVEN, REQUIRED, UNTESTED
from builddiet.verifier import IDENTICAL, RECREATED, fingerprint

from tests.helpers import fixture_config, make_project


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = make_project(Path(cls._tmp.name) / "proj")
        # Materialise derived data in the original, like a real working copy.
        cfg = fixture_config()
        for cmd in (cfg.regenerate, cfg.verify):
            subprocess.run(cmd, shell=True, cwd=cls.root, check=True)
        cls.original = fingerprint(cls.root, "full")
        cls.manifest = analyze(cls.root, fixture_config())
        cls.by_path = {e["path"]: e for e in cls.manifest["entries"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_original_is_untouched(self):
        self.assertEqual(fingerprint(self.root, "full").entries, self.original.entries)
        self.assertEqual(self.manifest["warnings"], [])

    def test_derived_directories_are_proven(self):
        self.assertEqual(self.by_path["build"]["verdict"], PROVEN)
        self.assertEqual(self.by_path["build"]["identity"], IDENTICAL)
        self.assertEqual(self.by_path["arena"]["verdict"], PROVEN)
        self.assertEqual(self.by_path["arena"]["identity"], IDENTICAL)

    def test_nondeterministic_output_is_recreated_not_identical(self):
        self.assertEqual(self.by_path["cache"]["verdict"], PROVEN)
        self.assertEqual(self.by_path["cache"]["identity"], RECREATED)

    def test_inputs_are_required(self):
        self.assertEqual(self.by_path["src"]["verdict"], REQUIRED)
        self.assertEqual(self.by_path["src"]["failed_step"], "regenerate")
        self.assertEqual(self.by_path["tools"]["verdict"], REQUIRED)
        self.assertEqual(self.by_path["checks"]["verdict"], REQUIRED)
        self.assertEqual(self.by_path["checks"]["failed_step"], "verify")

    def test_unneeded_but_irreproducible_data_is_not_disposable(self):
        photos = self.by_path["family_photos"]
        self.assertEqual(photos["verdict"], NOT_REGENERATED)
        self.assertIsNone(photos["rebuild_seconds"])

    def test_expensive_derivation_costs_more(self):
        self.assertGreater(self.by_path["arena"]["rebuild_seconds"], 0.3)
        self.assertGreater(
            self.by_path["arena"]["rebuild_seconds"], self.by_path["build"]["rebuild_seconds"]
        )

    def test_loose_files_are_untested(self):
        self.assertEqual(self.by_path["*"]["verdict"], UNTESTED)

    def test_totals_cover_workspace(self):
        self.assertEqual(
            sum(e["bytes"] for e in self.manifest["entries"]), self.manifest["total_bytes"]
        )

    def test_cli_report_plan_backup_scan(self):
        write_config(self.root, fixture_config(), overwrite=True)
        manifest.save(self.root, self.manifest)
        try:
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["report", str(self.root)]), 0)
                self.assertEqual(cli.main(["plan", str(self.root), "--free", "500B"]), 0)
                self.assertEqual(cli.main(["backup-plan", str(self.root)]), 0)
                self.assertEqual(cli.main(["scan", str(self.root.parent)]), 0)
            text = out.getvalue()
            self.assertIn("SPACE YOU CAN PROVABLY RECLAIM", text)
            self.assertIn("family_photos/", text)
            # build/ (800 B, fast) covers 500 B; arena/ (slow) must not be chosen
            plan_part = text.split("BUILDDIET PLAN")[1].split("BUILDDIET BACKUP")[0]
            self.assertIn("build/", plan_part)
            self.assertNotIn("arena/", plan_part)
        finally:
            (self.root / ".builddiet" / "manifest.json").unlink()
            (self.root / ".builddiet" / "config.toml").unlink()
            (self.root / ".builddiet").rmdir()


class BaselineTest(unittest.TestCase):
    def test_failing_baseline_aborts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(Path(tmp) / "proj")
            (root / "src" / "input.txt").unlink()
            with self.assertRaises(AnalysisError):
                analyze(root, fixture_config())


if __name__ == "__main__":
    unittest.main()
