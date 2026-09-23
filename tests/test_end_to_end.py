import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from builddiet import cli, manifest
from builddiet.config import write_config
from builddiet.experiment import AnalysisError, analyze
from builddiet.model import NOT_REGENERATED, PROVEN, REQUIRED, STALE
from builddiet.verifier import IDENTICAL, RECREATED, fingerprint

from tests.helpers import corrupt_derived, fixture_config, make_project


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = make_project(Path(cls._tmp.name) / "proj")
        # Materialise derived data in the original, like a real working copy.
        cfg = fixture_config()
        for cmd in (cfg.regenerate, cfg.verify):
            subprocess.run(cmd, shell=True, cwd=cls.root, check=True)
        corrupt_derived(cls.root)
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

    def test_single_files_are_experimented_on(self):
        index = self.by_path["index.txt"]
        self.assertEqual(index["kind"], "file")
        self.assertEqual(index["verdict"], PROVEN)
        self.assertEqual(index["identity"], IDENTICAL)
        self.assertEqual(self.by_path["README.txt"]["verdict"], NOT_REGENERATED)
        self.assertNotIn("*", self.by_path)  # min_size=0: every file is a candidate

    def test_corrupt_derived_copy_is_stale(self):
        # not rebuilt by the baseline because it exists
        self.assertEqual(self.by_path["summary.txt"]["verdict"], STALE)

    def test_stale_copy_refreshed_by_baseline_is_still_stale(self):
        # Regression: fingerprints must describe the user's bytes, not the
        # copy the baseline run refreshed.
        self.assertEqual(self.by_path["always.txt"]["verdict"], STALE)

    def test_stale_is_never_planned(self):
        from builddiet.planner import collect_items

        paths = {i.path for i in collect_items([self.manifest])}
        self.assertIn("index.txt", paths)
        self.assertNotIn("summary.txt", paths)
        self.assertNotIn("always.txt", paths)

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
            # build/ and index.txt each cover 500 B at ~0s (noise decides which);
            # arena/ (0.4s) must never be chosen.
            plan_part = text.split("BUILDDIET PLAN")[1].split("BUILDDIET BACKUP")[0]
            self.assertTrue("build/" in plan_part or "index.txt" in plan_part)
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
