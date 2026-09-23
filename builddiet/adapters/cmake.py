from __future__ import annotations

from pathlib import Path

from .base import Adapter, Suggestion


class CMakeAdapter(Adapter):
    name = "cmake"
    markers = ("CMakeLists.txt",)
    known = {
        "build": "CMake build tree",
        "cmake-build-*": "CLion build tree",
        "out": "Visual Studio CMake output",
    }

    def suggest(self, root: Path):
        return Suggestion(
            self.name,
            "cmake -S . -B build && cmake --build build",
            "ctest --test-dir build --output-on-failure",
            "CMakeLists.txt found",
        )
