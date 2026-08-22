import importlib.util
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("rift_helper", Path(__file__).parents[1] / "rift_helper.py")
rift = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(rift)


class RiftHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = patch.object(rift, "CONFIG_ROOT", root / "config")
        self.rifts = patch.object(rift, "RIFTS_ROOT", root / "config/rifts")
        self.state = patch.object(rift, "STATE_ROOT", root / "state")
        self.runtime = patch.object(rift, "RUNTIME_FILE", root / "state/runtime.json")
        for item in (self.config, self.rifts, self.state, self.runtime):
            item.start()

    def tearDown(self):
        patch.stopall()
        self.temp.cleanup()

    def test_save_records_selected_apps_and_workspace_association(self):
        apps = [
            {"id": "editor", "name": "Editor", "selected": True},
            {"id": "music", "name": "Music", "selected": False},
        ]
        with patch.object(rift, "current_apps", return_value=apps), patch.object(
            rift, "current_workspace", return_value={"id": 4, "name": "4"}
        ), patch.object(rift, "hypr_json", return_value=[{"id": 4}]):
            saved = rift.save_rift("Project Nova")

        self.assertEqual(saved["slug"], "project-nova")
        self.assertEqual([item["id"] for item in saved["apps"]], ["editor"])
        runtime = rift.read_json(rift.RUNTIME_FILE, {})
        self.assertEqual(runtime["open"]["project-nova"]["workspace_id"], 4)

    def test_save_aborts_if_focus_changes_during_snapshot(self):
        with patch.object(rift, "current_apps", return_value=[]), patch.object(
            rift,
            "current_workspace",
            side_effect=[{"id": 2, "name": "2"}, {"id": 3, "name": "3"}],
        ):
            with self.assertRaisesRegex(RuntimeError, "Workspace changed"):
                rift.save_rift("Unstable")

        self.assertFalse(rift.rift_path("unstable").exists())

    def test_atomic_json_supports_concurrent_writers(self):
        destination = rift.STATE_ROOT / "concurrent.json"
        barrier = threading.Barrier(12)
        failures = []

        def write(writer):
            try:
                barrier.wait()
                rift.atomic_json(destination, {"writer": writer, "payload": "x" * 10_000})
            except Exception as error:
                failures.append(error)

        threads = [threading.Thread(target=write, args=(writer,)) for writer in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(failures, [])
        self.assertIn(rift.read_json(destination, {})["writer"], range(12))
        self.assertEqual(list(destination.parent.glob("*.tmp")), [])

    def test_runtime_transaction_preserves_concurrent_mutations(self):
        barrier = threading.Barrier(8)
        failures = []

        def associate(workspace_id):
            try:
                barrier.wait()
                with rift.runtime_transaction() as state:
                    state.setdefault("open", {})[f"rift-{workspace_id}"] = {
                        "workspace_id": workspace_id,
                        "workspace_name": str(workspace_id),
                    }
                    time.sleep(0.01)
            except Exception as error:
                failures.append(error)

        with patch.object(rift, "hypr_json", return_value=[{"id": item} for item in range(1, 9)]):
            threads = [threading.Thread(target=associate, args=(item,)) for item in range(1, 9)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(failures, [])
        state = rift.read_json(rift.RUNTIME_FILE, {})
        self.assertEqual(set(state["open"]), {f"rift-{item}" for item in range(1, 9)})

    def test_explicit_empty_selection_saves_no_apps(self):
        apps = [{"id": "editor", "name": "Editor", "selected": True}]
        with patch.object(rift, "current_apps", return_value=apps), patch.object(
            rift, "current_workspace", return_value={"id": 2, "name": "2"}
        ), patch.object(rift, "hypr_json", return_value=[{"id": 2}]):
            saved = rift.save_rift("Empty", [])
        self.assertEqual(saved["apps"], [])

    def test_save_replaces_existing_rift_association_on_workspace(self):
        rift.ensure_dirs()
        rift.atomic_json(
            rift.RUNTIME_FILE,
            {
                "signature": rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
                "open": {"old": {"workspace_id": 2, "workspace_name": "2"}},
            },
        )
        with patch.object(rift, "current_apps", return_value=[]), patch.object(
            rift, "current_workspace", return_value={"id": 2, "name": "2"}
        ), patch.object(rift, "hypr_json", return_value=[{"id": 2}]):
            rift.save_rift("New")

        runtime = rift.read_json(rift.RUNTIME_FILE, {})
        self.assertEqual(
            runtime["open"],
            {"new": {"workspace_id": 2, "workspace_name": "2"}},
        )

    def test_open_existing_rift_focuses_without_relaunching(self):
        rift.ensure_dirs()
        rift.atomic_json(
            rift.rift_path("nova"),
            {"schemaVersion": 1, "slug": "nova", "name": "Nova", "apps": [{"id": "editor"}]},
        )
        rift.atomic_json(
            rift.RUNTIME_FILE,
            {
                "signature": rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
                "open": {"nova": {"workspace_id": 7, "workspace_name": "7"}},
            },
        )
        with patch.object(rift, "hypr_json", return_value=[{"id": 7}]), patch.object(
            rift, "hypr_dispatch"
        ) as dispatch, patch.object(rift, "launch_app") as launch:
            result = rift.open_rift("nova")
        dispatch.assert_called_once_with("workspace", "7")
        launch.assert_not_called()
        self.assertEqual(result["action"], "focused")

    def test_runtime_state_survives_transient_workspace_query_failure(self):
        expected = {
            "signature": rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
            "open": {"nova": {"workspace_id": 7, "workspace_name": "7"}},
        }
        rift.atomic_json(rift.RUNTIME_FILE, expected)

        with patch.object(rift, "hypr_json", side_effect=RuntimeError("Hyprland busy")):
            state = rift.runtime_state()

        self.assertEqual(state, expected)

    def test_runtime_state_recovers_from_wrong_json_shapes(self):
        signature = rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        malformed_values = [[], "broken", {"signature": signature, "open": []}]
        for malformed in malformed_values:
            with self.subTest(value=malformed):
                rift.atomic_json(rift.RUNTIME_FILE, malformed)
                with patch.object(rift, "hypr_json", return_value=[]):
                    self.assertEqual(rift.runtime_state(), {"signature": signature, "open": {}})

    def test_load_rifts_skips_invalid_files_and_app_recipes(self):
        rift.ensure_dirs()
        rift.atomic_json(rift.RIFTS_ROOT / "wrong-name.json", {
            "schemaVersion": 1, "slug": "different", "name": "Different", "apps": []
        })
        rift.atomic_json(rift.RIFTS_ROOT / "nova.json", {
            "schemaVersion": 1,
            "slug": "nova",
            "name": "Nova",
            "apps": [
                {"id": "bad", "launch": "missing-app"},
                {"id": "good", "launch": ["working-app", "--flag"]},
            ],
        })

        loaded = rift.load_rifts()

        self.assertEqual([item["slug"] for item in loaded], ["nova"])
        self.assertEqual([item["id"] for item in loaded[0]["apps"]], ["good"])
        self.assertEqual(loaded[0]["validationErrors"], ["Skipped invalid app recipe at index 0"])

    def test_load_rift_rejects_unsupported_schema(self):
        rift.ensure_dirs()
        rift.atomic_json(rift.RIFTS_ROOT / "nova.json", {
            "schemaVersion": 99, "slug": "nova", "name": "Nova", "apps": []
        })
        with self.assertRaisesRegex(ValueError, "not found or invalid"):
            rift.load_rift("nova")

    def test_lua_dispatch_formats_workspace_focus_for_hyprland_056(self):
        self.assertEqual(rift.lua_dispatch("workspace", "7"), "hl.dsp.focus({ workspace = 7 })")
        self.assertEqual(rift.lua_dispatch("workspace", "emptyn"), 'hl.dsp.focus({ workspace = "emptyn" })')

    def test_hypr_dispatch_falls_back_to_legacy_syntax(self):
        import subprocess as sp
        calls = []

        def fake(args):
            calls.append(args)
            if args[0].startswith("hl."):
                return sp.CompletedProcess(args, 0, "", "Invalid dispatcher")
            return sp.CompletedProcess(args, 0, "ok", "")

        with patch.object(rift, "_hyprctl_dispatch", side_effect=fake):
            rift.hypr_dispatch("workspace", "3")
        self.assertEqual(calls, [["hl.dsp.focus({ workspace = 3 })"], ["workspace", "3"]])

    def test_wait_for_workspace_change_handles_delayed_transition(self):
        with patch.object(
            rift,
            "current_workspace",
            side_effect=[{"id": 2, "name": "2"}, {"id": 2, "name": "2"}, {"id": 3, "name": "3"}],
        ), patch.object(rift.time, "sleep"):
            workspace = rift.wait_for_workspace_change(2)
        self.assertEqual(workspace["id"], 3)

    def test_wait_for_workspace_change_times_out(self):
        with patch.object(rift, "current_workspace", return_value={"id": 2, "name": "2"}):
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                rift.wait_for_workspace_change(2, timeout=0)

    def test_update_keeps_previous_recipe_and_revert_swaps_it_back(self):
        first = [{"id": "editor", "name": "Editor", "selected": True, "launch": ["editor"]}]
        second = [{"id": "browser", "name": "Browser", "selected": True, "launch": ["browser"]}]
        with patch.object(rift, "current_workspace", return_value={"id": 3, "name": "3"}), patch.object(
            rift, "hypr_json", return_value=[{"id": 3}]
        ):
            with patch.object(rift, "current_apps", return_value=first):
                rift.save_rift("Nova")
            with patch.object(rift, "current_apps", return_value=second):
                updated = rift.save_rift("Nova")

        self.assertEqual([a["id"] for a in updated["apps"]], ["browser"])
        self.assertEqual([a["id"] for a in updated["previous"]["apps"]], ["editor"])

        reverted = rift.revert_rift("nova")
        self.assertEqual([a["id"] for a in reverted["apps"]], ["editor"])
        self.assertEqual([a["id"] for a in reverted["previous"]["apps"]], ["browser"])
        self.assertEqual([a["id"] for a in rift.load_rift("nova")["apps"]], ["editor"])

    def test_revert_without_history_is_an_error(self):
        rift.ensure_dirs()
        rift.atomic_json(rift.rift_path("solo"), {"schemaVersion": 1, "slug": "solo", "name": "Solo", "apps": []})
        with self.assertRaisesRegex(ValueError, "Nothing to revert"):
            rift.revert_rift("solo")

    def test_version_is_in_lockstep_between_manifest_and_panel(self):
        import json, re
        root = Path(__file__).parents[1]
        manifest = json.loads((root / "manifest.json").read_text())["version"]
        panel = re.search(r'riftVersion:\s*"([^"]+)"', (root / "Panel.qml").read_text()).group(1)
        self.assertEqual(manifest, panel)

    def test_persist_rift_never_stores_validation_errors(self):
        rift.ensure_dirs()
        rift.atomic_json(
            rift.rift_path("clean"),
            {"schemaVersion": 1, "slug": "clean", "name": "Clean", "apps": [{"id": "x", "launch": ["x"]}, {"id": "bad"}]},
        )
        loaded = rift.load_rift("clean")
        self.assertTrue(loaded["validationErrors"])
        rift.set_startup("clean", True)
        stored = rift.read_json(rift.rift_path("clean"), {})
        self.assertNotIn("validationErrors", stored)
        self.assertTrue(stored["startup"])

    def test_terminal_recipe_keeps_project_directory(self):
        self.assertEqual(
            rift.terminal_recipe("ghostty", "/tmp/nova"),
            ["ghostty", "--working-directory=/tmp/nova"],
        )

    def test_desktop_exec_binary_parses_quoted_and_escaped_paths(self):
        self.assertEqual(
            rift.desktop_exec_binary('"/opt/My Editor/bin/editor" --open %F'),
            "/opt/My Editor/bin/editor",
        )
        self.assertEqual(
            rift.desktop_exec_binary(r"/opt/My\ Editor/bin/editor %U"),
            "/opt/My Editor/bin/editor",
        )

    def test_desktop_exec_binary_unwraps_environment_command(self):
        self.assertEqual(
            rift.desktop_exec_binary("env --ignore-environment THEME=dark /usr/bin/editor %F"),
            "/usr/bin/editor",
        )
        self.assertEqual(
            rift.desktop_exec_binary("/usr/bin/env --unset DISPLAY -- /usr/bin/editor"),
            "/usr/bin/editor",
        )

    def test_desktop_exec_binary_rejects_malformed_or_ambiguous_values(self):
        self.assertEqual(rift.desktop_exec_binary('"unterminated'), "")
        self.assertEqual(rift.desktop_exec_binary("env --split-string editor --flag"), "")
        self.assertEqual(rift.desktop_exec_binary("%F"), "")

    def test_unlaunchable_app_does_not_abort_rift(self):
        broken = {"launch": ["missing-app"], "cwd": "", "policy": "launch"}
        working = {"launch": ["working-app"], "cwd": "", "policy": "launch"}
        saved = {"name": "Nova", "slug": "nova"}

        with patch.object(
            rift.subprocess,
            "Popen",
            side_effect=[FileNotFoundError("missing-app"), unittest.mock.DEFAULT],
        ) as popen:
            launched = [rift.launch_app(app, saved) for app in (broken, working)]

        self.assertEqual(launched, [False, True])
        self.assertEqual(popen.call_count, 2)

    def test_open_rift_keeps_total_failure_retryable(self):
        saved = {"slug": "nova", "name": "Nova", "apps": [{"id": "missing"}]}
        runtime = {"signature": "", "open": {}}
        with patch.object(rift, "load_rift", return_value=saved), patch.object(
            rift, "runtime_state", return_value=runtime
        ), patch.object(rift, "save_runtime"), patch.object(
            rift, "hypr_dispatch"
        ), patch.object(
            rift.time, "sleep"
        ), patch.object(
            rift, "current_workspace", return_value={"id": 9, "name": "9"}
        ), patch.object(
            rift, "wait_for_workspace_change", return_value={"id": 9, "name": "9"}
        ), patch.object(
            rift,
            "launch_app_result",
            return_value={"app": "missing", "status": "failed", "error": "not found"},
        ):
            result = rift.open_rift("nova")

        self.assertEqual(result["action"], "failed")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(runtime["open"], {})

    def test_open_rift_records_partial_success(self):
        saved = {"slug": "nova", "name": "Nova", "apps": [{"id": "editor"}, {"id": "missing"}]}
        runtime = {"signature": "", "open": {}}
        outcomes = [
            {"app": "editor", "status": "launched"},
            {"app": "missing", "status": "failed", "error": "not found"},
        ]
        with patch.object(rift, "load_rift", return_value=saved), patch.object(
            rift, "runtime_state", return_value=runtime
        ), patch.object(rift, "save_runtime"), patch.object(
            rift, "hypr_dispatch"
        ), patch.object(
            rift.time, "sleep"
        ), patch.object(
            rift, "current_workspace", return_value={"id": 9, "name": "9"}
        ), patch.object(
            rift, "wait_for_workspace_change", return_value={"id": 9, "name": "9"}
        ), patch.object(rift, "launch_app_result", side_effect=outcomes):
            result = rift.open_rift("nova")

        self.assertEqual(result["action"], "partial")
        self.assertEqual((result["launched"], result["failed"]), (1, 1))
        self.assertEqual(runtime["open"]["nova"]["workspace_id"], 9)
        self.assertEqual(runtime["open"]["nova"]["failed_apps"], ["missing"])

    def test_open_rift_retries_failed_apps_on_existing_workspace(self):
        saved = {"slug": "nova", "name": "Nova", "apps": [{"id": "editor"}, {"id": "missing"}]}
        runtime = {
            "signature": "",
            "open": {"nova": {"workspace_id": 9, "workspace_name": "9", "failed_apps": ["missing"]}},
        }
        with patch.object(rift, "load_rift", return_value=saved), patch.object(
            rift, "runtime_state", return_value=runtime
        ), patch.object(rift, "save_runtime"), patch.object(
            rift, "hypr_dispatch"
        ) as dispatch, patch.object(
            rift, "launch_app_result", return_value={"app": "missing", "status": "launched"}
        ) as launch:
            result = rift.open_rift("nova")

        self.assertEqual(result["action"], "opened")
        dispatch.assert_called_once_with("workspace", "9")
        launch.assert_called_once_with(saved["apps"][1], saved)
        self.assertNotIn("failed_apps", runtime["open"]["nova"])

    def test_partial_startup_does_not_write_completion_marker(self):
        rift.ensure_dirs()
        rift.atomic_json(
            rift.rift_path("nova"),
            {"schemaVersion": 1, "slug": "nova", "name": "Nova", "startup": True, "apps": []},
        )
        signature = rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        marker = rift.STATE_ROOT / f"startup-{rift.re.sub(r'[^a-zA-Z0-9_.-]', '_', signature)}"
        with patch.object(rift, "open_rift", return_value={"action": "partial"}):
            with self.assertRaisesRegex(RuntimeError, "needs retry"):
                rift.startup_open()
        self.assertFalse(marker.exists())
    def test_failed_startup_can_be_retried(self):
        rift.ensure_dirs()
        rift.atomic_json(
            rift.rift_path("nova"),
            {"schemaVersion": 1, "slug": "nova", "name": "Nova", "startup": True, "apps": []},
        )
        signature = rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        marker = rift.STATE_ROOT / f"startup-{rift.re.sub(r'[^a-zA-Z0-9_.-]', '_', signature)}"

        with patch.object(rift, "open_rift", side_effect=RuntimeError("launch failed")):
            with self.assertRaisesRegex(RuntimeError, "launch failed"):
                rift.startup_open()

        self.assertFalse(marker.exists())
        with patch.object(rift, "open_rift", return_value={"action": "opened"}) as open_rift:
            result = rift.startup_open()
        self.assertEqual(result["action"], "startup")
        open_rift.assert_called_once_with("nova")

    def test_startup_lock_fallback_stays_in_private_state_directory(self):
        with patch.dict(rift.os.environ, {}, clear=False):
            rift.os.environ.pop("XDG_RUNTIME_DIR", None)
            path = rift.startup_lock_path()
        self.assertEqual(path, rift.STATE_ROOT / "locks/startup.lock")
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_startup_lock_rejects_symlink_without_touching_target(self):
        with patch.dict(rift.os.environ, {}, clear=False):
            rift.os.environ.pop("XDG_RUNTIME_DIR", None)
            path = rift.startup_lock_path()
            victim = Path(self.temp.name) / "victim"
            victim.write_text("keep me")
            path.symlink_to(victim)

            with self.assertRaises(OSError):
                rift.startup_open()

        self.assertEqual(victim.read_text(), "keep me")

    def test_startup_lock_contention_reports_already_running(self):
        with patch.dict(rift.os.environ, {}, clear=False):
            rift.os.environ.pop("XDG_RUNTIME_DIR", None)
            with rift.startup_lock() as lock:
                rift.fcntl.flock(lock.fileno(), rift.fcntl.LOCK_EX | rift.fcntl.LOCK_NB)
                self.assertEqual(rift.startup_open(), {"action": "already-running"})


if __name__ == "__main__":
    unittest.main()
