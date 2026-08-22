import importlib.util
import tempfile
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

    def test_terminal_recipe_keeps_project_directory(self):
        self.assertEqual(
            rift.terminal_recipe("ghostty", "/tmp/nova"),
            ["ghostty", "--working-directory=/tmp/nova"],
        )


if __name__ == "__main__":
    unittest.main()
