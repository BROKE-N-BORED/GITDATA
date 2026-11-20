"""Regression tests for the v0.1.6 P2 round (T5-T16).

Offscreen, no live network. Each class names the task item it
pins down.
"""
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtTest import QTest

from scout import convert, core, download
from scout.gui import MainWindow, QueueDialog, SelfTestDialog

APP = None


def get_app():
    global APP
    if APP is None:
        APP = QApplication.instance() or QApplication([])
    return APP


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


def _result(i=0):
    return {
        "title": f"some-owner/dataset-number-{i:02d}-with-a-long-name",
        "source": "Hugging Face" if i % 2 else "GitHub",
        "stars": 120 + i,
        "downloads": 5000 + i,
        "updated": "2026-11-15T10:00:00Z",
        "description": ("A dataset of math word problems with worked "
                        "solutions and answers for training."),
        "license": "Apache-2.0",
        "format": "jsonl",
        "url": f"https://huggingface.co/datasets/owner/ds-{i}",
        "category": "math",
    }


class ProblemFieldConverterTests(unittest.TestCase):
    """T8: the converter maps the canonical GSM8K/MATH layouts."""

    def test_problem_answer_maps(self):
        row, reason = convert.map_row(
            {"problem": "What is 2 + 2?", "answer": "4"})
        self.assertEqual(reason, "")
        self.assertEqual(row, {"prompt": "What is 2 + 2?",
                               "completion": "4"})

    def test_problem_solution_maps(self):
        row, reason = convert.map_row(
            {"problem": "What is 2 + 2?",
             "solution": "Two plus two is four."})
        self.assertEqual(reason, "")
        self.assertEqual(row, {"prompt": "What is 2 + 2?",
                               "completion": "Two plus two is four."})

    def test_problem_with_answer_and_solution_prefers_answer(self):
        row, _reason = convert.map_row(
            {"problem": "P", "answer": "4", "solution": "worked"})
        self.assertEqual(row["completion"], "4")

    def test_problem_row_with_extra_fields_converts(self):
        # The real Geraldxm/math-vault row shape from the review.
        rows, report = convert.convert_jsonl_text(
            '{"id": 1, "problem": "P?", "answer": "A", '
            '"solution": "S", "source": "gsm8k"}\n'
            '{"id": 2, "problem": "Q?", "answer": "B", '
            '"solution": "T", "source": "gsm8k"}\n')
        self.assertEqual(report["kept"], 2)
        self.assertEqual(rows[0], {"prompt": "P?", "completion": "A"})

    def test_problem_alone_still_unmappable(self):
        row, reason = convert.map_row({"problem": "P?"})
        self.assertIsNone(row)
        self.assertIn("no mapping", reason)

    def test_problem_columns_sniff_as_convertible(self):
        self.assertTrue(convert.columns_map(["problem", "answer"]))
        self.assertTrue(convert.columns_map(["problem", "solution"]))
        kind, _detail = convert.peek_shape(
            '{"problem": "P?", "answer": "A"}\n'
            '{"problem": "Q?", "answer": "B"}\n')
        self.assertEqual(kind, "convert")

    def test_problem_csv_header_recognised(self):
        rows, report = convert.convert_csv_text(
            "problem,solution\nWhat is 2 + 2?,Four\n"
            "What is 3 + 3?,Six\n")
        self.assertEqual(report["kept"], 2)
        self.assertEqual(rows[0]["prompt"], "What is 2 + 2?")


class OrphanTmpCleanupTests(unittest.TestCase):
    """T9: orphaned .tmp-* download folders are removed at startup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["SCOUT_HOME"] = self.tmp.name
        core._RING.clear()

    def tearDown(self):
        os.environ.pop("SCOUT_HOME", None)

    def test_orphan_removed_real_output_kept_and_logged(self):
        ready = Path(self.tmp.name) / "stella-ready"
        orphan = ready / ".tmp-some-dataset-abc123"
        orphan.mkdir(parents=True)
        (orphan / "repo.zip").write_bytes(b"partial")
        real = ready / "some-dataset-abc123"
        real.mkdir()
        (real / "train.jsonl").write_text("{}\n")
        removed = download.cleanup_orphan_tmp_folders()
        self.assertEqual(removed, 1)
        self.assertFalse(orphan.exists())
        self.assertTrue((real / "train.jsonl").exists())
        self.assertTrue(any(
            "unfinished download folder" in line
            for line in core.ring_lines()))

    def test_no_stella_ready_folder_is_quiet(self):
        self.assertEqual(download.cleanup_orphan_tmp_folders(), 0)


class DownloadFinishLineTests(GuiCase):
    """T6: the main window's finish line matches what converted."""

    def _run_download(self, done_ok, done_rows):
        class FakeDialog:
            def __init__(self, items, out_dir, parent=None):
                self._done_ok = done_ok
                self._done_rows = done_rows

            def start(self):
                pass

            def exec(self):
                return 0

        self.window.my_list = [_result(0)]
        self.window._refresh_list_tab()
        with unittest.mock.patch(
                "scout.gui.DownloadDialog", FakeDialog), \
                unittest.mock.patch.object(
                    self.window, "_selected_list_rows",
                    return_value=[0]):
            self.window.start_download()
        return self.window.statusBar().currentMessage()

    def test_zero_converted_is_honest(self):
        message = self._run_download(0, 0)
        self.assertEqual(
            message,
            "Nothing converted. See the download window for why.")
        self.assertEqual(self.window.finish_label.text(), "")

    def test_some_converted_names_the_count(self):
        message = self._run_download(2, 1234)
        self.assertIn("2 datasets converted", message)
        self.assertIn("1234 rows kept", message)
        self.assertEqual(self.window.finish_label.text(), "✓ Done")


class QueueLogDedupeTests(GuiCase):
    """T7: repeated stage lines collapse in the queue log."""

    def test_same_stage_prints_once(self):
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        for _ in range(5):
            dialog._on_progress("e1", "Downloading some/dataset",
                                10, 100)
        text = dialog.log_view.toPlainText()
        self.assertEqual(text.count("Downloading some/dataset"), 1)
        dialog._on_progress("e1", "Converting some/dataset", 0, 0)
        dialog._on_progress("e1", "Converting some/dataset", 0, 0)
        text = dialog.log_view.toPlainText()
        self.assertEqual(text.count("Converting some/dataset"), 1)

    def test_new_entry_prints_the_stage_again(self):
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        dialog._on_progress("e1", "Downloading some/dataset", 10, 100)
        dialog._on_entry_started("e2")
        dialog._on_progress("e2", "Downloading some/dataset", 10, 100)
        text = dialog.log_view.toPlainText()
        self.assertEqual(text.count("Downloading some/dataset"), 2)


class QueuedPopupStatusTests(GuiCase):
    """T5: the waiting message clears once the queued popup shows."""

    def test_waiting_message_does_not_survive_the_popup(self):
        modal = QDialog(self.window)
        modal.setModal(True)
        modal.show()
        QTest.qWait(50)
        shown = []
        try:
            self.window._queue_modal_dialog(
                lambda: shown.append(True),
                "A new version is out. It will show once the open "
                "dialog closes.")
            self.assertIn("will show once the open dialog closes",
                          self.window.statusBar().currentMessage())
            self.assertEqual(shown, [])
        finally:
            modal.close()
            QTest.qWait(50)
        self.window._drain_dialog_queue()
        self.assertEqual(shown, [True])
        self.assertNotIn(
            "will show once the open dialog closes",
            self.window.statusBar().currentMessage())


class CtrlFTests(GuiCase):
    """T12: Ctrl+F switches to the Search tab and focuses the box."""

    def test_shortcut_switches_tab_and_focuses(self):
        self.window.show()
        self.window.tabs.setCurrentIndex(1)
        QTest.qWait(50)
        self.window._shortcut_focus_search()
        QTest.qWait(50)
        self.assertEqual(self.window.tabs.currentIndex(), 0)
        focus = self.window.focusWidget()
        self.assertTrue(
            focus is self.window.search_box
            or focus is self.window.search_box.lineEdit())


class UndoMenuTests(GuiCase):
    """T13: Edit menu Undo items track their stacks."""

    def test_empty_stacks_disable_menu_items(self):
        self.assertFalse(self.window.undo_remove_action.isEnabled())
        self.assertFalse(self.window.undo_edit_action.isEnabled())

    def test_invoked_empty_says_nothing_to_undo(self):
        self.window.undo_remove()
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Nothing to undo.")
        self.window.undo_edit()
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Nothing to undo.")

    def test_stack_present_enables_menu_item(self):
        item = _result(0)
        self.window.my_list = [item]
        self.window._undo_stack.append([(0, item)])
        self.window.undo_button.setEnabled(True)
        self.window._sync_undo_actions()
        self.assertTrue(self.window.undo_remove_action.isEnabled())


class SelfTestDialogWrapTests(GuiCase):
    """T15: the self-test dialog header wraps at its default size."""

    def test_header_label_word_wrap(self):
        dialog = SelfTestDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.header_label.wordWrap())
        self.assertIn("changes nothing", dialog.header_label.text())


class ActionLoggingTests(GuiCase):
    """T14: the user's main actions leave one plain log line each."""

    def _ring_text(self):
        return "\n".join(core.ring_lines())

    def test_token_saved_logged_without_the_token(self):
        secret = "ghp_FAKEtokenForV016TestOnly"
        self.window.token_field.setText(secret)
        self.window.save_token()
        text = self._ring_text()
        self.assertIn("GitHub token saved.", text)
        self.assertNotIn(secret, text)
        self.assertNotIn(secret, core.debug_report())

    def test_website_add_logged(self):
        self.window.site_name.setText("V016 Site")
        self.window.site_url.setText("https://example.com/?q={q}")
        self.window.add_source()
        self.assertIn("Added website V016 Site.", self._ring_text())

    def test_watch_created_logged(self):
        self.window.search_box.setCurrentText("math problems")
        self.window.watch_current_search()
        self.assertIn("Watch created:", self._ring_text())

    def test_save_to_my_list_logged(self):
        self.window.results = [_result(0)]
        self.window._show_results()
        self.window.results_table.selectRow(0)
        self.window.add_selected()
        self.assertIn("Added 1 to My List.", self._ring_text())

    def test_queue_add_logged(self):
        self.window.my_list = [_result(0)]
        self.window._refresh_list_tab()
        self.window.list_table.selectRow(0)
        self.window.add_selected_to_queue()
        self.assertIn("to the download queue.", self._ring_text())


class ColumnFitTests(GuiCase):
    """T10/T11: tables fit at both sizes, Score capped, the
    reading columns get the freed space."""

    def _seed(self):
        self.window.my_list = [_result(0), _result(1)]
        self.window._save_list()
        self.window._refresh_list_tab()
        self.window.results = [_result(i) for i in range(17)]
        self.window._searched_once = True
        self.window._show_results()

    def _check(self, width, height):
        self.window.resize(width, height)
        self.window.show()
        QTest.qWait(100)
        self.window._refit_tables()
        QTest.qWait(50)
        results = self.window.results_table
        total = sum(results.columnWidth(c)
                    for c in range(results.columnCount()))
        self.assertLessEqual(total, results.viewport().width())
        self.assertEqual(results.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(results.columnWidth(9), 132)
        # The list tab must be current before measuring: a hidden
        # tab has no real geometry yet (same note as the v0.1.4
        # My List fit test).
        self.window.tabs.setCurrentIndex(self.window.list_tab_index)
        QTest.qWait(100)
        listing = self.window.list_table
        total = sum(listing.columnWidth(c)
                    for c in range(listing.columnCount()))
        self.assertLessEqual(total, listing.viewport().width())
        self.assertLessEqual(listing.columnWidth(9), 132)

    def test_fit_at_default_1100x700(self):
        self._seed()
        self._check(1100, 700)

    def test_fit_at_1366x768(self):
        self._seed()
        self._check(1366, 768)


if __name__ == "__main__":
    unittest.main()
