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
        self.settings = patch.object(rift, "SETTINGS_FILE", root / "config/settings.json")
        self.settings.start()
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

        with patch.object(rift, "hypr_json", return_value=[{"id": item, "windows": 1} for item in range(1, 9)]):
            threads = [threading.Thread(target=associate, args=(item,)) for item in range(1, 9)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(failures, [])
        state = rift.read_json(rift.RUNTIME_FILE, {})
        self.assertEqual(set(state["open"]), {f"rift-{item}" for item in range(1, 9)})

    def test_save_replaces_existing_rift_association_on_workspace(self):
        rift.ensure_dirs()
        rift.atomic_json(
            rift.RUNTIME_FILE,
            {
                "signature": rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
                "open": {"old": {"workspace_id": 2, "workspace_name": "2"}},
            },
        )
        with patch.object(
            rift,
            "current_apps",
            return_value=[{"id": "editor", "name": "Editor", "selected": True, "launch": ["editor"]}],
        ), patch.object(
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
        with patch.object(rift, "hypr_json", return_value=[{"id": 7, "windows": 1}]), patch.object(
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

    def test_runtime_state_preserves_valid_failed_app_ids(self):
        signature = rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        rift.atomic_json(
            rift.RUNTIME_FILE,
            {
                "signature": signature,
                "open": {
                    "nova": {
                        "workspace_id": 7,
                        "workspace_name": "7",
                        "failed_apps": ["desktop:slack", "", 42],
                    }
                },
            },
        )
        with patch.object(rift, "hypr_json", return_value=[{"id": 7, "windows": 1}]):
            state = rift.runtime_state()
        self.assertEqual(state["open"]["nova"]["failed_apps"], ["desktop:slack"])

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

    def test_hypr_json_treats_empty_or_invalid_stdout_as_runtime_error(self):
        import subprocess as sp

        with patch.object(
            rift.subprocess,
            "run",
            return_value=sp.CompletedProcess(["hyprctl", "-j", "clients"], 0, "", ""),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                rift.hypr_json("clients")

        with patch.object(
            rift.subprocess,
            "run",
            return_value=sp.CompletedProcess(["hyprctl", "-j", "clients"], 0, "not-json", ""),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                rift.hypr_json("clients")

        with patch.object(
            rift.subprocess,
            "run",
            return_value=sp.CompletedProcess(["hyprctl", "-j", "clients"], 0, "[]", ""),
        ):
            self.assertEqual(rift.hypr_json("clients"), [])

    def test_current_workspace_rejects_non_object_payload(self):
        with patch.object(rift, "hypr_json", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "unexpected JSON"):
                rift.current_workspace()

    def test_current_apps_skips_malformed_clients(self):
        workspace = {"id": 4, "name": "4"}
        clients = [
            "nope",
            {"workspace": 7},
            {"workspace": {"id": "bad"}},
            {"workspace": {"id": 9}, "class": "Other"},
            {"workspace": {"id": 4}, "pid": 11, "class": "kitty", "initialClass": "kitty"},
        ]
        with patch.object(rift, "hypr_json", return_value=clients), patch.object(
            rift, "desktop_entries", return_value=[]
        ), patch.object(
            rift,
            "app_from_client",
            side_effect=lambda client, _entries: {"id": "term", "name": "Kitty", "kind": "terminal"}
            if client.get("pid") == 11
            else None,
        ):
            self.assertEqual(rift.current_apps(workspace), [{"id": "term", "name": "Kitty", "kind": "terminal"}])

        with patch.object(rift, "hypr_json", return_value={"ok": True}):
            with self.assertRaisesRegex(RuntimeError, "unexpected JSON"):
                rift.current_apps(workspace)

    def test_app_from_client_tolerates_non_integer_pid(self):
        client = {"class": "x", "initialClass": "x", "pid": "nope", "title": ""}
        with patch.object(rift, "process_info", return_value=("", [], "")), patch.object(
            rift, "find_desktop_entry", return_value=None
        ):
            self.assertIsNone(rift.app_from_client(client, []))

    def test_wait_for_workspace_change_handles_delayed_transition(self):
        with patch.object(
            rift,
            "current_workspace",
            side_effect=[{"id": 2, "name": "2"}, {"id": 2, "name": "2"}, {"id": 3, "name": "3"}],
        ), patch.object(rift.time, "sleep"):
            workspace = rift.wait_for_workspace_change(2)
        self.assertEqual(workspace["id"], 3)

    def test_focus_empty_workspace_accepts_already_empty_focus(self):
        empty = {"id": 5, "name": "5", "windows": 0}
        with patch.object(rift, "current_workspace", return_value=empty), patch.object(
            rift, "hypr_dispatch"
        ), patch.object(
            rift, "wait_for_workspace_change", side_effect=RuntimeError("Timed out waiting for a fresh workspace")
        ):
            self.assertEqual(rift.focus_empty_workspace()["id"], 5)
            self.assertEqual(rift.new_workspace()["workspace"]["id"], 5)

    def test_focus_empty_workspace_still_fails_when_focus_has_windows(self):
        busy = {"id": 5, "name": "5", "windows": 3}
        with patch.object(rift, "current_workspace", return_value=busy), patch.object(
            rift, "hypr_dispatch"
        ), patch.object(
            rift, "wait_for_workspace_change", side_effect=RuntimeError("Timed out waiting for a fresh workspace")
        ):
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                rift.focus_empty_workspace()

    def test_wait_for_workspace_change_times_out(self):
        with patch.object(rift, "current_workspace", return_value={"id": 2, "name": "2"}):
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                rift.wait_for_workspace_change(2, timeout=0)

    def test_wait_for_workspace_change_tolerates_null_workspace_id(self):
        with patch.object(rift, "current_workspace", return_value={"id": None, "name": "?"}):
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                rift.wait_for_workspace_change(2, timeout=0)

    def test_numeric_id_coerces_null_and_garbage(self):
        self.assertEqual(rift.numeric_id(None), 0)
        self.assertEqual(rift.numeric_id(""), 0)
        self.assertEqual(rift.numeric_id("7"), 7)
        self.assertEqual(rift.numeric_id("nope"), 0)

    def test_update_keeps_previous_recipe_and_revert_swaps_it_back(self):
        first = [{"id": "editor", "name": "Editor", "selected": True, "launch": ["editor"]}]
        second = [{"id": "browser", "name": "Browser", "selected": True, "launch": ["browser"]}]
        with patch.object(rift, "current_workspace", return_value={"id": 3, "name": "3"}), patch.object(
            rift, "hypr_json", return_value=[{"id": 3, "windows": 1}]
        ):
            with patch.object(rift, "current_apps", return_value=first):
                rift.save_rift("Nova")
            with patch.object(rift, "current_apps", return_value=second):
                updated = rift.save_rift("Nova", update_of="nova")

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

    def test_update_refuses_when_panel_workspace_is_stale(self):
        with patch.object(rift, "current_workspace", return_value={"id": 10, "name": "10"}), patch.object(
            rift, "current_apps", return_value=[]
        ), patch.object(rift, "hypr_json", return_value=[{"id": 10, "windows": 0}]):
            with self.assertRaisesRegex(RuntimeError, "workspace 10 but the panel showed workspace 7"):
                rift.save_rift("Nova", expect_workspace=7)

    def test_update_refuses_when_rift_is_bound_elsewhere(self):
        rift.ensure_dirs()
        rift.atomic_json(
            rift.RUNTIME_FILE,
            {"signature": rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
             "open": {"nova": {"workspace_id": 7, "workspace_name": "7"}}},
        )
        with patch.object(rift, "current_workspace", return_value={"id": 10, "name": "10"}), patch.object(
            rift, "current_apps", return_value=[]
        ), patch.object(rift, "hypr_json", return_value=[{"id": 7, "windows": 1}, {"id": 10, "windows": 0}]):
            with self.assertRaisesRegex(RuntimeError, "belongs to workspace 7, not workspace 10"):
                rift.save_rift("Nova", expect_workspace=10, update_of="nova")
        self.assertFalse(rift.rift_path("nova").exists())

    def test_save_refuses_to_overwrite_an_existing_rift_without_update_of(self):
        rift.ensure_dirs()
        rift.atomic_json(
            rift.rift_path("nova"),
            {
                "schemaVersion": 1,
                "slug": "nova",
                "name": "Nova",
                "startup": True,
                "apps": [{"id": "keep", "launch": ["keep"]}],
            },
        )
        rift.atomic_json(
            rift.RUNTIME_FILE,
            {
                "signature": rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
                "open": {"nova": {"workspace_id": 7, "workspace_name": "7"}},
            },
        )
        with patch.object(rift, "current_workspace", return_value={"id": 10, "name": "10"}), patch.object(
            rift,
            "current_apps",
            return_value=[{"id": "editor", "name": "Editor", "selected": True, "launch": ["editor"]}],
        ), patch.object(rift, "hypr_json", return_value=[{"id": 7, "windows": 1}, {"id": 10, "windows": 1}]):
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                rift.save_rift("Nova")

        stored = rift.read_json(rift.rift_path("nova"), {})
        self.assertEqual(stored["apps"][0]["id"], "keep")
        self.assertTrue(stored["startup"])
        self.assertEqual(rift.runtime_state()["open"]["nova"]["workspace_id"], 7)

    def test_save_update_of_still_rewrites_the_intended_rift(self):
        with patch.object(rift, "current_workspace", return_value={"id": 7, "name": "7"}), patch.object(
            rift,
            "current_apps",
            return_value=[{"id": "editor", "name": "Editor", "selected": True, "launch": ["editor"]}],
        ), patch.object(rift, "hypr_json", return_value=[{"id": 7, "windows": 1}]):
            rift.save_rift("Nova")
            updated = rift.save_rift("Nova", update_of="nova")
        self.assertEqual([app["id"] for app in updated["apps"]], ["editor"])
    def test_save_refuses_an_empty_application_list(self):
        with patch.object(rift, "current_workspace", return_value={"id": 4, "name": "4"}), patch.object(
            rift, "current_apps", return_value=[]
        ), patch.object(rift, "hypr_json", return_value=[{"id": 4, "windows": 1}]):
            with self.assertRaisesRegex(ValueError, "at least one application"):
                rift.save_rift("Empty")
        self.assertFalse(rift.rift_path("empty").exists())

        with patch.object(rift, "current_workspace", return_value={"id": 4, "name": "4"}), patch.object(
            rift,
            "current_apps",
            return_value=[
                {"id": "editor", "name": "Editor", "selected": True, "launch": ["editor"]},
                {"id": "music", "name": "Music", "selected": False, "launch": ["music"]},
            ],
        ), patch.object(rift, "hypr_json", return_value=[{"id": 4, "windows": 1}]):
            with self.assertRaisesRegex(ValueError, "at least one application"):
                rift.save_rift("Empty", include_ids=[])
        self.assertFalse(rift.rift_path("empty").exists())

    def test_empty_workspace_never_keeps_an_association(self):
        rift.atomic_json(
            rift.RUNTIME_FILE,
            {"signature": rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
             "open": {"nova": {"workspace_id": 8, "workspace_name": "8"}}},
        )
        with patch.object(rift, "hypr_json", return_value=[{"id": 8, "windows": 0}]):
            self.assertEqual(rift.runtime_state()["open"], {})
        with patch.object(rift, "hypr_json", return_value=[{"id": 8, "windows": 2}]):
            self.assertIn("nova", rift.runtime_state()["open"])

    def test_history_keeps_several_recipes_and_revert_walks_back(self):
        ws = {"id": 3, "name": "3"}
        with patch.object(rift, "current_workspace", return_value=ws), patch.object(
            rift, "hypr_json", return_value=[{"id": 3, "windows": 1}]
        ):
            for name in ("a", "b", "c"):
                with patch.object(rift, "current_apps", return_value=[{"id": name, "name": name, "selected": True, "launch": [name]}]):
                    rift.save_rift("Nova", update_of="nova" if name != "a" else None)
        self.assertEqual([h["apps"][0]["id"] for h in rift.load_rift("nova")["history"]], ["b", "a"])
        self.assertEqual(rift.revert_rift("nova")["apps"][0]["id"], "b")
        self.assertEqual(rift.revert_rift("nova")["apps"][0]["id"], "a")

    def test_help_mode_is_on_for_new_users_until_first_save_and_can_be_reenabled(self):
        rift.ensure_dirs()
        self.assertTrue(rift.help_enabled())
        with patch.object(rift, "current_workspace", return_value={"id": 2, "name": "2"}), patch.object(
            rift, "current_apps", return_value=[{"id": "a", "name": "A", "selected": True, "launch": ["a"]}]
        ), patch.object(rift, "hypr_json", return_value=[{"id": 2, "windows": 1}]):
            rift.save_rift("First")
        self.assertFalse(rift.help_enabled())
        rift.set_help(True)
        self.assertTrue(rift.help_enabled())
        rift.set_help(False)
        self.assertFalse(rift.help_enabled())

    def test_rename_moves_file_and_association(self):
        rift.ensure_dirs()
        rift.atomic_json(rift.rift_path("old"), {"schemaVersion": 1, "slug": "old", "name": "Old", "apps": []})
        rift.atomic_json(
            rift.RUNTIME_FILE,
            {"signature": rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
             "open": {"old": {"workspace_id": 4, "workspace_name": "4"}}},
        )
        with patch.object(rift, "hypr_json", return_value=[{"id": 4, "windows": 1}]):
            renamed = rift.rename_rift("old", "Shiny New")
            self.assertEqual(renamed["slug"], "shiny-new")
            self.assertFalse(rift.rift_path("old").exists())
            self.assertEqual(rift.runtime_state()["open"]["shiny-new"]["workspace_id"], 4)

    def test_rename_keeps_old_file_if_runtime_update_fails(self):
        rift.ensure_dirs()
        rift.atomic_json(rift.rift_path("old"), {"schemaVersion": 1, "slug": "old", "name": "Old", "apps": []})
        with patch.object(rift, "runtime_transaction", side_effect=RuntimeError("runtime locked")):
            with self.assertRaisesRegex(RuntimeError, "runtime locked"):
                rift.rename_rift("old", "Shiny New")
        self.assertTrue(rift.rift_path("old").exists())
        self.assertTrue(rift.rift_path("shiny-new").exists())

    def test_terminal_session_finds_shell_cwd_and_foreground_program(self):
        # kitty(100) -> kitten(101), bash(102), kitten(103); bash -> claude(200)
        tree = {100: [101, 102, 103], 102: [200]}
        comm = {101: "kitten", 102: "bash", 103: "kitten", 200: "claude"}
        info = {
            100: ("/usr/bin/kitty", ["kitty"], "/home/pi"),
            102: ("/usr/bin/bash", ["bash"], "/home/pi/Projects/rift"),
            200: ("/usr/bin/node", ["claude", "--append-system-prompt-file", "/x/LARRY.md", "--dangerously-skip-permissions"], "/home/pi/Projects/rift"),
        }
        # bash pgrp 102, foreground pgroup is claude (200)
        ids = {102: (100, 102, 200), 200: (102, 200, 200)}
        with patch.object(rift, "child_pids", side_effect=lambda pid: tree.get(pid, [])), patch.object(
            rift, "proc_comm", side_effect=lambda pid: comm.get(pid, "")
        ), patch.object(rift, "process_info", side_effect=lambda pid: info.get(pid, ("", [], ""))), patch.object(
            rift, "proc_ids", side_effect=lambda pid: ids.get(pid)
        ):
            session = rift.terminal_session(100)
        self.assertEqual(session["cwd"], "/home/pi/Projects/rift")
        self.assertEqual(session["program"], "claude")
        resumed = rift.resume_command(session)
        self.assertEqual(resumed[-1], "--continue")
        self.assertIn("--dangerously-skip-permissions", resumed)
        self.assertEqual(
            rift.terminal_recipe("kitty", "/home/pi/Projects/rift", resumed)[:3],
            ["kitty", "--directory", "/home/pi/Projects/rift"],
        )

    def test_terminal_session_ignores_background_jobs_listed_after_the_shell(self):
        # /proc children order is not start order. reversed() used to pick sleep.
        tree = {100: [10], 10: [12, 11]}
        comm = {10: "zsh", 11: "sleep", 12: "claude"}
        info = {
            10: ("/bin/zsh", ["zsh"], "/work"),
            11: ("/bin/sleep", ["sleep", "100"], "/work"),
            12: ("/usr/bin/claude", ["claude"], "/work"),
        }
        ids = {10: (100, 10, 12), 11: (10, 11, 12), 12: (10, 12, 12)}
        with patch.object(rift, "child_pids", side_effect=lambda pid: tree.get(pid, [])), patch.object(
            rift, "proc_comm", side_effect=lambda pid: comm.get(pid, "")
        ), patch.object(rift, "process_info", side_effect=lambda pid: info.get(pid, ("", [], ""))), patch.object(
            rift, "proc_ids", side_effect=lambda pid: ids.get(pid)
        ):
            session = rift.terminal_session(100)
        self.assertEqual(session["program"], "claude")
        self.assertEqual(session["command"], ["claude"])

    def test_terminal_session_captures_direct_minus_e_without_a_shell(self):
        tree = {100: [200]}
        comm = {200: "claude"}
        info = {
            100: ("/usr/bin/kitty", ["kitty", "-e", "claude"], "/home/pi"),
            200: ("/usr/bin/claude", ["claude"], "/proj"),
        }
        ids = {100: (1, 100, 200), 200: (100, 200, 200)}
        with patch.object(rift, "child_pids", side_effect=lambda pid: tree.get(pid, [])), patch.object(
            rift, "proc_comm", side_effect=lambda pid: comm.get(pid, "")
        ), patch.object(rift, "process_info", side_effect=lambda pid: info.get(pid, ("", [], ""))), patch.object(
            rift, "proc_ids", side_effect=lambda pid: ids.get(pid)
        ):
            session = rift.terminal_session(100)
        self.assertEqual(session["program"], "claude")
        self.assertEqual(session["cwd"], "/proj")

    def test_resume_command_policy(self):
        self.assertEqual(rift.resume_command({"command": ["codex", "--yolo"], "program": "codex"}), ["codex", "resume", "--last"])
        self.assertEqual(rift.resume_command({"command": ["nvim", "a.py"], "program": "nvim"}), ["nvim", "a.py"])
        self.assertEqual(rift.resume_command({"command": ["python3", "-m", "http.server"], "program": "python3"}), [])
        self.assertEqual(rift.resume_command({"command": [], "program": ""}), [])
        self.assertEqual(rift.resume_command({"command": ["claude", "--continue"], "program": "claude"}), ["claude", "--continue"])
        self.assertEqual(
            rift.resume_command({"command": ["claude", "--dangerously-skip-permissions"], "program": "claude"}),
            ["claude", "--dangerously-skip-permissions", "--continue"],
        )
        self.assertEqual(rift.resume_command({"command": ["claude", "mcp"], "program": "claude"}), [])
        self.assertEqual(rift.resume_command({"command": ["claude", "doctor"], "program": "claude"}), [])
        self.assertEqual(rift.resume_command({"command": ["claude", "-p", "hello"], "program": "claude"}), [])
        self.assertEqual(rift.resume_command({"command": ["claude", "--print"], "program": "claude"}), [])
        self.assertEqual(
            rift.resume_command({
                "command": ["claude", "--append-system-prompt-file", "/x/LARRY.md", "--dangerously-skip-permissions"],
                "program": "claude",
            }),
            ["claude", "--append-system-prompt-file", "/x/LARRY.md", "--dangerously-skip-permissions", "--continue"],
        )

    def test_terminal_recipe_runs_command_per_terminal(self):
        self.assertEqual(rift.terminal_recipe("ghostty", "/p", ["btop"]), ["ghostty", "--working-directory=/p", "--wait-after-command=true", "-e", "btop"])
        self.assertEqual(rift.terminal_recipe("alacritty", "/p", ["btop"]), ["alacritty", "--working-directory", "/p", "--hold", "-e", "btop"])
        self.assertEqual(rift.terminal_recipe("kitty", "/p", ["btop"]), ["kitty", "--directory", "/p", "--hold", "btop"])
        self.assertEqual(rift.terminal_recipe("wezterm", "/p", ["btop"]), ["wezterm", "start", "--cwd", "/p", "--", "btop"])
        self.assertEqual(rift.terminal_recipe("foot", "", []), ["foot"])

    def test_resolved_directory_args_uses_process_cwd_not_helper_cwd(self):
        project = Path(self.temp.name) / "the-project"
        project.mkdir()
        self.assertEqual(
            rift.resolved_directory_args(["code", "."], str(project)),
            [str(project.resolve())],
        )
        self.assertEqual(
            rift.resolved_directory_args(["code", "--new-window", str(project)], "/unrelated"),
            [str(project.resolve())],
        )
        self.assertEqual(rift.resolved_directory_args(["code", "."], ""), [])

    def test_gui_editor_keeps_project_folder_from_relative_argv(self):
        project = Path(self.temp.name) / "the-project"
        project.mkdir()
        client = {"class": "Code", "initialClass": "Code", "pid": 9, "title": "rift"}
        entries = [{"id": "code", "name": "Code", "startup_class": "Code", "exec": "code"}]
        with patch.object(
            rift,
            "process_info",
            return_value=("/usr/bin/code", ["code", "."], str(project)),
        ):
            app = rift.app_from_client(client, entries)
        self.assertIsNotNone(app)
        self.assertEqual(app["launch"], ["/usr/bin/code", str(project.resolve())])
        self.assertIn(str(project.resolve()), app["id"])

    def test_state_reports_failed_apps_per_open_rift(self):
        rift.ensure_dirs()
        rift.atomic_json(rift.rift_path("nova"), {"schemaVersion": 1, "slug": "nova", "name": "Nova", "apps": [{"id": "a", "launch": ["a"]}]})
        rift.atomic_json(
            rift.RUNTIME_FILE,
            {"signature": rift.os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
             "open": {"nova": {"workspace_id": 3, "workspace_name": "3", "failed_apps": ["a"]}}},
        )
        def fake_hypr(subject):
            return {"workspaces": [{"id": 3, "windows": 1}], "activeworkspace": {"id": 3, "name": "3"}, "clients": []}[subject]
        with patch.object(rift, "hypr_json", side_effect=fake_hypr), patch.object(rift, "desktop_entries", return_value=[]):
            state = rift.state_payload()
        nova = next(r for r in state["rifts"] if r["slug"] == "nova")
        self.assertEqual(nova["failedApps"], ["a"])
        self.assertEqual(nova["openWorkspace"], 3)
        rift.set_startup("nova", True)
        self.assertNotIn("failedApps", rift.read_json(rift.rift_path("nova"), {}))

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

    def test_launch_fails_when_recorded_cwd_is_gone(self):
        app = {
            "id": "terminal:kitty:/gone:claude",
            "launch": ["kitty", "--directory", "/gone", "--hold", "claude", "--continue"],
            "cwd": "/definitely-not-a-rift-directory-xyz",
        }
        with patch.object(rift.subprocess, "Popen") as popen:
            result = rift.launch_app_result(app, {"name": "Nova", "slug": "nova"})
        self.assertEqual(result["status"], "failed")
        self.assertIn("no longer exists", result["error"])
        popen.assert_not_called()

    def test_launch_without_recorded_cwd_still_uses_home(self):
        with patch.object(rift.subprocess, "Popen") as popen:
            result = rift.launch_app_result(
                {"id": "desktop:code", "launch": ["gtk-launch", "code"]},
                {"name": "Nova", "slug": "nova"},
            )
        self.assertEqual(result["status"], "launched")
        self.assertEqual(popen.call_args.kwargs["cwd"], str(Path.home()))

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

    def test_ensure_app_does_not_launch_when_client_state_is_unknown(self):
        app = {
            "id": "desktop:slack",
            "class": "Slack",
            "policy": "ensure",
            "launch": ["gtk-launch", "slack"],
        }
        with patch.object(rift, "hypr_json", side_effect=RuntimeError("Hyprland busy")), patch.object(
            rift.subprocess, "Popen"
        ) as popen:
            result = rift.launch_app_result(app, {"name": "Work", "slug": "work"})

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "state-unknown")
        popen.assert_not_called()

    def test_ensure_app_launches_only_after_confirmed_absence(self):
        app = {
            "id": "desktop:slack",
            "class": "Slack",
            "policy": "ensure",
            "launch": ["gtk-launch", "slack"],
        }
        with patch.object(rift, "hypr_json", return_value=[]), patch.object(rift.subprocess, "Popen") as popen:
            result = rift.launch_app_result(app, {"name": "Work", "slug": "work"})
        self.assertEqual(result["status"], "launched")
        popen.assert_called_once()

    def test_ensure_app_reports_existing_client_without_launching(self):
        app = {
            "id": "desktop:slack",
            "class": "Slack",
            "policy": "ensure",
            "launch": ["gtk-launch", "slack"],
        }
        clients = [{"class": "slack", "initialClass": "Slack"}]
        with patch.object(rift, "hypr_json", return_value=clients), patch.object(
            rift.subprocess, "Popen"
        ) as popen:
            result = rift.launch_app_result(app, {"name": "Work", "slug": "work"})
        self.assertEqual(result["status"], "already-running")
        popen.assert_not_called()

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

    def test_open_rift_does_not_hold_runtime_lock_while_launching(self):
        rift.ensure_dirs()
        rift.atomic_json(
            rift.rift_path("nova"),
            {
                "schemaVersion": 1,
                "slug": "nova",
                "name": "Nova",
                "apps": [{"id": "editor", "launch": ["editor"]}],
            },
        )
        entered = threading.Event()

        def launch(_app, _saved):
            def other():
                with rift.runtime_transaction() as runtime:
                    runtime["probe"] = True
                entered.set()

            worker = threading.Thread(target=other, daemon=True)
            worker.start()
            self.assertTrue(entered.wait(1.5), "runtime lock was still held while launching apps")
            worker.join(timeout=1)
            return {"app": "editor", "status": "launched"}

        with patch.object(rift, "hypr_dispatch"), patch.object(
            rift, "current_workspace", return_value={"id": 2, "name": "2"}
        ), patch.object(
            rift, "wait_for_workspace_change", return_value={"id": 9, "name": "9"}
        ), patch.object(
            rift, "hypr_json", return_value=[{"id": 9, "windows": 1}]
        ), patch.object(rift, "launch_app_result", side_effect=launch):
            result = rift.open_rift("nova")
            # Assert while hypr_json is still patched: outside the block the
            # liveness rule consults the REAL compositor and prunes workspace 9.
            self.assertEqual(result["action"], "opened")
            self.assertEqual(rift.runtime_state()["open"]["nova"]["workspace_id"], 9)

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
