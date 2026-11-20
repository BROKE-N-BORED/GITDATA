"""GUI smoke tests — offscreen, no live network."""
import os
import socket
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

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

from scout import apis, core, watch
from scout.gui import (
    HOW_TO_USE_SECTIONS, R_SCORE, R_STARS, SETTINGS_APP, SETTINGS_ORG,
    SHORTCUTS, EditItemDialog, MainWindow, SingleInstance,
)

APP = None


def get_app():
    global APP
    if APP is None:
        # Reuse the singleton when another test module already
        # made it: a second QApplication raises (libshiboken).
        APP = QApplication.instance() or QApplication([])
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
            self.window.search_box.setCurrentText("reasoning")
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

    def test_add_found_link_to_huggingface_gets_that_source(self):
        # v0.1.4 T3: the source comes from the URL, so the sniff
        # and download dispatch on it.
        self.window.found_url.setText(
            "https://huggingface.co/datasets/acme/tiny")
        self.window.found_title.setText("Tiny")
        self.window.add_found_link()
        self.assertEqual(self.window.my_list[0]["source"],
                         "Hugging Face")

    def test_add_found_link_to_github_gets_that_source(self):
        self.window.found_url.setText("https://github.com/acme/tiny")
        self.window.found_title.setText("Tiny")
        self.window.add_found_link()
        self.assertEqual(self.window.my_list[0]["source"], "GitHub")

    def test_add_found_link_blocks_a_bad_link(self):
        # v0.1.4 T22: a link that fails the inline check is
        # refused, not added.
        self.window.found_url.setText("not a link")
        self.window.found_title.setText("Tiny")
        self.window.add_found_link()
        self.assertEqual(len(self.window.my_list), 0)
        self.assertIn("does not look like a link",
                      self.window.statusBar().currentMessage())


class SourcesTabTests(GuiTestCase):
    def test_sources_table_lists_defaults(self):
        self.assertEqual(self.window.sources_table.rowCount(), 2)

    def test_add_and_remove_source_round_trip(self):
        self.window.site_name.setText("Example Site")
        self.window.site_url.setText("https://example.com/?q={q}")
        self.window.add_source()
        self.assertEqual(self.window.sources_table.rowCount(), 3)
        self.assertEqual(len(core.load_sources()), 3)
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.window.remove_source("Example Site")
        self.assertEqual(self.window.sources_table.rowCount(), 2)
        self.assertEqual(len(core.load_sources()), 2)

    def test_restore_defaults(self):
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.window.remove_source("Kaggle Datasets")
        self.assertEqual(self.window.sources_table.rowCount(), 1)
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.window.restore_sources()
        self.assertEqual(self.window.sources_table.rowCount(), 2)

    def test_refresh_never_doubles_the_remove_buttons(self):
        # Regression: _refresh_sources_table used to leave the old
        # Remove buttons behind (setCellWidget does not delete the
        # widget it replaces), so each row showed two of them.
        self.window.site_name.setText("Example Site")
        self.window.site_url.setText("https://example.com/?q={q}")
        self.window.add_source()
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
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

    def test_add_source_blocks_a_bad_address(self):
        # v0.1.6 T2, mirroring the Add a link junk-input test: an
        # address that fails the app's own check is refused, not
        # added, the form stays as typed, and the inline message
        # is the same one the Add a link dialog shows.
        self.window.site_name.setText("Bad Site")
        self.window.site_url.setText("not a link")
        self.window.add_source()
        self.assertEqual(self.window.sources_table.rowCount(), 2)
        self.assertEqual(len(core.load_sources()), 2)
        self.assertEqual(self.window.site_name.text(), "Bad Site")
        self.assertEqual(self.window.site_url.text(), "not a link")
        self.assertEqual(self.window.site_url_error.text(),
                         "That does not look like a link. Check it.")
        self.assertFalse(self.window.site_url_error.isHidden())

    def test_save_token_persists_and_never_leaks_to_report(self):
        self.window.token_field.setText("ghp_GUI_TOKEN_999")
        self.window.save_token()
        self.assertEqual(core.get_github_token(), "ghp_GUI_TOKEN_999")
        report = core.debug_report()
        self.assertNotIn("ghp_GUI_TOKEN_999", report)

    def test_add_source_clears_errors_after_success(self):
        # v0.1.4 T8: a failed add leaves red errors; a good add
        # clears them instead of re-validating the emptied form.
        self.window.site_name.setText("")
        self.window.site_url.setText("not a link")
        self.window.add_source()
        self.assertTrue(self.window.site_name_error.text()
                        or self.window.site_url_error.text())
        self.window.site_name.setText("Example Site")
        self.window.site_url.setText("https://example.com/?q={q}")
        self.window.add_source()
        self.assertEqual(self.window.site_name_error.text(), "")
        self.assertEqual(self.window.site_url_error.text(), "")
        self.assertTrue(self.window.site_name_error.isHidden())
        self.assertTrue(self.window.site_url_error.isHidden())

    def test_save_token_names_a_junk_token(self):
        # v0.1.4 T21: a token that fails the check still saves,
        # but the status says so in one line instead of
        # claiming plain success beside a red error.
        self.window.token_field.setText("x")
        self.window.save_token()
        self.assertEqual(core.get_github_token(), "x")
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Saved, but that token looks too short. "
            "Paste the whole token.")


class ListTabTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.window.my_list = [core.normalize_item({
            "title": "A", "url": "https://example.com/a",
            "category": "reasoning", "source": "GitHub",
            "stars": 5, "updated": "", "license": "", "notes": ""})]
        core.save_list(self.window.my_list)
        self.window._note_list_loaded()
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
            self.window.search_box.setCurrentText("reasoning")
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
        self.window._note_list_loaded()
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
        self.window._note_list_loaded()
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

    def test_stop_drops_in_flight_checks_at_once(self):
        # v0.1.4 T27: Stop drops the checks in flight instead of
        # waiting for them, so the run ends at once — and the
        # count only covers checks that finished.
        def slow_check(url):
            time.sleep(2)
            return "ok"

        with unittest.mock.patch.object(
                apis, "check_link", side_effect=slow_check):
            started = time.monotonic()
            self.window.start_link_check()
            QTest.qWait(300)
            self.window.stop_link_check()
            self._wait_for_check_button()
            elapsed = time.monotonic() - started
        # The checks take 2 s each; a stop that waited for them
        # would take at least 2 s.
        self.assertLess(elapsed, 1.5)
        self.assertEqual(self.window._check_done_count, 0)
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Stopped, 0 of 8 checked.")
        self.assertNotIn(
            "checking",
            [item["status"] for item in self.window.my_list])


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
            self.window.search_box.setCurrentText("reasoning")
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
            self.window.search_box.setCurrentText("reasoning")
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
            self.window.search_box.setCurrentText("reasoning")
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

    def test_both_fail_with_the_same_reason_says_it_once(self):
        # v0.1.4 T23: both sources failed for the same cause
        # (the internet is down), so the message is one plain
        # sentence instead of the same sentence twice, once
        # per source.
        reason = ("Could not reach the internet. Check the "
                  "connection and try again.")
        with unittest.mock.patch.object(
                apis, "search_github",
                side_effect=apis.SearchError(reason)), \
             unittest.mock.patch.object(
                apis, "search_huggingface",
                side_effect=apis.SearchError(reason)), \
             unittest.mock.patch.object(
                QMessageBox, "warning") as warning:
            self.window.search_box.setCurrentText("reasoning")
            self.window.start_search()
            self._wait_for_search_button()
            deadline = 100
            while not warning.called and deadline > 0:
                QTest.qWait(50)
                deadline -= 1
        self.assertTrue(warning.called)
        message = warning.call_args[0][2]
        self.assertEqual(message, reason)
        self.assertNotIn("GitHub", message)
        self.assertNotIn(";", message)

    def test_token_rejection_is_named_on_screen(self):
        # v0.1.4 T6: GitHub rejected the token, so the reason is
        # shown in full instead of "did not answer".
        reason = ("GitHub did not accept that token. Check the "
                  "token in Settings, or clear it to search "
                  "without one.")
        self.window._search_done(list(self.FAKE_HF),
                                 [("GitHub", reason)])
        self.assertIn(reason,
                      self.window.statusBar().currentMessage())
        self.assertNotIn("did not answer",
                         self.window.statusBar().currentMessage())

    def test_failed_search_labels_the_empty_results(self):
        # v0.1.4 T29: after a failed search the empty results
        # label says the search failed, not the never-searched
        # text.
        with unittest.mock.patch.object(QMessageBox, "warning"):
            self.window._search_failed("Search failed. GitHub: down")
        self.assertIn("That search failed",
                      self.window.results_empty_label.text())


class EmptySearchTests(GuiTestCase):
    """v0.1.4 T5: an empty search box refuses instead of
    searching for the generic word "dataset"."""

    def test_empty_search_is_refused(self):
        with unittest.mock.patch.object(
                apis, "search_github") as fake_gh, \
             unittest.mock.patch.object(
                apis, "search_huggingface") as fake_hf:
            self.window.start_search()
        fake_gh.assert_not_called()
        fake_hf.assert_not_called()
        self.assertIsNone(self.window.search_worker)
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Type a few words first.")

    def test_empty_watch_is_refused(self):
        self.window.watch_current_search()
        self.assertEqual(watch.load_watches(), [])
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Type a few words first.")


class StellaCheckGuiTests(GuiTestCase):
    """v0.1.4 T13/T17: the score follows the STELLA badge, and
    the check summary counts what it found."""

    def test_score_cell_refreshes_with_the_badge(self):
        item = {"title": "Fresh Data",
                "url": "https://example.com/a",
                "source": "Hugging Face", "stars": 500,
                "downloads": 1000, "updated": "2026-11-14T00:00:00Z",
                "description": "", "license": "MIT", "format": "",
                "stella_status": ""}
        self.window.results = [item]
        self.window._show_results()
        before = self.window.results_table.item(0, R_SCORE)
        self.assertIn("not checked yet", before.toolTip())
        item["stella_status"] = "ready"
        self.window._update_result_stella_cell(0)
        after = self.window.results_table.item(0, R_SCORE)
        self.assertNotIn("not checked yet", after.toolTip())
        self.assertIn("Already in STELLA shape", after.toolTip())

    def test_check_summary_counts_what_it_found(self):
        def _item(status):
            return {"title": status,
                    "url": f"https://example.com/{status}",
                    "stella_status": status}
        self.window._stella_target = "results"
        self.window.stella_worker = SimpleNamespace(items=[
            (0, _item("ready")),
            (1, _item("convert")),
            (2, _item("unknown")),
            (3, _item("no-fields")),
            (4, _item("needs-reader")),
        ])
        self.window._stella_check_done(False)
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "✓ STELLA check done: 1 ready now, 1 need conversion, "
            "1 could not be checked, 1 have no usable files, "
            "1 need the Parquet reader.")
        # The close handler stops every worker it finds; a fake
        # one must not survive the test.
        self.window.stella_worker = None


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
        self.window._note_list_loaded()
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
        self.window._note_list_loaded()
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

    def test_blank_title_blocks_the_save(self):
        # v0.1.4 T26: a blank title stays in the dialog with a
        # message beside the field, instead of saving while
        # silently keeping the old title.
        item = core.normalize_item(
            {"title": "A", "url": "https://example.com/a",
             "category": "reasoning"})
        dialog = EditItemDialog(item, self.window)
        dialog.title_field.setText("   ")
        dialog.accept()
        self.assertEqual(dialog.result(),
                         QDialog.DialogCode.Rejected)
        self.assertEqual(dialog.title_error.text(),
                         "Give the link a title.")
        dialog.close()

    def test_bad_link_blocks_the_save(self):
        # v0.1.4 T22: a link that fails the inline check blocks
        # the edit, as on the add form.
        item = core.normalize_item(
            {"title": "A", "url": "https://example.com/a",
             "category": "reasoning"})
        dialog = EditItemDialog(item, self.window)
        dialog.url_field.setText("not a link")
        dialog.accept()
        self.assertEqual(dialog.result(),
                         QDialog.DialogCode.Rejected)
        self.assertIn("does not look like a link",
                      dialog.url_error.text())
        dialog.close()


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
        self.window._note_list_loaded()
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


class SearchButtonsDisabledTests(GuiTestCase):
    """v0.1.3 T17: Add and Check wait while a search runs."""

    def test_add_buttons_disabled_during_search(self):
        def slow_github(*args, **kwargs):
            time.sleep(1)
            return []

        with unittest.mock.patch.object(
                apis, "search_github", side_effect=slow_github), \
                unittest.mock.patch.object(
                    apis, "search_huggingface", return_value=[]):
            self.window.search_box.setCurrentText("math")
            self.window.start_search()
            self.assertFalse(
                self.window.add_selected_button.isEnabled())
            self.assertFalse(self.window.add_all_button.isEnabled())
            self.assertFalse(
                self.window.stella_check_button.isEnabled())
            deadline = 100
            while (not self.window.search_button.isEnabled()
                   and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertTrue(self.window.search_button.isEnabled())
        self.assertTrue(self.window.add_selected_button.isEnabled())
        self.assertTrue(self.window.add_all_button.isEnabled())
        self.assertTrue(self.window.stella_check_button.isEnabled())


class StellaCheckStopTests(GuiTestCase):
    """v0.1.3 T18: the STELLA check shows progress and offers Stop."""

    def setUp(self):
        super().setUp()
        results = [core.normalize_item({
            "title": f"Item {n}",
            "url": f"https://example.com/{n}",
            "category": "reasoning", "source": "GitHub",
            "stars": 5, "updated": "2026-11-01T00:00:00Z",
            "license": "", "notes": ""}) for n in range(4)]
        self.window._search_done(results)
        self.window.results_table.selectAll()

    def test_stop_button_and_count(self):
        def slow_sniff(item, token=""):
            time.sleep(0.2)
            return {"status": "ready", "files": ["train.jsonl"],
                    "total_size": 10, "detail": "ok"}

        with unittest.mock.patch.object(
                apis, "sniff_item", side_effect=slow_sniff):
            self.window.start_stella_check("results")
            self.assertTrue(
                self.window.stop_stella_button.isEnabled())
            self.assertTrue(
                self.window.list_stop_stella_button.isEnabled())
            self.assertEqual(self.window.statusBar().currentMessage(),
                             "Checking 0 of 4...")
            deadline = 100
            while (self.window._stella_done_count < 1
                   and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertGreaterEqual(self.window._stella_done_count, 1)
            self.assertEqual(self.window.statusBar().currentMessage(),
                             f"Checking "
                             f"{self.window._stella_done_count} of 4...")
            self.window.stop_stella_check()
            deadline = 100
            while (not self.window.stella_check_button.isEnabled()
                   and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertTrue(self.window.stella_check_button.isEnabled())
        checked = self.window._stella_done_count
        self.assertGreaterEqual(checked, 1)
        self.assertLess(checked, 4)
        self.assertEqual(self.window.statusBar().currentMessage(),
                         f"Stopped, {checked} of 4 checked.")
        self.assertFalse(self.window.stop_stella_button.isEnabled())
        self.assertFalse(
            self.window.list_stop_stella_button.isEnabled())


class SourcesConfirmTests(GuiTestCase):
    """v0.1.3 T19: removing or restoring websites asks first."""

    def test_remove_cancelled_keeps_the_site(self):
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            self.window.remove_source("Kaggle Datasets")
        self.assertEqual(len(core.load_sources()), 2)
        self.assertFalse(self.window.undo_site_button.isEnabled())

    def test_undo_restores_removed_site_in_place(self):
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.window.remove_source("Hugging Face Datasets")
        self.assertEqual([s["name"] for s in core.load_sources()],
                         ["Kaggle Datasets"])
        self.assertTrue(self.window.undo_site_button.isEnabled())
        self.window.undo_remove_source()
        self.assertEqual([s["name"] for s in core.load_sources()],
                         ["Hugging Face Datasets", "Kaggle Datasets"])
        self.assertFalse(self.window.undo_site_button.isEnabled())

    def test_restore_cancelled_keeps_custom_sites(self):
        self.window.site_name.setText("Example Site")
        self.window.site_url.setText("https://example.com/?q={q}")
        self.window.add_source()
        self.assertEqual(len(core.load_sources()), 3)
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            self.window.restore_sources()
        self.assertEqual(len(core.load_sources()), 3)


class LoadMoreTests(GuiTestCase):
    """v0.1.3 T22: Load more appends the next page."""

    def _results(self, titles):
        return [core.normalize_item({
            "title": title, "url": f"https://example.com/{title}",
            "category": "reasoning", "source": "GitHub",
            "stars": 5, "updated": "2026-11-01T00:00:00Z",
            "license": "", "notes": ""}) for title in titles]

    def test_load_more_appends_and_dedups(self):
        self.window._search_done(self._results(["A", "B"]))
        self.assertEqual(self.window.results_table.rowCount(), 2)
        self.assertTrue(self.window.load_more_button.isEnabled())
        # Page 2 repeats A and adds C.
        self.window._load_more_done(self._results(["A", "C"]))
        titles = [self.window.results_table.item(row, 0).text()
                  for row in range(self.window.results_table.rowCount())]
        self.assertEqual(sorted(titles), ["A", "B", "C"])
        self.assertTrue(self.window.load_more_button.isEnabled())
        self.assertIn("Loaded 1 more result",
                      self.window.statusBar().currentMessage())

    def test_load_more_with_nothing_new_says_so(self):
        self.window._search_done(self._results(["A"]))
        self.window._load_more_done(self._results(["A"]))
        self.assertEqual(self.window.results_table.rowCount(), 1)
        self.assertFalse(self.window.load_more_button.isEnabled())
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "No more results.")


class EditDialogCategoryTests(GuiTestCase):
    """v0.1.3 T31/T43: an untouched save keeps category and state."""

    def _dialog(self, **fields):
        item = core.normalize_item(
            {"title": "A", "url": "https://example.com/a", **fields})
        dialog = EditItemDialog(item, self.window)
        self.addCleanup(dialog.close)
        return dialog

    def test_custom_category_survives_untouched_save(self):
        dialog = self._dialog(category="my_cat")
        self.assertEqual(dialog.values()["category"], "my_cat")

    def test_mixed_case_category_survives_untouched_save(self):
        dialog = self._dialog(category="MyCat")
        self.assertEqual(dialog.values()["category"], "MyCat")

    def test_changing_the_category_still_works(self):
        dialog = self._dialog(category="my_cat")
        dialog.category_field.setCurrentText("Reasoning")
        self.assertEqual(dialog.values()["category"], "reasoning")

    def test_unknown_state_is_kept_not_reset(self):
        dialog = self._dialog(state="waiting-review")
        self.assertEqual(dialog.values()["state"], "waiting-review")


class RenameCollectionTests(GuiTestCase):
    """v0.1.3 T36: renaming saves the active list first."""

    def test_rename_keeps_in_memory_changes(self):
        self.window.my_list = [core.normalize_item({
            "title": "A", "url": "https://example.com/a"})]
        core.save_list(self.window.my_list)
        # An in-memory-only change: the status was never saved.
        self.window.my_list[0]["status"] = "ok"
        with unittest.mock.patch.object(
                QInputDialog, "getText",
                return_value=("Renamed", True)):
            self.window.rename_collection()
        _active, names = core.load_collections()
        self.assertIn("Renamed", names)
        self.assertEqual(core.load_list()[0]["status"], "ok")


class TimezoneSortTests(GuiTestCase):
    """v0.1.3 T37: newest first means newest instant, not string."""

    def test_timezone_offsets_do_not_misorder(self):
        results = [
            {"title": "old", "url": "https://example.com/old",
             "category": "reasoning", "source": "GitHub", "stars": 5,
             "updated": "2026-01-01T00:00:00+12:00",
             "description": "", "license": ""},
            {"title": "new", "url": "https://example.com/new",
             "category": "reasoning", "source": "GitHub", "stars": 5,
             "updated": "2025-12-31T13:00:00Z",
             "description": "", "license": ""},
        ]
        self.window.active_check.setChecked(False)
        self.window._search_done(results)
        titles = [self.window.results_table.item(row, 0).text()
                  for row in range(self.window.results_table.rowCount())]
        self.assertEqual(titles, ["new", "old"])


class FieldExplanationTests(GuiTestCase):
    """v0.1.3 T39: every field explains itself underneath."""

    def test_list_tab_explanations_are_present(self):
        labels = [child.text()
                  for child in self.window.findChildren(QLabel)]
        self.assertIn("Lists keep your links grouped. Switch lists, "
                      "make a new one, rename it, or delete it.",
                      labels)
        self.assertIn("Type to narrow this list by title or link. "
                      "State keeps only links in one step of the "
                      "pipeline.", labels)
        self.assertIn("Export saves this list as a file. Import reads "
                      "one back in. Send to STELLA writes the manifest "
                      "where STELLA can find it.", labels)

    def test_edit_dialog_explanations_are_present(self):
        item = core.normalize_item(
            {"title": "A", "url": "https://example.com/a"})
        dialog = EditItemDialog(item, self.window)
        self.addCleanup(dialog.close)
        labels = [child.text() for child in dialog.findChildren(QLabel)]
        self.assertIn("The name shown in My List.", labels)
        self.assertIn("Pick one, or type your own category.", labels)
        self.assertIn("Anything to remember about it.", labels)
        self.assertIn("Where this link is in the pipeline: found, "
                      "downloaded, converted, or in STELLA.", labels)


class EmptySearchMessageTests(GuiTestCase):
    """v0.1.3 T41: no results means no 'Pick rows' suffix."""

    def test_empty_search_has_no_pick_rows_suffix(self):
        self.window._search_done([])
        self.assertEqual(self.window.results_table.rowCount(), 0)
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Found 0 results.")

    def test_results_still_get_the_pick_rows_suffix(self):
        results = [core.normalize_item({
            "title": "A", "url": "https://example.com/a",
            "category": "reasoning", "source": "GitHub",
            "stars": 5, "updated": "2026-11-01T00:00:00Z",
            "license": "", "notes": ""})]
        self.window._search_done(results)
        self.assertIn("Pick rows and add them to My List.",
                      self.window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
