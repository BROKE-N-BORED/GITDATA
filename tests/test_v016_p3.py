"""Regression tests for the v0.1.6 P3 round (T17-T39).

Offscreen, no live network. Each class names the task item it
pins down.
"""
import copy
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from scout import core, download, stella_link, updater, watch
from scout import queue as dlqueue
from scout.gui import (
    AddLinkDialog, DownloadDialog, EditItemDialog, HOW_TO_USE_TEXT,
    MainWindow, QueueDialog, SHORTCUTS, ShortcutsDialog, WatchDialog,
    _download_dir,
)

APP = None


def get_app():
    global APP
    if APP is None:
        APP = QApplication.instance() or QApplication([])
    return APP


def _result(i=0, updated="2026-11-01T00:00:00Z", title=None):
    return {
        "title": title or f"owner/dataset-{i}",
        "source": "Hugging Face",
        "stars": 120,
        "downloads": 500,
        "updated": updated,
        "description": "A dataset.",
        "license": "Apache-2.0",
        "format": "jsonl",
        "url": f"https://huggingface.co/datasets/owner/ds-{i}",
        "category": "reasoning",
    }


class GuiCase(unittest.TestCase):
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

    # Popup helpers, mirroring tests/test_update_check.py.
    def _arm_popup_click(self, button_text, sink):
        def poll():
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, QMessageBox) and widget.isVisible():
                    for button in widget.buttons():
                        if button.text() == button_text:
                            sink.append(widget)
                            button.click()
                            self._click_timer.stop()
                            return

        self._click_timer = QTimer()
        self._click_timer.setInterval(25)
        self._click_timer.timeout.connect(poll)
        self._click_timer.start()
        self.addCleanup(self._click_timer.stop)


class ManualUpdateReceiptTests(GuiCase):
    """T17: a manual check that ends in the popup leaves a
    status-bar receipt, not "Checking for updates..."."""

    def test_manual_popup_dismissal_leaves_a_receipt(self):
        from scout import apis

        clicked = []
        self._arm_popup_click("Not now", clicked)
        with unittest.mock.patch.object(
                apis, "latest_release_tag", return_value="v9.9.9"):
            self.window.check_for_updates()
            deadline = time.monotonic() + 5
            while not clicked and time.monotonic() < deadline:
                QTest.qWait(50)
        self.assertTrue(clicked, "the update popup never showed")
        message = self.window.statusBar().currentMessage()
        self.assertEqual(message, "Dataset Scout 9.9.9 is out.")
        self.assertNotIn("Checking for updates", message)


class VersionFormatTests(GuiCase):
    """T19: one version format, no "v", matching the About box."""

    def test_display_version_strips_the_v(self):
        from scout.gui import _display_version

        self.assertEqual(_display_version("v0.1.6"), "0.1.6")
        self.assertEqual(_display_version("0.1.6"), "0.1.6")
        self.assertEqual(updater.display_version("v0.1.6"), "0.1.6")

    def test_restart_prompt_has_no_v(self):
        clicked = []
        self._arm_popup_click("Later", clicked)
        result = updater.ask_restart(self.window, "v9.9.9")
        self.assertFalse(result)
        self.assertTrue(clicked, "the restart prompt never showed")
        text = clicked[0].text()
        self.assertIn("9.9.9", text)
        self.assertNotIn("v9.9.9", text)


class CancelledUpdateTests(GuiCase):
    """T18: a cancelled update download says what happened."""

    def test_cancel_shows_the_plain_line(self):
        with unittest.mock.patch.object(
                updater, "download_release",
                side_effect=updater.UpdateCancelled), \
                unittest.mock.patch.object(
                    updater, "_show_info") as info, \
                unittest.mock.patch.object(
                    updater, "_show_error") as error:
            result = updater.start_update(self.window, "v9.9.9")
        self.assertFalse(result)
        error.assert_not_called()
        info.assert_called_once()
        self.assertEqual(info.call_args.args[1],
                         "Update cancelled. Nothing was changed.")


class StellaBackupNameTests(unittest.TestCase):
    """T20: the Replace success message names the dataset backup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "stella"
        profile_dir = self.home / "profiles" / "demo"
        profile_dir.mkdir(parents=True)
        (profile_dir / "profile.json").write_text(json.dumps({
            "stella_version": "0.24.0", "kind": "profile",
            "name": "demo", "display_name": "demo",
        }), encoding="utf-8")
        self.project_dir = profile_dir / "projects" / "w2-proj"
        self.project_dir.mkdir(parents=True)
        (self.project_dir / "project.json").write_text(json.dumps({
            "stella_version": "0.24.0", "kind": "project",
            "name": "w2-proj", "display_name": "w2-proj",
            "profile": "demo", "dataset_ref": "w2-proj.jsonl",
            "settings": dict(stella_link.STELLA_DEFAULT_SETTINGS),
            "settings_source": "manual",
            "metamorph": {"mode": "morph", "notes": ""},
            "status": "draft", "created": "2026-11-01T00:00:00+00:00",
        }), encoding="utf-8")
        old = ('{"prompt": "old", "completion": "data"}\n'
               '{"prompt": "older", "completion": "data"}\n')
        (self.project_dir / "w2-proj.jsonl").write_text(
            old, encoding="utf-8")
        self.jsonl = Path(self.tmp.name) / "new.jsonl"
        self.jsonl.write_text(
            '{"prompt": "a", "completion": "b"}\n'
            '{"prompt": "c", "completion": "d"}\n', encoding="utf-8")

    def test_message_names_the_dataset_backup(self):
        result = stella_link.link_dataset(
            self.jsonl, "demo", "w2-proj", home=self.home,
            replace=True)
        self.assertTrue(result["ok"], result["message"])
        self.assertIn("w2-proj.jsonl.scout-bak", result["message"])
        self.assertIsNotNone(result["dataset_backup"])
        self.assertTrue(
            Path(result["dataset_backup"]).is_file())
        self.assertTrue(
            (self.project_dir / "w2-proj.jsonl.scout-bak").is_file())
        self.assertTrue(
            (self.project_dir / "project.json.scout-bak").is_file())


class WatchFirstRunTests(GuiCase):
    """T21: the first run says it set the starting point, and
    watch outcomes land in the main window status bar."""

    def test_first_run_sets_baseline_in_watch_data(self):
        item = watch.new_watch("math problems", "all")
        fresh = [_result(0), _result(1)]
        new_items = watch.apply_watch_results(item, fresh)
        self.assertEqual(new_items, [])
        self.assertTrue(item["just_started"])
        self.assertEqual(item["baseline_count"], 2)

    def test_first_run_status_bar_and_dialog_wording(self):
        saved = watch.new_watch("math problems", "all")
        watch.save_watches([saved])
        finished = copy.deepcopy(saved)
        watch.apply_watch_results(finished, [_result(0), _result(1)])
        self.window._on_watch_finished(finished, [], [])
        message = self.window.statusBar().currentMessage()
        self.assertIn("Watch started: 2 results saved as the "
                      "starting point.", message)
        dialog = WatchDialog(self.window)
        self.addCleanup(dialog.close)
        dialog.watch_list.setCurrentRow(0)
        self.assertIn("starting point", dialog.new_label.text())
        self.assertNotIn("Nothing new", dialog.new_label.text())

    def test_quiet_run_still_reports_to_the_status_bar(self):
        saved = watch.new_watch("math problems", "all")
        saved["last_run"] = "2026-11-18T00:00:00+00:00"
        saved["last_seen"] = [_result(0)["url"]]
        watch.save_watches([saved])
        finished = copy.deepcopy(saved)
        new_items = watch.apply_watch_results(finished, [_result(0)])
        self.window._on_watch_finished(finished, new_items, [])
        self.assertIn("nothing new",
                      self.window.statusBar().currentMessage())


class SkipReasonOrderTests(unittest.TestCase):
    """T22: the dominant skip reason leads the per-file line."""

    def test_text_only_reason_comes_first(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source = Path(tmp.name) / "rows.jsonl"
        source.write_text(
            '{"text": "one"}\n{"text": "two"}\n{"text": "three"}\n',
            encoding="utf-8")
        ok, message, rows = download._convert_file(
            source, Path(tmp.name) / "out.jsonl")
        self.assertFalse(ok)
        self.assertEqual(rows, 0)
        lead = "3 rows skipped: text only, no prompt to pair it with"
        self.assertIn(lead, message)
        self.assertLess(message.index(lead),
                        message.index("STELLA needs at least 2"))


class HiddenFilterTests(GuiCase):
    """T23: name the hiding filter, offer one-click relax, and
    leave the saved default exactly as it was."""

    def _search_with_old_results(self):
        fresh = [_result(0), _result(1)]
        old = [_result(7, updated="2020-01-01T00:00:00Z"),
               _result(8, updated="2020-06-01T00:00:00Z"),
               _result(9, updated="2019-01-01T00:00:00Z")]
        self.window._search_done(fresh + old, [])

    def test_label_names_active_only(self):
        self.window.show()
        self._search_with_old_results()
        QTest.qWait(50)
        self.assertTrue(self.window.filter_hidden_label.isVisible())
        text = self.window.filter_hidden_label.text()
        self.assertIn("3 results hidden by filters", text)
        self.assertIn("Active only", text)
        self.assertTrue(self.window.show_hidden_button.isVisible())

    def test_show_anyway_relaxes_for_this_search_only(self):
        self._search_with_old_results()
        self.assertEqual(len(self.window.results), 2)
        self.window._show_hidden_anyway()
        self.assertEqual(len(self.window.results), 5)
        self.assertFalse(self.window.active_check.isChecked())
        # The saved choice did not move: the default is untouched.
        self.assertTrue(
            core.load_config().get("active_only", True))

    def test_default_is_still_active_only_12_months(self):
        self.assertTrue(self.window.active_check.isChecked())
        self.assertEqual(self.window.window_combo.currentText(),
                         "12 months")


class QueuePauseTests(GuiCase):
    """T24: Pause settles the row at once and stage lines stop."""

    def test_pause_settles_the_row(self):
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        entries, _added = dlqueue.enqueue([], _result(0))
        dlqueue.save_queue(entries)
        dialog.refresh()
        entry = dialog.entries[0]
        entry["state"] = dlqueue.RUNNING
        dialog.queue_table.item(0, 1).setText("Downloading")

        class FakeWorker:
            def __init__(self):
                self.stopped = False

            def isRunning(self):  # noqa: N802 - Qt naming
                return True

            def request_stop(self):
                self.stopped = True

            def wait(self, _ms=0):
                return True

        fake = FakeWorker()
        dialog.worker = fake
        dialog.pause_queue()
        self.assertTrue(fake.stopped)
        self.assertEqual(dialog.queue_table.item(0, 1).text(),
                         "Paused")
        dialog._on_progress(entry["id"], "Downloading owner/ds-0",
                            10, 100)
        self.assertNotIn("Downloading owner/ds-0",
                         dialog.log_view.toPlainText())


class HelpTextTests(unittest.TestCase):
    """T25/T31/T32/T33/T35: the guide says what the app does."""

    def test_no_terminal_sentence_in_self_test_help(self):
        self.assertNotIn("scout-cli --self-test runs", HOW_TO_USE_TEXT)
        self.assertNotIn("from a terminal", HOW_TO_USE_TEXT)

    def test_installer_has_no_command_line_tail(self):
        text = (Path(__file__).resolve().parent.parent
                / "install.sh").read_text(encoding="utf-8")
        self.assertNotIn("Command line is available too", text)

    def test_row_density_is_explained(self):
        self.assertIn("Compact rows", HOW_TO_USE_TEXT)
        self.assertIn("Comfortable rows", HOW_TO_USE_TEXT)

    def test_all_four_score_bands_are_explained(self):
        self.assertIn("Worth a look", HOW_TO_USE_TEXT)
        self.assertIn("80 or more", HOW_TO_USE_TEXT)
        self.assertIn("Promising", HOW_TO_USE_TEXT)
        self.assertIn("50 to 79", HOW_TO_USE_TEXT)
        self.assertIn("Maybe", HOW_TO_USE_TEXT)
        self.assertIn("25 to 49", HOW_TO_USE_TEXT)
        self.assertIn("Probably skip", HOW_TO_USE_TEXT)
        self.assertIn("under 25", HOW_TO_USE_TEXT)

    def test_fourth_stella_status_is_named(self):
        self.assertIn("No usable fields", HOW_TO_USE_TEXT)
        self.assertIn("pairing was found", HOW_TO_USE_TEXT)

    def test_token_help_says_where_tokens_come_from(self):
        self.assertIn("token page", HOW_TO_USE_TEXT)


class EnterKeyTests(GuiCase):
    """T26: Enter inside a text field does not accept the dialog."""

    def test_add_link_buttons_are_not_auto_default(self):
        from PySide6.QtWidgets import QPushButton

        dialog = AddLinkDialog(self.window)
        self.addCleanup(dialog.close)
        buttons = dialog.findChildren(QPushButton)
        self.assertTrue(buttons)
        for button in buttons:
            self.assertFalse(button.autoDefault())

    def test_enter_in_title_does_not_accept(self):
        dialog = AddLinkDialog(self.window)
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.found_url.setText("https://example.com/data")
        dialog.found_title.setFocus()
        QTest.keyClick(dialog.found_title, Qt.Key.Key_Return)
        QTest.qWait(50)
        self.assertEqual(dialog.result(), 0)
        self.assertTrue(dialog.isVisible())

    def test_enter_in_edit_title_does_not_accept(self):
        dialog = EditItemDialog(_result(0), self.window)
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.title_field.setFocus()
        QTest.keyClick(dialog.title_field, Qt.Key.Key_Return)
        QTest.qWait(50)
        self.assertEqual(dialog.result(), 0)
        self.assertTrue(dialog.isVisible())


class DownloadHeaderTests(GuiCase):
    """T27: no raw path in the header, the right pronoun."""

    def test_singular_header(self):
        dialog = DownloadDialog(
            [(0, _result(0))], Path(self.tmp.name), parent=self.window)
        self.addCleanup(dialog.close)
        text = dialog.header_label.text()
        self.assertEqual(
            text,
            "Downloading 1 dataset and converting it to STELLA "
            ".jsonl files in your Dataset Scout folder.")
        self.assertNotIn(self.tmp.name, text)

    def test_plural_header(self):
        dialog = DownloadDialog(
            [(0, _result(0)), (1, _result(1))],
            Path(self.tmp.name), parent=self.window)
        self.addCleanup(dialog.close)
        text = dialog.header_label.text()
        self.assertIn("2 datasets and converting them", text)
        self.assertNotIn(self.tmp.name, text)


class SourcesTableSizeTests(GuiCase):
    """T28: the websites table sizes to its rows."""

    def test_two_rows_do_not_reserve_a_tall_box(self):
        # Two default websites: header plus two rows, capped.
        self.assertLessEqual(
            self.window.sources_table.maximumHeight(), 200)


class CategoryDefaultTests(GuiCase):
    """T29: the Category combo never defaults to "All"."""

    def _combo_texts(self, combo):
        return [combo.itemText(i) for i in range(combo.count())]

    def test_add_link_defaults_to_a_real_category(self):
        dialog = AddLinkDialog(self.window)
        self.addCleanup(dialog.close)
        texts = self._combo_texts(dialog.found_category)
        self.assertNotIn("All", texts)
        current = dialog.found_category.currentText()
        self.assertNotEqual(current, "All")
        labels = [label for key, (label, _s) in core.CATEGORIES.items()
                  if key != "all"]
        self.assertIn(current, labels)

    def test_edit_combo_has_no_all(self):
        dialog = EditItemDialog(_result(0), self.window)
        self.addCleanup(dialog.close)
        self.assertNotIn("All", self._combo_texts(dialog.category_field))
        self.assertEqual(dialog.category_field.currentText(),
                         "Reasoning")


class MyListPrimaryTests(GuiCase):
    """T30: Download and convert is the primary; Edit and Export
    start collapsed on first view, remembered state wins."""

    def test_download_button_is_styled_primary(self):
        style = self.window.download_button.styleSheet()
        self.assertIn("#ffd166", style)
        self.assertIn("bold", style)

    def test_first_view_sections(self):
        self.assertTrue(self.window.remove_section.is_expanded())
        self.assertFalse(self.window.edit_section.is_expanded())
        self.assertFalse(self.window.export_section.is_expanded())

    def test_remembered_state_wins_over_first_view_default(self):
        self.window.edit_section.set_expanded(True)
        second = MainWindow()
        self.addCleanup(second.close)
        self.assertTrue(second.edit_section.is_expanded())


class WebsiteEditTests(GuiCase):
    """T34/T35: edit a saved website; the token page is one click."""

    def _add_site(self):
        self.window.site_name.setText("Fix Me")
        self.window.site_url.setText("https://example.com/find?q={q}")
        self.window.add_source()
        sites = core.load_sources()
        self.assertIn("Fix Me", [s["name"] for s in sites])

    def test_edit_loads_and_saves_in_place(self):
        self._add_site()
        before = len(core.load_sources())
        self.window.edit_source("Fix Me")
        self.assertEqual(self.window.site_name.text(), "Fix Me")
        self.assertEqual(self.window.site_add_button.text(),
                         "Save changes")
        self.window.site_url.setText("https://example.org/find?q={q}")
        self.window.add_source()
        sites = core.load_sources()
        self.assertEqual(len(sites), before)
        fixed = [s for s in sites if s["name"] == "Fix Me"]
        self.assertEqual(fixed[0]["url"],
                         "https://example.org/find?q={q}")
        self.assertEqual(self.window.site_add_button.text(),
                         "Add website")

    def test_edited_address_goes_through_validation(self):
        self.window.show()
        self.window.tabs.setCurrentIndex(2)
        QTest.qWait(50)
        self._add_site()
        self.window.edit_source("Fix Me")
        self.window.site_url.setText("not a link")
        self.window.add_source()
        sites = core.load_sources()
        fixed = [s for s in sites if s["name"] == "Fix Me"]
        self.assertEqual(fixed[0]["url"],
                         "https://example.com/find?q={q}")
        self.assertTrue(self.window.site_url_error.isVisible())

    def test_double_click_loads_the_form(self):
        self._add_site()
        table = self.window.sources_table
        row = next(r for r in range(table.rowCount())
                   if table.item(r, 0).text() == "Fix Me")
        self.window._source_double_clicked(table.model().index(row, 0))
        self.assertEqual(self.window.site_name.text(), "Fix Me")

    def test_token_sentence_and_page_button(self):
        self.assertTrue(hasattr(self.window, "token_page_button"))
        with unittest.mock.patch(
                "scout.gui.QDesktopServices.openUrl") as open_url:
            self.window._open_token_page()
        opened = open_url.call_args.args[0]
        self.assertEqual(opened.toString(),
                         "https://github.com/settings/tokens")


class TabShortcutTests(GuiCase):
    """T36: Ctrl+1/2/3 and Ctrl+Tab switch tabs, and the overlay
    lists them."""

    def test_registry_has_tab_keys(self):
        keys = [key for key, _d, _s, _a in SHORTCUTS]
        for key in ("Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+Tab",
                    "Ctrl+Shift+Tab"):
            self.assertIn(key, keys)

    def test_handlers_switch_tabs(self):
        self.window._shortcut_tab_list()
        self.assertEqual(self.window.tabs.currentIndex(), 1)
        self.window._shortcut_tab_sources()
        self.assertEqual(self.window.tabs.currentIndex(), 2)
        self.window._shortcut_tab_search()
        self.assertEqual(self.window.tabs.currentIndex(), 0)
        self.window._shortcut_tab_next()
        self.assertEqual(self.window.tabs.currentIndex(), 1)
        self.window._shortcut_tab_prev()
        self.assertEqual(self.window.tabs.currentIndex(), 0)

    def test_overlay_lists_tab_keys(self):
        dialog = ShortcutsDialog(self.window)
        self.addCleanup(dialog.close)
        text = dialog.text.toPlainText()
        self.assertIn("Ctrl+1", text)
        self.assertIn("Ctrl+Tab", text)


class InMyListMarkerTests(GuiCase):
    """T37: saved results carry a visible marker."""

    def test_marker_appears_and_clears(self):
        item = _result(0)
        self.window.results = [dict(item)]
        self.window._show_results()
        cell = self.window.results_table.item(0, 0)
        self.assertNotIn("In My List", cell.text())
        self.window.my_list = [dict(item)]
        self.window._refresh_list_tab()
        cell = self.window.results_table.item(0, 0)
        self.assertIn("In My List", cell.text())
        self.window.my_list = []
        self.window._refresh_list_tab()
        cell = self.window.results_table.item(0, 0)
        self.assertNotIn("In My List", cell.text())


class DownloadDestinationTests(GuiCase):
    """T38: the destination is pickable, remembered, and shared
    with the queue."""

    def test_picker_remembers_and_queue_uses_it(self):
        target = Path(self.tmp.name) / "elsewhere"
        target.mkdir()
        dialog = DownloadDialog(
            [(0, _result(0))], _download_dir(), parent=self.window)
        self.addCleanup(dialog.close)
        with unittest.mock.patch.object(
                QFileDialog, "getExistingDirectory",
                return_value=str(target)):
            dialog._choose_folder()
        self.assertEqual(dialog.out_dir, target)
        self.assertEqual(_download_dir(), target)
        self.assertEqual(core.load_config().get("download_dir"),
                         str(target))
        queue_dialog = QueueDialog(self.window)
        self.addCleanup(queue_dialog.close)
        self.assertEqual(queue_dialog.out_dir, target)

    def test_default_is_the_stella_ready_folder(self):
        self.assertEqual(_download_dir(),
                         core.home_dir() / "stella-ready")


class OpenConvertedFolderTests(GuiCase):
    """T39: one button on My List opens the converted folder."""

    def test_button_opens_the_folder(self):
        folder = _download_dir()
        folder.mkdir(parents=True, exist_ok=True)
        self.assertTrue(hasattr(self.window, "open_converted_button"))
        with unittest.mock.patch(
                "scout.gui.QDesktopServices.openUrl") as open_url:
            self.window.open_converted_folder()
        opened = open_url.call_args.args[0]
        self.assertEqual(opened.toLocalFile(), str(folder))

    def test_missing_folder_says_so(self):
        with unittest.mock.patch(
                "scout.gui.QDesktopServices.openUrl") as open_url:
            self.window.open_converted_folder()
        open_url.assert_not_called()
        self.assertIn("no converted folder",
                      self.window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
