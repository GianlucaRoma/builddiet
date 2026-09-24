import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from builddiet import level0
from builddiet.config import Config
from builddiet.scanner import scan
from builddiet.verifier import fingerprint


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def candidates(root: Path, **cfg):
    return scan(root, Config(min_size=0, **cfg))


class Level0Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def find(self, **cfg):
        return level0.find_recoverable(self.root, candidates(self.root, **cfg))

    def test_duplicate_file_keeps_one_copy(self):
        blob = os.urandom(5000)
        write(self.root / "a.bin", blob)
        write(self.root / "b.bin", blob)
        found = self.find()
        self.assertEqual(set(found), {"a.bin"})  # b.bin stays as the source
        self.assertEqual(found["a.bin"].method, level0.DUPLICATE)
        self.assertEqual(found["a.bin"].source, "b.bin")

    def test_duplicate_directory(self):
        for name in ("models", "models_backup"):
            write(self.root / name / "w.bin", b"w" * 3000)
            write(self.root / name / "sub" / "cfg.json", b"{}")
        found = self.find()
        self.assertEqual(set(found), {"models"})
        self.assertEqual(found["models"].source, "models_backup")

    def test_near_duplicate_directory_is_not_recoverable(self):
        write(self.root / "a" / "w.bin", b"w" * 3000)
        write(self.root / "b" / "w.bin", b"w" * 3000)
        write(self.root / "b" / "extra.txt", b"only in b")
        found = self.find()
        # a directory is a duplicate only of a directory with exactly the same files
        self.assertNotIn("a", found)
        self.assertNotIn("b", found)

    def test_directory_with_link_is_not_proven_recoverable(self):
        for name in ("a", "b"):
            write(self.root / name / "w.bin", b"w" * 3000)
        try:
            os.symlink(self.root / "b" / "w.bin", self.root / "a" / "linked.bin")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available")
        self.assertNotIn("a", self.find())

    def test_zip_with_top_level_folder(self):
        write(self.root / "assets" / "tex" / "wall.png", os.urandom(4000))
        write(self.root / "assets" / "readme.txt", b"hello")
        with zipfile.ZipFile(self.root / "assets_v1.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            for f in (self.root / "assets").rglob("*"):
                if f.is_file():
                    zf.write(f, "assets_v1/" + f.relative_to(self.root / "assets").as_posix())
        found = self.find()
        self.assertEqual(found["assets"].method, level0.ARCHIVE)
        self.assertEqual(found["assets"].prefix, "assets_v1/")
        self.assertFalse(found["assets"].estimated)

    def test_tar_gz_and_single_member(self):
        write(self.root / "data.csv", os.urandom(2000))
        with tarfile.open(self.root / "bundle.tar.gz", "w:gz") as tf:
            tf.add(self.root / "data.csv", "export/data.csv")
        found = self.find()
        self.assertEqual(found["data.csv"].method, level0.ARCHIVE)
        self.assertTrue(found["data.csv"].single_file)

    def test_archive_with_different_bytes_is_rejected(self):
        write(self.root / "out" / "x.bin", b"A" * 1000)
        with zipfile.ZipFile(self.root / "out.zip", "w") as zf:
            zf.writestr("x.bin", b"B" * 1000)  # same size, different content
        self.assertNotIn("out", self.find())

    def test_restore_roundtrip(self):
        blob = os.urandom(3000)
        write(self.root / "dup.bin", blob)
        write(self.root / "orig.bin", blob)
        write(self.root / "pkg" / "a" / "b.txt", b"deep")
        with zipfile.ZipFile(self.root / "pkg.zip", "w") as zf:
            zf.writestr("pkg/a/b.txt", b"deep")
        found = self.find()
        for rel, rec in found.items():
            before = fingerprint(self.root / rel)
            target = self.root / rel
            shutil.rmtree(target) if target.is_dir() else target.unlink()
            level0.restore(rec.to_dict(), self.root, rel)
            self.assertEqual(fingerprint(self.root / rel).entries, before.entries, rel)

    def test_source_changes_are_detected(self):
        blob = os.urandom(3000)
        write(self.root / "a.bin", blob)
        write(self.root / "b.bin", blob)
        rec = self.find()["a.bin"].to_dict()
        self.assertTrue(level0.source_unchanged(self.root, rec))
        write(self.root / "b.bin", os.urandom(3000))
        self.assertFalse(level0.source_unchanged(self.root, rec))

    def test_same_size_and_mtime_source_change_is_detected(self):
        write(self.root / "a.bin", b"A" * 3000)
        write(self.root / "b.bin", b"A" * 3000)
        rec = self.find()["a.bin"].to_dict()
        source = self.root / "b.bin"
        old = source.stat()
        write(source, b"B" * 3000)
        os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertFalse(level0.source_unchanged(self.root, rec))

    def test_same_size_and_mtime_archive_change_is_detected(self):
        write(self.root / "out" / "x.bin", b"A" * 3000)
        archive = self.root / "out.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("x.bin", b"A" * 3000)
        rec = self.find()["out"].to_dict()
        old = archive.stat()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("x.bin", b"B" * 3000)
        self.assertEqual(archive.stat().st_size, old.st_size)
        os.utime(archive, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertFalse(level0.source_unchanged(self.root, rec))


@unittest.skipIf(shutil.which("git") is None, "git not installed")
class GitTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()

        def git(*args):
            subprocess.run(["git", "-C", str(self.root), *args], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self.git = git
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        git("config", "core.autocrlf", "false")
        write(self.root / "docs" / "manual.pdf", os.urandom(4000))
        write(self.root / "tracked.bin", os.urandom(2000))
        git("add", "docs", "tracked.bin")
        git("commit", "-q", "-m", "init")

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_tracked_files_are_recoverable(self):
        found = level0.find_recoverable(self.root, candidates(self.root))
        self.assertEqual(found["docs"].method, level0.GIT)
        self.assertEqual(found["tracked.bin"].method, level0.GIT)

    def test_modified_or_untracked_files_are_not(self):
        write(self.root / "tracked.bin", os.urandom(2000))  # modified
        write(self.root / "docs" / "notes.txt", b"untracked, would be lost")
        found = level0.find_recoverable(self.root, candidates(self.root))
        self.assertNotIn("tracked.bin", found)
        self.assertNotIn("docs", found)

    def test_git_restore(self):
        found = level0.find_recoverable(self.root, candidates(self.root))
        before = fingerprint(self.root / "docs")
        shutil.rmtree(self.root / "docs")
        level0.restore(found["docs"].to_dict(), self.root, "docs")
        self.assertEqual(fingerprint(self.root / "docs").entries, before.entries)


if __name__ == "__main__":
    unittest.main()
