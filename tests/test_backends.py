from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _jsonl(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events))


class BackendFixturesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cwd = self.root / "work"
        self.cwd.mkdir()
        self.env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "ASMITH_CACHE": str(self.root / "cache"),
            "COPILOT_HOME": str(self.root / "copilot"),
            "COPILOT_DB": str(self.root / "copilot" / "session-store.db"),
            "COPILOT_STATE": str(self.root / "copilot" / "session-state"),
            "CLAUDE_HOME": str(self.root / "claude"),
            "CODEX_HOME": str(self.root / "codex"),
            "CODEX_DB": str(self.root / "codex" / "state_5.sqlite"),
            "CODEX_SESSIONS": str(self.root / "codex" / "sessions"),
            "NO_COLOR": "1",
        }
        self._copilot()
        self._claude()
        self._codex()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "agentsmith", *args],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=True,
        )

    def _copilot(self) -> None:
        home = Path(self.env["COPILOT_HOME"])
        home.mkdir(parents=True)
        sid = "11111111-1111-1111-1111-111111111111"
        con = sqlite3.connect(self.env["COPILOT_DB"])
        con.executescript(
            """
            CREATE TABLE sessions (
              id TEXT, cwd TEXT, repository TEXT, branch TEXT, summary TEXT,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE turns (
              session_id TEXT, turn_index INTEGER, user_message TEXT,
              assistant_response TEXT
            );
            CREATE TABLE session_files (
              session_id TEXT, file_path TEXT, tool_name TEXT, turn_index INTEGER
            );
            CREATE TABLE assistant_usage_events (
              session_id TEXT, model TEXT, input_tokens INTEGER,
              output_tokens INTEGER, cache_read_tokens INTEGER,
              cache_write_tokens INTEGER, reasoning_tokens INTEGER,
              total_nano_aiu INTEGER
            );
            CREATE TABLE checkpoints (
              session_id TEXT, checkpoint_number INTEGER, title TEXT,
              overview TEXT, next_steps TEXT
            );
            """
        )
        con.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                sid,
                str(self.cwd),
                "git@example/repo",
                "main",
                "copilot fixture",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:01:00Z",
            ),
        )
        con.execute(
            "INSERT INTO turns VALUES (?, 0, 'hello', 'hi')",
            (sid,),
        )
        con.commit()
        con.close()
        _jsonl(
            Path(self.env["COPILOT_STATE"]) / sid / "events.jsonl",
            [
                {"type": "user.message", "data": {"content": "hello"}},
                {"type": "assistant.message", "data": {"content": "hi"}},
            ],
        )

    def _claude(self) -> None:
        sid = "22222222-2222-2222-2222-222222222222"
        path = Path(self.env["CLAUDE_HOME"]) / "projects" / "fixture" / f"{sid}.jsonl"
        _jsonl(
            path,
            [
                {
                    "type": "user",
                    "cwd": str(self.cwd),
                    "timestamp": "2026-01-02T00:00:00Z",
                    "message": {"content": "hello"},
                },
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-fixture",
                        "content": [{"type": "text", "text": "hi"}],
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 2,
                            "cache_read_input_tokens": 20,
                            "cache_creation_input_tokens": 0,
                        },
                    },
                },
            ],
        )
        memory = path.parent / "memory"
        memory.mkdir()
        (memory / "MEMORY.md").write_text("# fixture memory\n")

    def _codex(self) -> None:
        home = Path(self.env["CODEX_HOME"])
        home.mkdir(parents=True)
        parent = "33333333-3333-3333-3333-333333333333"
        child = "44444444-4444-4444-4444-444444444444"
        parent_path = home / "sessions" / f"rollout-{parent}.jsonl"
        child_path = home / "sessions" / f"rollout-{child}.jsonl"
        for sid, path, prompt, usage in (
            (parent, parent_path, "parent", (100, 80, 5)),
            (child, child_path, "child", (50, 40, 3)),
        ):
            _jsonl(
                path,
                [
                    {
                        "type": "session_meta",
                        "payload": {"id": sid, "cwd": str(self.cwd)},
                    },
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": prompt},
                    },
                    {
                        "type": "turn_context",
                        "payload": {"model": "gpt-fixture"},
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": usage[0],
                                    "cached_input_tokens": usage[1],
                                    "output_tokens": usage[2],
                                }
                            },
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        },
                    },
                ],
            )
        con = sqlite3.connect(self.env["CODEX_DB"])
        con.executescript(
            """
            CREATE TABLE threads (
              id TEXT PRIMARY KEY, rollout_path TEXT, created_at INTEGER,
              updated_at INTEGER, cwd TEXT, title TEXT, archived INTEGER
            );
            CREATE TABLE thread_spawn_edges (
              parent_thread_id TEXT, child_thread_id TEXT, status TEXT
            );
            CREATE TABLE thread_dynamic_tools (thread_id TEXT);
            """
        )
        con.executemany(
            "INSERT INTO threads VALUES (?, ?, 1, 2, ?, ?, 0)",
            [
                (parent, str(parent_path), str(self.cwd), "codex parent"),
                (child, str(child_path), str(self.cwd), "codex child"),
            ],
        )
        con.execute(
            "INSERT INTO thread_spawn_edges VALUES (?, ?, 'completed')",
            (parent, child),
        )
        con.commit()
        con.close()

    def test_list_labels_every_harness_and_hides_codex_child(self) -> None:
        result = self.run_cli("list")
        self.assertIn("copilot", result.stdout)
        self.assertIn("claude", result.stdout)
        self.assertIn("codex", result.stdout)
        self.assertIn("33333333", result.stdout)
        self.assertNotIn("44444444", result.stdout)

    def test_codex_usage_aggregates_child_and_normalizes_cache(self) -> None:
        result = self.run_cli("usage", "-H", "codex", "33333333")
        self.assertIn("fresh        30", result.stdout)
        self.assertIn("cache r       120", result.stdout)
        self.assertIn("2 calls", result.stdout)

    def test_path_export_includes_roots_and_memory(self) -> None:
        destination = self.root / "bundle"
        result = self.run_cli(
            "export",
            str(self.cwd),
            "--include-memory",
            "-o",
            str(destination),
        )
        self.assertIn("exported 3 session(s)", result.stdout)
        manifest = json.loads((destination / "manifest.json").read_text())
        self.assertEqual(len(manifest["sessions"]), 3)
        memories = list((destination / "project-memory").rglob("MEMORY.md"))
        self.assertEqual(len(memories), 1)

    def test_z_codex_parent_removal_includes_child(self) -> None:
        self.run_cli("rm", "-H", "codex", "-y", "33333333")
        con = sqlite3.connect(self.env["CODEX_DB"])
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM threads").fetchone()[0], 0
            )
        finally:
            con.close()
        self.assertEqual(
            list(Path(self.env["CODEX_SESSIONS"]).glob("*.jsonl")),
            [],
        )


if __name__ == "__main__":
    unittest.main()
