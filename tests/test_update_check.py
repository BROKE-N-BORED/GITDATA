"""Update-check tests (v0.1.5 U1, U2, U7) — offscreen, no live network.

The startup check is silent when up to date or unreachable, and
both the startup and manual checks show the plain-words popup
when a newer release is out. The manual check keeps its
status-bar receipt for the up-to-date and unreachable cases.
"""
import os
import sys
import tempfile
import time
import types
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import scout
from scout import apis, core
from scout.gui import MainWindow

APP = None


def get_app():
    global APP
    if APP is None:
        # Reuse the singleton when another test module already
        # made it: a second QApplication raises (libshiboken).
        APP = QApplication.instance() or QApplication([])
    return APP


class UpdateCheckTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        get_app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["SCOUT_HOME"] = self.tmp.name
        core._RING.clear()
        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.window.statusBar().clearMessage()
        # The updater belongs to the other worker (v0.1.5): stub
        # it here, in the tests only, never in gui.py. The stub
        # shadows a real scout/updater.py for the test run, so
        # these tests pass whether or not it exists yet.
        self._had_updater_module = "scout.updater" in sys.modules
        self._real_updater_module = sys.modules.get("scout.updater")
        self._had_updater_attr = hasattr(scout, "updater")
        self._real_updater_attr = getattr(scout, "updater", None)
        fake = types.ModuleType("scout.updater")
        fake.start_update = unittest.mock.Mock()
        sys.modules["scout.updater"] = fake
        scout.updater = fake
        self.updater = fake
        self._click_timer = None
        self._popup_clicked = []

    def tearDown(self):
        if self._click_timer is not None:
            self._click_timer.stop()
        os.environ.pop("SCOUT_HOME", None)
        if self._had_updater_module:
            sys.modules["scout.updater"] = self._real_updater_module
        else:
            sys.modules.pop("scout.updater", None)
        if self._had_updater_attr:
            scout.updater = self._real_updater_attr
        else:
            delattr(scout, "updater")

    # ------------------------------------------------------------ helpers

    def _wait_for_update_check(self, timeout_ms=5000):
        """Wait until the update worker finished and its queued
        result landed, or False on timeout."""
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            worker = self.window.update_worker
            if worker is not None and not worker.isRunning():
                # One more pump so the queued finished signal lands.
                QTest.qWait(50)
                return True
            QTest.qWait(50)
        return False

    def _arm_popup_click(self, button_text):
        """Click a popup button as soon as one appears.

        Armed BEFORE the popup shows: the click timer fires
        inside the popup's own event loop, so a blocking exec()
        still gets its button clicked.
        """
        def poll():
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, QMessageBox) and widget.isVisible():
                    for button in widget.buttons():
                        if button.text() == button_text:
                            self._popup_clicked.append(widget)
                            button.click()
                            self._click_timer.stop()
                            return

        self._click_timer = QTimer()
        self._click_timer.setInterval(25)
        self._click_timer.timeout.connect(poll)
        self._click_timer.start()

    def _wait_for_popup_click(self, timeout_ms=5000):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if self._popup_clicked:
                return True
            QTest.qWait(50)
        return False

    def _assert_no_popup_text(self, box):
        """Plain-words copy rules: no em-dash, no bare &."""
        texts = [box.windowTitle(), box.text()]
        texts += [button.text() for button in box.buttons()]
        for text in texts:
            self.assertNotIn("\u2014", text)
            self.assertNotIn("&", text)

    # ------------------------------------------------------------ U1: silent startup

    def test_startup_check_silent_when_up_to_date(self):
        with unittest.mock.patch.object(
                apis, "latest_release_tag",
                return_value="v" + core.VERSION), \
                unittest.mock.patch.object(
                    MainWindow, "_offer_update") as offer:
            self.window.check_for_updates(silent=True)
            self.assertTrue(self._wait_for_update_check())
        offer.assert_not_called()
        self.assertEqual(self.window.statusBar().currentMessage(), "")

    def test_startup_check_silent_when_unreachable(self):
        with unittest.mock.patch.object(
                apis, "latest_release_tag", return_value=None), \
                unittest.mock.patch.object(
                    MainWindow, "_offer_update") as offer:
            self.window.check_for_updates(silent=True)
            self.assertTrue(self._wait_for_update_check())
        offer.assert_not_called()
        self.assertEqual(self.window.statusBar().currentMessage(), "")

    def test_start_background_checks_runs_silent_update_check(self):
        # U1 hooks into start_background_checks, which main()
        # calls after the window shows — never in __init__.
        with unittest.mock.patch.object(
                apis, "latest_release_tag",
                return_value="v9.9.9"), \
                unittest.mock.patch.object(
                    apis, "is_online", return_value=False), \
                unittest.mock.patch.object(
                    MainWindow, "_offer_update") as offer:
            self.window.start_background_checks()
            self.assertTrue(self._wait_for_update_check())
            # Wait for the online probe too, while the mock is
            # still up: a real probe would touch the network.
            deadline = time.monotonic() + 5
            while (self.window._online_worker is not None
                   and self.window._online_worker.isRunning()
                   and time.monotonic() < deadline):
                QTest.qWait(50)
        offer.assert_called_once_with("v9.9.9")

    # ------------------------------------------------------------ U2: the popup

    def test_startup_check_shows_popup_when_newer(self):
        with unittest.mock.patch.object(
                apis, "latest_release_tag",
                return_value="v9.9.9"), \
                unittest.mock.patch.object(
                    MainWindow, "_offer_update") as offer:
            self.window.check_for_updates(silent=True)
            self.assertTrue(self._wait_for_update_check())
        offer.assert_called_once_with("v9.9.9")

    def test_manual_check_shows_popup_when_newer(self):
        with unittest.mock.patch.object(
                apis, "latest_release_tag",
                return_value="v9.9.9"), \
                unittest.mock.patch.object(
                    MainWindow, "_offer_update") as offer:
            self.window.check_for_updates()
            self.assertTrue(self._wait_for_update_check())
        offer.assert_called_once_with("v9.9.9")

    def test_manual_check_keeps_receipt_when_up_to_date(self):
        with unittest.mock.patch.object(
                apis, "latest_release_tag",
                return_value="v" + core.VERSION), \
                unittest.mock.patch.object(
                    MainWindow, "_offer_update") as offer:
            self.window.check_for_updates()
            self.assertTrue(self._wait_for_update_check())
        offer.assert_not_called()
        self.assertIn("up to date",
                      self.window.statusBar().currentMessage())

    def test_manual_check_keeps_receipt_when_unreachable(self):
        with unittest.mock.patch.object(
                apis, "latest_release_tag", return_value=None), \
                unittest.mock.patch.object(
                    MainWindow, "_offer_update") as offer:
            self.window.check_for_updates()
            self.assertTrue(self._wait_for_update_check())
        offer.assert_not_called()
        self.assertIn("Could not check",
                      self.window.statusBar().currentMessage())

    def test_popup_not_now_dismisses_without_updater(self):
        self._arm_popup_click("Not now")
        self.window._offer_update("v9.9.9")
        self.assertTrue(self._popup_clicked, "the popup never showed")
        box = self._popup_clicked[0]
        self.assertEqual(box.windowTitle(), "A new version is out")
        # One version format everywhere (v0.1.6 T19): no "v",
        # matching the About box.
        self.assertIn("9.9.9", box.text())
        self.assertNotIn("v9.9.9", box.text())
        self.assertIn(core.VERSION, box.text())
        self.assertEqual(
            sorted(button.text() for button in box.buttons()),
            ["Get it", "Not now"])
        self._assert_no_popup_text(box)
        self.updater.start_update.assert_not_called()

    def test_popup_get_it_calls_updater_with_tag(self):
        self._arm_popup_click("Get it")
        self.window._offer_update("v9.9.9")
        self.assertTrue(self._popup_clicked, "the popup never showed")
        self.updater.start_update.assert_called_once_with(
            self.window, "v9.9.9")

    def test_startup_check_end_to_end_popup_when_newer(self):
        # The full path: worker, silent flag, real popup, Not now.
        self._arm_popup_click("Not now")
        with unittest.mock.patch.object(
                apis, "latest_release_tag", return_value="v9.9.9"):
            self.window.check_for_updates(silent=True)
            self.assertTrue(self._wait_for_popup_click())
            self.assertTrue(self._wait_for_update_check())
        self.assertEqual(
            self._popup_clicked[0].windowTitle(), "A new version is out")
        self.updater.start_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
