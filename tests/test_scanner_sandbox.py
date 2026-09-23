import tempfile
import unittest
from pathlib import Path

from builddiet.config import Config
from builddiet.sandbox import Sandbox, SandboxError
from builddiet.scanner import CANDIDATE, EXCLUDED, LOOSE, METADATA, NOT_SELECTED, SMALL, scan
from builddiet.verifier import ABSENT, IDENTICAL, PARTIAL, RECREATED, compare, fingerprint


def write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write(self.root / "models" / "m.bin", 5000)
        write(self.root / "experiments" / "cache" / "c.bin", 3000)
        write(self.root / "experiments" / "runs" / "r.bin", 2000)
        write(self.root / "experiments" / "notes.txt", 10)
        write(self.root / "node_modules" / "pkg" / "i.js", 4000)
        write(self.root / "family_photos" / "a.jpg", 7000)
        write(self.root / "tiny" / "t", 5)
        write(self.root / ".git" / "HEAD", 20)
        write(self.root / "package.json", 2)

    def tearDown(self):
        self._tmp.cleanup()

    def test_partition(self):
        cfg = Config(verify="x", include=["experiments/cache"], exclude=["family_photos"], min_size=100)
        regions = {r.path: r for r in scan(self.root, cfg)}
        self.assertEqual(regions["models"].status, CANDIDATE)
        self.assertEqual(regions["experiments/cache"].status, CANDIDATE)
        self.assertEqual(regions["experiments/runs"].status, CANDIDATE)
        self.assertEqual(regions["experiments/*"].status, LOOSE)
        self.assertNotIn("experiments", regions)
        self.assertEqual(regions["family_photos"].status, EXCLUDED)
        self.assertEqual(regions["tiny"].status, SMALL)
        self.assertEqual(regions[".git"].status, METADATA)
        self.assertEqual(regions["*"].status, LOOSE)
        self.assertEqual(regions["node_modules"].known, "node: npm dependencies")
        self.assertIsNone(regions["models"].known)
        total = sum(r.bytes for r in regions.values())
        self.assertEqual(total, 5000 + 3000 + 2000 + 10 + 4000 + 7000 + 5 + 20 + 2)

    def test_file_candidates(self):
        write(self.root / "dataset.bin", 500)
        cfg = Config(verify="x", include=["experiments/runs/r.bin"], min_size=100)
        regions = {r.path: r for r in scan(self.root, cfg)}
        self.assertEqual(regions["dataset.bin"].kind, "file")
        self.assertEqual(regions["dataset.bin"].status, CANDIDATE)
        self.assertEqual(regions["experiments/runs/r.bin"].kind, "file")
        self.assertEqual(regions["experiments/runs/r.bin"].status, CANDIDATE)
        self.assertNotIn("experiments/runs", regions)
        self.assertEqual(regions["*"].files, 1)  # package.json is below min_size
        self.assertEqual(sum(r.bytes for r in regions.values()),
                         5000 + 3000 + 2000 + 10 + 4000 + 7000 + 5 + 20 + 2 + 500)

    def test_auto_off(self):
        cfg = Config(verify="x", auto=False, include=["experiments/cache"], min_size=0)
        regions = {r.path: r for r in scan(self.root, cfg)}
        self.assertEqual(regions["experiments/cache"].status, CANDIDATE)
        self.assertEqual(regions["models"].status, NOT_SELECTED)
        self.assertEqual(regions["experiments/runs"].status, NOT_SELECTED)


class SandboxTest(unittest.TestCase):
    def test_refuses_sandbox_inside_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SandboxError):
                Sandbox(Path(tmp), base=Path(tmp) / "inner")

    def test_set_aside_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "proj"
            write(src / "build" / "a.o", 10)
            write(src / ".builddiet" / "config.toml", 1)
            with Sandbox(src, base=Path(tmp) / "sb") as sb:
                sb.populate()
                self.assertFalse((sb.project / ".builddiet").exists())
                before = fingerprint(sb.project / "build")
                token = sb.set_aside("build")
                self.assertFalse((sb.project / "build").exists())
                write(sb.project / "build" / "junk", 3)
                sb.restore("build", token)
                self.assertEqual(fingerprint(sb.project / "build").entries, before.entries)
                for bad in ("..", "../proj", "."):
                    with self.assertRaises(Exception):
                        sb.target(bad)
                root = sb.root
            self.assertFalse(root.exists())
            self.assertTrue((src / "build" / "a.o").exists())


class VerifierTest(unittest.TestCase):
    def test_compare(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "d"
            write(d / "a", 3)
            write(d / "sub" / "b", 4)
            before = fingerprint(d)
            self.assertEqual(compare(before, fingerprint(d))[0], IDENTICAL)
            (d / "a").write_bytes(b"yyy")
            self.assertEqual(compare(before, fingerprint(d))[0], RECREATED)
            (d / "sub" / "b").unlink()
            self.assertEqual(compare(before, fingerprint(d))[0], PARTIAL)
            self.assertEqual(compare(before, fingerprint(Path(tmp) / "missing"))[0], ABSENT)

    def test_split_differences(self):
        from builddiet.verifier import Fingerprint, split_differences

        original = Fingerprint({"a": (1, "o"), "b": (1, "o"), "c": (1, "same")})
        first = Fingerprint({"a": (1, "x"), "b": (1, "t1"), "c": (1, "same")})
        second = Fingerprint({"a": (1, "x"), "b": (1, "t2"), "c": (1, "same")})
        self.assertEqual(split_differences(original, first, second), (["a"], ["b"]))

    def test_single_file_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "f.bin"
            write(f, 10)
            fp = fingerprint(f)
            self.assertEqual(list(fp.entries), ["."])
            self.assertEqual(fp.total_bytes, 10)


if __name__ == "__main__":
    unittest.main()
