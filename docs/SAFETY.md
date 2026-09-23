# Safety

BuildDiet exists because people are afraid to delete the wrong thing. It must never be the tool that deletes the wrong thing.

## Invariants

1. **The original project is read-only to BuildDiet.** The only file BuildDiet writes there is `.builddiet/` (config and manifest).
2. **Destruction happens only in the sandbox.** `Sandbox.target()` normalises every path, rejects `..`, absolute paths and the sandbox root itself, and checks that the result resolves inside the sandbox copy. `set_aside`/`restore` go through it.
3. **The sandbox can't be inside the project.** This is checked at construction, and again before the sandbox is removed.
4. **No proof without a passing baseline.** The workflow must pass on the untouched copy twice (cold and warm). Otherwise the analysis aborts.
5. **"Not required" is not "disposable".** A candidate counts as PROVEN only if the workflow recreated all of its files and verify passed. A candidate that the workflow simply ignores is reported as NOT REGENERATED and is never offered for reclaiming.
5b. **"Regenerable" is not "your bytes are regenerable".** Identity is checked against the user's original bytes, which are fingerprinted before any workflow run. If the workflow reproducibly produces something different (a stale or corrupt output, or hand edits), the verdict is STALE and the candidate is never offered.
6. **Proofs expire.** The manifest records the platform, git HEAD, a hash of top-level project files, and the config digest. `plan` refuses stale proofs unless `--allow-stale` is passed.
6b. **Plans are verified jointly.** `plan` presents a set as a JOINTLY VERIFIED PLAN only after removing all of its items together in a sandbox and re-checking the PROVEN invariants. A set that fails is shown as a rejected candidate and is never presented as safe. `--no-verify` output is labelled NOT JOINTLY VERIFIED.
7. **v0.1 does not delete.** `plan` prints a list. You decide.

## What BuildDiet can't protect you from

* **Commands with side effects outside the project.** Your regenerate and verify commands run for real, in the sandbox directory. If they write through absolute paths, push to a registry, send emails or drop a database, they will do that for real. BuildDiet detects writes to the *original project* and warns, but it can't see the rest of your machine. See [THREAT_MODEL.md](THREAT_MODEL.md).
* **A weak verify command.** A PROVEN verdict means "recreated, and your gate passed". If your gate is `true`, the proof is only as strong as that.
* **Hidden inputs.** If regeneration downloads something from the network today, the proof assumes it will still be downloadable tomorrow. Consider this before deleting data that depends on remote artifacts.
