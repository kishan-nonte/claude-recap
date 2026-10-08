#!/usr/bin/env python3
"""Tests for recap.py. Fixtures are synthetic - no real transcripts involved."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.join(HERE, os.pardir, "skills", "recap", "recap.py")
PROJECT = "/proj/demo"
ENCODED = "-proj-demo"


def user(text, source="typed", ts="2026-01-01T10:00:00Z"):
    return {
        "type": "user",
        "cwd": PROJECT,
        "gitBranch": "main",
        "timestamp": ts,
        "promptSource": source,
        "message": {"content": text},
    }


def tool_result(ts="2026-01-01T10:00:30Z"):
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"content": [{"type": "tool_result", "content": "huge output"}]},
    }


def assistant(blocks, ts="2026-01-01T10:01:00Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"content": blocks}}


def tool_use(name, **inp):
    return {"type": "tool_use", "name": name, "input": inp}


class RecapTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.proj = os.path.join(self.home, ".claude", "projects", ENCODED)
        os.makedirs(self.proj)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def write(self, name, rows):
        path = os.path.join(self.proj, name)
        with open(path, "w") as handle:
            handle.write("\n".join(json.dumps(r) for r in rows))
        return path

    def write_history(self, rows):
        path = os.path.join(self.home, ".claude", "history.jsonl")
        with open(path, "w") as handle:
            handle.write("\n".join(json.dumps(r) for r in rows))

    def run_recap(self, *args):
        env = dict(os.environ, HOME=self.home)
        return subprocess.run(
            [sys.executable, "-I", RECAP, "--project", PROJECT, *args],
            capture_output=True, text=True, env=env,
        )

    def test_tool_results_are_not_questions(self):
        self.write("a.jsonl", [
            user("first real question"),
            tool_result(), tool_result(), tool_result(),
            assistant([tool_use("Read", file_path="/proj/demo/app.py")]),
            user("second real question", ts="2026-01-01T10:05:00Z"),
        ])
        out = self.run_recap()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("2 questions", out.stdout)

    def test_skill_payloads_are_filtered(self):
        self.write("a.jsonl", [
            user("my real question"),
            user("Base directory for this skill: /x\n# Some Skill", source=None),
            user("<task-notification>done</task-notification>", source="system"),
            user("another real one", source="suggestion_accepted"),
        ])
        out = self.run_recap()
        self.assertIn("2 questions", out.stdout)
        self.assertNotIn("Base directory", out.stdout)
        self.assertNotIn("task-notification", out.stdout)

    def test_tool_calls_become_the_summary(self):
        self.write("a.jsonl", [
            user("fix the bug"),
            assistant([
                tool_use("Read", file_path="/proj/demo/auth.py"),
                tool_use("Edit", file_path="/proj/demo/auth.py"),
                tool_use("Bash", command="pytest", description="Run tests"),
            ]),
        ])
        out = self.run_recap()
        self.assertIn("auth.py", out.stdout)
        self.assertIn("Run tests", out.stdout)
        self.assertIn("Edit", out.stdout)

    def test_reply_shown_when_no_tools_used(self):
        self.write("a.jsonl", [
            user("what is a monad?"),
            assistant([{"type": "text", "text": "A monad is a design pattern that..."}]),
        ])
        out = self.run_recap()
        self.assertIn("design pattern", out.stdout)

    def test_secrets_are_redacted(self):
        self.write("a.jsonl", [
            user("my key is AKIAIOSFODNN7EXAMPLE and token ghp_" + "a" * 36),
        ])
        out = self.run_recap()
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", out.stdout)
        self.assertIn("[AWS_KEY_ID]", out.stdout)
        self.assertIn("[GITHUB_TOKEN]", out.stdout)

    def test_pasted_content_is_collapsed(self):
        body = "x" * 3000
        self.write("a.jsonl", [
            # closing tag carries attributes, as Claude Code actually writes it
            user('look at this\n<pasted_content id="ab12">' + body + '</pasted_content id="ab12">'),
        ])
        out = self.run_recap()
        self.assertNotIn("xxxxxxxxxx", out.stdout)
        self.assertIn("[pasted", out.stdout)

    def test_unclosed_pasted_content_is_collapsed(self):
        self.write("a.jsonl", [user('see <pasted_content id="zz"> truncated...')])
        out = self.run_recap()
        self.assertNotIn("<pasted_content", out.stdout)

    def test_command_tags_are_stripped(self):
        self.write("a.jsonl", [
            user("<local-command-caveat>ran directly</local-command-caveat>real text"),
        ])
        out = self.run_recap()
        self.assertNotIn("local-command-caveat", out.stdout)
        self.assertIn("real text", out.stdout)

    def test_sidechain_entries_ignored(self):
        rows = [user("real question")]
        sub = user("subagent prompt", ts="2026-01-01T10:02:00Z")
        sub["isSidechain"] = True
        rows.append(sub)
        self.write("a.jsonl", rows)
        out = self.run_recap()
        self.assertIn("1 question", out.stdout)
        self.assertNotIn("subagent prompt", out.stdout)

    def test_legacy_transcript_falls_back_with_warning(self):
        self.write("a.jsonl", [
            {"type": "user", "cwd": PROJECT, "timestamp": "2026-01-01T10:00:00Z",
             "message": {"content": "old question, no promptSource"}},
        ])
        out = self.run_recap()
        self.assertIn("no promptSource", out.stdout)
        self.assertIn("old question", out.stdout)

    def test_expired_transcript_falls_back_to_history(self):
        self.write_history([
            {"display": "question from a deleted session", "project": PROJECT,
             "sessionId": "dead1234-0000-0000-0000-000000000000",
             "timestamp": 1767261600000},
        ])
        out = self.run_recap("--session", "dead1234")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("transcript expired", out.stdout)
        self.assertIn("question from a deleted session", out.stdout)

    def test_unknown_session_exits_nonzero(self):
        out = self.run_recap("--session", "nope0000")
        self.assertEqual(out.returncode, 1)

    def test_list_mode(self):
        self.write("a.jsonl", [user("question one")])
        out = self.run_recap("--list")
        self.assertIn("1 session", out.stdout)
        self.assertIn("question one", out.stdout)

    def test_prose_mode_emits_valid_json(self):
        self.write("a.jsonl", [
            user("do the thing"),
            assistant([tool_use("Bash", command="ls", description="List files")]),
        ])
        out = self.run_recap("--prose")
        payload = json.loads(out.stdout)
        self.assertEqual(len(payload["turns"]), 1)
        self.assertEqual(payload["turns"][0]["question"], "do the thing")
        self.assertEqual(payload["turns"][0]["tools"], {"Bash": 1})

    def test_malformed_lines_are_skipped(self):
        path = self.write("a.jsonl", [user("good question")])
        with open(path, "a") as handle:
            handle.write("\n{not json at all\n\n")
        out = self.run_recap()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("good question", out.stdout)

    def test_empty_project_exits_nonzero(self):
        out = self.run_recap()
        self.assertEqual(out.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
