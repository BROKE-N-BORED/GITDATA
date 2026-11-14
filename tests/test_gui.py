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
    QApplication, QCheckBox, QDialog, QGroupBox, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QStatusBar,
    QTextEdit, QWidget,
)
from PySide6.QtTest import QTest
from PySide6.QtCore import QModelIndex, QSettings, Qt

from scout import apis, core
from scout.gui import (
    HOW_TO_USE_SECTIONS, R_STARS, SETTINGS_APP, SETTINGS_ORG,
    SHORTCUTS, EditItemDialog, MainWindow, SingleInstance,
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
        self.assertEqual(self.window.sources_table.rowCount(), 2)

    def test_add_and_remove_source_round_trip(self):
        self.window.site_name.setText("Example Site")
        self.window.site_url.setText("https://example.com/?q={q}")
        self.window.add_source()
        self.assertEqual(self.window.sources_table.rowCount(), 3)
        self.assertEqual(len(core.load_sources()), 3)
        self.window.remove_source("Example Site")
        self.assertEqual(self.window.sources_table.rowCount(), 2)
        self.assertEqual(len(core.load_sources()), 2)

    def test_restore_defaults(self):
        self.window.remove_source("Kaggle Datasets")
        self.assertEqual(self.window.sources_table.rowCount(), 1)
        self.window.restore_sources()
        self.assertEqual(self.window.sources_table.rowCount(), 2)

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
        self.assertEqual(len(buttons), 2)
        for row in range(2):
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
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
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
        menus = {a.text(): a.menu()
                 for a in self.window.menuBar().actions()}
        self.assertIn("Help", menus)
        help_menu = menus["Help"]
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
            for n in range(8)]
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
                f"Checking {count} of 8...")
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
        self.assertLess(checked, 8)
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            f"Stopped, {checked} of 8 checked.")
        statuses = [item["status"] for item in self.window.my_list]
        self.assertEqual(statuses.count("ok"), checked)
        self.assertEqual(statuses.count(""), 8 - checked)
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


class MenusTests(GuiTestCase):
    """T23: real menus, not just Help."""

    def _menus(self):
        return {action.text(): action.menu()
                for action in self.window.menuBar().actions()}

    def test_four_menus_exist(self):
        self.assertEqual(set(self._menus()),
                         {"File", "Edit", "View", "Help"})

    def test_file_menu_has_export_import_send_exit(self):
        actions = {a.text()
                   for a in self._menus()["File"].actions()}
        self.assertIn("Export as Markdown list", actions)
        self.assertIn("Export as JSON", actions)
        self.assertIn("Export STELLA manifest", actions)
        self.assertIn("Import a list", actions)
        self.assertIn("Send to STELLA", actions)
        self.assertIn("Exit", actions)

    def test_edit_menu_has_undo_remove_clear(self):
        actions = {a.text()
                   for a in self._menus()["Edit"].actions()}
        self.assertIn("Undo remove", actions)
        self.assertIn("Remove selected", actions)
        self.assertIn("Clear My List", actions)

    def test_view_menu_jumps_to_tabs(self):
        actions = {a.text(): a
                   for a in self._menus()["View"].actions()}
        actions["My List tab"].trigger()
        self.assertEqual(
            self.window.tabs.currentIndex(),
            self.window.list_tab_index)
        actions["Search tab"].trigger()
        self.assertEqual(self.window.tabs.currentIndex(), 0)

    def test_help_menu_has_updates_and_about(self):
        actions = {a.text()
                   for a in self._menus()["Help"].actions()}
        self.assertIn("How to Use", actions)
        self.assertIn("Check for updates", actions)
        self.assertIn("About Dataset Scout", actions)


class ResultsSortingTests(GuiTestCase):
    """T4/T11: results are newest first and sortable."""

    def _items(self):
        return [core.normalize_item({
            "title": title, "url": f"https://example.com/{title}",
            "category": "reasoning", "source": "GitHub",
            "stars": stars, "updated": updated,
            "license": "", "notes": ""})
            for title, stars, updated in
            [("Zulu", 5, "2026-01-01"), ("Alpha", 100, "2026-11-01"),
             ("Mike", 20, "2026-06-01")]]

    def _shown_titles(self):
        return [self.window.results_table.item(row, 0).text()
                for row in range(self.window.results_table.rowCount())]

    def test_results_show_newest_first(self):
        self.window._search_done(self._items())
        self.assertEqual(self._shown_titles(), ["Alpha", "Mike", "Zulu"])

    def test_sort_by_stars_is_numeric(self):
        self.window._search_done(self._items())
        self.window.results_table.sortByColumn(
            R_STARS, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self._shown_titles(), ["Zulu", "Mike", "Alpha"])

    def test_sorting_is_enabled(self):
        self.assertTrue(self.window.results_table.isSortingEnabled())


class RedesignWidgetsTests(GuiTestCase):
    """T16/T25/T26/T27/T32: the redesigned Search and List tabs."""

    def test_license_combo_has_choices(self):
        texts = [self.window.license_combo.itemText(n)
                 for n in range(self.window.license_combo.count())]
        self.assertIn("Any license", texts)
        self.assertIn("Apache-2.0", texts)
        self.assertIn("MIT", texts)

    def test_stella_check_buttons_exist(self):
        self.assertIsNotNone(self.window.stella_check_button)
        self.assertIsNotNone(self.window.list_stella_button)

    def test_stats_label_counts_links(self):
        self.window.my_list = [
            core.normalize_item({"title": "A",
                                 "url": "https://example.com/a",
                                 "status": "ok"}),
            core.normalize_item({"title": "B",
                                 "url": "https://example.com/b",
                                 "status": "dead"}),
        ]
        self.window._refresh_stats()
        text = self.window.stats_label.text()
        self.assertIn("Links: 2", text)
        self.assertIn("Working: 1", text)
        self.assertIn("Dead: 1", text)

    def test_rate_label_exists(self):
        self.assertIn("GitHub", self.window.rate_label.text())

    def test_empty_labels_visible_when_empty(self):
        self.assertFalse(self.window.results_empty_label.isHidden())
        self.assertFalse(self.window.list_empty_label.isHidden())

    def test_skeleton_shows_loading_rows(self):
        self.window._show_skeleton()
        self.assertEqual(self.window.results_table.rowCount(), 5)
        self.assertEqual(
            self.window.results_table.item(0, 0).text(), "Loading...")
        self.window._clear_skeleton()
        self.assertEqual(self.window.results_table.rowCount(), 0)

    def test_shortcuts_come_from_one_registry(self):
        keys = [key for key, _desc, _scope, _action in SHORTCUTS]
        self.assertIn("Ctrl+F", keys)
        self.assertIn("Ctrl+E", keys)
        self.assertIn("Delete", keys)
        # The How to Use text is built from the same registry.
        from scout.gui import HOW_TO_USE_TEXT
        for key in keys:
            self.assertIn(key, HOW_TO_USE_TEXT)


class CollectionsGuiTests(GuiTestCase):
    """T30: collections in the window."""

    def _seed(self):
        core.save_collections("One", [
            {"name": "One", "items": [
                {"title": "A", "url": "https://example.com/a"}]},
            {"name": "Two", "items": [
                {"title": "B", "url": "https://example.com/b"}]},
        ])
        self.window._refresh_collection_combo()

    def test_combo_lists_collections(self):
        self._seed()
        names = [self.window.collection_combo.itemText(n)
                 for n in range(self.window.collection_combo.count())]
        self.assertEqual(names, ["One", "Two"])

    def test_switching_collections_shows_its_items(self):
        self._seed()
        self.window.collection_combo.setCurrentText("Two")
        self.assertEqual(
            [item["title"] for item in self.window.my_list], ["B"])
        self.assertEqual(
            self.window.list_table.item(0, 0).text(), "B")

    def test_new_collection_from_dialog(self):
        with unittest.mock.patch.object(
                QInputDialog, "getText", return_value=("Work", True)):
            self.window.new_collection()
        _active, names = core.load_collections()
        self.assertIn("Work", names)
        self.assertEqual(self.window._active_collection, "Work")

    def test_delete_last_collection_is_refused(self):
        self.window.delete_collection()
        self.assertIn("last list",
                      self.window.statusBar().currentMessage())


class UndoTests(GuiTestCase):
    """T18: remove asks first and can be undone."""

    def setUp(self):
        super().setUp()
        self.window.my_list = [core.normalize_item({
            "title": title, "url": f"https://example.com/{title}",
            "category": "reasoning", "source": "GitHub",
            "stars": 5, "updated": "", "license": "", "notes": ""})
            for title in ("A", "B")]
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()

    def _remove_row(self, row):
        self.window.list_table.selectRow(row)
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.window.remove_selected()

    def test_undo_restores_removed_row_in_place(self):
        self._remove_row(0)
        self.assertEqual(
            [item["title"] for item in self.window.my_list], ["B"])
        self.assertTrue(self.window.undo_button.isEnabled())
        self.window.undo_remove()
        self.assertEqual(
            [item["title"] for item in self.window.my_list], ["A", "B"])
        self.assertFalse(self.window.undo_button.isEnabled())

    def test_remove_cancelled_keeps_the_row(self):
        self.window.list_table.selectRow(0)
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            self.window.remove_selected()
        self.assertEqual(len(self.window.my_list), 2)


class EditDialogTests(GuiTestCase):
    """T12: edit a saved link in place."""

    def test_dialog_values_map_the_category(self):
        item = core.normalize_item(
            {"title": "A", "url": "https://example.com/a",
             "category": "reasoning"})
        dialog = EditItemDialog(item, self.window)
        dialog.title_field.setText("New title")
        dialog.category_field.setCurrentText("My Custom")
        dialog.notes_field.setText("a note")
        values = dialog.values()
        self.assertEqual(values["title"], "New title")
        self.assertEqual(values["category"], "my-custom")
        self.assertEqual(values["notes"], "a note")
        dialog.close()

    def test_edit_selected_updates_the_item(self):
        self.window.my_list = [core.normalize_item({
            "title": "A", "url": "https://example.com/a",
            "category": "reasoning", "source": "GitHub",
            "stars": 5, "updated": "", "license": "", "notes": ""})]
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()
        self.window.list_table.selectRow(0)
        fake = unittest.mock.Mock()
        fake.exec.return_value = QDialog.DialogCode.Accepted
        fake.values.return_value = {
            "title": "Renamed", "category": "training",
            "notes": "edited", "state": "downloaded"}
        with unittest.mock.patch(
                "scout.gui.EditItemDialog", return_value=fake):
            self.window.edit_selected()
        item = self.window.my_list[0]
        self.assertEqual(item["title"], "Renamed")
        self.assertEqual(item["category"], "training")
        self.assertEqual(item["notes"], "edited")
        self.assertEqual(item["state"], "downloaded")


class SearchCacheGuiTests(GuiTestCase):
    """T14: a repeated search answers from the cache."""

    def _wait_for_search(self, timeout_ms=5000):
        deadline = timeout_ms // 50
        while (not self.window.search_button.isEnabled()
               and deadline > 0):
            QTest.qWait(50)
            deadline -= 1
        self.assertTrue(self.window.search_button.isEnabled())

    def test_second_identical_search_uses_cache(self):
        gh = unittest.mock.Mock(return_value=[{
            "title": "gh", "url": "https://example.com/gh",
            "category": "all", "source": "GitHub", "stars": 1,
            "updated": "2026-11-01", "license": "",
            "description": ""}])
        hf = unittest.mock.Mock(return_value=[])
        with unittest.mock.patch.object(apis, "search_github", gh), \
                unittest.mock.patch.object(apis, "search_huggingface", hf):
            self.window.search_box.setCurrentText("math")
            self.window.start_search()
            self._wait_for_search()
            self.assertEqual(gh.call_count, 1)
            self.assertEqual(hf.call_count, 1)
            self.assertEqual(self.window.results_table.rowCount(), 1)
            self.window.start_search()
            self.assertEqual(gh.call_count, 1)
            self.assertEqual(hf.call_count, 1)
            self.assertIn("from the last hour",
                          self.window.statusBar().currentMessage())


class SearchStopGuiTests(GuiTestCase):
    """T3: the search Stop button ends a slow search."""

    def test_stop_button_stops_a_slow_search(self):
        def slow_github(*args, **kwargs):
            time.sleep(2)
            return []

        with unittest.mock.patch.object(
                apis, "search_github", side_effect=slow_github), \
                unittest.mock.patch.object(
                    apis, "search_huggingface", return_value=[]):
            self.window.search_box.setCurrentText("math")
            self.window.start_search()
            self.assertTrue(self.window.stop_search_button.isEnabled())
            self.window.stop_search()
            deadline = 100
            while (not self.window.search_button.isEnabled()
                   and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertTrue(self.window.search_button.isEnabled())
            self.assertEqual(
                self.window.statusBar().currentMessage(),
                "Search stopped.")


class ReorderTests(GuiTestCase):
    """T28: rows move up, down, and by drag."""

    def setUp(self):
        super().setUp()
        self.window.my_list = [core.normalize_item({
            "title": title, "url": f"https://example.com/{title}",
            "category": "reasoning", "source": "GitHub",
            "stars": 5, "updated": "", "license": "", "notes": ""})
            for title in ("A", "B", "C")]
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()

    def _titles(self):
        return [item["title"] for item in self.window.my_list]

    def test_move_down_swaps_rows(self):
        self.window.list_table.selectRow(0)
        self.window._move_selected(1)
        self.assertEqual(self._titles(), ["B", "A", "C"])
        self.assertEqual(
            [item["title"] for item in core.load_list()],
            ["B", "A", "C"])

    def test_move_up_at_top_is_a_noop(self):
        self.window.list_table.selectRow(0)
        self.window._move_selected(-1)
        self.assertEqual(self._titles(), ["A", "B", "C"])

    def test_drag_reorders_and_saves(self):
        model = self.window.list_table.model()
        moved = model.moveRows(QModelIndex(), 0, 1, QModelIndex(), 3)
        self.assertTrue(moved)
        QTest.qWait(100)
        self.assertEqual(self._titles(), ["B", "C", "A"])
        self.assertEqual(
            [item["title"] for item in core.load_list()],
            ["B", "C", "A"])


if __name__ == "__main__":
    unittest.main()
