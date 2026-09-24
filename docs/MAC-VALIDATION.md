# macOS validation (2026-09-24)

This gate was run on macOS with isolated temporary workspaces. It did not analyze or delete files from a real user project.

## Results

| Check | Result |
|---|---|
| BD-ZERO, no project config | 12/12 expected verdicts |
| BD-REAL, declared workflow | 12/12 expected verdicts |
| BD-ZERO reclaim and restore | 4 items, 2.3 MB reclaimed; all 70 original project files returned with the same SHA-256 hashes |
| Unit and end-to-end suite | Python 3.9, 3.12 and 3.14: 137 tests run per version (3 platform-specific tests skipped); final linked-parent regressions also passed on all three versions (49 targeted tests per version), and the 138-test final suite passed on Python 3.14 (3 skipped) |
| Package | A wheel built with setuptools 84.0.0 and installed in a clean Python 3.12 virtual environment; the `builddiet` entry point worked |
| `watch --once --no-dialog` | Produced a verified proposal and deleted nothing without approval |
| 201 MB synthetic workspace | 67.1 MB duplicate proved in 0.51 s without a sandbox copy; reclaim and restore returned the same four file hashes |

The BD-ZERO workspace was about 6 MB; BD-REAL was about 12 MB. The 201 MB synthetic workspace contained only three large binary files, so it tests byte volume but not a tree with many small files. These checks do not establish performance or safety on multi-GB workspaces, network volumes, or concurrently modified projects.

## Reproduce the functional gates

From the repository root, use a scratch directory:

```bash
BD_MAC_BASE=$(mktemp -d)
python3 benchmarks/bd_zero/make_workspace.py "$BD_MAC_BASE"
BUILDDIET_HOME="$BD_MAC_BASE/home" python3 -m builddiet analyze "$BD_MAC_BASE/workspace" \
  --min-size 1KB --agent-logs-dir "$BD_MAC_BASE/agent-logs" \
  --sandbox-dir "$BD_MAC_BASE/sandboxes" --yes
python3 benchmarks/bd_zero/check.py "$BD_MAC_BASE"
BUILDDIET_HOME="$BD_MAC_BASE/home" python3 -m builddiet reclaim "$BD_MAC_BASE/workspace" \
  --option normale --yes --sandbox-dir "$BD_MAC_BASE/sandboxes"
BUILDDIET_HOME="$BD_MAC_BASE/home" python3 -m builddiet restore "$BD_MAC_BASE/workspace"
```

Run `benchmarks/bd_real/make_workspace.py`, `builddiet analyze` and `benchmarks/bd_real/check.py` in a separate scratch directory for BD-REAL. To verify byte identity around reclaim and restore, hash every workspace file except `.builddiet/` before reclaim and compare that inventory after restore.

## Performance observation

Profiling BD-ZERO analysis on Python 3.14 took about 5.7 seconds in one run. About 4.1 seconds were spent waiting for 23 recipe command runs; copying the 6 MB workspace took about 0.08 seconds. The main cost in this small gate was executing the recovery experiments. Recipe probes now run progressively, so a candidate that is already proven does not trigger probes for its remaining recipes. The BD-ZERO fixture happened to need all five discovered recipes, so its command-run count did not change.

On the test Mac, `watch` observed 6.5% free space and needed roughly 33 GB to reach its configured 20% target. This tiny workspace offered only 2.3 MB, which `watch` correctly reported as insufficient; with automatic reclaim disabled and no approval, nothing was deleted. The result illustrates why a useful watch deployment needs a sufficiently large set of analyzed projects.
