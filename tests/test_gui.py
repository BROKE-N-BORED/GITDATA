"""GUI smoke tests — offscreen, no live network."""
import os
import socket
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QGroupBox, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QStatusBar, QTextEdit, QWidget,
)
from PySide6.QtTest import QTest
from PySide6.QtCore import QSettings, Qt

from scout import apis, core
from scout.gui import (
    HOW_TO_USE_SECTIONS, SETTINGS_APP, SETTINGS_ORG, MainWindow,
    SingleInstance,
)

APP = None


def get_app():
    global APP
    if APP is None:
        APP = QApplication([])
    return APP


class GuiTestCase(unittest.TestCase):
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

    def tearDown(self):
        os.environ.pop("SCOUT_HOME", None)

    def close_howto(self):
        if self.window.howto_window is not None:
            self.window.howto_window.close()
            QTest.qWait(50)


def _all_visible_texts(widget):
    texts = []
    for label in widget.findChildren(QLabel):
        texts.append(label.text())
    for button in widget.findChildren(QPushButton):
        texts.append(button.text())
    for box in widget.findChildren(QCheckBox):
        texts.append(box.text())
    for group in widget.findChildren(QGroupBox):
        texts.append(group.title())
    for edit in widget.findChildren(QLineEdit):
        texts.append(edit.placeholderText())
    for browser in widget.findChildren(QTextEdit):
        texts.append(browser.toPlainText())
    if isinstance(widget, QMainWindow):
        for action in widget.menuBar().actions():
            texts.append(action.text())
            menu = action.menu()
            if menu is not None:
                for sub in menu.actions():
                    texts.append(sub.text())
        for index in range(widget.tabs.count()):
            texts.append(widget.tabs.tabText(index))
    return texts


class WindowTests(GuiTestCase):
    def test_window_builds_with_three_tabs(self):
        self.assertEqual(self.window.tabs.count(), 3)
        self.assertEqual(self.window.tabs.tabText(0), "Search")
        self.assertTrue(self.window.tabs.tabText(1).startswith("My List"))
        self.assertEqual(self.window.tabs.tabText(2), "Sources")

    def test_status_bar_is_the_only_status_surface(self):
        bars = self.window.findChildren(QStatusBar)
        self.assertEqual(len(bars), 1)
        self.assertIs(bars[0], self.window.statusBar())
        for label in self.window.findChildren(QLabel):
            self.assertNotIn("status", label.objectName().lower())

    def test_no_bare_ampersand_in_visible_labels(self):
        for text in _all_visible_texts(self.window):
            self.assertNotIn("&", text, f"bare & in visible text: {text!r}")
        for index in range(self.window.tabs.count()):
            self.assertNotIn("&", self.window.tabs.tabText(index))

    def test_active_only_defaults_on_and_window_defaults_12_months(self):
        self.assertTrue(self.window.active_check.isChecked())
        self.assertEqual(self.window.window_combo.currentText(), "12 months")
        self.assertEqual(self.window.stars_combo.currentText(), "0")

    def test_exactly_one_chip_checked_and_clicking_switches(self):
        checked = [k for k, b in self.window.chip_buttons.items()
                   if b.isChecked()]
        self.assertEqual(checked, ["all"])
        QTest.mouseClick(self.window.chip_buttons["reasoning"],
                         Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.selected_category(), "reasoning")
        checked = [k for k, b in self.window.chip_buttons.items()
                   if b.isChecked()]
        self.assertEqual(checked, ["reasoning"])


class SearchFlowTests(GuiTestCase):
    def test_search_done_populates_table_and_filters(self):
        results = [
            {"title": "fresh", "url": "https://example.com/fresh",
             "category": "reasoning", "source": "GitHub", "stars": 50,
             "updated": "2026-11-01T00:00:00Z", "description": "d",
             "license": "MIT"},
            {"title": "stale", "url": "https://example.com/stale",
             "category": "reasoning", "source": "GitHub", "stars": 50,
             "updated": "2020-01-01T00:00:00Z", "description": "d",
             "license": "MIT"},
        ]
        self.window._search_done(results)
        # Active-only is on by default: the stale row is dropped.
        self.assertEqual(self.window.results_table.rowCount(), 1)
        self.assertEqual(
            self.window.results_table.item(0, 0).text(), "fresh")

    def test_add_selected_moves_rows_to_my_list(self):
        results = [
            {"title": "fresh", "url": "https://example.com/fresh",
             "category": "reasoning", "source": "GitHub", "stars": 50,
             "updated": "2026-11-01T00:00:00Z", "description": "d",
             "license": "MIT"},
        ]
        self.window._search_done(results)
        self.window.results_table.selectRow(0)
        self.window.add_selected()
        self.assertEqual(len(self.window.my_list), 1)
        self.assertIn("(1)", self.window.tabs.tabText(1))
        self.assertEqual(len(core.load_list()), 1)

    def test_live_search_path_uses_worker_and_never_blocks(self):
        # Fake the network calls; the thread path is what is exercised.
        fake_gh = [{"title": "gh", "url": "https://example.com/gh",
                    "category": "all", "source": "GitHub", "stars": 1,
                    "updated": "2026-11-01T00:00:00Z", "description": "",
                    "license": ""}]
        fake_hf = [{"title": "hf", "url": "https://example.com/hf",
                    "category": "all", "source": "Hugging Face", "stars": 2,
                    "updated": "2026-11-01T00:00:00Z", "description": "",
                    "license": ""}]
        with unittest.mock.patch.object(
                apis, "search_github", return_value=fake_gh), \
             unittest.mock.patch.object(
                apis, "search_huggingface", return_value=fake_hf):
            self.window.start_search()
            self.assertFalse(self.window.search_button.isEnabled())
            deadline = 100  # 100 x 50 ms = 5 s max
            while (not self.window.search_button.isEnabled()
                   and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertTrue(self.window.search_button.isEnabled())
        self.assertEqual(self.window.results_table.rowCount(), 2)


class FoundLinkTests(GuiTestCase):
    def test_add_found_link_lands_in_my_list(self):
        self.window.found_url.setText("example.com/dataset")
        self.window.found_title.setText("Hand found")
        self.window.found_note.setText("from Kaggle")
        self.window.add_found_link()
        self.assertEqual(len(self.window.my_list), 1)
        item = self.window.my_list[0]
        self.assertEqual(item["url"], "https://example.com/dataset")
        self.assertEqual(item["title"], "Hand found")
        self.assertEqual(item["notes"], "from Kaggle")
        self.assertEqual(item["source"], "Found by hand")
        self.assertEqual(self.window.found_url.text(), "")

    def test_add_found_link_needs_a_url(self):
        self.window.add_found_link()
        self.assertEqual(len(self.window.my_list), 0)


class SourcesTabTests(GuiTestCase):
    def test_sources_table_lists_defaults(self):
        self.assertEqual(self.window.sources_table.rowCount(), 3)

    def test_add_and_remove_source_round_trip(self):
        self.window.site_name.setText("Example Site")
        self.window.site_url.setText("https://example.com/?q={q}")
        self.window.add_source()
        self.assertEqual(self.window.sources_table.rowCount(), 4)
        self.assertEqual(len(core.load_sources()), 4)
        self.window.remove_source("Example Site")
        self.assertEqual(self.window.sources_table.rowCount(), 3)
        self.assertEqual(len(core.load_sources()), 3)

    def test_restore_defaults(self):
        self.window.remove_source("Kaggle Datasets")
        self.assertEqual(self.window.sources_table.rowCount(), 2)
        self.window.restore_sources()
        self.assertEqual(self.window.sources_table.rowCount(), 3)

    def test_refresh_never_doubles_the_remove_buttons(self):
        # Regression: _refresh_sources_table used to leave the old
        # Remove buttons behind (setCellWidget does not delete the
        # widget it replaces), so each row showed two of them.
        self.window.site_name.setText("Example Site")
        self.window.site_url.setText("https://example.com/?q={q}")
        self.window.add_source()
        self.window.remove_source("Example Site")
        # Spin the real event loop: one processEvents() call does NOT
        # flush deleteLater() deletions in this PySide6 build (proven
        # by probe 2026-11-13 — count stayed 10 after processEvents,
        # dropped to 3 after qWait).
        QTest.qWait(100)
        buttons = [
            b for b in self.window.sources_table.findChildren(QPushButton)
            if b.text() == "Remove"
        ]
        self.assertEqual(len(buttons), 3)
        for row in range(3):
            self.assertIn(
                self.window.sources_table.cellWidget(row, 2), buttons
            )

    def test_save_token_persists_and_never_leaks_to_report(self):
        self.window.token_field.setText("ghp_GUI_TOKEN_999")
        self.window.save_token()
        self.assertEqual(core.get_github_token(), "ghp_GUI_TOKEN_999")
        report = core.debug_report()
        self.assertNotIn("ghp_GUI_TOKEN_999", report)


class ListTabTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.window.my_list = [core.normalize_item({
            "title": "A", "url": "https://example.com/a",
            "category": "reasoning", "source": "GitHub",
            "stars": 5, "updated": "", "license": "", "notes": ""})]
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()

    def test_remove_selected(self):
        self.window.list_table.selectRow(0)
        self.window.remove_selected()
        self.assertEqual(len(self.window.my_list), 0)
        self.assertEqual(core.load_list(), [])

    def test_copy_all_links(self):
        self.window.copy_all_links()
        clipboard = QApplication.clipboard().text()
        self.assertIn("https://example.com/a", clipboard)

    def test_link_check_worker_marks_status(self):
        with unittest.mock.patch.object(
                apis, "check_link", return_value="ok"):
            self.window.start_link_check()
            deadline = 100
            while (not self.window.check_button.isEnabled()
                   and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertTrue(self.window.check_button.isEnabled())
        self.assertEqual(self.window.my_list[0]["status"], "ok")


class EmDashTests(GuiTestCase):
    """U33: no em-dashes in visible UI text."""

    def test_no_em_dash_in_visible_ui_text(self):
        for text in _all_visible_texts(self.window):
            self.assertNotIn("—", text, f"em-dash in visible text: {text!r}")
        self.assertNotIn("—", core.STELLA_FORMAT_NOTE)

    def test_no_em_dash_in_how_to_use_window(self):
        self.window.show_how_to_use()
        self.addCleanup(self.close_howto)
        for text in _all_visible_texts(self.window.howto_window):
            self.assertNotIn("—", text, f"em-dash in How to Use: {text!r}")

    def test_no_em_dash_in_status_messages(self):
        # The empty-list status messages the code can emit.
        self.window.start_link_check()
        self.assertNotIn("—", self.window.statusBar().currentMessage())
        self.window.copy_all_links()
        self.assertNotIn("—", self.window.statusBar().currentMessage())
        self.window.export_list("md")
        self.assertNotIn("—", self.window.statusBar().currentMessage())
        # The partial-source search messages.
        self.window._search_done([], [("Hugging Face", "no answer")])
        self.assertNotIn("—", self.window.statusBar().currentMessage())
        self.window._search_done([], [("GitHub", "no answer")])
        self.assertNotIn("—", self.window.statusBar().currentMessage())


class HowToUseTests(GuiTestCase):
    """U22: Help menu with a How to Use window."""

    def test_how_to_use_opens_with_all_sections(self):
        self.window.show_how_to_use()
        self.addCleanup(self.close_howto)
        howto = self.window.howto_window
        self.assertIsNotNone(howto)
        self.assertEqual(howto.windowTitle(), "How to Use Dataset Scout")
        self.assertFalse(howto.isHidden())
        text = howto.text.toPlainText()
        for title, _body in HOW_TO_USE_SECTIONS:
            self.assertIn(title, text)
        self.assertNotIn("—", text)
        self.assertNotIn("&", text)

    def test_how_to_use_opens_from_the_help_menu(self):
        help_menu = self.window.menuBar().actions()[0].menu()
        self.assertEqual(help_menu.title(), "Help")
        actions = {a.text(): a for a in help_menu.actions()}
        self.assertIn("How to Use", actions)
        actions["How to Use"].trigger()
        self.addCleanup(self.close_howto)
        self.assertIsNotNone(self.window.howto_window)

    def test_reopening_raises_the_same_window(self):
        self.window.show_how_to_use()
        self.addCleanup(self.close_howto)
        first = self.window.howto_window
        self.window.show_how_to_use()
        self.assertIs(self.window.howto_window, first)
        first.close()
        QTest.qWait(100)
        self.assertIsNone(self.window.howto_window)
        self.window.show_how_to_use()
        self.assertIsNot(self.window.howto_window, first)


class SingleInstanceTests(GuiTestCase):
    """U27: one GUI window at a time.

    The full two-process raise is covered by a live verification
    run, not by this suite: it needs two real processes.
    """

    def test_second_instance_cannot_take_the_lock(self):
        first = SingleInstance()
        self.assertTrue(first.try_take())
        self.addCleanup(first.release)
        second = SingleInstance()
        self.assertFalse(second.try_take())

    def test_lock_released_can_be_taken_again(self):
        first = SingleInstance()
        self.assertTrue(first.try_take())
        first.release()
        second = SingleInstance()
        self.assertTrue(second.try_take())
        self.addCleanup(second.release)

    def test_stale_lock_file_from_a_dead_process_is_taken_over(self):
        # Simulate a crashed run: a lock file naming a process that
        # does not exist, old enough to be past the stale window.
        fake = Path(self.tmp.name) / "app.lock"
        fake.write_text(
            f"999999999\npython3.12\n{socket.gethostname()}\n",
            encoding="utf-8",
        )
        old = time.time() - 3600
        os.utime(fake, (old, old))
        guard = SingleInstance()
        self.assertTrue(guard.try_take())
        self.addCleanup(guard.release)

    def test_raise_message_reaches_the_running_instance(self):
        first = SingleInstance()
        self.assertTrue(first.try_take())
        self.addCleanup(first.release)
        received = []
        self.assertTrue(first.start_server(lambda: received.append(True)))
        second = SingleInstance()
        self.assertTrue(second.notify_running())
        QTest.qWait(200)
        self.assertEqual(received, [True])


def _clear_geometry_settings():
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    settings.remove("window")
    settings.sync()


class GeometryTests(GuiTestCase):
    """U5: default size fits his screen; geometry remembered."""

    def setUp(self):
        # Registered first so it runs last, after the window's own
        # close (which saves geometry again): settings end clean.
        self.addCleanup(_clear_geometry_settings)
        _clear_geometry_settings()
        super().setUp()

    def test_default_size_is_1100x700(self):
        window = MainWindow()
        self.addCleanup(window.close)
        self.assertEqual(window.width(), 1100)
        self.assertEqual(window.height(), 700)

    def test_geometry_save_and_restore_round_trip(self):
        first = MainWindow()
        first.setGeometry(50, 40, 900, 600)
        first.show()
        QTest.qWait(50)
        first.close()
        QTest.qWait(50)
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self.assertEqual(settings.value("window/x", type=int), 50)
        self.assertEqual(settings.value("window/y", type=int), 40)
        self.assertEqual(settings.value("window/width", type=int), 900)
        self.assertEqual(settings.value("window/height", type=int), 600)
        second = MainWindow()
        self.addCleanup(second.close)
        self.assertEqual(second.geometry().getRect(), (50, 40, 900, 600))

    def test_off_screen_geometry_falls_back_to_default(self):
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        settings.setValue("window/x", 5000)
        settings.setValue("window/y", 5000)
        settings.setValue("window/width", 900)
        settings.setValue("window/height", 600)
        settings.sync()
        window = MainWindow()
        self.addCleanup(window.close)
        self.assertEqual(window.width(), 1100)
        self.assertEqual(window.height(), 700)


class BusyDotTests(GuiTestCase):
    """U16: a busy light while work runs, hidden otherwise."""

    def test_busy_dot_hidden_at_rest(self):
        self.assertTrue(self.window.busy_dot.isHidden())

    def test_busy_dot_shows_during_search_and_hides_after(self):
        release = threading.Event()

        def slow_github(*args, **kwargs):
            release.wait(timeout=10)
            return []

        with unittest.mock.patch.object(
                apis, "search_github", side_effect=slow_github), \
             unittest.mock.patch.object(
                apis, "search_huggingface", return_value=[]):
            self.window.start_search()
            self.assertFalse(self.window.busy_dot.isHidden())
            release.set()
            deadline = 100
            while (not self.window.search_button.isEnabled()
                   and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertTrue(self.window.search_button.isEnabled())
        self.assertTrue(self.window.busy_dot.isHidden())

    def test_busy_dot_shows_during_link_check(self):
        self.window.my_list = [core.normalize_item({
            "title": "A", "url": "https://example.com/a",
            "category": "reasoning", "source": "GitHub",
            "stars": 5, "updated": "", "license": "", "notes": ""})]
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()
        release = threading.Event()

        def slow_check(url):
            release.wait(timeout=10)
            return "ok"

        with unittest.mock.patch.object(
                apis, "check_link", side_effect=slow_check):
            self.window.start_link_check()
            self.assertFalse(self.window.busy_dot.isHidden())
            release.set()
            deadline = 100
            while (not self.window.check_button.isEnabled()
                   and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertTrue(self.window.check_button.isEnabled())
        self.assertTrue(self.window.busy_dot.isHidden())


class LinkCheckStopTests(GuiTestCase):
    """U26: link check shows progress and offers cancel."""

    def setUp(self):
        super().setUp()
        self.window.my_list = [core.normalize_item({
            "title": f"Item {n}",
            "url": f"https://example.com/{n}",
            "category": "reasoning", "source": "GitHub",
            "stars": 5, "updated": "", "license": "", "notes": ""})
            for n in range(4)]
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()

    def _wait_for_check_button(self, timeout_ms=5000):
        deadline = timeout_ms // 50
        while (not self.window.check_button.isEnabled()
               and deadline > 0):
            QTest.qWait(50)
            deadline -= 1
        self.assertTrue(self.window.check_button.isEnabled())

    def test_stop_button_enabled_only_while_checking(self):
        self.assertFalse(self.window.stop_check_button.isEnabled())
        release = threading.Event()

        def slow_check(url):
            release.wait(timeout=10)
            return "ok"

        with unittest.mock.patch.object(
                apis, "check_link", side_effect=slow_check):
            self.window.start_link_check()
            self.assertTrue(self.window.stop_check_button.isEnabled())
            release.set()
            self._wait_for_check_button()
        self.assertFalse(self.window.stop_check_button.isEnabled())

    def test_progress_counts_up_as_links_finish(self):
        def slow_check(url):
            time.sleep(0.2)
            return "ok"

        with unittest.mock.patch.object(
                apis, "check_link", side_effect=slow_check):
            self.window.start_link_check()
            deadline = 100
            while (self.window._check_done_count < 1 and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertGreaterEqual(self.window._check_done_count, 1)
            count = self.window._check_done_count
            self.assertEqual(
                self.window.statusBar().currentMessage(),
                f"Checking {count} of 4...")
            self.window.stop_link_check()
            self._wait_for_check_button()

    def test_stop_keeps_checked_rows_and_reports_stopped(self):
        def slow_check(url):
            time.sleep(0.2)
            return "ok"

        with unittest.mock.patch.object(
                apis, "check_link", side_effect=slow_check):
            self.window.start_link_check()
            deadline = 100
            while (self.window._check_done_count < 1 and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertGreaterEqual(self.window._check_done_count, 1)
            self.window.stop_link_check()
            self._wait_for_check_button()
        checked = self.window._check_done_count
        self.assertGreaterEqual(checked, 1)
        self.assertLess(checked, 4)
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            f"Stopped, {checked} of 4 checked.")
        statuses = [item["status"] for item in self.window.my_list]
        self.assertEqual(statuses.count("ok"), checked)
        self.assertEqual(statuses.count(""), 4 - checked)
        self.assertNotIn("checking", statuses)


class PartialSearchTests(GuiTestCase):
    """U32: one source failing never hides the other, and the user
    is told which source did not answer."""

    FAKE_HF = [{"title": "hf", "url": "https://example.com/hf",
                "category": "all", "source": "Hugging Face", "stars": 2,
                "updated": "2026-11-01T00:00:00Z", "description": "",
                "license": ""}]
    FAKE_GH = [{"title": "gh", "url": "https://example.com/gh",
                "category": "all", "source": "GitHub", "stars": 1,
                "updated": "2026-11-01T00:00:00Z", "description": "",
                "license": ""}]

    def _wait_for_search_button(self, timeout_ms=5000):
        deadline = timeout_ms // 50
        while (not self.window.search_button.isEnabled()
               and deadline > 0):
            QTest.qWait(50)
            deadline -= 1
        self.assertTrue(self.window.search_button.isEnabled())

    def test_github_fails_huggingface_results_still_shown(self):
        with unittest.mock.patch.object(
                apis, "search_github",
                side_effect=apis.SearchError("GitHub is down")), \
             unittest.mock.patch.object(
                apis, "search_huggingface", return_value=self.FAKE_HF):
            self.window.start_search()
            self._wait_for_search_button()
        self.assertEqual(self.window.results_table.rowCount(), 1)
        self.assertIn(
            "GitHub search did not answer, showing Hugging Face "
            "results only.",
            self.window.statusBar().currentMessage())

    def test_huggingface_fails_github_results_still_shown(self):
        with unittest.mock.patch.object(
                apis, "search_github", return_value=self.FAKE_GH), \
             unittest.mock.patch.object(
                apis, "search_huggingface",
                side_effect=apis.SearchError("Hugging Face is down")):
            self.window.start_search()
            self._wait_for_search_button()
        self.assertEqual(self.window.results_table.rowCount(), 1)
        self.assertIn(
            "Hugging Face search did not answer, showing GitHub "
            "results only.",
            self.window.statusBar().currentMessage())

    def test_both_fail_shows_error_dialog_naming_both(self):
        with unittest.mock.patch.object(
                apis, "search_github",
                side_effect=apis.SearchError("GitHub is down")), \
             unittest.mock.patch.object(
                apis, "search_huggingface",
                side_effect=apis.SearchError("Hugging Face is down")), \
             unittest.mock.patch.object(
                QMessageBox, "warning") as warning:
            self.window.start_search()
            self._wait_for_search_button()
            deadline = 100
            while not warning.called and deadline > 0:
                QTest.qWait(50)
                deadline -= 1
        self.assertTrue(warning.called)
        message = warning.call_args[0][2]
        self.assertIn("GitHub", message)
        self.assertIn("Hugging Face", message)
        self.assertEqual(self.window.results_table.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
