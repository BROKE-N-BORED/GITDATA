"""GUI tests for the P4 leap items (T62-T67) — offscreen, no live
network. The startup background checks (online probe, watch timer)
are started by main(), not by MainWindow.__init__, so constructing
a window here never touches the network.
"""

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt

from scout import core, queue, stella_link, watch
from scout.gui import (
    R_SCORE, DownloadDialog, MainWindow, QueueDialog, StellaLinkDialog,
    WatchDialog,
)

JSONL = ('{"prompt": "a", "completion": "b"}\n'
         '{"prompt": "c", "completion": "d"}\n')


def get_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class LeapGuiTestCase(unittest.TestCase):
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
        os.environ.pop("STELLA_HOME", None)


class ScoreColumnTests(LeapGuiTestCase):
    def test_score_column_exists_and_fills(self):
        headers = [
            self.window.results_table.horizontalHeaderItem(col).text()
            for col in range(self.window.results_table.columnCount())
        ]
        self.assertIn("Score", headers)
        self.window.results = [{
            "title": "Fresh Data", "url": "https://example.com/a",
            "source": "Hugging Face", "stars": 500, "downloads": 1000,
            "updated": "2026-11-14T00:00:00Z", "description": "",
            "license": "MIT", "format": "parquet", "stella_status": "ready",
        }]
        self.window._show_results()
        cell = self.window.results_table.item(0, R_SCORE)
        self.assertIsNotNone(cell)
        self.assertIn("Worth a look", cell.text())
        stored = cell.data(Qt.ItemDataRole.UserRole)
        self.assertIsInstance(stored, int)
        self.assertGreaterEqual(stored, 80)
        self.assertIn("Updated", cell.toolTip())

    def test_detail_view_explains_the_score(self):
        self.window.results = [{
            "title": "Fresh Data", "url": "https://example.com/a",
            "source": "Hugging Face", "stars": 10, "downloads": 0,
            "updated": "2026-11-14T00:00:00Z",
            "description": "Some data.", "license": "MIT",
            "format": "", "stella_status": "",
        }]
        self.window._show_results()
        self.window.results_table.selectRow(0)
        self.window._show_result_detail()
        text = self.window.detail_view.toPlainText()
        self.assertIn("Score", text)
        self.assertIn("MIT", text)


class OfflineTests(LeapGuiTestCase):
    def test_banner_hidden_when_online(self):
        self.window._set_online(True)
        # isHidden, not isVisible: the window is never shown in
        # tests, so isVisible stays False either way.
        self.assertTrue(self.window.offline_banner.isHidden())

    def test_banner_shows_when_offline(self):
        self.window._set_online(False)
        self.assertFalse(self.window.offline_banner.isHidden())
        self.assertIn("offline", self.window.offline_banner.text().lower())

    def test_search_offline_without_cache_says_so(self):
        self.window._set_online(False)
        self.window.search_box.setCurrentText("math word problems")
        self.window.start_search()
        self.assertIn("offline",
                      self.window.statusBar().currentMessage().lower())
        self.assertFalse(
            self.window.search_worker is not None
            and self.window.search_worker.isRunning())

    def test_search_offline_with_cache_shows_saved_results(self):
        core.save_search_cache(
            "math word problems reasoning dataset", "reasoning",
            [{"title": "Saved", "url": "https://example.com/saved",
              "source": "Hugging Face", "stars": 0,
              "updated": "2026-11-14T00:00:00Z", "description": "",
              "license": "", "format": "", "topics": [], "downloads": 0,
              "notes": "", "category": "reasoning"}])
        self.window._set_online(False)
        for key, button in self.window.chip_buttons.items():
            button.setChecked(key == "reasoning")
        self.window.search_box.setCurrentText("math word problems")
        self.window.start_search()
        self.assertEqual(len(self.window.results), 1)
        self.assertEqual(self.window.results[0]["title"], "Saved")

    def test_offline_guards_online_actions(self):
        self.window._set_online(False)
        self.window.my_list = [{
            "title": "One", "url": "https://example.com/a",
            "source": "Hugging Face", "stars": 0, "updated": "",
            "license": "", "notes": "", "status": "",
            "downloads": 0, "format": "", "size_category": "",
            "stella_status": "", "stella_files": [], "stella_size": 0,
            "state": "found", "category": "all",
        }]
        self.window.start_link_check()
        self.assertIn("offline",
                      self.window.statusBar().currentMessage().lower())
        self.window.check_for_updates()
        self.assertIn("offline",
                      self.window.statusBar().currentMessage().lower())

    def test_convert_a_local_file_offline(self):
        source = Path(self.tmp.name) / "local.jsonl"
        source.write_text(JSONL, encoding="utf-8")
        with unittest.mock.patch(
                "scout.gui.QFileDialog.getOpenFileName",
                return_value=(str(source), "")), \
                unittest.mock.patch.object(
                    QMessageBox, "question",
                    return_value=QMessageBox.StandardButton.No):
            self.window.convert_local_file()
        out_dir = Path(self.tmp.name) / "stella-ready"
        outputs = list(out_dir.glob("*.jsonl"))
        self.assertEqual(len(outputs), 1)
        lines = outputs[0].read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("Converted",
                      self.window.statusBar().currentMessage())

    def test_convert_same_stem_files_in_two_folders(self):
        # v0.1.4 T1: two files called train.jsonl in different
        # folders convert to two outputs — the second convert
        # must not destroy the first file's rows.
        folder_a = Path(self.tmp.name) / "folder-a"
        folder_b = Path(self.tmp.name) / "folder-b"
        folder_a.mkdir()
        folder_b.mkdir()
        (folder_a / "train.jsonl").write_text(JSONL, encoding="utf-8")
        other = ('{"prompt": "x", "completion": "y"}\n'
                 '{"prompt": "z", "completion": "w"}\n')
        (folder_b / "train.jsonl").write_text(other, encoding="utf-8")
        with unittest.mock.patch(
                "scout.gui.QFileDialog.getOpenFileName",
                side_effect=[(str(folder_a / "train.jsonl"), ""),
                             (str(folder_b / "train.jsonl"), "")]), \
                unittest.mock.patch.object(
                    QMessageBox, "question",
                    return_value=QMessageBox.StandardButton.No):
            self.window.convert_local_file()
            self.window.convert_local_file()
        out_dir = Path(self.tmp.name) / "stella-ready"
        outputs = sorted(out_dir.glob("*.jsonl"))
        self.assertEqual(len(outputs), 2)
        names = [path.name for path in outputs]
        self.assertIn("folder-a-train.jsonl", names)
        self.assertIn("folder-b-train.jsonl", names)
        first = (out_dir / "folder-a-train.jsonl").read_text(
            encoding="utf-8")
        second = (out_dir / "folder-b-train.jsonl").read_text(
            encoding="utf-8")
        self.assertIn('"prompt": "a"', first)
        self.assertIn('"prompt": "x"', second)

    def test_search_offline_without_cache_clears_stale_results(self):
        # v0.1.4 T18: the previous search's results go away
        # under the new query instead of reading as its answer.
        self.window.results = [{
            "title": "Old", "url": "https://example.com/old",
            "source": "Hugging Face", "stars": 0, "updated": "",
            "description": "", "license": "", "format": "",
            "stella_status": ""}]
        self.window._show_results()
        self.assertEqual(self.window.results_table.rowCount(), 1)
        self.window._set_online(False)
        self.window.search_box.setCurrentText("a new query")
        self.window.start_search()
        self.assertEqual(self.window.results, [])
        self.assertEqual(self.window.results_table.rowCount(), 0)
        self.assertIn("offline",
                      self.window.results_empty_label.text().lower())


class WatchGuiTests(LeapGuiTestCase):
    def test_watch_button_saves_the_current_search(self):
        self.window.search_box.setCurrentText("math word problems")
        self.window.watch_current_search()
        watches = watch.load_watches()
        self.assertEqual(len(watches), 1)
        self.assertEqual(watches[0]["query"], "math word problems")
        self.assertEqual(watches[0]["category"], "all")

    def test_watch_button_dedups(self):
        self.window.search_box.setCurrentText("math word problems")
        self.window.watch_current_search()
        self.window.watch_current_search()
        self.assertEqual(len(watch.load_watches()), 1)

    def test_watch_dialog_lists_watches_and_new_items(self):
        made = watch.new_watch("math", "reasoning")
        made["new"] = [{"title": "Brand New",
                        "url": "https://example.com/new",
                        "source": "Hugging Face"}]
        watch.save_watches([made])
        dialog = WatchDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.watch_list.count(), 1)
        dialog.watch_list.setCurrentRow(0)
        dialog._watch_selected()
        self.assertEqual(dialog.new_list.count(), 1)
        self.assertIn("since the last run",
                      dialog.new_label.text().lower())


class QueueGuiTests(LeapGuiTestCase):
    def test_add_selected_to_queue(self):
        item = {"title": "One", "url": "https://example.com/a",
                "source": "Hugging Face", "stars": 0,
                "updated": "", "license": "", "notes": "", "status": "",
                "downloads": 0, "format": "", "size_category": "",
                "stella_status": "", "stella_files": [], "stella_size": 0,
                "state": "found", "category": "all"}
        self.window.my_list = [item]
        self.window._refresh_list_tab()
        self.window.list_table.selectRow(0)
        self.window.add_selected_to_queue()
        entries, _interrupted = queue.load_queue()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["item"]["url"], "https://example.com/a")

    def test_queue_dialog_shows_saved_entries(self):
        entries, _added = queue.enqueue(
            [], {"title": "One", "url": "https://example.com/a",
                 "source": "Hugging Face"})
        entries, _added = queue.enqueue(
            entries, {"title": "Two", "url": "https://example.com/b",
                      "source": "GitHub"})
        queue.save_queue(entries)
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.queue_table.rowCount(), 2)
        self.assertEqual(dialog.queue_table.item(0, 0).text(), "One")


class WatchDialogV014Tests(LeapGuiTestCase):
    """v0.1.4 T42/T43/T44/T46/T48: the watch dialog's buttons,
    selection, and empty state."""

    def _save_watch(self, query="math", new=None, enabled=True):
        made = watch.new_watch(query, "reasoning")
        made["enabled"] = enabled
        if new:
            made["new"] = new
            made["new_seen"] = False
        watch.save_watches([made])
        return made

    def test_empty_dialog_disables_buttons_and_teaches(self):
        dialog = WatchDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.check_now_button.isEnabled())
        self.assertFalse(dialog.add_new_button.isEnabled())
        self.assertFalse(dialog.pause_button.isEnabled())
        self.assertFalse(dialog.remove_button.isEnabled())
        self.assertIn("Watch this search", dialog.new_label.text())

    def test_pause_keeps_selection_and_shows_resume(self):
        first = watch.new_watch("math", "reasoning")
        second = watch.new_watch("stories", "reasoning")
        watch.save_watches([first, second])
        dialog = WatchDialog(self.window)
        self.addCleanup(dialog.close)
        dialog.watch_list.setCurrentRow(1)
        dialog._toggle_paused()
        self.assertEqual(dialog.watch_list.currentRow(), 1)
        self.assertEqual(dialog.pause_button.text(), "Resume this watch")
        self.assertTrue(dialog.pause_button.isEnabled())

    def test_picking_a_watch_clears_the_flag_but_keeps_the_items(self):
        self._save_watch("math", new=[{
            "title": "Brand New", "url": "https://example.com/new",
            "source": "Hugging Face"}])
        dialog = WatchDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertIn("1 new dataset",
                      dialog.watch_list.item(0).text())
        dialog.watch_list.setCurrentRow(0)
        self.assertNotIn("new dataset",
                         dialog.watch_list.item(0).text())
        self.assertEqual(dialog.new_list.count(), 1)
        self.assertEqual(dialog.new_list.item(0).text(), "Brand New")

    def test_offline_check_now_says_so(self):
        self._save_watch("math")
        self.window._online = False
        dialog = WatchDialog(self.window)
        self.addCleanup(dialog.close)
        dialog._check_now()
        self.assertIn("offline",
                      self.window.statusBar().currentMessage().lower())

    def test_force_run_with_nothing_to_check_says_so(self):
        self.window._online = True
        self.window._run_due_watches(force=True)
        self.assertIn("No watched searches to check right now.",
                      self.window.statusBar().currentMessage())


class QueueDialogV014Tests(LeapGuiTestCase):
    """v0.1.4 T44/T45/T48: the queue dialog's buttons, retry,
    and empty state."""

    def test_empty_dialog_disables_buttons_and_teaches(self):
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.start_button.isEnabled())
        self.assertFalse(dialog.retry_button.isEnabled())
        self.assertFalse(dialog.remove_button.isEnabled())
        self.assertFalse(dialog.clear_button.isEnabled())
        self.assertFalse(dialog.empty_label.isHidden())
        self.assertIn("Add selected to queue",
                      dialog.empty_label.text())

    def test_retry_failed_requeues_and_enables_start(self):
        entries, _added = queue.enqueue(
            [], {"title": "One", "url": "https://example.com/a",
                 "source": "Hugging Face"})
        entries[0]["state"] = queue.FAILED
        entries[0]["error"] = "Download failed."
        queue.save_queue(entries)
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.start_button.isEnabled())
        self.assertTrue(dialog.retry_button.isEnabled())
        dialog._retry_failed()
        self.assertEqual(dialog.entries[0]["state"], queue.QUEUED)
        self.assertEqual(dialog.entries[0]["error"], "")
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertFalse(dialog.retry_button.isEnabled())
        self.assertIn("queued again", dialog.log_view.toPlainText())

    def test_retry_failed_only_retries_the_picked_row(self):
        entries, _added = queue.enqueue(
            [], {"title": "One", "url": "https://example.com/a",
                 "source": "Hugging Face"})
        entries, _added = queue.enqueue(
            entries, {"title": "Two", "url": "https://example.com/b",
                      "source": "Hugging Face"})
        entries[0]["state"] = queue.FAILED
        entries[1]["state"] = queue.FAILED
        queue.save_queue(entries)
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        dialog.queue_table.selectRow(1)
        dialog._retry_failed()
        self.assertEqual(dialog.entries[0]["state"], queue.FAILED)
        self.assertEqual(dialog.entries[1]["state"], queue.QUEUED)

    def test_remove_enables_only_with_a_selection(self):
        entries, _added = queue.enqueue(
            [], {"title": "One", "url": "https://example.com/a",
                 "source": "Hugging Face"})
        queue.save_queue(entries)
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.remove_button.isEnabled())
        dialog.queue_table.selectRow(0)
        self.assertTrue(dialog.remove_button.isEnabled())

    def test_refresh_mid_run_keeps_the_running_row(self):
        # v0.1.4 T14: while a run is in progress the in-memory
        # entries are the truth — no disk reload, no false
        # "stopped part way through" line, and "Downloading" shows.
        entries, _added = queue.enqueue(
            [], {"title": "One", "url": "https://example.com/a",
                 "source": "Hugging Face"})
        queue.save_queue(entries)
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        dialog.entries[0]["state"] = queue.RUNNING
        dialog.worker = unittest.mock.Mock()
        dialog.worker.isRunning.return_value = True
        dialog.refresh()
        self.assertEqual(dialog.entries[0]["state"], queue.RUNNING)
        self.assertEqual(dialog.queue_table.item(0, 1).text(),
                         "Downloading")
        self.assertNotIn("stopped part way",
                         dialog.log_view.toPlainText())

    def test_entry_done_marks_the_list_item_converted(self):
        # v0.1.4 T15: a queue run marks the matching My List row
        # converted, by URL.
        item = {"title": "One", "url": "https://example.com/a",
                "source": "Hugging Face", "state": "found"}
        self.window.my_list = [dict(item)]
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()
        entries, _added = queue.enqueue([], dict(item))
        queue.save_queue(entries)
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        entry_id = dialog.entries[0]["id"]
        dialog._on_entry_done(entry_id, True, "Converted 2 rows.",
                              ["/tmp/out.jsonl"], 2, "converted")
        self.assertEqual(self.window.my_list[0]["state"], "converted")
        self.assertEqual(core.load_list()[0]["state"], "converted")

    def test_paused_entry_logs_a_pause_not_a_failure(self):
        # v0.1.4 T14: a clean pause logs "Paused", not the
        # failure report "Nothing could be downloaded."
        entries, _added = queue.enqueue(
            [], {"title": "One", "url": "https://example.com/a",
                 "source": "Hugging Face"})
        queue.save_queue(entries)
        dialog = QueueDialog(self.window)
        self.addCleanup(dialog.close)
        entry_id = dialog.entries[0]["id"]
        dialog._on_entry_done(entry_id, False,
                              "Nothing could be downloaded.",
                              [], 0, "stopped")
        self.assertIn("Paused. Start resumes the download.",
                      dialog.log_view.toPlainText())
        self.assertNotIn("Nothing could be downloaded.",
                         dialog.log_view.toPlainText())
        self.assertEqual(dialog.entries[0]["state"], queue.QUEUED)


class DownloadDialogV014Tests(LeapGuiTestCase):
    """v0.1.4 T16/T30: a run that converted nothing ends at 0
    with no checkmark, and Open the folder stays disabled."""

    def test_zero_conversion_run_has_no_checkmark(self):
        item = {"title": "One", "url": "https://example.com/a",
                "source": "Hugging Face", "state": "found"}
        dialog = DownloadDialog(
            [(0, item)], Path(self.tmp.name) / "out",
            parent=self.window)
        self.addCleanup(dialog.close)
        dialog._on_item_done(0, False, "Nothing could be downloaded.",
                             [], 0, "found")
        dialog._on_all_done(False)
        self.assertEqual(dialog.bar.value(), 0)
        self.assertNotIn("✓", dialog.log_view.toPlainText())
        self.assertIn("Nothing converted.",
                      dialog.log_view.toPlainText())
        self.assertFalse(dialog.open_button.isEnabled())
        self.assertTrue(dialog.close_button.isEnabled())


class StellaLinkGuiTests(LeapGuiTestCase):
    def _fake_stella_home(self):
        home = Path(self.tmp.name) / "stella"
        profile_dir = home / "profiles" / "demo"
        profile_dir.mkdir(parents=True)
        (profile_dir / "profile.json").write_text(json.dumps({
            "stella_version": "0.24.0", "kind": "profile",
            "name": "demo", "display_name": "demo",
            "ollama_tag": "demo-model:2b", "base_ref": "demo-model:2b",
            "created": "2026-11-01T00:00:00+00:00",
        }), encoding="utf-8")
        os.environ["STELLA_HOME"] = str(home)
        return home

    def test_download_dialog_offers_stella_after_a_convert(self):
        items = [(0, {"title": "One",
                      "url": "https://example.com/a",
                      "source": "Hugging Face", "state": "found"})]
        dialog = DownloadDialog(
            items, Path(self.tmp.name) / "out", parent=self.window)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.stella_button.isEnabled())
        dialog._on_item_done(0, True, "2 rows", ["/tmp/out/train.jsonl"],
                             2, "converted")
        dialog._on_all_done(False)
        self.assertTrue(dialog.stella_button.isEnabled())

    def test_stella_link_dialog_creates_the_project(self):
        home = self._fake_stella_home()
        jsonl = Path(self.tmp.name) / "train.jsonl"
        jsonl.write_text(JSONL, encoding="utf-8")
        dialog = StellaLinkDialog([str(jsonl)], parent=self.window)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.profile_combo.count(), 1)
        dialog.project_field.setText("my first dataset")
        result = dialog.link()
        self.assertTrue(result["ok"], result["message"])
        project_dir = home / "profiles" / "demo" / "projects" \
            / "my-first-dataset"
        self.assertTrue((project_dir / "project.json").is_file())
        self.assertTrue(
            (project_dir / "my-first-dataset.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
