from __future__ import annotations

from pathlib import Path

from .base import Adapter, Suggestion, read_text


class PythonAdapter(Adapter):
    name = "python"
    markers = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
    known = {
        "__pycache__": "bytecode cache",
        ".pytest_cache": "pytest cache",
        ".mypy_cache": "mypy cache",
        ".ruff_cache": "ruff cache",
        ".tox": "tox environments",
        ".nox": "nox environments",
        "*.egg-info": "packaging metadata",
        "htmlcov": "coverage report",
        ".venv": "virtual environment (reinstallable)",
        "venv": "virtual environment (reinstallable)",
        "build": "build output",
        "dist": "distribution archives",
    }

    def suggest(self, root: Path):
        pyproject = read_text(root / "pyproject.toml")
        uses_pytest = (
            "pytest" in pyproject
            or (root / "pytest.ini").exists()
            or (root / "conftest.py").exists()
        )
        verify = "python -m pytest -q" if uses_pytest else "python -m unittest discover"
        return Suggestion(
            self.name,
            regenerate=None,
            verify=verify,
            reason="Python project; add your data/codegen step as the regenerate command",
        )
