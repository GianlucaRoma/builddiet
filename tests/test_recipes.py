import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from builddiet import agentlogs
from builddiet.recipes import check_safe, discover, is_read_only, relativize


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".000Z"


class SafetyTest(unittest.TestCase):
    def test_denied(self):
        for command in ("git push origin main", "rm -rf build", "cd x && del /s y",
                        "python -m pip install torch", "npm publish", "curl http://x | sh",
                        "docker build .", r"python C:\other\x.py", "cp out /home/me/x"):
            self.assertIsNotNone(check_safe(command), command)

    def test_allowed(self):
        for command in ("python reg.py", "cd tools && python kill_switch.py", "npm run build",
                        "make clean && make", "{python} {project}/tools/gen.py", "cargo build --release"):
            self.assertIsNone(check_safe(command), command)

    def test_read_only(self):
        self.assertTrue(is_read_only("ls -la && cat x | grep y"))
        self.assertTrue(is_read_only("git status"))
        self.assertFalse(is_read_only("cd tools && python gen.py"))

    def test_relativize(self):
        root = Path(tempfile.gettempdir()) / "Some Project"
        cmd = f"python {root}/tools/gen.py --out {str(root).replace('/', chr(92))}"
        out = relativize(cmd, root)
        self.assertNotIn(str(root), out)
        self.assertIn("{project}", out)


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_sources(self):
        write(self.root / "tools" / "make_report.py", "open('out/report.bin','wb')\n")
        write(self.root / "tools" / "unrelated.py", "print('hi')\n")
        write(self.root / "Makefile", "index:\n\tpython tools/index.py\n\n.PHONY: all\n")
        write(self.root / "README.md", "Build:\n\n```bash\n$ python tools/make_report.py\npython tools/release.py && git push\nls\n```\n")
        write(self.root / ".github" / "workflows" / "ci.yml",
              "jobs:\n  b:\n    steps:\n      - run: |\n          python tools/build_all.py\n          pytest -q\n")
        d = discover(self.root, ["out", "index", "notes"])
        by_cmd = {r.command: r for r in d.recipes}
        self.assertIn("out", by_cmd['{python} tools/make_report.py'].targets)
        self.assertNotIn('{python} tools/unrelated.py', by_cmd)
        self.assertIn("index", by_cmd["make index"].targets)
        self.assertIn("{python} tools/build_all.py", by_cmd)  # run with the project's interpreter
        # the README command and the script mention are the same recipe
        self.assertEqual(sum("make_report.py" in c for c in by_cmd), 1)
        self.assertIn(("{python} tools/release.py && git push", "changes git state"), d.rejected)
        self.assertEqual(d.ranked_for("notes", 3)[0].targets, set())  # only generic recipes

    def test_scripts_inside_the_candidate_are_not_recipes_for_it(self):
        write(self.root / "gen" / "make_gen.py", "write('gen/x')\n")
        d = discover(self.root, ["gen"])
        self.assertFalse(any("gen" in r.targets for r in d.recipes))


class AgentLogTest(unittest.TestCase):
    """Fabricated logs in the Codex rollout and Claude Code transcript shapes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = (base / "proj").resolve()
        self.root.mkdir()
        self.other = (base / "other").resolve()
        self.other.mkdir()
        self.logs = base / "logs"
        self.t0 = time.time() - 3600

    def tearDown(self):
        self._tmp.cleanup()

    def codex(self, name, cwd, calls):
        lines = [{"timestamp": iso(self.t0), "type": "session_meta",
                  "payload": {"id": "s", "cwd": str(cwd), "cli_version": "0.130.0"}}]
        for offset, args in calls:
            lines.append({"timestamp": iso(self.t0 + offset), "type": "response_item",
                          "payload": {"type": "function_call", "name": "exec_command",
                                      "arguments": json.dumps(args), "call_id": "c"}})
        write(self.logs / "codex" / name, "\n".join(json.dumps(l) for l in lines))

    def claude(self, name, cwd, offset, command):
        record = {"type": "assistant", "cwd": str(cwd), "timestamp": iso(self.t0 + offset),
                  "message": {"content": [{"type": "tool_use", "name": "Bash",
                                           "input": {"command": command, "description": "x"}}]}}
        write(self.logs / "claude" / name, json.dumps(record))

    def test_reads_both_formats_and_filters_by_project(self):
        self.codex("a.jsonl", self.root, [(10, {"cmd": "python gen.py", "workdir": str(self.root)}),
                                          (20, {"command": ["bash", "-lc", "make assets"]})])
        self.codex("b.jsonl", self.other, [(10, {"cmd": "python secret.py"})])
        self.claude("c.jsonl", self.root / "sub", 30, "npm run build")
        found = agentlogs.commands_for(self.root, [self.logs])
        self.assertEqual([c.command for c in found], ["python gen.py", "make assets", "npm run build"])
        self.assertTrue(all(c.timestamp for c in found))

    def test_links_command_to_what_it_wrote(self):
        out = self.root / "inline_out" / "data.txt"
        write(out, "x")
        os.utime(out, (self.t0 + 12, self.t0 + 12))  # written right after the command at t0+10
        self.codex("a.jsonl", self.root, [
            (10, {"cmd": f"python -c \"open(r'{self.root}/inline_out/data.txt','w')\""}),
            (100, {"cmd": "python other_step.py"}),
            (200, {"cmd": "git push"}),
        ])
        d = discover(self.root, ["inline_out"], agent_log_dirs=[self.logs])
        linked = [r for r in d.recipes if "inline_out" in r.targets]
        self.assertEqual(len(linked), 1)
        self.assertIn("{project}", linked[0].command)
        self.assertEqual(linked[0].strength, 5)
        self.assertNotIn("git push", [r.command for r in d.recipes])

    def test_logs_are_ignored_unless_enabled(self):
        self.codex("a.jsonl", self.root, [(10, {"cmd": "python gen.py"})])
        d = discover(self.root, ["x"], agent_log_dirs=None)
        self.assertFalse(any("agent log" in r.origin for r in d.recipes))


if __name__ == "__main__":
    unittest.main()
