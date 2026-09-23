"""Test isolation.

* Every temporary folder and sandbox the tests create lives in one test area
  ($BUILDDIET_TEST_TMP, or `builddiet-tests` on the local drive with the most
  free space), never in a nearly full system temp folder.
* The test area must have room: the suite refuses to start otherwise, with an
  explicit message, instead of failing later with generic errors.
* Tests never read or write the real ~/.builddiet (protections, settings).
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

from builddiet.sandbox import default_sandbox_base

MIN_FREE = int(os.environ.get("BUILDDIET_TEST_MIN_FREE", 1 << 30))  # 1 GiB; override only for experiments

_root = os.environ.get("BUILDDIET_TEST_TMP")
if not _root:
    _base = default_sandbox_base()
    _root = str(_base.parent / "builddiet-tests") if _base.name == "builddiet-sandboxes" else str(_base / "builddiet-tests")
os.makedirs(_root, exist_ok=True)
_free = shutil.disk_usage(_root).free
if _free < MIN_FREE:
    raise RuntimeError(f"test area {_root} has only {_free / 1e6:.0f} MB free (need 1 GB); "
                       "set BUILDDIET_TEST_TMP to a folder on a drive with room")

tempfile.tempdir = _root
os.environ["TMP"] = os.environ["TEMP"] = os.environ["TMPDIR"] = _root  # child processes too
os.environ["BUILDDIET_SANDBOX_DIR"] = str(Path(_root) / "sandboxes")
_home = tempfile.mkdtemp(prefix="home-")
os.environ["BUILDDIET_HOME"] = _home
os.environ.setdefault("BUILDDIET_NO_DIALOG", "1")
atexit.register(shutil.rmtree, _home, True)
