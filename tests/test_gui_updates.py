"""Update GUI state regression tests; no network or hardware access."""
from pathlib import Path
import json
import queue
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import Application


class UpdateGuiTests(unittest.TestCase):
    def setUp(self):
        self.app = Application.__new__(Application)
        self.app.window = MagicMock()
        self.app.closing = self.app.busy = self.app.update_busy = False
        self.app.ready = True
        self.app.update_manager = None
        self.app.active = None
        self.app.events = queue.Queue()
        self.app.manifest = {"source_commit": "a" * 40}
        self.app.spec = "current"
        self.app.bundled_manifest = {"source_commit": "b" * 40}
        self.app.bundled_spec = "bundled"
        self.app.update_candidate = None
        self.app._log = MagicMock()
        self.app._update_actions = MagicMock()
        self.app.update_status = MagicMock()
        self.app.firmware_version = MagicMock()
        self.app.firmware_badge = MagicMock()
        self.app.auto_update_value = MagicMock()
        self.app.auto_update_value.get.return_value = True

    @patch("deployer_core.list_candidate_ports")
    def test_update_job_blocks_flash_before_serial_discovery(self, enumerate_ports):
        self.app.update_busy = True
        self.app._start("flash")
        enumerate_ports.assert_not_called()

    def test_update_check_deferred_while_flashing(self):
        self.app.busy = True
        self.app._new_update_manager = MagicMock()
        self.app._start_update("check")
        self.app._new_update_manager.assert_not_called()

    def test_no_download_when_no_update_was_found(self):
        self.app._new_update_manager = MagicMock()
        self.app._start_update("prepare")
        self.app._new_update_manager.assert_not_called()

    @patch("deployer_core.Deployer")
    def test_prepare_result_switches_validated_firmware_without_flashing(self, deployer):
        self.app.update_busy = True
        self.app.events.put(("update_prepared", ({"source_commit": "c" * 40}, "verified-update")))
        self.app.events.put(("update_idle", None))
        self.app._drain()
        self.assertEqual(self.app.spec, "verified-update")
        self.assertEqual(self.app.manifest["source_commit"], "c" * 40)
        self.assertTrue(self.app.ready)
        self.assertFalse(self.app.update_busy)
        self.assertIsNone(self.app.update_manager)
        deployer.assert_not_called()

    def test_failure_preserves_selected_firmware_and_readiness(self):
        self.app.update_busy = True
        self.app.events.put(("update_error", "network unavailable"))
        self.app.events.put(("update_idle", None))
        self.app._drain()
        self.assertEqual(self.app.spec, "current")
        self.assertEqual(self.app.manifest["source_commit"], "a" * 40)
        self.assertTrue(self.app.ready)
        self.assertFalse(self.app.update_busy)
        self.assertIn("network unavailable", self.app.update_status.configure.call_args.kwargs["text"])

    def test_current_firmware_not_switched_by_check(self):
        candidate = types.SimpleNamespace(source_commit="c" * 40, available=False)
        self.app.events.put(("update_checked", candidate))
        self.app._drain()
        self.assertEqual(self.app.spec, "current")
        self.assertIn("已是最新", self.app.update_status.configure.call_args.kwargs["text"])

    def test_automatic_check_honors_user_setting(self):
        self.app._start_update = MagicMock()
        self.app.auto_update_value.get.return_value = False
        self.app._auto_check_update()
        self.app._start_update.assert_not_called()
        self.app.window.after.assert_called_once_with(21600000, self.app._auto_check_update)

    def test_automatic_check_calls_check_only(self):
        self.app._start_update = MagicMock()
        self.app._auto_check_update()
        self.app._start_update.assert_called_once_with("check")

    def test_auto_check_preference_saved_as_boolean(self):
        with tempfile.TemporaryDirectory() as directory:
            self.app.settings_path = Path(directory) / "settings.json"
            self.app.auto_update_value.get.return_value = False
            self.app._save_settings()
            self.assertEqual(json.loads(self.app.settings_path.read_text(encoding="utf-8")),
                             {"auto_check_updates": False})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_close_cancels_update_before_destroying_window(self):
        self.app.update_busy = True
        self.app.update_manager = MagicMock()
        self.app.close()
        self.assertTrue(self.app.closing)
        self.app.update_manager.cancel.assert_called_once()
        self.app.window.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
