import os
import tempfile

# Tests never read or write the real ~/.builddiet (protected paths, settings).
os.environ["BUILDDIET_HOME"] = tempfile.mkdtemp(prefix="builddiet-test-home-")
os.environ.setdefault("BUILDDIET_NO_DIALOG", "1")
