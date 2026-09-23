"""Verdicts and report buckets.

Verdicts are the outcome of an experiment (or the lack of one):

PROVEN           removed in the sandbox -> workflow recreated it -> verify passed.
REQUIRED         removed in the sandbox -> regenerate or verify failed.
NOT_REGENERATED  the workflow passes without it, but nothing recreated it.
                 This is NOT a license to delete (think family_photos/).
STALE            the workflow recreates it, but deterministically produces
                 different bytes than the current copy (stale or corrupt
                 output, or hand edits). The current bytes are not
                 reproducible, so it is never offered for reclaiming.
IN_GIT           tracked and clean in git: `git checkout` restores it byte-for-byte.
                 Usually source code, so it is only planned with --include-git.
INCONCLUSIVE     the experiment timed out or could not run.
UNTESTED         no experiment (excluded, too small, loose files, metadata...).
"""

from __future__ import annotations

PROVEN = "proven"
REQUIRED = "required"
NOT_REGENERATED = "not-regenerated"
STALE = "stale"
IN_GIT = "in-git"
INCONCLUSIVE = "inconclusive"
UNTESTED = "untested"

BUCKET_REQUIRED = "required"
BUCKET_REGENERABLE = "regenerable"
BUCKET_NOT_REGENERATED = "not-regenerated"
BUCKET_STALE = "stale"
BUCKET_IN_GIT = "in-git"
BUCKET_KNOWN = "known"
BUCKET_UNKNOWN = "unknown"

BUCKET_LABELS = {
    BUCKET_REQUIRED: "PROVEN REQUIRED",
    BUCKET_REGENERABLE: "PROVEN REGENERABLE",
    BUCKET_NOT_REGENERATED: "NOT REGENERATED (keep)",
    BUCKET_STALE: "STALE COPY (review)",
    BUCKET_IN_GIT: "IN GIT (not planned)",
    BUCKET_KNOWN: "KNOWN, NOT PROVEN",
    BUCKET_UNKNOWN: "UNKNOWN / NOT TESTED",
}

BUCKET_ORDER = (
    BUCKET_REQUIRED,
    BUCKET_REGENERABLE,
    BUCKET_NOT_REGENERATED,
    BUCKET_STALE,
    BUCKET_IN_GIT,
    BUCKET_KNOWN,
    BUCKET_UNKNOWN,
)


def bucket(entry: dict) -> str:
    verdict = entry.get("verdict")
    if verdict == PROVEN:
        return BUCKET_REGENERABLE
    if verdict == REQUIRED:
        return BUCKET_REQUIRED
    if verdict == NOT_REGENERATED:
        return BUCKET_NOT_REGENERATED
    if verdict == STALE:
        return BUCKET_STALE
    if verdict == IN_GIT:
        return BUCKET_IN_GIT
    if entry.get("known"):
        return BUCKET_KNOWN
    return BUCKET_UNKNOWN


def bucket_totals(entries) -> dict:
    totals = {b: 0 for b in BUCKET_ORDER}
    for entry in entries:
        totals[bucket(entry)] += entry.get("bytes", 0)
    return totals


def display(entry) -> str:
    """'build/' for directories, the plain path for files and file groups."""
    kind = entry["kind"] if isinstance(entry, dict) else entry.kind
    path = entry["path"] if isinstance(entry, dict) else entry.path
    return path + "/" if kind == "dir" else path


def how(entry: dict) -> str:
    """How a proven entry comes back, in words."""
    method = entry.get("method")
    recovery = entry.get("recovery") or {}
    if method == "duplicate":
        return f"copy of {recovery.get('source')}"
    if method == "archive":
        return f"extract from {recovery.get('source')}"
    if method == "git":
        return "git checkout"
    if method == "recipe":
        where = f" (in {entry['recipe_cwd']})" if entry.get("recipe_cwd") else ""
        command = (entry.get("recipe") or "").replace("{python}", "python").replace("{project}", ".")
        return f"run: {command}{where}"
    return "your workflow"
