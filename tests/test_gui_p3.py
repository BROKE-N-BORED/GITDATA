"""GUI regression tests for the v0.1.4 P3 items (T55-T65).

Offscreen, no live network. Follows the test_gui.py harness:
one shared QApplication, a fresh SCOUT_HOME per test.
"""
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication, QDialog, QMessageBox, QPushButton,
)
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices

from scout import core, history, score
from scout import queue as dlqueue
from scout.gui import (
    L_SCORE, R_TITLE, DownloadDialog, EditItemDialog,
    HistoryDialog, MainWindow, QueueDialog, SelfTestDialog,
)

APP = None


def get_app():
    global APP
    if APP is None:
        APP = QApplication.instance() or QApplication([])
    return APP


class P3GuiTestCase(unittest.TestCase):
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

    def _menus(self):
        return {action.text(): action.menu()
                for action in self.window.menuBar().actions()}

    def _set_list(self, items):
        self.window.my_list = [core.normalize_item(item)
                               for item in items]
        core.save_list(self.window.my_list)
        self.window._note_list_loaded()
        self.window._refresh_list_tab()

    def _set_results(self, results):
        self.window.results = [core.normalize_item(item)
                               for item in results]
        self.window._show_results()

    def _visible_result_titles(self):
        table = self.window.results_table
        titles = []
        for row in range(table.rowCount()):
            if table.isRowHidden(row):
                continue
            cell = table.item(row, R_TITLE)
            titles.append(cell.text())
        return titles


class ResultsFilterTests(P3GuiTestCase):
    """T55: "Find in these results" on the Search tab."""

    def _results(self):
        return [
            {"title": "Math word problems", "url": "https://example.com/a",
             "category": "reasoning", "source": "Hugging Face",
             "stars": 5, "updated": "2026-11-01",
             "description": "Arithmetic practice", "license": "MIT"},
            {"title": "Cat pictures", "url": "https://example.com/b",
             "category": "training", "source": "GitHub",
             "stars": 3, "updated": "2026-10-01",
             "description": "Photos of cats", "license": ""},
        ]

    def test_filter_box_exists_with_plain_placeholder(self):
        self.assertEqual(self.window.results_filter_box.placeholderText(),
                         "Type to filter by title, description, or source")

    def test_filter_keeps_only_matching_rows(self):
        self._set_results(self._results())
        self.window.results_filter_box.setText("math")
        self.assertEqual(self._visible_result_titles(),
                         ["Math word problems"])
        self.window.results_filter_box.setText("cats")
        self.assertEqual(self._visible_result_titles(), ["Cat pictures"])

    def test_filter_matches_description_and_source(self):
        self._set_results(self._results())
        self.window.results_filter_box.setText("arithmetic")
        self.assertEqual(self._visible_result_titles(),
                         ["Math word problems"])
        self.window.results_filter_box.setText("github")
        self.assertEqual(self._visible_result_titles(), ["Cat pictures"])

    def test_clearing_the_filter_shows_all_rows(self):
        self._set_results(self._results())
        self.window.results_filter_box.setText("math")
        self.assertEqual(len(self._visible_result_titles()), 1)
        self.window.results_filter_box.setText("")
        self.assertEqual(len(self._visible_result_titles()), 2)

    def test_filter_matching_nothing_says_so(self):
        self._set_results(self._results())
        self.window.results_filter_box.setText("zzz-no-such-word")
        self.assertEqual(self._visible_result_titles(), [])
        self.assertFalse(self.window.results_empty_label.isHidden())
        self.assertIn("No results match that filter",
                      self.window.results_empty_label.text())

    def test_filter_applies_to_fresh_results(self):
        self.window.results_filter_box.setText("math")
        self._set_results(self._results())
        self.assertEqual(self._visible_result_titles(),
                         ["Math word problems"])

    def test_filter_survives_a_sort(self):
        self._set_results(self._results())
        self.window.results_filter_box.setText("math")
        self.window.results_table.sortByColumn(
            R_TITLE, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self._visible_result_titles(),
                         ["Math word problems"])


class ListScoreTests(P3GuiTestCase):
    """T57: the quality score carries into My List."""

    def test_score_column_exists_and_matches_the_result(self):
        item = {"title": "Math", "url": "https://example.com/a",
                "category": "reasoning", "source": "Hugging Face",
                "stars": 100, "updated": "2026-11-10",
                "description": "", "license": "MIT",
                "stella_status": "ready"}
        self._set_list([item])
        headers = [self.window.list_table.horizontalHeaderItem(col).text()
                   for col in range(self.window.list_table.columnCount())]
        self.assertIn("Score", headers)
        expected = score.score_item(core.normalize_item(item))
        cell = self.window.list_table.item(0, L_SCORE)
        self.assertEqual(cell.text(),
                         f"{expected['score']} {expected['label']}")
        self.assertIn("Score", cell.toolTip())

    def test_score_cell_follows_the_stella_check(self):
        item = {"title": "Math", "url": "https://example.com/a",
                "category": "reasoning", "source": "Hugging Face",
                "stars": 10, "updated": "2026-11-10",
                "description": "", "license": "MIT"}
        self._set_list([item])
        before = self.window.list_table.item(0, L_SCORE).text()
        self.window.my_list[0]["stella_status"] = "ready"
        self.window._update_list_stella_cell(0)
        expected = score.score_item(self.window.my_list[0])
        after = self.window.list_table.item(0, L_SCORE).text()
        self.assertEqual(after, f"{expected['score']} {expected['label']}")
        self.assertNotEqual(before, after)


class EditDialogUrlTests(P3GuiTestCase):
    """T58: fix a mistyped URL in the Edit dialog."""

    def _dialog(self, **fields):
        item = core.normalize_item(
            {"title": "A", "url": "https://example.com/a", **fields})
        dialog = EditItemDialog(item, self.window)
        self.addCleanup(dialog.close)
        return dialog

    def test_link_is_editable_and_values_carry_it(self):
        dialog = self._dialog()
        self.assertEqual(dialog.url_field.text(), "https://example.com/a")
        dialog.url_field.setText("https://example.com/fixed")
        self.assertEqual(dialog.values()["url"],
                         "https://example.com/fixed")

    def test_empty_link_blocks_accept(self):
        dialog = self._dialog()
        dialog.url_field.setText("")
        self.assertFalse(dialog._validate_url(force=True))
        self.assertEqual(dialog.url_error.text(), "Paste a link first.")
        self.assertFalse(dialog.url_error.isHidden())
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_bad_looking_link_blocks_the_save(self):
        # The same inline check as the add form (T22): a link the
        # check rejects blocks the save, with the message beside
        # the field.
        dialog = self._dialog()
        dialog.url_field.setText("not a link")
        self.assertFalse(dialog._validate_url(force=True))
        self.assertIn("does not look like a link", dialog.url_error.text())
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_duplicate_link_blocks_accept(self):
        item = core.normalize_item(
            {"title": "A", "url": "https://example.com/a"})
        dialog = EditItemDialog(
            item, self.window,
            taken_urls=["https://example.com/b"])
        self.addCleanup(dialog.close)
        dialog.url_field.setText("https://example.com/b")
        self.assertFalse(dialog._validate_url(force=True))
        self.assertEqual(dialog.url_error.text(),
                         "That link is already in My List.")

    def test_unchanged_link_is_not_a_duplicate(self):
        item = core.normalize_item(
            {"title": "A", "url": "https://example.com/a"})
        dialog = EditItemDialog(
            item, self.window,
            taken_urls=["https://example.com/b"])
        self.addCleanup(dialog.close)
        self.assertTrue(dialog._validate_url(force=True))

    def test_edit_selected_applies_a_fixed_link(self):
        self._set_list([{"title": "A", "url": "https://example.com/a"}])
        self.window.list_table.selectRow(0)
        fake = unittest.mock.Mock()
        fake.exec.return_value = QDialog.DialogCode.Accepted
        fake.values.return_value = {
            "title": "A", "url": "https://example.com/fixed",
            "category": "all", "notes": "", "state": "found"}
        with unittest.mock.patch(
                "scout.gui.EditItemDialog", return_value=fake):
            self.window.edit_selected()
        self.assertEqual(self.window.my_list[0]["url"],
                         "https://example.com/fixed")

    def test_edit_selected_adds_https_to_a_bare_host(self):
        self._set_list([{"title": "A", "url": "https://example.com/a"}])
        self.window.list_table.selectRow(0)
        fake = unittest.mock.Mock()
        fake.exec.return_value = QDialog.DialogCode.Accepted
        fake.values.return_value = {
            "title": "A", "url": "example.com/bare",
            "category": "all", "notes": "", "state": "found"}
        with unittest.mock.patch(
                "scout.gui.EditItemDialog", return_value=fake):
            self.window.edit_selected()
        self.assertEqual(self.window.my_list[0]["url"],
                         "https://example.com/bare")


class UndoEditTests(P3GuiTestCase):
    """T59: undo for edits."""

    def _edit_with(self, values):
        self.window.list_table.selectRow(0)
        fake = unittest.mock.Mock()
        fake.exec.return_value = QDialog.DialogCode.Accepted
        fake.values.return_value = values
        with unittest.mock.patch(
                "scout.gui.EditItemDialog", return_value=fake):
            self.window.edit_selected()

    def test_edit_then_undo_restores_everything(self):
        self._set_list([{
            "title": "Old", "url": "https://example.com/a",
            "category": "reasoning", "notes": "old note",
            "state": "found"}])
        self._edit_with({
            "title": "New", "url": "https://example.com/b",
            "category": "training", "notes": "new note",
            "state": "converted"})
        self.assertTrue(self.window.undo_edit_button.isEnabled())
        self.window.undo_edit()
        item = self.window.my_list[0]
        self.assertEqual(item["title"], "Old")
        self.assertEqual(item["url"], "https://example.com/a")
        self.assertEqual(item["category"], "reasoning")
        self.assertEqual(item["notes"], "old note")
        self.assertEqual(item["state"], "found")
        self.assertFalse(self.window.undo_edit_button.isEnabled())
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Edit undone.")
        # And the undo is saved, not just on screen.
        self.assertEqual(core.load_list()[0]["title"], "Old")

    def test_undo_survives_a_reorder(self):
        self._set_list([
            {"title": "A", "url": "https://example.com/a"},
            {"title": "B", "url": "https://example.com/b"},
        ])
        self._edit_with({
            "title": "A renamed", "url": "https://example.com/a",
            "category": "all", "notes": "", "state": "found"})
        # Move the edited row down: undo must still find it.
        self.window.list_table.selectRow(0)
        self.window._move_selected(1)
        self.window.undo_edit()
        titles = [item["title"] for item in self.window.my_list]
        self.assertEqual(titles, ["B", "A"])

    def test_no_change_edit_is_not_undoable(self):
        self._set_list([{"title": "A", "url": "https://example.com/a",
                         "category": "all", "notes": "",
                         "state": "found"}])
        self._edit_with({
            "title": "A", "url": "https://example.com/a",
            "category": "all", "notes": "", "state": "found"})
        self.assertFalse(self.window.undo_edit_button.isEnabled())

    def test_undo_with_nothing_to_undo_is_quiet(self):
        self._set_list([{"title": "A", "url": "https://example.com/a"}])
        self.window.undo_edit()  # no crash, no message change
        self.assertEqual(self.window.my_list[0]["title"], "A")

    def test_undo_after_remove_says_the_link_is_gone(self):
        self._set_list([{"title": "A", "url": "https://example.com/a"}])
        self._edit_with({
            "title": "Renamed", "url": "https://example.com/a",
            "category": "all", "notes": "", "state": "found"})
        self.window.my_list = []
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()
        self.window.undo_edit()
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "That link is gone. Nothing to undo.")

    def test_undo_edit_is_in_the_edit_menu(self):
        actions = {a.text() for a in self._menus()["Edit"].actions()}
        self.assertIn("Undo edit", actions)


class AddLinkReachableTests(P3GuiTestCase):
    """T60: "Add a link I found" from My List and the menu."""

    def test_file_menu_has_add_a_link(self):
        actions = {a.text(): a for a in self._menus()["File"].actions()}
        self.assertIn("Add a link I found", actions)
        with unittest.mock.patch.object(
                self.window.add_link_dialog, "exec",
                return_value=QDialog.DialogCode.Rejected) as mocked:
            actions["Add a link I found"].trigger()
        mocked.assert_called_once()

    def test_my_list_has_an_add_a_link_button(self):
        button = self.window.list_add_link_button
        self.assertIsInstance(button, QPushButton)
        self.assertEqual(button.text(), "Add a link I found")
        with unittest.mock.patch.object(
                self.window.add_link_dialog, "exec",
                return_value=QDialog.DialogCode.Rejected) as mocked:
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        mocked.assert_called_once()


class ConvertedFilesTests(P3GuiTestCase):
    """T56: a way back to converted files from My List."""

    def _item(self):
        return core.normalize_item(
            {"title": "Math", "url": "https://example.com/math",
             "source": "Hugging Face", "state": "converted"})

    def _make_converted(self, item, names=("train.jsonl",)):
        folder = self.window._converted_folder(item)
        folder.mkdir(parents=True)
        paths = []
        for name in names:
            path = folder / name
            path.write_text('{"prompt": "q", "completion": "a"}\n',
                            encoding="utf-8")
            paths.append(path)
        return paths

    def _menu_for_row(self, row=0):
        self.window.list_table.selectRow(row)
        rows = self.window._selected_list_rows()
        return self.window._build_list_context_menu(rows)

    def test_converted_files_found_in_the_dataset_folder(self):
        item = self._item()
        self._set_list([item])
        paths = self._make_converted(item)
        self.assertEqual(self.window._converted_files(item), paths)

    def test_no_converted_files_without_a_folder(self):
        item = self._item()
        self.assertEqual(self.window._converted_files(item), [])

    def test_context_menu_offers_open_converted_file_and_folder(self):
        item = self._item()
        self._set_list([item])
        self._make_converted(item)
        menu = self._menu_for_row()
        texts = [action.text() for action in menu.actions()]
        self.assertIn("Open converted file", texts)
        self.assertIn("Open converted folder", texts)

    def test_context_menu_hides_converted_actions_without_files(self):
        item = self._item()
        self._set_list([item])
        menu = self._menu_for_row()
        texts = [action.text() for action in menu.actions()]
        self.assertNotIn("Open converted file", texts)
        self.assertNotIn("Open converted folder", texts)

    def test_several_files_become_a_submenu(self):
        item = self._item()
        self._set_list([item])
        self._make_converted(item, ("train.jsonl", "test.jsonl"))
        menu = self._menu_for_row()
        open_action = [action for action in menu.actions()
                       if action.text() == "Open converted file"][0]
        submenu = open_action.menu()
        self.assertIsNotNone(submenu)
        self.assertEqual(
            sorted(a.text() for a in submenu.actions()),
            ["test.jsonl", "train.jsonl"])

    def test_open_converted_file_opens_the_file(self):
        item = self._item()
        paths = self._make_converted(item)
        with unittest.mock.patch.object(
                QDesktopServices, "openUrl") as mocked:
            self.window._open_converted_file(paths[0])
        mocked.assert_called_once()
        url = mocked.call_args[0][0]
        self.assertEqual(url.toLocalFile(), str(paths[0]))

    def test_open_converted_folder_opens_the_folder(self):
        item = self._item()
        self._make_converted(item)
        with unittest.mock.patch.object(
                QDesktopServices, "openUrl") as mocked:
            self.window._open_converted_folder(item)
        mocked.assert_called_once()
        url = mocked.call_args[0][0]
        self.assertEqual(
            url.toLocalFile(),
            str(self.window._converted_folder(item)))

    def test_open_folder_without_files_says_so(self):
        item = self._item()
        with unittest.mock.patch.object(
                QDesktopServices, "openUrl") as mocked:
            self.window._open_converted_folder(item)
        mocked.assert_not_called()
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "There are no converted files for that link yet.")


class HistoryGuiTests(P3GuiTestCase):
    """T61: download history, visible in the app."""

    def test_download_dialog_records_a_conversion(self):
        items = [(0, {"title": "One",
                      "url": "https://example.com/a",
                      "source": "Hugging Face", "state": "found"})]
        dialog = DownloadDialog(
            items, Path(self.tmp.name) / "out", parent=self.window)
        self.addCleanup(dialog.close)
        dialog._on_item_done(0, True, "2 rows",
                             ["/tmp/out/train.jsonl"], 2, "converted")
        entries = history.load_history()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "One")
        self.assertEqual(entries[0]["rows"], 2)
        self.assertEqual(entries[0]["files"], ["train.jsonl"])

    def test_download_dialog_records_nothing_on_failure(self):
        items = [(0, {"title": "One",
                      "url": "https://example.com/a",
                      "source": "Hugging Face", "state": "found"})]
        dialog = DownloadDialog(
            items, Path(self.tmp.name) / "out", parent=self.window)
        self.addCleanup(dialog.close)
        dialog._on_item_done(0, False, "could not download", [], 0,
                             "found")
        self.assertEqual(history.load_history(), [])

    def test_queue_dialog_records_a_conversion(self):
        entries, _added = dlqueue.enqueue(
            [], {"title": "Queued",
                 "url": "https://example.com/a",
                 "source": "Hugging Face"})
        dlqueue.save_queue(entries)
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        entry_id = dialog.entries[0]["id"]
        dialog._on_entry_done(entry_id, True, "3 rows",
                              ["/tmp/out/train.jsonl"], 3, "converted")
        loaded = history.load_history()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["title"], "Queued")
        self.assertEqual(loaded[0]["rows"], 3)

    def test_history_dialog_shows_entries(self):
        history.record_conversion("Math", "https://example.com/a",
                                  "Hugging Face", 42, ["train.jsonl"])
        dialog = HistoryDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.history_table.rowCount(), 1)
        self.assertEqual(dialog.history_table.item(0, 0).text(), "Math")
        self.assertEqual(dialog.history_table.item(0, 2).text(), "42")
        self.assertEqual(dialog.history_table.item(0, 3).text(),
                         "train.jsonl")
        self.assertTrue(dialog.empty_label.isHidden())

    def test_history_dialog_empty_state(self):
        dialog = HistoryDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.history_table.rowCount(), 0)
        self.assertFalse(dialog.empty_label.isHidden())
        self.assertFalse(dialog.open_button.isEnabled())
        self.assertFalse(dialog.clear_button.isEnabled())

    def test_history_dialog_open_folder(self):
        folder = Path(self.tmp.name) / "stella-ready" / "math-abc"
        folder.mkdir(parents=True)
        (folder / "train.jsonl").write_text("{}\n", encoding="utf-8")
        history.record_conversion("Math", "https://example.com/a",
                                  "Hugging Face", 1,
                                  [str(folder / "train.jsonl")])
        dialog = HistoryDialog(self.window)
        self.addCleanup(dialog.close)
        dialog.history_table.selectRow(0)
        with unittest.mock.patch.object(
                QDesktopServices, "openUrl") as mocked:
            dialog.open_folder()
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args[0][0].toLocalFile(),
                         str(folder))

    def test_history_dialog_open_folder_when_gone(self):
        history.record_conversion("Math", "https://example.com/a",
                                  "Hugging Face", 1,
                                  ["/tmp/nowhere/train.jsonl"])
        dialog = HistoryDialog(self.window)
        self.addCleanup(dialog.close)
        dialog.history_table.selectRow(0)
        with unittest.mock.patch.object(
                QMessageBox, "information") as mocked:
            dialog.open_folder()
        mocked.assert_called_once()

    def test_history_dialog_clear_asks_first(self):
        history.record_conversion("Math", "", "", 1, ["a.jsonl"])
        dialog = HistoryDialog(self.window)
        self.addCleanup(dialog.close)
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            dialog.clear_history()
        self.assertEqual(len(history.load_history()), 1)
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            dialog.clear_history()
        self.assertEqual(history.load_history(), [])
        self.assertEqual(dialog.history_table.rowCount(), 0)

    def test_download_history_is_in_the_file_menu(self):
        actions = {a.text(): a for a in self._menus()["File"].actions()}
        self.assertIn("Download history", actions)
        actions["Download history"].trigger()
        self.assertIsNotNone(self.window._history_dialog)
        self.addCleanup(self.window._history_dialog.close)


class RemoveDeadTests(P3GuiTestCase):
    """T62: "Remove the dead ones" after a link check."""

    def _dead_list(self):
        self._set_list([
            {"title": "Alive", "url": "https://example.com/a",
             "status": "ok"},
            {"title": "Dead one", "url": "https://example.com/b",
             "status": "dead"},
            {"title": "Dead two", "url": "https://example.com/c",
             "status": "dead"},
            {"title": "Unknown", "url": "https://example.com/d",
             "status": "unknown"},
        ])

    def test_button_only_live_when_rows_are_dead(self):
        self._set_list([{"title": "Alive",
                         "url": "https://example.com/a",
                         "status": "ok"}])
        self.assertFalse(self.window.remove_dead_button.isEnabled())
        self._dead_list()
        self.assertTrue(self.window.remove_dead_button.isEnabled())

    def test_remove_dead_removes_only_dead_rows(self):
        self._dead_list()
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.window.remove_dead()
        titles = [item["title"] for item in self.window.my_list]
        self.assertEqual(titles, ["Alive", "Unknown"])
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Removed 2 dead links from My List.")
        self.assertEqual(len(core.load_list()), 2)

    def test_remove_dead_is_undoable(self):
        self._dead_list()
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.window.remove_dead()
        self.window.undo_remove()
        titles = [item["title"] for item in self.window.my_list]
        self.assertEqual(titles,
                         ["Alive", "Dead one", "Dead two", "Unknown"])

    def test_declining_keeps_the_dead_rows(self):
        self._dead_list()
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            self.window.remove_dead()
        self.assertEqual(len(self.window.my_list), 4)

    def test_remove_dead_with_no_dead_rows_says_so(self):
        self._set_list([{"title": "Alive",
                         "url": "https://example.com/a",
                         "status": "ok"}])
        self.window.remove_dead()
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "No dead links in My List.")


class MoveCollectionTests(P3GuiTestCase):
    """T63: links move between collections."""

    def _two_lists(self):
        core.save_collections("My List", [
            {"name": "My List", "items": [
                core.normalize_item(
                    {"title": "A", "url": "https://example.com/a"}),
                core.normalize_item(
                    {"title": "B", "url": "https://example.com/b"})]},
            {"name": "Second", "items": []},
        ])
        self.window.my_list = core.load_list()
        self.window._note_list_loaded()
        self.window._refresh_collection_combo()
        self.window._refresh_list_tab()

    def test_move_selected_to_another_list(self):
        self._two_lists()
        self.window.list_table.selectRow(0)
        self.window.move_selected_to_collection("Second")
        data = core.load_list_data()
        by_name = {col["name"]: col["items"]
                   for col in data["collections"]}
        self.assertEqual([item["title"] for item in by_name["My List"]],
                         ["B"])
        self.assertEqual([item["title"] for item in by_name["Second"]],
                         ["A"])
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Moved 1 link to Second.")
        self.assertEqual(len(self.window.my_list), 1)

    def test_move_skips_a_link_already_there(self):
        self._two_lists()
        core.save_collections("My List", [
            {"name": "My List", "items": [
                core.normalize_item(
                    {"title": "A", "url": "https://example.com/a"})]},
            {"name": "Second", "items": [
                core.normalize_item(
                    {"title": "A", "url": "https://example.com/a"})]},
        ])
        self.window.my_list = core.load_list()
        self.window._note_list_loaded()
        self.window._refresh_list_tab()
        self.window.list_table.selectRow(0)
        self.window.move_selected_to_collection("Second")
        data = core.load_list_data()
        by_name = {col["name"]: col["items"]
                   for col in data["collections"]}
        self.assertEqual(len(by_name["Second"]), 1)
        self.assertEqual(len(by_name["My List"]), 0)
        self.assertIn("already there",
                      self.window.statusBar().currentMessage())

    def test_move_with_nothing_selected_says_so(self):
        self._two_lists()
        self.window.move_selected_to_collection("Second")
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Nothing selected to move.")

    def test_move_to_the_same_list_does_nothing(self):
        self._two_lists()
        self.window.list_table.selectRow(0)
        self.window.move_selected_to_collection("My List")
        self.assertEqual(len(self.window.my_list), 2)

    def test_context_menu_offers_move_to_list(self):
        self._two_lists()
        self.window.list_table.selectRow(0)
        menu = self.window._build_list_context_menu(
            self.window._selected_list_rows())
        move_action = [action for action in menu.actions()
                       if action.text() == "Move to list"]
        self.assertEqual(len(move_action), 1)
        submenu = move_action[0].menu()
        self.assertEqual([a.text() for a in submenu.actions()],
                         ["Second"])

    def test_context_menu_hides_move_with_one_list(self):
        self._set_list([{"title": "A", "url": "https://example.com/a"}])
        self.window.list_table.selectRow(0)
        menu = self.window._build_list_context_menu(
            self.window._selected_list_rows())
        texts = [a.text() for a in menu.actions()]
        self.assertNotIn("Move to list", texts)


class SelfTestGuiTests(P3GuiTestCase):
    """T64: a GUI entry for --self-test."""

    def _passing_checks(self):
        return [
            {"name": "Search", "ok": True, "detail": "found 3 results"},
            {"name": "Sniff", "ok": True,
             "detail": "math set: STELLA-ready"},
            {"name": "Convert", "ok": True,
             "detail": "3 rows converted from the sample"},
        ]

    def test_help_menu_has_run_self_test(self):
        actions = {a.text() for a in self._menus()["Help"].actions()}
        self.assertIn("Run self-test", actions)

    def test_selftest_dialog_shows_the_plain_words_report(self):
        dialog = SelfTestDialog(self.window)
        self.addCleanup(dialog.close)
        with unittest.mock.patch(
                "scout.gui.selftest.run_checks",
                return_value=self._passing_checks()):
            dialog.start_test()
            for _ in range(100):
                if dialog.run_button.isEnabled():
                    break
                QTest.qWait(50)
        self.assertTrue(dialog.run_button.isEnabled())
        report = dialog.report_view.toPlainText()
        self.assertIn("PASS: Search - found 3 results", report)
        self.assertIn("PASS: Convert - 3 rows converted from the sample",
                      report)
        self.assertIn("Self-test: 3 of 3 checks passed.", report)

    def test_selftest_dialog_shows_failures_plainly(self):
        dialog = SelfTestDialog(self.window)
        self.addCleanup(dialog.close)
        dialog._show_report([
            {"name": "Search", "ok": False,
             "detail": "neither GitHub nor Hugging Face answered"},
            {"name": "Sniff", "ok": False,
             "detail": "no search result to check"},
            {"name": "Convert", "ok": True,
             "detail": "3 rows converted from the sample"},
        ])
        report = dialog.report_view.toPlainText()
        self.assertIn("FAIL: Search - neither GitHub nor Hugging Face "
                      "answered", report)
        self.assertIn("Self-test: 1 of 3 checks passed.", report)

    def test_show_selftest_opens_the_dialog_and_runs(self):
        with unittest.mock.patch(
                "scout.gui.selftest.run_checks",
                return_value=self._passing_checks()):
            self.window.show_selftest()
            dialog = self.window._selftest_dialog
            self.assertIsNotNone(dialog)
            self.addCleanup(dialog.close)
            for _ in range(100):
                if dialog.run_button.isEnabled():
                    break
                QTest.qWait(50)
        self.assertIn("Self-test: 3 of 3 checks passed.",
                      dialog.report_view.toPlainText())


class WindowPersistenceTests(P3GuiTestCase):
    """T65: the default stays Active-only 12 months, and the user's
    choice is remembered between runs."""

    def test_default_is_still_active_only_12_months(self):
        self.assertTrue(self.window.active_check.isChecked())
        self.assertEqual(self.window.window_combo.currentText(),
                         "12 months")
        self.assertNotIn("active_window", core.load_config())

    def test_window_choice_persists_across_restarts(self):
        self.window.window_combo.setCurrentText("6 months")
        self.window.active_check.setChecked(False)
        config = core.load_config()
        self.assertEqual(config["active_window"], "6 months")
        self.assertIs(config["active_only"], False)
        second = MainWindow()
        self.addCleanup(second.close)
        self.assertEqual(second.window_combo.currentText(), "6 months")
        self.assertFalse(second.active_check.isChecked())

    def test_unknown_saved_window_falls_back_to_12_months(self):
        config = core.load_config()
        config["active_window"] = "99 years"
        core.save_config(config)
        second = MainWindow()
        self.addCleanup(second.close)
        self.assertEqual(second.window_combo.currentText(), "12 months")

    def test_active_only_choice_persists_on_its_own(self):
        self.window.active_check.setChecked(False)
        second = MainWindow()
        self.addCleanup(second.close)
        self.assertFalse(second.active_check.isChecked())
        self.assertEqual(second.window_combo.currentText(), "12 months")


if __name__ == "__main__":
    unittest.main()
