"""Regression checks for Tcl-only popup focus and windowed callback errors.

No serial port is opened and no firmware is written.
"""
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import Application
from deployer_core import PortInfo


PORT = PortInfo("COM3", "USB JTAG", 0x303A, 0x1001, "board-a", "1-2")


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.app = Application.__new__(Application)
        self.app.busy = self.app.closing = False
        self.app.window = MagicMock()
        self.app.window.tk.call.return_value = ""
        self.app.window.focus_get.side_effect = AssertionError("Must not resolve Tcl-only children")
        self.app.port_box = MagicMock()
        self.app.port_box.__str__.return_value = ".ports"
        self.app.mode_box = MagicMock()
        self.app.mode_box.__str__.return_value = ".mode"
        self.app.port_value = MagicMock()
        self.app.port_value.get.return_value = PORT.label
        self.app.port_hint = MagicMock()
        self.app.port_signature = None
        self.app._log = MagicMock()
        self.app._update_actions = MagicMock()

    @patch("deployer_core.list_candidate_ports", return_value=[PORT])
    def test_each_posted_dropdown_defers_refresh_and_preserves_selection(self, enumerate_ports):
        for focus in (".ports.popdown.f.l", ".mode.popdown.f.l"):
            with self.subTest(focus=focus):
                self.app.window.tk.call.return_value = focus
                self.app._auto_refresh()
                enumerate_ports.assert_not_called()
                self.app.port_box.configure.assert_not_called()
                self.app.port_value.set.assert_not_called()
                self.app.window.after.assert_called_with(3000, self.app._auto_refresh)

    @patch("deployer_core.list_candidate_ports", return_value=[PORT])
    def test_refresh_resumes_when_popup_closes(self, enumerate_ports):
        self.app._auto_refresh()
        enumerate_ports.assert_called_once_with(include_device="COM3")
        self.app.port_box.configure.assert_called_once_with(values=(PORT.label,))
        self.app.port_value.set.assert_called_once_with(PORT.label)
        self.app.window.focus_get.assert_not_called()

    @patch("deployer_core.list_candidate_ports", return_value=[PORT])
    def test_manual_com_input_survives_refresh(self, _enumerate_ports):
        self.app.window.tk.call.return_value = ".ports"
        self.app._refresh_ports()
        self.app.port_value.set.assert_not_called()

    @patch("deployer_core.list_candidate_ports", return_value=[PORT])
    def test_unrelated_widget_with_similar_name_does_not_defer_refresh(self, enumerate_ports):
        self.app.window.tk.call.return_value = ".ports2"
        self.app._refresh_ports()
        enumerate_ports.assert_called_once()

    def test_refresh_timer_survives_callback_failure(self):
        self.app._refresh_ports = MagicMock(side_effect=RuntimeError("refresh failure"))
        with self.assertRaisesRegex(RuntimeError, "refresh failure"):
            self.app._auto_refresh()
        self.app.window.after.assert_called_once_with(3000, self.app._auto_refresh)

    def test_event_timer_survives_callback_failure(self):
        self.app.events = queue.Queue()
        self.app.events.put(("log", "worker progress"))
        self.app._log.side_effect = RuntimeError("widget failure")
        with self.assertRaisesRegex(RuntimeError, "widget failure"):
            self.app._drain()
        self.app.window.after.assert_called_once_with(80, self.app._drain)

    def test_closing_application_does_not_reschedule(self):
        self.app.closing = True
        self.app._auto_refresh()
        self.app._drain()
        self.app.window.after.assert_not_called()

    def test_windowed_callback_report_never_uses_console(self):
        with patch.object(sys, "stderr", None), patch.object(sys, "stdout", None):
            self.app._report_callback_exception(KeyError, KeyError("popdown"), None)
        self.assertIn("popdown", self.app._log.call_args.args[0])

    def test_callback_diagnostics_survive_broken_log_widget(self):
        with tempfile.TemporaryDirectory() as directory:
            self.app.log_path = Path(directory) / "callback.log"
            self.app._log.side_effect = RuntimeError("destroyed widget")
            with patch.object(sys, "stderr", None):
                self.app._report_callback_exception(KeyError, KeyError("popdown"), None)
            self.assertIn("popdown", self.app.log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
