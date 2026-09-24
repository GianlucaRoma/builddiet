"""Plain-ASCII rendering of analyses, plans and backup plans."""

from __future__ import annotations

from pathlib import Path

from .model import (
    BUCKET_IN_GIT,
    BUCKET_KNOWN,
    BUCKET_LABELS,
    BUCKET_NOT_REGENERATED,
    BUCKET_ORDER,
    BUCKET_REGENERABLE,
    BUCKET_REQUIRED,
    BUCKET_STALE,
    BUCKET_UNKNOWN,
    bucket,
    bucket_totals,
    display,
    how,
)
from .units import format_duration, format_size, shorten

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


def _sorted(entries):
    return sorted(entries, key=lambda e: -e["bytes"])


def render_report(m: dict) -> str:
    entries = m["entries"]
    by_bucket = {b: [e for e in entries if bucket(e) == b] for b in BUCKET_ORDER}
    totals = bucket_totals(entries)
    cmds = m["commands"]
    lines = [f"BUILDDIET - {m['name']}", m["project"], f"analyzed {m['created']}"]
    mode = m.get("mode", "workflow")
    labels = dict(BUCKET_LABELS)
    if mode != "workflow":
        labels[BUCKET_NOT_REGENERATED] = "NOT PROVEN (keep)"
    if cmds.get("regenerate") or cmds.get("verify"):
        lines += [f"regenerate: {cmds.get('regenerate') or '-'}", f"verify:     {cmds.get('verify') or '-'}"]
    if m.get("baseline"):
        lines.append(f"baseline:   cold {format_duration(m['baseline']['cold_seconds'])}, "
                     f"warm {format_duration(m['baseline']['warm_seconds'])}")
    lines.append({
        "hash-only": "mode:       hash proofs only (identical copies, archives, git)",
        "recipes": "mode:       hash proofs + discovered recipes (byte-identical regeneration)",
        "workflow": "mode:       hash proofs + your declared workflow",
    }.get(mode, f"mode:       {mode}"))
    lines += [
        "",
        table(
            [["Total workspace", format_size(m["total_bytes"])]]
            + [[labels[b], format_size(totals[b])] for b in BUCKET_ORDER if totals[b]],
            right=(1,),
        ),
    ]

    proven = sorted(by_bucket[BUCKET_REGENERABLE], key=lambda e: (e["rebuild_seconds"] or 0) / max(e["bytes"], 1))
    lines += ["", RULE, "SPACE YOU CAN PROVABLY RECLAIM", RULE]
    if proven:
        rows = [
            [display(e), format_size(e["bytes"]), format_duration(e["rebuild_seconds"]), shorten(how(e), 70)]
            for e in proven
        ]
        rows.append(["TOTAL", format_size(totals[BUCKET_REGENERABLE]),
                     format_duration(sum(e["rebuild_seconds"] or 0 for e in proven)), ""])
        lines.append(table(rows, ["path", "size", "rebuild", "how to get it back"], right=(1, 2)))
    else:
        lines.append("  nothing proven regenerable yet")

    not_regenerated = (
        "NOT REGENERATED - the workflow passes without these, but nothing\n"
        "recreates them. That is NOT proof they are disposable. Keep them."
        if mode == "workflow" else
        "NOT PROVEN - no identical copy, archive, git source or discovered recipe\n"
        "recreates these. Keep them (or declare a workflow with `builddiet init`)."
    )
    sections = [
        (BUCKET_REQUIRED, "REQUIRED - removing these breaks the workflow"),
        (BUCKET_NOT_REGENERATED, not_regenerated),
        (BUCKET_STALE,
         "STALE - these can be regenerated, but deterministically with DIFFERENT\n"
         "bytes than your current copy: stale or corrupt output, or hand edits.\n"
         "Excluded from plans. Review before deleting."),
        (BUCKET_IN_GIT, "IN GIT - tracked and clean; `git checkout` restores them byte-for-byte.\n"
                        "Not planned unless you pass --include-git."),
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
            rows.append([display(e), format_size(e["bytes"]), shorten(note or "", 110)])
        lines.append(table(rows, right=(1,)))
        if len(group) > 25:
            lines.append(f"  ... and {len(group) - 25} more")

    tried = m.get("recipes") or []
    skipped = m.get("rejected_recipes") or []
    if tried or skipped:
        lines += ["", RULE, "RECIPES (found automatically, run only in the sandbox)", RULE]
        for r in tried:
            state = "usable" if r["usable"] else f"rejected: {shorten(r['reason'], 60)}"
            lines.append(f"  {shorten(r['command'], 60)}   <- {r['origin']}   [{state}]")
        for r in skipped:
            lines.append(f"  {shorten(r['command'], 60)}   [never run: {r['reason']}]")

    if m.get("warnings"):
        lines += ["", RULE, "WARNINGS", RULE] + [f"  ! {w}" for w in m["warnings"]]
    lines += ["", "Nothing was deleted. `builddiet reclaim` or `watch` delete only jointly verified items."]
    return "\n".join(lines)


def _plan_table(plan, multi: bool) -> str:
    headers = (["project"] if multi else []) + ["path", "size", "rebuild", "p(reuse)", "expected",
                                                  "how to get it back"]
    rows = []
    for i in plan.items:
        row = [i.project] if multi else []
        row += [display(i), format_size(i.bytes), format_duration(i.rebuild_seconds),
                f"{i.reuse:.2f}", format_duration(i.cost), shorten(i.how, 50)]
        rows.append(row)
    total = ["TOTAL", ""] if multi else ["TOTAL"]
    total += [format_size(plan.freed), format_duration(plan.rebuild_seconds), "", format_duration(plan.cost), ""]
    rows.append(total)
    offset = 1 if multi else 0
    return table(rows, headers, right=tuple(c + offset for c in (1, 2, 3, 4)))


def render_plan(search, naive=None, skipped=(), multi=False, verified_requested=True) -> str:
    """Render a planner search. Only a JOINTLY VERIFIED PLAN is presented as safe."""
    first = search.first
    lines = [f"BUILDDIET PLAN - free {format_size(search.target)}", ""]
    for reason in skipped:
        lines.append(f"  ! skipped {reason}")
    if skipped:
        lines.append("")
    if not first.items:
        lines.append("  nothing PROVEN regenerable available (run `builddiet analyze`)")
        return "\n".join(lines)
    if not first.feasible:
        lines += [
            "CANDIDATE PLAN (not enough proven space; not verified)",
            _plan_table(first, multi),
            "",
            f"  At most {format_size(first.freed)} can be reclaimed with individual proofs "
            f"(target {format_size(search.target)}).",
        ]
        return "\n".join(lines)

    if not verified_requested:
        lines += [
            "CANDIDATE PLAN - NOT JOINTLY VERIFIED (--no-verify)",
            _plan_table(first, multi),
            "",
            f"  Frees {format_size(first.freed)}; expected rebuild penalty {format_duration(first.cost)}.",
            "  Built from individual (leave-one-out) proofs only: removing these items",
            "  together has NOT been tested. Run without --no-verify before deleting.",
        ]
        return "\n".join(lines)

    for n, attempt in enumerate(search.attempts, 1):
        lines += [f"CANDIDATE PLAN #{n}", _plan_table(attempt.plan, multi)]
        for root, check in attempt.checks.items():
            where = f" [{Path(root).name}]" if multi else ""
            status = "PASSED" if check.ok else "FAILED"
            lines.append(f"  -> joint check{where} {status}: {check.detail}")
        lines.append("")

    lines.append("=" * 64)
    if search.verified is None:
        lines += [
            f"NO JOINTLY VERIFIED PLAN for {format_size(search.target)}",
            "=" * 64,
            f"  {search.stopped}.",
            "  The candidates above are NOT safe to delete together.",
        ]
        return "\n".join(lines)

    plan = search.verified.plan
    joint = [c.rebuild_seconds for c in search.verified.checks.values()]
    lines += [
        "JOINTLY VERIFIED PLAN",
        "=" * 64,
        _plan_table(plan, multi),
        "",
        f"  Frees {format_size(plan.freed)}. Joint recovery check passed",
        f"  (see the checks above). Measured joint rebuild {format_duration(sum(joint))}; "
        f"individual estimates sum to {format_duration(plan.rebuild_seconds)}.",
    ]
    # only mention it when the difference is above timing noise
    if naive is not None and naive.feasible and naive.cost - plan.cost > max(0.05, 0.05 * plan.cost):
        lines.append(f"  An unverified biggest-first choice would cost {format_duration(naive.cost)}.")
    lines += ["", "  Nothing was deleted. To delete this plan: builddiet reclaim --free <SIZE> (asks first)."]
    return "\n".join(lines)


def render_backup(m: dict) -> str:
    entries = m["entries"]
    totals = bucket_totals(entries)
    must = [e for e in entries
            if bucket(e) in (BUCKET_REQUIRED, BUCKET_NOT_REGENERATED, BUCKET_STALE, BUCKET_UNKNOWN)]
    must_total = sum(e["bytes"] for e in must)
    lines = [
        f"BUILDDIET BACKUP PLAN - {m['name']}",
        "",
        table(
            [
                ["MUST BACK UP (irreproducible or unproven)", format_size(must_total)],
                ["REGENERABLE (proven)", format_size(totals[BUCKET_REGENERABLE])],
                ["IN GIT (restorable from the repository)", format_size(totals[BUCKET_IN_GIT])],
                ["KNOWN (by convention, not proven)", format_size(totals[BUCKET_KNOWN])],
            ],
            right=(1,),
        ),
        "",
        RULE,
        "MUST BACK UP",
        RULE,
        table([[display(e), format_size(e["bytes"]), BUCKET_LABELS[bucket(e)]] for e in _sorted(must)],
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
            format_size(m["total_bytes"] - totals[BUCKET_REQUIRED] - totals[BUCKET_REGENERABLE]),
            "STALE" if stale else "ok",
        ])
    rows.append(["TOTAL", format_size(grand_total), format_size(grand[BUCKET_REQUIRED]),
                 format_size(grand[BUCKET_REGENERABLE]),
                 format_size(grand_total - grand[BUCKET_REQUIRED] - grand[BUCKET_REGENERABLE]), ""])
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


def _items_summary(option) -> str:
    words = {"copies": ("copy", "copies"), "archives": ("archive", "archives"), "recipes": ("recipe", "recipes"),
             "workflow": ("workflow output", "workflow outputs"), "git": ("git file", "git files")}
    parts = [f"{n} {words.get(k, (k, k))[0 if n == 1 else 1]}"
             for k, n in sorted(option.breakdown().items(), key=lambda kv: -kv[1])]
    return f"{len(option.items)}" + (f": {', '.join(parts)}" if parts else "")


def render_options(options, *, where: str, projects: int, skipped=(), protected=(), excluded=(),
                   rule=None, details: bool = False) -> str:
    """LEGGERO / NORMALE / ESTREMO, with how each item was placed."""
    lines = [f"BUILDDIET - what {where} can give back", ""]
    context = [f"{projects} project{'s' if projects != 1 else ''} analyzed"]
    if protected:
        context.append(f"{len(protected)} protected path{'s' if len(protected) != 1 else ''} left out")
    if excluded:
        context.append(f"{len(excluded)} excluded for this run")
    if skipped:
        context.append(f"{len(skipped)} skipped")
    lines += ["  " + " | ".join(context), ""]
    rows = []
    for o in options:
        if o.items:
            joint = "PASS" + (f" ({len(o.dropped)} left out)" if o.dropped else "")
        else:
            joint = "-" if not o.dropped else "FAIL"
        rows.append([o.name + ("  <- recommended" if o.recommended else ""), format_size(o.freed),
                     format_duration(o.rebuild_seconds) if o.items else "-", _items_summary(o), joint])
    lines.append(table(rows, ["option", "frees", "rebuild", "items", "joint verification"], right=(1, 2)))

    previous: set = set()
    for o in options:
        added = [i for i in o.items if (i.root, i.path) not in previous]
        previous |= {(i.root, i.path) for i in o.items}
        if not added and not o.dropped:
            continue
        lines += ["", f"{o.name}" + (" adds:" if o.name != "LEGGERO" else ":")]
        shown = added if details else sorted(added, key=lambda i: -i.bytes)[:8]
        for i in sorted(shown, key=lambda i: -i.bytes):
            label = i.path if len({x.root for x in o.items}) <= 1 else f"{i.project}/{i.path}"
            lines.append(f"  {shorten(label, 45):45}  {format_size(i.bytes):>9}  {format_duration(i.rebuild_seconds):>7}"
                         f"  {shorten(i.how, 45)}")
        if len(added) > len(shown):
            lines.append(f"  ... and {len(added) - len(shown)} more (--details)")
        for i, reason in o.dropped:
            lines.append(f"  left out: {i.path}  ({shorten(reason, 80)})")

    def limit(seconds: float) -> str:  # exact: a boundary the user must understand
        return f"{seconds:g}s" if seconds < 60 else format_duration(seconds)

    light = limit(rule.light_max) if rule else "1s"
    normal = limit(rule.normal_max) if rule else "5m00s"
    lines += [
        "",
        "How an item is placed (by the measured cost of getting that item back):",
        f"  LEGGERO  restore copies bytes that already exist (copy, archive, git), or rebuild <= {light}",
        f"  NORMALE  rebuild <= {normal}",
        "  ESTREMO  everything proven, whatever it costs",
        "Only byte-identical items: recipes and workflows are jointly restored in a sandbox;",
        "copy/archive/git-only options use checked source hashes. Protected paths stay out.",
    ]
    for p in protected:
        lines.append(f"  protected: {p}")
    for p in excluded:
        lines.append(f"  excluded:  {p}")
    for s in skipped:
        lines.append(f"  skipped:   {s}")
    return "\n".join(lines)
