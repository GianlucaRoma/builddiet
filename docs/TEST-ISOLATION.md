# The intermittent test failure: investigation and fix

During the options work, one full run of the suite failed intermittently, and the next run passed. This page records what was measured, not what was guessed.

## Evidence

Environment: Windows 10 (19045), Python 3.10.2. C: had about 140 MB free. D: had about 48 GB free.

1. **The suite itself uses almost nothing on C:.** The suite was run 20 times with every temporary folder and sandbox isolated in `D:\builddiet-tests`. All 20 runs passed (`OK (skipped=2)`). The minimum free space on C: stayed within about 2 MB of the starting value in 18 runs, and dropped to 124 MB in run 8.
2. **Something outside the suite fills C: to zero.** In run 13, C: dropped from 142 MB to **0 MB** while the suite ran, and the isolated run still passed. A separate monitor, which sampled C: free space and the sizes of `pagefile.sys`, `swapfile.sys` and `hiberfil.sys` every 0.5 s, recorded another such event with no test running: 145 MB → 0 MB at 21:09:17–22, then back to 144 MB at 21:09:23. All three files kept the same size throughout, so the paging file is ruled out.
3. **The old configuration fails exactly when that happens.** The suite was then run 7 times with the old configuration, where temp folders and sandboxes were on C:.
   * Run 1 **failed with 16 errors**. Every error is the explicit pre-copy space check: "the sandbox needs about 33.6 MB but only 31.0 MB is free …", "… only 27.2 MB …", and so on.
   * Runs 2–7 passed.
   * No error of any other kind occurred.

**Conclusion.** The intermittent failure was not an unhandled ENOSPC or a logic bug. It was BuildDiet's own space check, correctly refusing to copy a 33.6 MB sandbox onto a C: drive that another process was temporarily filling to zero. The process that fills C: was **not identified**: a file-growth watcher ran for the rest of the session and did not catch another event. The fix does not depend on identifying it.

## Fix

* **Tests are isolated** (`tests/__init__.py`). Every temp folder, sandbox, `TMP`/`TEMP` for child processes and `BUILDDIET_HOME` lives in one test area: `$BUILDDIET_TEST_TMP`, or `builddiet-tests` on the local drive with the most free space. The suite refuses to start if that area has less than 1 GB free, and it says so.
* **Running out of space is an explicit, safe failure** (`OutOfSpace`, a `SandboxError`). There are three cases:
  * the space check before a sandbox copy;
  * ENOSPC or disk-full during the copy;
  * a workflow run that failed with less than 16 MB left.

  In every case, BuildDiet stops with "out of disk space in X … Nothing in your project was changed or deleted". A joint verification that runs out of space is fatal, so no option or plan is verified. `reclaim` writes its restore log *before* each deletion: if the log cannot be written, the item is not deleted (`tests/test_options.py::OutOfSpaceTest`).
