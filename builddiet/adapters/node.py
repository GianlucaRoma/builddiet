from __future__ import annotations

import json
from pathlib import Path

from .base import Adapter, Suggestion, read_text


class NodeAdapter(Adapter):
    name = "node"
    markers = ("package.json",)
    known = {
        "node_modules": "npm dependencies",
        ".next": "Next.js build",
        ".nuxt": "Nuxt build",
        ".svelte-kit": "SvelteKit build",
        ".parcel-cache": "Parcel cache",
        ".turbo": "Turborepo cache",
        ".vite": "Vite cache",
        "coverage": "coverage report",
        "dist": "bundle output",
        "build": "build output",
    }

    def suggest(self, root: Path):
        try:
            scripts = json.loads(read_text(root / "package.json") or "{}").get("scripts", {})
        except ValueError:
            scripts = {}
        if (root / "pnpm-lock.yaml").exists():
            install = "pnpm install --frozen-lockfile"
        elif (root / "yarn.lock").exists():
            install = "yarn install --frozen-lockfile"
        elif (root / "package-lock.json").exists():
            install = "npm ci"
        else:
            install = "npm install"
        regenerate = install + (" && npm run build" if "build" in scripts else "")
        verify = "npm test" if "test" in scripts else None
        return Suggestion(self.name, regenerate, verify, "package.json found")
