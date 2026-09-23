"""Plain-ASCII rendering of analyses, plans and backup plans."""

from __future__ import annotations

from .model import (
    BUCKET_KNOWN,
    BUCKET_LABELS,
    BUCKET_NOT_REGENERATED,
    BUCKET_ORDER,
    BUCKET_REGENERABLE,
    BUCKET_REQUIRED,
    BUCKET_UNKNOWN,
    bucket,
    bucket_totals,
)
from .units import format_duration, format_size

RULE = "-" * 64


def table(rows, headers=None, right=()) -> str:
    all_rows = ([headers] if headers else []) + rows
    if not all_rows:
        return ""
    widths = [max(len(str(r[c])) for r in all_rows) for c in range(len(all_rows[0]))]
    out = []
    for n, row in enumerate(all_rows):
        cells = [
            str(v).rjust(widths[c]) if c in right else str(v).ljust(widths[c])
            for c, v in enumerate(row)
        ]
        out.append("  " + "   ".join(cells).rstrip())
        if headers and n == 0:
            out.append("  " + "   ".join("-" * w for w in widths))
    return "\n".join(out)


def _display(entry: dict) -> str:
    return entry["path"] + "/" if entry["kind"] == "dir" else entry["path"]


def _sorted(entries):
    return sorted(entries, key=lambda e: -e["bytes"])


def render_report(m: dict) -> str:
    entries = m["entries"]
    by_bucket = {b: [e for e in entries if bucket(e) == b] for b in BUCKET_ORDER}
    totals = bucket_totals(entries)
    cmds = m["commands"]
    lines = [
        f"BUILDDIET - {m['name']}",
        m["project"],
        f"analyzed {m['created']}",
        f"regenerate: {cmds.get('regenerate') or '-'}",
        f"verify:     {cmds.get('verify') or '-'}",
        f"baseline:   cold {format_duration(m['baseline']['cold_seconds'])}, "
        f"warm {format_duration(m['baseline']['warm_seconds'])}",
        "",
        table(
            [["Total workspace", format_size(m["total_bytes"])]]
            + [[BUCKET_LABELS[b], format_size(totals[b])] for b in BUCKET_ORDER],
            right=(1,),
        ),
    ]

    proven = sorted(by_bucket[BUCKET_REGENERABLE], key=lambda e: (e["rebuild_seconds"] or 0) / max(e["bytes"], 1))
    lines += ["", RULE, "SPACE YOU CAN PROVABLY RECLAIM", RULE]
    if proven:
        rows = [
            [_display(e), format_size(e["bytes"]), format_duration(e["rebuild_seconds"]), e["identity"]]
            for e in proven
        ]
        rows.append(["TOTAL", format_size(totals[BUCKET_REGENERABLE]),
                     format_duration(sum(e["rebuild_seconds"] or 0 for e in proven)), ""])
        lines.append(table(rows, ["path", "size", "rebuild", "identity"], right=(1, 2)))
    else:
        lines.append("  nothing proven regenerable yet")

    sections = [
        (BUCKET_REQUIRED, "REQUIRED - removing these breaks the workflow"),
        (BUCKET_NOT_REGENERATED,
         "NOT REGENERATED - the workflow passes without these, but nothing\n"
         "recreates them. That is NOT proof they are disposable. Keep them."),
        (BUCKET_KNOWN, "KNOWN - catalog says regenerable by convention; not proven here"),
        (BUCKET_UNKNOWN, "UNKNOWN / NOT TESTED"),
    ]
    for key, title in sections:
        group = _sorted(by_bucket[key])
        if not group:
            continue
        lines += ["", RULE, title, RULE]
        rows = []
        for e in group[:25]:
            note = e.get("known") if key == BUCKET_KNOWN else e.get("detail", "")
            rows.append([_display(e), format_size(e["bytes"]), note or ""])
        lines.append(table(rows, right=(1,)))
        if len(group) > 25:
            lines.append(f"  ... and {len(group) - 25} more")

    if m.get("warnings"):
        lines += ["", RULE, "WARNINGS", RULE] + [f"  ! {w}" for w in m["warnings"]]
    lines += ["", "Nothing was deleted. BuildDiet v0.1 is report-only."]
    return "\n".join(lines)


def render_plan(plan, naive=None, skipped=(), multi=False) -> str:
    lines = [f"BUILDDIET PLAN - free {format_size(plan.target)}", ""]
    for reason in skipped:
        lines.append(f"  ! skipped {reason}")
    if skipped:
        lines.append("")
    if not plan.items:
        lines.append("  no PROVEN regenerable directories available (run `builddiet analyze`)")
        return "\n".join(lines)
    headers = (["project"] if multi else []) + ["path", "size", "rebuild", "p(reuse)", "expected"]
    rows = []
    for i in plan.items:
        row = [i.project] if multi else []
        row += [i.path + "/", format_size(i.bytes), format_duration(i.rebuild_seconds),
                f"{i.reuse:.2f}", format_duration(i.cost)]
        rows.append(row)
    total = ["TOTAL", ""] if multi else ["TOTAL"]
    total += [format_size(plan.freed), format_duration(plan.rebuild_seconds), "", format_duration(plan.cost)]
    rows.append(total)
    offset = 1 if multi else 0
    lines.append(table(rows, headers, right=tuple(c + offset for c in (1, 2, 3, 4))))
    lines.append("")
    if not plan.feasible:
        lines.append(
            f"  Not enough proven space: at most {format_size(plan.freed)} can be reclaimed "
            f"with proof (target {format_size(plan.target)})."
        )
    else:
        lines.append(f"  Frees {format_size(plan.freed)}; expected rebuild penalty "
                     f"{format_duration(plan.cost)} (worst case {format_duration(plan.rebuild_seconds)}).")
    if naive is not None and naive.feasible and naive.cost > plan.cost + 1e-9:
        lines.append(f"  Deleting biggest-first would cost {format_duration(naive.cost)} instead.")
    lines += ["", "  Nothing was deleted. Review the list, then delete it yourself."]
    return "\n".join(lines)


def render_backup(m: dict) -> str:
    entries = m["entries"]
    totals = bucket_totals(entries)
    must = [e for e in entries if bucket(e) in (BUCKET_REQUIRED, BUCKET_NOT_REGENERATED, BUCKET_UNKNOWN)]
    must_total = sum(e["bytes"] for e in must)
    lines = [
        f"BUILDDIET BACKUP PLAN - {m['name']}",
        "",
        table(
            [
                ["MUST BACK UP (irreproducible or unproven)", format_size(must_total)],
                ["REGENERABLE (proven)", format_size(totals[BUCKET_REGENERABLE])],
                ["KNOWN (by convention, not proven)", format_size(totals[BUCKET_KNOWN])],
            ],
            right=(1,),
        ),
        "",
        RULE,
        "MUST BACK UP",
        RULE,
        table([[_display(e), format_size(e["bytes"]), BUCKET_LABELS[bucket(e)]] for e in _sorted(must)],
              right=(1,)),
        "",
        "  Regenerable does not mean 'never back up': it means these bytes can be",
        "  recreated by the recorded workflow. Use `--excludes` to get a list.",
    ]
    return "\n".join(lines)


def render_scan(manifests_with_state, unanalyzed=()) -> str:
    rows = []
    grand = {b: 0 for b in BUCKET_ORDER}
    grand_total = 0
    for m, stale in manifests_with_state:
        totals = bucket_totals(m["entries"])
        for b in BUCKET_ORDER:
            grand[b] += totals[b]
        grand_total += m["total_bytes"]
        rows.append([
            m["name"],
            format_size(m["total_bytes"]),
            format_size(totals[BUCKET_REQUIRED]),
            format_size(totals[BUCKET_REGENERABLE]),
            format_size(totals[BUCKET_NOT_REGENERATED] + totals[BUCKET_UNKNOWN] + totals[BUCKET_KNOWN]),
            "STALE" if stale else "ok",
        ])
    rows.append(["TOTAL", format_size(grand_total), format_size(grand[BUCKET_REQUIRED]),
                 format_size(grand[BUCKET_REGENERABLE]),
                 format_size(grand[BUCKET_NOT_REGENERATED] + grand[BUCKET_UNKNOWN] + grand[BUCKET_KNOWN]), ""])
    lines = [
        "BUILDDIET SCAN",
        "",
        table(rows, ["project", "total", "required", "regenerable", "other", "proof"], right=(1, 2, 3, 4)),
    ]
    if unanalyzed:
        lines += ["", "  not analyzed yet: " + ", ".join(unanalyzed)]
    lines += ["", f"  Provably reclaimable across projects: {format_size(grand[BUCKET_REGENERABLE])}",
              "  Next: builddiet plan --free <SIZE> <dir>"]
    return "\n".join(lines)
