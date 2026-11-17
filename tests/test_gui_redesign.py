"""GUI tests for the v0.1.3 UI redesign (T44-T61) — offscreen, no live network."""
import json
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication, QDialog, QLabel, QMessageBox, QPushButton, QScrollArea,
)
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from scout import apis, core
from scout.gui import (
    DARK_STYLESHEET, HOW_TO_USE_SECTIONS, SHORTCUTS, AddLinkDialog,
    CollapsibleSection, EditItemDialog, FlowLayout, L_LINK, L_NOTES,
    L_STATE, L_STELLA, L_TITLE, MainWindow, R_DESC, R_SCORE, R_STELLA,
    R_TITLE, ShortcutsDialog, _explain, _heading, _looks_like_url,
)

APP = None


def get_app():
    global APP
    if APP is None:
        # Reuse the singleton when another test module already
        # made it: a second QApplication raises (libshiboken).
        APP = QApplication.instance() or QApplication([])
    return APP


class RedesignTestCase(unittest.TestCase):
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

    def _menu_actions(self, menu_name):
        menus = {a.text(): a.menu()
                 for a in self.window.menuBar().actions()}
        return {a.text(): a for a in menus[menu_name].actions()}


class FocusRingTests(RedesignTestCase):
    """T44 (023): a visible focus ring, as a border."""

    def test_stylesheet_has_gold_focus_border(self):
        self.assertIn(":focus", DARK_STYLESHEET)
        self.assertIn("2px solid #ffd166", DARK_STYLESHEET)

    def test_focus_rule_covers_the_focusable_families(self):
        for family in ("QPushButton:focus", "QLineEdit:focus",
                       "QComboBox:focus", "QTableWidget:focus",
                       "QTabBar::tab:focus"):
            self.assertIn(family, DARK_STYLESHEET)


class ShortcutsTests(RedesignTestCase):
    """T45 (065, 028): discoverable shortcuts."""

    def test_question_mark_is_in_the_registry(self):
        keys = [key for key, _d, _s, _a in SHORTCUTS]
        self.assertIn("?", keys)

    def test_overlay_lists_every_registry_shortcut(self):
        dialog = ShortcutsDialog(self.window)
        text = dialog.text.toPlainText()
        for key, desc, _scope, _action in SHORTCUTS:
            self.assertIn(key, text)
            self.assertIn(desc, text)
        dialog.close()

    def test_overlay_groups_by_context(self):
        dialog = ShortcutsDialog(self.window)
        text = dialog.text.toPlainText()
        self.assertIn("Anywhere in the window", text)
        self.assertIn("In My List", text)
        dialog.close()

    def test_help_menu_has_keyboard_shortcuts(self):
        actions = self._menu_actions("Help")
        self.assertIn("Keyboard shortcuts", actions)
        self.assertEqual(actions["Keyboard shortcuts"].shortcut(),
                         QKeySequence("?"))

    def test_menu_items_show_their_keys(self):
        file_actions = self._menu_actions("File")
        self.assertEqual(
            file_actions["Export STELLA manifest"].shortcut(),
            QKeySequence("Ctrl+E"))
        edit_actions = self._menu_actions("Edit")
        self.assertEqual(
            edit_actions["Remove selected"].shortcut(),
            QKeySequence("Delete"))

    def test_search_box_tooltip_names_ctrl_f(self):
        self.assertIn("Ctrl+F", self.window.search_box.toolTip())

    def test_overlay_opens_from_the_menu_item(self):
        actions = self._menu_actions("Help")
        with unittest.mock.patch(
                "scout.gui.ShortcutsDialog") as dialog_cls:
            actions["Keyboard shortcuts"].trigger()
            dialog_cls.assert_called_once()
            dialog_cls.return_value.exec.assert_called_once()


class EmptyStateTests(RedesignTestCase):
    """T46 (031): never-searched vs zero-results, Sources empty state."""

    def test_fresh_window_shows_never_searched_text(self):
        self.assertIn("No results yet",
                      self.window.results_empty_label.text())

    def test_zero_results_suggests_broadening(self):
        self.window._search_done([])
        self.assertIn("No results found",
                      self.window.results_empty_label.text())
        self.assertIn("Try fewer words",
                      self.window.results_empty_label.text())

    def test_clearing_the_skeleton_shows_the_empty_label_again(self):
        self.window._show_skeleton()
        self.assertTrue(self.window.results_empty_label.isHidden())
        self.window._clear_skeleton()
        self.assertFalse(self.window.results_empty_label.isHidden())
        self.assertIn("No results yet",
                      self.window.results_empty_label.text())

    def test_sources_empty_state_shows_when_all_sites_removed(self):
        self.assertTrue(self.window.sources_empty_label.isHidden())
        core.save_sources([])
        self.window._refresh_sources_table()
        self.assertFalse(self.window.sources_empty_label.isHidden())
        self.assertIn("No websites here",
                      self.window.sources_empty_label.text())


class TooltipTests(RedesignTestCase):
    """T47 (040): tooltips that teach, verb-first, 60-75 chars max."""

    def test_action_buttons_have_tooltips(self):
        buttons = [
            self.window.search_button, self.window.stop_search_button,
            self.window.add_selected_button, self.window.add_all_button,
            self.window.stella_check_button, self.window.found_add_button,
            self.window.remove_button, self.window.clear_button,
            self.window.undo_button, self.window.check_button,
            self.window.stop_check_button, self.window.copy_button,
            self.window.edit_button, self.window.list_stella_button,
            self.window.download_button, self.window.export_md_button,
            self.window.export_json_button, self.window.export_stella_button,
            self.window.import_button, self.window.send_button,
            self.window.site_add_button, self.window.restore_button,
            self.window.token_save_button, self.window.debug_button,
            self.window.collection_new_button,
            self.window.collection_rename_button,
            self.window.collection_delete_button,
        ]
        for button in buttons:
            self.assertTrue(button.toolTip(),
                            f"no tooltip on {button.text()!r}")
            self.assertLessEqual(len(button.toolTip()), 75,
                                 f"tooltip too long on {button.text()!r}")

    def test_button_tooltips_name_their_keys(self):
        self.assertIn("Delete", self.window.remove_button.toolTip())
        self.assertIn("Ctrl+E", self.window.export_stella_button.toolTip())

    def test_menu_items_carry_status_tips(self):
        for menu_name in ("File", "Edit", "View", "Help"):
            for text, action in self._menu_actions(menu_name).items():
                if action.isSeparator():
                    continue
                self.assertTrue(action.statusTip(),
                                f"no status tip on menu item {text!r}")


class InlineValidationTests(RedesignTestCase):
    """T48 (044): inline validation on blur, beside the field."""

    def test_untouched_field_gets_no_error_on_blur(self):
        self.window.site_url.editingFinished.emit()
        self.assertTrue(self.window.site_url_error.isHidden())

    def test_bad_url_warns_beside_the_field(self):
        self.window.site_url.setText("not a url")
        self.window.site_url.setModified(True)
        self.window.site_url.editingFinished.emit()
        self.assertFalse(self.window.site_url_error.isHidden())
        self.assertIn("does not look like a web address",
                      self.window.site_url_error.text())

    def test_good_url_clears_the_warning(self):
        self.window.site_url.setText("not a url")
        self.window.site_url.setModified(True)
        self.window.site_url.editingFinished.emit()
        self.assertFalse(self.window.site_url_error.isHidden())
        self.window.site_url.setText("https://example.com/s?q={q}")
        self.window.site_url.editingFinished.emit()
        self.assertTrue(self.window.site_url_error.isHidden())

    def test_empty_name_warned_on_blur_after_typing(self):
        self.window.site_name.setText("x")
        self.window.site_name.setText("")
        self.window.site_name.setModified(True)
        self.window.site_name.editingFinished.emit()
        self.assertFalse(self.window.site_name_error.isHidden())

    def test_add_source_with_empty_fields_shows_inline_errors(self):
        before = len(core.load_sources())
        self.window.site_name.setText("")
        self.window.site_url.setText("")
        self.window.add_source()
        self.assertFalse(self.window.site_name_error.isHidden())
        self.assertFalse(self.window.site_url_error.isHidden())
        self.assertEqual(len(core.load_sources()), before)

    def test_short_token_warns_but_still_saves(self):
        self.window.token_field.setText("abc")
        self.window.token_field.setModified(True)
        self.window.token_field.editingFinished.emit()
        self.assertFalse(self.window.token_error.isHidden())
        self.window.save_token()
        self.assertEqual(core.get_github_token(), "abc")

    def test_add_link_dialog_stays_open_on_empty_link(self):
        dialog = AddLinkDialog(self.window)
        dialog.found_url.setText("")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertFalse(dialog.url_error.isHidden())
        self.assertIn("Paste a link first", dialog.url_error.text())
        dialog.close()

    def test_add_link_dialog_accepts_a_good_link(self):
        dialog = AddLinkDialog(self.window)
        dialog.found_url.setText("https://example.com/data")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog.close()

    def test_url_plausibility_check(self):
        self.assertTrue(_looks_like_url("https://example.com/s?q={q}"))
        self.assertTrue(_looks_like_url("example.com/search"))
        self.assertFalse(_looks_like_url("not a url"))
        self.assertFalse(_looks_like_url(""))
        self.assertFalse(_looks_like_url("localhost"))


class MyListSectionsTests(RedesignTestCase):
    """T49 (012, 049): collapsible sections and a scroll area."""

    def test_three_sections_exist_and_start_expanded(self):
        for section in (self.window.remove_section,
                        self.window.edit_section,
                        self.window.export_section):
            self.assertIsInstance(section, CollapsibleSection)
            self.assertTrue(section.is_expanded())
            self.assertFalse(section.content.isHidden())

    def test_collapsing_hides_the_buttons(self):
        self.window.remove_section.set_expanded(False)
        self.assertFalse(self.window.remove_section.is_expanded())
        self.assertTrue(self.window.remove_section.content.isHidden())
        self.assertTrue(
            self.window.remove_section.toggle.text().startswith("▸ "))
        self.window.remove_section.set_expanded(True)
        self.assertTrue(
            self.window.remove_section.toggle.text().startswith("▾ "))

    def test_buttons_live_inside_the_sections(self):
        self.assertIs(self.window.remove_button.parent(),
                      self.window.remove_section.content)
        self.assertIs(self.window.edit_button.parent(),
                      self.window.edit_section.content)
        self.assertIs(self.window.export_md_button.parent(),
                      self.window.export_section.content)

    def test_my_list_tab_has_a_scroll_area(self):
        tab = self.window.tabs.widget(self.window.list_tab_index)
        areas = tab.findChildren(QScrollArea)
        self.assertEqual(len(areas), 1)
        self.assertTrue(areas[0].widgetResizable())


class DetailViewTests(RedesignTestCase):
    """T50 (051): overview first, details on demand."""

    def _seed_result(self):
        self.window._search_done([{
            "title": "Math sets", "url": "https://example.com/math",
            "category": "reasoning", "source": "GitHub", "stars": 5,
            "updated": "2026-11-01T00:00:00Z",
            "description": "A very long description of a math dataset.",
            "license": "MIT", "format": "jsonl",
            "stella_files": ["train.jsonl"], "stella_size": 1234}])

    def test_placeholder_before_any_selection(self):
        self.assertIn("Click a row to see",
                      self.window.detail_view.toPlainText())

    def test_selecting_a_row_shows_full_detail(self):
        self._seed_result()
        self.window.results_table.selectRow(0)
        QTest.qWait(50)
        text = self.window.detail_view.toPlainText()
        self.assertIn("A very long description of a math dataset.", text)
        self.assertIn("License: MIT", text)
        self.assertIn("Files: train.jsonl", text)
        self.assertIn("Size: 1.2 KB total", text)
        self.assertIn("Link: https://example.com/math", text)

    def test_unchecked_result_says_files_are_not_checked_yet(self):
        self.window._search_done([{
            "title": "No files", "url": "https://example.com/none",
            "category": "reasoning", "source": "GitHub", "stars": 1,
            "updated": "2026-11-01T00:00:00Z",
            "description": "d", "license": "", "format": ""}])
        self.window.results_table.selectRow(0)
        QTest.qWait(50)
        self.assertIn("Files: not checked yet",
                      self.window.detail_view.toPlainText())


class SiteWrapTests(RedesignTestCase):
    """T51 (012): the site buttons wrap."""

    def test_site_buttons_use_a_flow_layout(self):
        self.assertIsInstance(self.window.site_buttons_row, FlowLayout)

    def test_narrow_width_wraps_to_more_rows(self):
        sites = [{"name": f"Site {i}",
                  "url": f"https://example{i}.com/s?q={{q}}"}
                 for i in range(1, 13)]
        core.save_sources(sites)
        self.window._refresh_site_buttons()
        layout = self.window.site_buttons_row
        self.assertEqual(layout.count(), 12)
        narrow = layout.heightForWidth(400)
        wide = layout.heightForWidth(2000)
        self.assertGreater(narrow, wide)


class SkeletonTests(RedesignTestCase):
    """T52 (011): the skeleton matches the table, elapsed time shows."""

    def test_skeleton_fills_every_column(self):
        self.window._show_skeleton()
        for col in range(self.window.results_table.columnCount()):
            cell = self.window.results_table.item(0, col)
            self.assertIsNotNone(cell, f"column {col} is empty")
            self.assertTrue(cell.text())
        self.assertEqual(self.window.results_table.item(0, 0).text(),
                         "Loading...")
        self.window._clear_skeleton()

    def test_elapsed_time_shows_past_ten_seconds(self):
        self.window._search_started_at = time.monotonic() - 15
        self.window._tick_search_elapsed()
        self.assertIn("15 seconds",
                      self.window.search_elapsed_label.text())

    def test_elapsed_time_hidden_before_ten_seconds(self):
        self.window._search_started_at = time.monotonic() - 5
        self.window._tick_search_elapsed()
        self.assertEqual(self.window.search_elapsed_label.text(), "")


class DensityTests(RedesignTestCase):
    """T53 (064): density control, persisted."""

    def test_comfortable_is_the_default(self):
        self.assertEqual(self.window._density(), "comfortable")
        self.assertEqual(
            self.window.results_table.verticalHeader()
            .defaultSectionSize(), 48)
        self.assertEqual(
            self.window.list_table.verticalHeader()
            .defaultSectionSize(), 48)

    def test_compact_sets_32px_rows_and_persists(self):
        self.window.set_density("compact")
        self.assertEqual(
            self.window.results_table.verticalHeader()
            .defaultSectionSize(), 32)
        self.assertEqual(
            self.window.list_table.verticalHeader()
            .defaultSectionSize(), 32)
        self.assertEqual(core.load_config().get("density"), "compact")
        self.assertTrue(self.window.density_compact_action.isChecked())

    def test_compact_survives_a_new_window(self):
        self.window.set_density("compact")
        second = MainWindow()
        self.addCleanup(second.close)
        self.assertEqual(second._density(), "compact")
        self.assertEqual(
            second.results_table.verticalHeader()
            .defaultSectionSize(), 32)

    def test_view_menu_has_both_densities(self):
        actions = self._menu_actions("View")
        self.assertIn("Comfortable rows", actions)
        self.assertIn("Compact rows", actions)


class HowToUseIndexTests(RedesignTestCase):
    """T54 (012): How to Use gets a clickable index."""

    def setUp(self):
        super().setUp()
        self.window.show_how_to_use()
        self.howto = self.window.howto_window
        self.addCleanup(self._close_howto)

    def _close_howto(self):
        if self.window.howto_window is not None:
            self.window.howto_window.close()
            QTest.qWait(50)

    def test_index_lists_all_sections_plus_all(self):
        self.assertEqual(self.howto.index.count(),
                         len(HOW_TO_USE_SECTIONS) + 1)
        self.assertEqual(self.howto.index.item(0).text(), "All sections")
        for row, (title, _body) in enumerate(HOW_TO_USE_SECTIONS, start=1):
            self.assertEqual(self.howto.index.item(row).text(), title)

    def test_default_view_shows_every_section(self):
        text = self.howto.text.toPlainText()
        for title, _body in HOW_TO_USE_SECTIONS:
            self.assertIn(title, text)

    def test_picking_a_section_shows_only_that_section(self):
        self.howto.index.setCurrentRow(1)
        QTest.qWait(50)
        text = self.howto.text.toPlainText()
        self.assertTrue(text.startswith("Search\n"))
        self.assertNotIn("Debug report", text)


class HealthBarTests(RedesignTestCase):
    """T55 (037): one small bar on the stats strip."""

    def test_bar_and_takeaway_follow_the_list(self):
        self.assertEqual(self.window.health_takeaway.text(),
                         "No links saved yet")
        self.window.my_list = [core.normalize_item({
            "title": t, "url": f"https://example.com/{t}",
            "status": s}) for t, s in
            (("a", "ok"), ("b", "ok"), ("c", "dead"), ("d", ""))]
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()
        self.assertEqual(self.window.health_takeaway.text(),
                         "Most links work")
        self.assertIn("Working: 2, dead: 1, unknown: 1",
                      self.window.health_bar.toolTip())

    def test_all_dead_takeaway(self):
        self.window.my_list = [core.normalize_item({
            "title": "a", "url": "https://example.com/a",
            "status": "dead"})]
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()
        self.assertEqual(self.window.health_takeaway.text(),
                         "Many links are dead")


class ChecklistTests(RedesignTestCase):
    """T56 (020): first-run setup checklist."""

    def test_first_step_is_honestly_ticked(self):
        labels = self.window.checklist_labels
        self.assertTrue(labels["installed"].text().startswith("✓ "))
        self.assertTrue(labels["search"].text().startswith("○ "))
        self.assertTrue(labels["save"].text().startswith("○ "))

    def test_search_ticks_its_step(self):
        self.window._search_done([])
        self.assertTrue(
            self.window.checklist_labels["search"].text().startswith("✓ "))

    def test_checklist_goes_away_when_search_and_save_are_done(self):
        self.window._search_done([])
        self.assertFalse(self.window.checklist_box.isHidden())
        self.window.my_list = [core.normalize_item({
            "title": "a", "url": "https://example.com/a"})]
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()
        # The completion beat (v0.1.4 T36): the ticked list shows
        # for a moment before hiding, instead of vanishing with
        # the save step still unticked.
        self.assertFalse(self.window.checklist_box.isHidden())
        self.assertTrue(
            self.window.checklist_labels["save"].text().startswith("✓ "))
        QTest.qWait(1700)
        self.assertTrue(self.window.checklist_box.isHidden())

    def test_dismiss_hides_it_for_good(self):
        self.window._dismiss_checklist()
        self.assertTrue(self.window.checklist_box.isHidden())
        self.assertTrue(core.load_config().get("checklist_dismissed"))
        second = MainWindow()
        self.addCleanup(second.close)
        self.assertTrue(second.checklist_box.isHidden())


class CompletionTests(RedesignTestCase):
    """T57 (008, 001): mark the completion moment."""

    def test_link_check_finish_shows_checkmark_and_summary(self):
        self.window.my_list = [core.normalize_item({
            "title": t, "url": f"https://example.com/{t}",
            "status": s}) for t, s in (("a", "ok"), ("b", "dead"))]
        self.window._link_check_done(stopped=False)
        self.assertEqual(self.window.finish_label.text(), "✓ Done")
        self.assertTrue(
            self.window.statusBar().currentMessage().startswith("✓ "))
        self.assertIn("1 working, 1 dead",
                      self.window.statusBar().currentMessage())

    def test_stopped_check_is_not_a_finish(self):
        self.window._link_check_done(stopped=True)
        self.assertEqual(self.window.finish_label.text(), "")
        self.assertNotIn("✓", self.window.statusBar().currentMessage())

    def test_stella_check_finish_counts_ready(self):
        from scout.gui import StellaCheckWorker
        items = [core.normalize_item({
            "title": t, "url": f"https://example.com/{t}",
            "stella_status": s}) for t, s in
            (("a", "ready"), ("b", "convert"))]
        self.window.results = items
        self.window._stella_target = "results"
        self.window.stella_worker = StellaCheckWorker(
            [(0, items[0]), (1, items[1])])
        self.window._stella_check_done(stopped=False)
        self.assertEqual(self.window.finish_label.text(), "✓ Done")
        # v0.1.4 T17: the summary counts ready and convertible
        # separately, instead of only counting ready.
        self.assertIn("STELLA check done: 1 ready now, "
                      "1 need conversion.",
                      self.window.statusBar().currentMessage())

    def test_starting_work_clears_the_finish(self):
        self.window._mark_finished("Link check done: 1 working.")
        self.assertEqual(self.window.finish_label.text(), "✓ Done")
        self.window._clear_finished()
        self.assertEqual(self.window.finish_label.text(), "")


class TextScalingTests(RedesignTestCase):
    """T58 (053): text sizes come from the font."""

    def test_explain_has_no_fixed_pixel_size(self):
        label = _explain("hello")
        self.assertNotIn("font-size", label.styleSheet())

    def test_heading_is_bold_and_bigger_than_body(self):
        label = _heading("Title")
        self.assertTrue(label.font().bold())
        plain = _explain("body")
        self.assertGreater(label.font().pointSizeF(),
                           plain.font().pointSizeF())

    def test_no_fixed_font_sizes_left_in_the_gui(self):
        import scout.gui as gui_module
        source = Path(gui_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("font-size: 12px", source)
        self.assertNotIn("font-size: 16px", source)


class MicrocopyTests(RedesignTestCase):
    """T59 (071): buttons say what happens; no hard-coded count."""

    def test_add_link_dialog_button_says_add_link(self):
        dialog = AddLinkDialog(self.window)
        buttons = dialog.findChildren(QPushButton)
        texts = [button.text() for button in buttons]
        self.assertIn("Add link", texts)
        self.assertNotIn("OK", texts)
        dialog.close()

    def test_edit_dialog_button_says_save_changes(self):
        item = core.normalize_item(
            {"title": "A", "url": "https://example.com/a"})
        dialog = EditItemDialog(item, self.window)
        texts = [b.text() for b in dialog.findChildren(QPushButton)]
        self.assertIn("Save changes", texts)
        self.assertNotIn("OK", texts)
        dialog.close()

    def test_no_hard_coded_five_in_visible_text(self):
        import scout.gui as gui_module
        source = Path(gui_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("Pick one of the five", source)
        dialog = AddLinkDialog(self.window)
        for label in dialog.findChildren(QLabel):
            self.assertNotIn("five", label.text())
        dialog.close()

    def test_how_to_use_names_the_categories_from_data(self):
        body = dict(HOW_TO_USE_SECTIONS)["Categories and filters"]
        for key, (label, _suffix) in core.CATEGORIES.items():
            if key == "all":
                continue
            self.assertIn(label, body)


class DialogQueueTests(RedesignTestCase):
    """T60 (003, U18): completion popups queue behind open modals."""

    def test_search_failure_queues_behind_a_modal(self):
        modal = QDialog(self.window)
        modal.setModal(True)
        modal.show()
        QTest.qWait(50)
        try:
            with unittest.mock.patch.object(
                    QMessageBox, "warning") as warning:
                self.window._search_failed("Search failed. Both down.")
                self.assertFalse(warning.called)
                self.assertEqual(len(self.window._dialog_queue), 1)
                self.assertIn("will show when the open window closes",
                              self.window.statusBar().currentMessage())
        finally:
            modal.close()
            QTest.qWait(50)
        with unittest.mock.patch.object(
                QMessageBox, "warning") as warning:
            self.window._drain_dialog_queue()
            self.assertTrue(warning.called)
            self.assertEqual(self.window._dialog_queue, [])

    def test_search_failure_shows_at_once_with_no_modal(self):
        with unittest.mock.patch.object(
                QMessageBox, "warning") as warning:
            self.window._search_failed("Search failed. Both down.")
            self.assertTrue(warning.called)
            self.assertEqual(self.window._dialog_queue, [])

    def test_update_check_uses_the_status_bar_not_a_popup(self):
        with unittest.mock.patch.object(
                QMessageBox, "information") as info:
            self.window._update_check_done("v9.9.9")
            self.assertFalse(info.called)
        self.assertIn("Version v9.9.9 is out",
                      self.window.statusBar().currentMessage())
        with unittest.mock.patch.object(
                QMessageBox, "information") as info:
            self.window._update_check_done(None)
            self.assertFalse(info.called)


class SourcesHeaderTests(RedesignTestCase):
    """T61 (071): the Remove column is labeled."""

    def test_remove_column_has_a_header(self):
        header = self.window.sources_table.horizontalHeaderItem(2)
        self.assertEqual(header.text(), "Remove")


LONG_TITLE = ("A very long dataset title that keeps going and going to "
              "test how the table copes with long text in a narrow window")
LONG_DESC = ("A very long description that keeps going and going with "
             "lots of detail about the dataset contents and its purpose.")


def _result(**overrides):
    item = {"title": "Math sets", "url": "https://example.com/math",
            "category": "reasoning", "source": "Hugging Face", "stars": 5,
            "downloads": 100, "updated": "2026-11-01T00:00:00Z",
            "description": "A math dataset.", "license": "MIT",
            "format": "JSONL", "stella_status": "convert",
            "stella_files": ["train.jsonl"], "stella_size": 1234}
    item.update(overrides)
    return item


class SearchTabFitTests(RedesignTestCase):
    """v0.1.4 T31: the Search tab fits at the default 1100x700."""

    def test_results_columns_and_buttons_fit_at_1100x700(self):
        self.window.show()
        self.window.resize(1100, 700)
        QTest.qWait(100)
        self.window._search_done([
            _result(title=LONG_TITLE, description=LONG_DESC),
            _result(title="Short", url="https://example.com/short"),
        ])
        QTest.qWait(100)
        # The fit runs on search and on resize; re-fit once the
        # layout has settled, as a resize would.
        self.window._fit_results_columns()
        QTest.qWait(100)
        table = self.window.results_table
        total = sum(table.columnWidth(col)
                    for col in range(table.columnCount()))
        self.assertLessEqual(total, table.viewport().width())
        self.assertFalse(table.horizontalScrollBar().isVisible())
        tab = self.window.tabs.widget(0)
        scroll = tab.findChild(QScrollArea)
        scroll.verticalScrollBar().setValue(0)
        QTest.qWait(50)
        load_more = self.window.load_more_button
        self.assertLessEqual(load_more.y() + load_more.height(),
                             scroll.viewport().height())
        add = self.window.add_selected_button
        self.assertLessEqual(add.y() + add.height(),
                             scroll.viewport().height())

    def test_fit_survives_the_first_run_checklist(self):
        # The checklist is visible on a first search: the tab
        # must still fit, not squeeze the detail pane to 2 lines.
        self.window.show()
        self.window.resize(1100, 700)
        QTest.qWait(100)
        self.window._search_done([_result()])
        QTest.qWait(100)
        self.assertTrue(self.window.checklist_box.isVisible())
        tab = self.window.tabs.widget(0)
        scroll = tab.findChild(QScrollArea)
        scroll.verticalScrollBar().setValue(0)
        QTest.qWait(50)
        load_more = self.window.load_more_button
        self.assertLessEqual(load_more.y() + load_more.height(),
                             scroll.viewport().height())


class MyListFitTests(RedesignTestCase):
    """v0.1.4 T32: Title is capped, State and STELLA stay visible."""

    def test_long_title_does_not_push_state_off_screen(self):
        self.window.my_list = [core.normalize_item(_result(
            title=LONG_TITLE, notes="A very long note that also keeps "
            "going and going past any reasonable width for a cell"))]
        core.save_list(self.window.my_list)
        self.window.show()
        self.window.resize(1366, 768)
        # The tab must be current before measuring: a hidden tab
        # has no real geometry yet.
        self.window.tabs.setCurrentIndex(self.window.list_tab_index)
        self.window._refresh_list_tab()
        QTest.qWait(100)
        table = self.window.list_table
        self.assertLessEqual(table.columnWidth(L_TITLE), 320)
        self.assertLessEqual(table.columnWidth(L_NOTES), 200)
        stella_right = sum(table.columnWidth(col)
                           for col in range(L_STELLA + 1))
        self.assertLessEqual(stella_right, table.viewport().width())


class StatusBarPriorityTests(RedesignTestCase):
    """v0.1.4 T33: the message gets priority over the widgets."""

    def test_narrow_window_hides_takeaway_and_health_bar(self):
        self.window.show()
        self.window.resize(1100, 700)
        QTest.qWait(100)
        self.assertTrue(self.window.health_takeaway.isHidden())
        self.assertTrue(self.window.health_bar.isHidden())
        self.window.resize(1366, 768)
        QTest.qWait(100)
        self.assertFalse(self.window.health_takeaway.isHidden())
        self.assertFalse(self.window.health_bar.isHidden())

    def test_rate_meter_is_short(self):
        self.assertLessEqual(len("GitHub: quota unknown"), 25)
        # GITHUB_RATE is module state: earlier tests leave a
        # remaining count behind, so clear it first.
        saved = dict(apis.GITHUB_RATE)
        self.addCleanup(apis.GITHUB_RATE.update, saved)
        apis.GITHUB_RATE.clear()
        self.window._refresh_rate()
        self.assertEqual(self.window.rate_label.text(),
                         "GitHub: quota unknown")


class EmptyListMessageTests(RedesignTestCase):
    """v0.1.4 T34/T37: empty actions say so."""

    def test_clear_all_on_empty_list_says_so(self):
        self.assertEqual(self.window.my_list, [])
        self.window.clear_list()
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "My List is empty. Nothing to clear.")

    def test_add_all_with_no_results_says_so(self):
        self.window.add_all_results()
        self.assertIn("No results to add yet",
                      self.window.statusBar().currentMessage())

    def test_add_all_when_everything_is_already_saved_says_so(self):
        self.window._search_done([_result()])
        self.window.add_all_results()
        self.assertIn("Added 1 to My List.",
                      self.window.statusBar().currentMessage())
        self.window.add_all_results()
        self.assertIn("already in My List",
                      self.window.statusBar().currentMessage())


class DeclinedConfirmTests(RedesignTestCase):
    """v0.1.4 T35: answering No says what was kept."""

    def _decline(self, call):
        with unittest.mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            call()

    def test_declining_a_remove_says_what_was_kept(self):
        self.window.my_list = [core.normalize_item(_result())]
        self.window._refresh_list_tab()
        self.window.list_table.selectRow(0)
        self._decline(self.window.remove_selected)
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Kept 1 link in My List.")
        self.assertEqual(len(self.window.my_list), 1)

    def test_declining_clear_says_what_was_kept(self):
        self.window.my_list = [core.normalize_item(_result()),
                               core.normalize_item(_result(
                                   title="Two",
                                   url="https://example.com/two"))]
        self.window._refresh_list_tab()
        self._decline(self.window.clear_list)
        self.assertIn("Kept My List as it is (2 links)",
                      self.window.statusBar().currentMessage())

    def test_declining_a_website_remove_says_what_was_kept(self):
        self._decline(lambda: self.window.remove_source(
            core.load_sources()[0]["name"]))
        first = core.load_sources()[0]["name"]
        self.assertEqual(self.window.statusBar().currentMessage(),
                         f"Kept {first}.")

    def test_declining_a_restore_says_what_was_kept(self):
        self._decline(self.window.restore_sources)
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Kept your websites as they are.")


class SourcesLayoutTests(RedesignTestCase):
    """v0.1.4 T39/T54: the Remove column fits its button, and the
    empty-state guidance sits where the eye lands first."""

    def test_remove_column_is_sized_to_its_button(self):
        self.window.show()
        self.window.resize(1366, 768)
        QTest.qWait(100)
        table = self.window.sources_table
        self.assertLess(table.columnWidth(2), table.columnWidth(1))
        self.assertLessEqual(table.columnWidth(2), 130)

    def test_empty_guidance_sits_above_the_table(self):
        tab = self.window.tabs.widget(2)
        layout = tab.layout()
        self.assertLess(layout.indexOf(self.window.sources_empty_label),
                        layout.indexOf(self.window.sources_table))


class DetailFilesTests(RedesignTestCase):
    """v0.1.4 T41/T49: the file list is capped, the link clicks."""

    def _seed_many_files(self):
        self.window._search_done([_result(
            stella_files=[f"data/file-{n:04d}.jsonl"
                          for n in range(200)])])
        self.window.results_table.selectRow(0)
        QTest.qWait(50)

    def test_many_files_cap_at_five_with_and_n_more(self):
        self._seed_many_files()
        text = self.window.detail_view.toPlainText()
        self.assertIn("and 195 more files.", text)
        longest = max(len(line) for line in text.splitlines())
        self.assertLess(longest, 1000)
        self.assertLess(len(text), 10000)

    def test_detail_link_is_clickable(self):
        self._seed_many_files()
        self.assertTrue(self.window.detail_view.openExternalLinks())
        html = self.window.detail_view.toHtml()
        self.assertIn("<a href=\"https://example.com/math\">", html)
        self.assertIn("Link: https://example.com/math",
                      self.window.detail_view.toPlainText())


class SectionMemoryTests(RedesignTestCase):
    """v0.1.4 T47: collapsing a section survives a restart."""

    def test_collapsed_section_stays_collapsed(self):
        self.window.edit_section.set_expanded(False)
        self.assertEqual(
            core.load_config().get("sections", {}).get("list-edit"),
            False)
        second = MainWindow()
        self.addCleanup(second.close)
        self.assertFalse(second.edit_section.is_expanded())
        self.assertTrue(second.remove_section.is_expanded())

    def test_sections_ship_expanded(self):
        self.assertTrue(self.window.remove_section.is_expanded())
        self.assertTrue(self.window.edit_section.is_expanded())
        self.assertTrue(self.window.export_section.is_expanded())


class StellaCheckAllTests(RedesignTestCase):
    """v0.1.4 T51: with nothing selected, the check covers all."""

    def _seed_results(self, count=4):
        self.window._search_done([
            _result(title=f"Item {n}",
                    url=f"https://example.com/{n}")
            for n in range(count)])

    def test_unselected_results_check_checks_everything(self):
        self._seed_results(4)

        def fast_sniff(item, token=""):
            return {"status": "ready", "files": ["train.jsonl"],
                    "total_size": 10, "detail": "ok"}

        with unittest.mock.patch.object(
                apis, "sniff_item", side_effect=fast_sniff):
            self.window.start_stella_check("results")
            self.assertEqual(self.window._stella_total, 4)
            deadline = 100
            while (not self.window.stella_check_button.isEnabled()
                   and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertTrue(self.window.stella_check_button.isEnabled())

    def test_unselected_list_check_checks_everything(self):
        self.window.my_list = [core.normalize_item(_result(
            title=f"Item {n}", url=f"https://example.com/{n}"))
            for n in range(3)]
        core.save_list(self.window.my_list)
        self.window._refresh_list_tab()

        def fast_sniff(item, token=""):
            return {"status": "ready", "files": ["train.jsonl"],
                    "total_size": 10, "detail": "ok"}

        with unittest.mock.patch.object(
                apis, "sniff_item", side_effect=fast_sniff):
            self.window.start_stella_check("list")
            self.assertEqual(self.window._stella_total, 3)
            deadline = 100
            while (not self.window.list_stella_button.isEnabled()
                   and deadline > 0):
                QTest.qWait(50)
                deadline -= 1
            self.assertTrue(self.window.list_stella_button.isEnabled())

    def test_empty_list_has_nothing_to_check(self):
        self.window.start_stella_check("list")
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Nothing to check yet.")


class ShortcutsTeachTests(RedesignTestCase):
    """v0.1.4 T52: the ? overlay teaches Enter, Ctrl+A, double-click."""

    def test_overlay_lists_the_missing_keys(self):
        dialog = ShortcutsDialog(self.window)
        self.addCleanup(dialog.close)
        text = dialog.text.toPlainText()
        self.assertIn("Enter", text)
        self.assertIn("Ctrl+A", text)
        self.assertIn("Double-click", text)
        self.assertIn("selects every row in the table", text)
        self.assertIn("opens the picked row in your browser", text)

    def test_ctrl_a_selects_every_row(self):
        self.window._search_done([_result(),
                                  _result(title="Two",
                                          url="https://example.com/two")])
        table = self.window.results_table
        table.setFocus()
        table.selectRow(0)
        QTest.keyClick(table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(50)
        self.assertEqual(len(self.window._selected_results()), 2)


class RecoveryRecordTests(RedesignTestCase):
    """v0.1.4 T53: a .bak recovery leaves a persistent record."""

    def test_recovery_is_recorded_and_shown_in_about(self):
        path = core.list_path()
        bak = path.with_name(path.name + ".bak")
        bak.write_text(json.dumps({"active": "My List",
                                   "collections": [{"name": "My List",
                                                    "items": [
                                                        core.normalize_item(
                                                            _result()),
                                                        core.normalize_item(
                                                            _result(
                                                                title="Two",
                                                                url="https://example.com/two")),
                                                    ]}]}),
                       encoding="utf-8")
        path.write_text("{damaged", encoding="utf-8")
        core.list_load_notice()  # drain any earlier notes
        items = core.load_list()
        self.assertEqual(len(items), 2)
        record = core.load_config().get("list_recovered")
        self.assertIsNotNone(record)
        self.assertEqual(record["links"], 2)
        about = self.window._about_text()
        self.assertIn("backup copy", about)
        self.assertIn("2 links recovered", about)

    def test_about_has_no_recovery_line_without_a_recovery(self):
        self.assertNotIn("backup copy", self.window._about_text())


if __name__ == "__main__":
    unittest.main()
