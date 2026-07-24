from __future__ import annotations

import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentsmith.util import clean_user

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
            "ASMITH_ENV_HOME": str(self.root / "portable-home"),
            "ASMITH_STATE": str(self.root / "state"),
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

    def test_clean_user_removes_vendor_neutral_enterprise_policy_banner(self) -> None:
        text = (
            "ExampleCorp enterprise Copilot CLI policy: restricted\n"
            "keep this user request"
        )
        self.assertEqual(clean_user(text), "keep this user request")

    def run_cli(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "agentsmith", *args],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=check,
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
            CREATE VIRTUAL TABLE search_index USING fts5(
              content, session_id UNINDEXED, source_type UNINDEXED
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
            "INSERT INTO turns VALUES (?, 0, 'hello there', 'hi')",
            (sid,),
        )
        con.execute(
            "INSERT INTO search_index VALUES ('hello there', ?, 'turn')",
            (sid,),
        )
        con.commit()
        con.close()
        _jsonl(
            Path(self.env["COPILOT_STATE"]) / sid / "events.jsonl",
            [
                {"type": "user.message", "data": {"content": "hello there"}},
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
                    "message": {"content": "hello there"},
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
        _jsonl(
            path.with_suffix("") / "subagents" / "agent-fixture.jsonl",
            [
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-fixture",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {"file_path": "/tmp/subagent.txt"},
                            }
                        ],
                        "usage": {
                            "input_tokens": 7,
                            "output_tokens": 1,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    },
                }
            ],
        )

    def _codex(self) -> None:
        home = Path(self.env["CODEX_HOME"])
        home.mkdir(parents=True)
        parent = "33333333-3333-3333-3333-333333333333"
        child = "44444444-4444-4444-4444-444444444444"
        parent_path = home / "sessions" / f"rollout-{parent}.jsonl"
        child_path = home / "sessions" / f"rollout-{child}.jsonl"
        for sid, path, prompt, usage in (
            (parent, parent_path, "hello there", (100, 80, 5)),
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
        main = self.run_cli("usage", "-H", "codex", "--main-only", "33333333")
        self.assertIn("fresh        20", main.stdout)
        self.assertIn("cache r        80", main.stdout)
        self.assertIn("1 calls", main.stdout)

    def test_claude_usage_and_files_include_subagents_by_default(self) -> None:
        usage = self.run_cli("usage", "-H", "claude", "22222222")
        self.assertIn("fresh        17", usage.stdout)
        main_usage = self.run_cli("usage", "-H", "claude", "--main-only", "22222222")
        self.assertIn("fresh        10", main_usage.stdout)
        files = self.run_cli("files", "-H", "claude", "22222222")
        self.assertIn("/tmp/subagent.txt", files.stdout)
        main_files = self.run_cli("files", "-H", "claude", "--main-only", "22222222")
        self.assertNotIn("/tmp/subagent.txt", main_files.stdout)

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
        verified = self.run_cli("verify", str(destination))
        self.assertIn("verified 3 session(s)", verified.stdout)

        conversation = next(destination.rglob("conversation.md"))
        with conversation.open("a") as stream:
            stream.write("corrupt\n")
        failed = self.run_cli("verify", str(destination), check=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("size mismatch", failed.stdout)

    def test_import_prepares_bundle_and_raw_dump_handoffs(self) -> None:
        bundle = self.root / "bundle-import"
        self.run_cli("export", str(self.cwd), "-o", str(bundle))
        prepared = self.root / "prepared-bundle"
        result = self.run_cli(
            "import",
            str(bundle),
            "--to",
            "codex",
            "--cwd",
            str(self.cwd),
            "-o",
            str(prepared),
        )
        self.assertIn("prepared 3 recovered session(s)", result.stdout)
        handoff = (prepared / "HANDOFF.md").read_text()
        self.assertIn("Destination agent: **codex**", handoff)
        self.assertIn("hello there", handoff)
        self.assertTrue((prepared / "sources" / "001-bundle-import").is_dir())

        raw = (
            Path(self.env["CLAUDE_HOME"])
            / "projects"
            / "fixture"
            / "22222222-2222-2222-2222-222222222222.jsonl"
        )
        raw_prepared = self.root / "prepared-raw"
        raw_result = self.run_cli(
            "import",
            str(raw),
            "--to",
            "copilot",
            "-o",
            str(raw_prepared),
        )
        self.assertIn("prepared 1 recovered session(s)", raw_result.stdout)
        self.assertIn("raw dump may omit", raw_result.stderr)
        self.assertIn("hello there", (raw_prepared / "HANDOFF.md").read_text())

        archive = self.root / "old-copilot-archive"
        archive.mkdir()
        events = (
            Path(self.env["COPILOT_STATE"])
            / "11111111-1111-1111-1111-111111111111"
            / "events.jsonl"
        )
        with (
            events.open("rb") as source,
            gzip.open(archive / "events.jsonl.gz", "wb") as destination,
        ):
            destination.write(source.read())
        (archive / "plan.md").write_text("# preserved companion\n")
        archive_prepared = self.root / "prepared-archive"
        archive_result = self.run_cli(
            "import",
            str(archive),
            "--to",
            "claude",
            "-o",
            str(archive_prepared),
        )
        self.assertIn("prepared 1 recovered session(s)", archive_result.stdout)
        copied = archive_prepared / "sources" / "001-old-copilot-archive"
        self.assertTrue((copied / "plan.md").is_file())
        self.assertIn("hello there", (archive_prepared / "HANDOFF.md").read_text())

        multi_archive = self.root / "multi-dump-archive"
        multi_archive.mkdir()
        shutil.copy2(raw, multi_archive / "claude.jsonl")
        shutil.copy2(
            Path(self.env["CODEX_SESSIONS"])
            / "rollout-33333333-3333-3333-3333-333333333333.jsonl",
            multi_archive / "codex.jsonl",
        )
        (multi_archive / "notes.md").write_text("# preserved companion\n")
        multi_prepared = self.root / "prepared-multi-archive"
        multi_result = self.run_cli(
            "import",
            str(multi_archive),
            "--to",
            "copilot",
            "-o",
            str(multi_prepared),
        )
        self.assertIn("prepared 2 recovered session(s)", multi_result.stdout)
        multi_handoff = (multi_prepared / "HANDOFF.md").read_text()
        self.assertIn("Native claude dump", multi_handoff)
        self.assertIn("Native codex dump", multi_handoff)
        self.assertTrue(
            (
                multi_prepared / "sources" / "001-multi-dump-archive" / "notes.md"
            ).is_file()
        )

    def test_project_and_global_agent_context_stay_separate(self) -> None:
        (self.cwd / "AGENTS.md").write_text("# project instructions\n")
        project_hook = self.cwd / ".claude" / "hooks" / "check.sh"
        project_hook.parent.mkdir(parents=True)
        project_hook.write_text("#!/bin/sh\n")
        user_config = Path(self.env["ASMITH_ENV_HOME"]) / ".codex" / "config.toml"
        user_config.parent.mkdir(parents=True)
        user_config.write_text("model = 'fixture'\n")
        auth = user_config.parent / "auth.json"
        auth.write_text('{"secret":"must-not-export"}\n')

        bundle = self.root / "environment-bundle"
        self.run_cli(
            "export",
            str(self.cwd),
            "--include-project-context",
            "-o",
            str(bundle),
        )
        manifest = json.loads((bundle / "manifest.json").read_text())
        paths = {entry["path"] for entry in manifest["environment"]}
        self.assertTrue(any(path.endswith("/shared/AGENTS.md") for path in paths))
        self.assertTrue(
            any(path.endswith("/claude/.claude/hooks/check.sh") for path in paths)
        )
        self.assertFalse(any("/global/" in path for path in paths))
        self.assertTrue(
            all(
                entry["project_root"] == str(self.cwd.resolve())
                for entry in manifest["environment"]
            )
        )

        prepared = self.root / "environment-import"
        result = self.run_cli(
            "import",
            str(bundle),
            "--to",
            "claude",
            "-o",
            str(prepared),
        )
        self.assertIn("staged for review", result.stderr)
        self.assertIn("Staged agent environment", (prepared / "HANDOFF.md").read_text())

        global_bundle = self.root / "global-bundle"
        global_export = self.run_cli(
            "export",
            "--global",
            "-o",
            str(global_bundle),
        )
        self.assertIn("global agent configuration", global_export.stdout)
        global_manifest = json.loads((global_bundle / "manifest.json").read_text())
        global_paths = {entry["path"] for entry in global_manifest["environment"]}
        self.assertIn("global/codex/config.toml", global_paths)
        self.assertFalse(any("auth.json" in path for path in global_paths))
        self.assertFalse(any("AGENTS.md" in path for path in global_paths))
        global_manifest["environment"][0]["destination"] = None
        global_manifest["environment"][0]["destinations"] = [
            ".claude/rules/shared.md",
            ".copilot/instructions/shared.md",
        ]
        (global_bundle / "manifest.json").write_text(
            json.dumps(global_manifest, indent=2) + "\n"
        )

        global_staging = self.root / "global-staging"
        global_import = self.run_cli(
            "import",
            str(global_bundle),
            "-o",
            str(global_staging),
        )
        self.assertIn("staged", global_import.stdout)
        self.assertIn(
            f"next: asmith launch AGENT {global_staging.resolve() / 'HANDOFF.md'}",
            global_import.stdout,
        )
        import_instructions = (global_staging / "HANDOFF.md").read_text()
        self.assertIn("~/.claude/rules/shared.md", import_instructions)
        self.assertIn("~/.copilot/instructions/shared.md", import_instructions)
        self.assertIn("explicit user exclusion", import_instructions)
        self.assertIn(
            "Preserve applicable instruction meaning verbatim", import_instructions
        )
        self.assertIn("must not depend at runtime", import_instructions)
        self.assertIn("original export bundle", import_instructions)
        self.assertIn(
            "Consolidate overlapping instruction sources", import_instructions
        )
        self.assertTrue((global_staging / "candidate").is_dir())
        self.assertTrue((global_staging / "source").is_dir())
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        fake_codex = fake_bin / "codex"
        fake_codex.write_text("#!/bin/sh\nprintf 'fake-codex %s\\n' \"$*\"\n")
        fake_codex.chmod(0o755)
        self.env["PATH"] = str(fake_bin) + os.pathsep + self.env.get("PATH", "")
        launched = self.run_cli(
            "launch",
            "codex",
            str(global_staging / "HANDOFF.md"),
        )
        self.assertIn("fake-codex", launched.stdout)
        self.assertIn(str(global_staging / "HANDOFF.md"), launched.stdout)
        import_help = self.run_cli("import", "--help")
        self.assertNotIn("--harness", import_help.stdout)
        self.assertIn("SOURCE", import_help.stdout)
        self.assertIn("PREPARED", import_help.stdout)
        launch_help = self.run_cli("launch", "--help")
        self.assertNotIn("--harness", launch_help.stdout)
        self.assertNotIn("--to", launch_help.stdout)
        self.assertIn("AGENT", launch_help.stdout)
        self.assertIn("HANDOFF", launch_help.stdout)
        top_help = self.run_cli("--help")
        self.assertNotIn("==SUPPRESS==", top_help.stdout)
        self.assertNotIn("export-global", top_help.stdout)
        self.assertNotIn("import-global", top_help.stdout)
        old_launch = self.run_cli(
            "launch",
            str(global_staging),
            "--to",
            "codex",
            check=False,
        )
        self.assertNotEqual(old_launch.returncode, 0)
        old_export_global = self.run_cli("export-global", "--help", check=False)
        self.assertNotEqual(old_export_global.returncode, 0)
        old_import_global = self.run_cli("import-global", "--help", check=False)
        self.assertNotEqual(old_import_global.returncode, 0)
        export_help = self.run_cli("export", "--help")
        self.assertIn("TARGET", export_help.stdout)
        self.assertIn("BUNDLE", export_help.stdout)

        standalone = self.root / "standalone-handoff.md"
        standalone.write_text("# Continue this work\n")
        generic = self.run_cli(
            "launch",
            "codex",
            str(standalone),
            "--cwd",
            str(self.cwd),
        )
        self.assertIn(str(standalone), generic.stdout)

        claude_global = self.root / "claude-global"
        self.run_cli(
            "export",
            self.env["CLAUDE_HOME"],
            "-o",
            str(claude_global),
        )
        claude_manifest = json.loads((claude_global / "manifest.json").read_text())
        self.assertEqual(claude_manifest["schema"], "agentsmith-global-export")
        self.assertTrue(
            all(
                entry["harness"] in {"claude", "shared"}
                for entry in claude_manifest["environment"]
            )
        )

        other = self.root / "other-work"
        other.mkdir()
        (other / "AGENTS.md").write_text("# other instructions\n")
        other_id = "55555555-5555-5555-5555-555555555555"
        _jsonl(
            Path(self.env["CLAUDE_HOME"]) / "projects" / "other" / f"{other_id}.jsonl",
            [
                {
                    "type": "user",
                    "cwd": str(other),
                    "timestamp": "2026-01-03T00:00:00Z",
                    "message": {"content": "other project"},
                }
            ],
        )
        multi = self.root / "multi-project-bundle"
        multi_result = self.run_cli(
            "export",
            str(self.cwd),
            str(other),
            "-o",
            str(multi),
        )
        self.assertIn("exported 4 session(s)", multi_result.stdout)
        multi_manifest = json.loads((multi / "manifest.json").read_text())
        roots = {entry["project_root"] for entry in multi_manifest["environment"]}
        self.assertEqual(roots, {str(self.cwd.resolve()), str(other.resolve())})

    def test_merge_prepares_all_live_sessions_without_modifying_them(self) -> None:
        destination = self.root / "merged"
        result = self.run_cli(
            "merge",
            str(self.cwd),
            "--to",
            "codex",
            "-o",
            str(destination),
        )
        self.assertIn("merged 3 session(s)", result.stdout)
        handoff = (destination / "HANDOFF.md").read_text()
        self.assertIn("copilot fixture", handoff)
        self.assertIn("codex parent", handoff)
        self.assertTrue(
            (
                Path(self.env["CLAUDE_HOME"])
                / "projects"
                / "fixture"
                / "22222222-2222-2222-2222-222222222222.jsonl"
            ).is_file()
        )

    def test_search_uses_literal_phrase_across_backends(self) -> None:
        result = self.run_cli("search", "hello", "there", "-n", "10")
        self.assertIn("11111111", result.stdout)
        self.assertIn("22222222", result.stdout)
        self.assertIn("33333333", result.stdout)

    def test_usage_cache_invalidates_when_rollout_changes(self) -> None:
        first = self.run_cli("usage", "-H", "codex", "33333333")
        self.assertIn("fresh        30", first.stdout)
        cache = (
            Path(self.env["ASMITH_CACHE"])
            / "usage"
            / "codex"
            / "33333333-3333-3333-3333-333333333333.json"
        )
        self.assertTrue(cache.is_file())
        rollout = (
            Path(self.env["CODEX_SESSIONS"])
            / "rollout-33333333-3333-3333-3333-333333333333.jsonl"
        )
        with rollout.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": 20,
                                    "cached_input_tokens": 0,
                                    "output_tokens": 1,
                                }
                            },
                        },
                    }
                )
                + "\n"
            )
        second = self.run_cli("usage", "-H", "codex", "33333333")
        self.assertIn("fresh        50", second.stdout)

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
