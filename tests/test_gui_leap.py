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
