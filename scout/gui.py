"""Dataset Scout window — one window, three tabs, plain words.

Tabs: Search | My List (n) | Sources.
The status bar is the ONLY status surface in the window.
"""

from __future__ import annotations

import hashlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PySide6.QtCore import QLockFile, QRect, QSettings, QThread, Qt, Signal
from PySide6.QtGui import (
    QDesktopServices, QGuiApplication, QIcon, QKeySequence, QShortcut,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout,
    QWidget,
)

from . import apis, core, download

HERE = Path(__file__).resolve().parent.parent
ICON_PATH = HERE / "assets" / "scout-256.png"

# QSettings home for the saved window geometry (U5).
SETTINGS_ORG = "BrokeNBored"
SETTINGS_APP = "DatasetScout"

DEFAULT_WIDTH = 1100
DEFAULT_HEIGHT = 700

RESULT_COLUMNS = ["Title", "Source", "Stars", "Downloads", "Updated",
                  "Description", "License", "Format", "STELLA"]
LIST_COLUMNS = ["Title", "Category", "Source", "Stars", "Updated",
                "Link", "Notes", "State", "STELLA"]

# Results table column indexes.
(R_TITLE, R_SOURCE, R_STARS, R_DOWNLOADS, R_UPDATED, R_DESC,
 R_LICENSE, R_FORMAT, R_STELLA) = range(len(RESULT_COLUMNS))
# List table column indexes.
(L_TITLE, L_CATEGORY, L_SOURCE, L_STARS, L_UPDATED, L_LINK,
 L_NOTES, L_STATE, L_STELLA) = range(len(LIST_COLUMNS))

# Item data roles: UserRole holds the sort value, UserRole + 1 the
# index of the row's item in self.results / self.my_list.
SORT_ROLE = Qt.ItemDataRole.UserRole
ROW_ROLE = Qt.ItemDataRole.UserRole + 1

LICENSE_CHOICES = ["", "MIT", "Apache-2.0", "BSD-2-Clause",
                   "BSD-3-Clause", "CC0-1.0", "CC-BY-4.0",
                   "CC-BY-SA-4.0", "GPL-3.0", "LGPL-3.0", "MPL-2.0",
                   "Unlicense"]

# Dark stylesheet drawn from the icon (T24, U15): deep navy
# surfaces (#1b2a4a), gold accent (#ffd166), light blue secondary
# (#7ee0ff). Cheap static styling only, no animation.
DARK_STYLESHEET = """
QMainWindow, QDialog {
    background-color: #16233d;
    color: #e8ecf4;
}
QWidget {
    background-color: #16233d;
    color: #e8ecf4;
}
QTabWidget::pane {
    border: 1px solid #2a3f66;
    background-color: #16233d;
}
QTabBar::tab {
    background-color: #1b2a4a;
    color: #9fb0cc;
    padding: 6px 14px;
    border: 1px solid #2a3f66;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected {
    background-color: #22355c;
    color: #ffd166;
}
QPushButton {
    background-color: #24365e;
    color: #e8ecf4;
    border: 1px solid #2a3f66;
    border-radius: 6px;
    padding: 5px 12px;
}
QPushButton:hover { background-color: #2e4674; }
QPushButton:pressed { background-color: #1b2a4a; }
QPushButton:disabled { color: #6b7a99; }
QPushButton:checked {
    background-color: #ffd166;
    color: #16233d;
    border-color: #ffd166;
}
QLineEdit, QComboBox, QTextEdit, QTextBrowser {
    background-color: #0f1a30;
    color: #e8ecf4;
    border: 1px solid #2a3f66;
    border-radius: 6px;
    padding: 4px 6px;
    selection-background-color: #2e4a7a;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background-color: #0f1a30;
    color: #e8ecf4;
    selection-background-color: #2e4a7a;
}
QTableWidget {
    background-color: #0f1a30;
    color: #e8ecf4;
    gridline-color: #2a3f66;
    selection-background-color: #2e4a7a;
    alternate-background-color: #13203a;
}
QHeaderView::section {
    background-color: #22355c;
    color: #ffd166;
    padding: 4px 6px;
    border: 1px solid #2a3f66;
}
QGroupBox {
    border: 1px solid #2a3f66;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 10px;
}
QGroupBox::title {
    color: #7ee0ff;
    subcontrol-origin: margin;
    left: 8px;
}
QMenuBar, QMenu {
    background-color: #1b2a4a;
    color: #e8ecf4;
}
QMenu::item:selected { background-color: #2e4a7a; }
QStatusBar { background-color: #1b2a4a; color: #9fb0cc; }
QScrollBar:vertical, QScrollBar:horizontal {
    background-color: #1b2a4a;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background-color: #2a3f66;
    border-radius: 4px;
}
QProgressBar {
    border: 1px solid #2a3f66;
    border-radius: 6px;
    background-color: #0f1a30;
    color: #e8ecf4;
    text-align: center;
}
QProgressBar::chunk { background-color: #ffd166; }
"""


class SortableItem(QTableWidgetItem):
    """A table cell that sorts by its stored value, not its text.

    Stars and downloads sort as numbers; dates sort as ISO text.
    The stored value goes in SORT_ROLE; the shown text stays human.
    """

    def __lt__(self, other):
        mine = self.data(SORT_ROLE)
        theirs = other.data(SORT_ROLE)
        if mine is not None and theirs is not None:
            try:
                return mine < theirs
            except TypeError:
                pass
        # Never call super().__lt__ here: in this PySide6 build it
        # dispatches back into this override and recurses forever.
        # Compare the shown text instead.
        return self.text() < other.text()


def _cell(text: str, sort_value=None, row_index: int | None = None):
    """Build a table cell with an optional sort value and row index."""
    item = SortableItem(str(text))
    if sort_value is not None:
        item.setData(SORT_ROLE, sort_value)
    if row_index is not None:
        item.setData(ROW_ROLE, row_index)
    return item


def _explain(text: str) -> QLabel:
    """A small muted explanation line under a field (U20)."""
    label = QLabel(text)
    label.setStyleSheet("color: #9fb0cc; font-size: 12px;")
    label.setWordWrap(True)
    return label


class SearchWorker(QThread):
    """Runs the live searches off the UI thread, in parallel (T10).

    Both sources are always tried, even if one fails (U32: never
    silently skip). finished_ok carries the results plus the list
    of failed sources as (name, reason) pairs, so the window can
    say plainly which source did not answer. finished_error fires
    only when BOTH sources fail. request_stop ends the search
    after the sources running now finish (T3, U26).
    """

    finished_ok = Signal(list, list)
    finished_error = Signal(str)
    finished_stopped = Signal()

    def __init__(self, query: str, category: str, token: str,
                 per_page: int = 30, parent=None):
        super().__init__(parent)
        self.query = query
        self.category = category
        self.token = token
        self.per_page = per_page
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        """Ask the worker to stop after the searches running now."""
        self._stop_event.set()

    def run(self) -> None:  # noqa: N802 - Qt naming
        results: list[dict] = []
        failures: list[tuple[str, str]] = []
        lock = threading.Lock()

        def run_one(name: str, func):
            try:
                found = func()
            except (apis.SearchError, core.RateLimitedError) as exc:
                core.log_error(f"{name} search failed: {exc}")
                with lock:
                    failures.append((name, str(exc)))
                return
            with lock:
                results.extend(found)

        threads = [
            threading.Thread(
                target=run_one,
                args=("GitHub", lambda: apis.search_github(
                    self.query, self.category, token=self.token,
                    per_page=self.per_page)),
                daemon=True),
            threading.Thread(
                target=run_one,
                args=("Hugging Face", lambda: apis.search_huggingface(
                    self.query, self.category, limit=self.per_page)),
                daemon=True),
        ]
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads):
            if self._stop_event.is_set():
                self.finished_stopped.emit()
                return
            time.sleep(0.05)
        for thread in threads:
            thread.join()
        if self._stop_event.is_set():
            self.finished_stopped.emit()
            return
        if len(failures) == 2:
            detail = "; ".join(
                f"{name}: {reason}" for name, reason in failures
            )
            self.finished_error.emit(f"Search failed. {detail}")
            return
        self.finished_ok.emit(results, failures)


class LinkCheckWorker(QThread):
    """Checks every link in My List off the UI thread, 4 at a time.

    Network checks are I/O-bound, so 4 concurrent workers stay
    light on the PC (T10). all_done carries True when the run was
    stopped part way (U26); links already checked stay marked
    either way.
    """

    item_checked = Signal(int, str)
    all_done = Signal(bool)

    def __init__(self, urls: list[str], parent=None, workers: int = 4):
        super().__init__(parent)
        self.urls = urls
        self.workers = workers
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        """Ask the worker to stop after the links running now."""
        self._stop_event.set()

    def run(self) -> None:  # noqa: N802 - Qt naming
        stopped = False
        executor = ThreadPoolExecutor(max_workers=self.workers)
        try:
            futures = {executor.submit(apis.check_link, url): index
                       for index, url in enumerate(self.urls)}
            for future in as_completed(futures):
                if self._stop_event.is_set():
                    stopped = True
                    break
                index = futures[future]
                try:
                    status = future.result()
                except Exception as exc:  # noqa: BLE001 - a bad link must not kill the run
                    core.log_error(
                        f"Link check failed for {self.urls[index]}: {exc}")
                    status = "unknown"
                self.item_checked.emit(index, status)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        self.all_done.emit(stopped)


class StellaCheckWorker(QThread):
    """Checks selected items for STELLA-ready data files (T29).

    One item at a time, on demand only: the APIs are rate
    limited, so a whole results page is never checked at once.
    item_checked carries (index, status, detail, files, size).
    """

    item_checked = Signal(int, str, str, list, object)
    all_done = Signal(bool)

    def __init__(self, items: list, token: str = "", parent=None):
        super().__init__(parent)
        self.items = items  # [(index, item dict), ...]
        self.token = token
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # noqa: N802 - Qt naming
        stopped = False
        for index, item in self.items:
            if self._stop_event.is_set():
                stopped = True
                break
            try:
                found = apis.sniff_item(item, token=self.token)
            except Exception as exc:  # noqa: BLE001 - one bad link must not kill the run
                core.log_error(
                    f"STELLA check failed for {item.get('url')}: {exc}")
                found = {"status": "unknown", "files": [],
                         "total_size": None, "detail": str(exc)}
            self.item_checked.emit(index, found["status"],
                                   found.get("detail", ""),
                                   found.get("files", []),
                                   found.get("total_size"))
        self.all_done.emit(stopped)


class DownloadWorker(QThread):
    """Downloads datasets and converts them to STELLA .jsonl (T35).

    progress carries (index, stage, downloaded, total); total is
    -1 when the server does not say. item_done carries (index, ok,
    message, outputs, rows, state).
    """

    progress = Signal(int, str, int, int)
    item_done = Signal(int, bool, str, list, int, str)
    all_done = Signal(bool)

    def __init__(self, items: list, out_dir, token: str = "",
                 parent=None):
        super().__init__(parent)
        self.items = items  # [(index, item dict), ...]
        self.out_dir = out_dir
        self.token = token
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # noqa: N802 - Qt naming
        stopped = False
        for index, item in self.items:
            if self._stop_event.is_set():
                stopped = True
                break

            def say(stage: str, got: int = 0, total=None, idx=index):
                self.progress.emit(idx, stage, got,
                                   -1 if total is None else total)

            try:
                report = download.download_and_convert(
                    item, self.out_dir, token=self.token,
                    progress=say, stop=self._stop_event)
            except Exception as exc:  # noqa: BLE001 - one bad item must not kill the run
                core.log_error(
                    f"Download failed for {item.get('url')}: {exc}")
                report = {"ok": False, "message": str(exc),
                          "outputs": [], "rows": 0, "state": "found"}
            self.item_done.emit(index, report["ok"], report["message"],
                                report["outputs"], report["rows"],
                                report["state"])
        self.all_done.emit(stopped)


class UpdateCheckWorker(QThread):
    """Reads the latest release tag off the UI thread (T21)."""

    finished = Signal(object)  # tag string, or None when unreadable

    def run(self) -> None:  # noqa: N802 - Qt naming
        self.finished.emit(apis.latest_release_tag())


# ------------------------------------------------------------ how to use

# One registry for every keyboard shortcut (library 028): the
# shortcuts and the How to Use text are both built from this, so
# the help can never drift out of date with the bindings.
# (key, plain-words description, scope, action name on MainWindow)
SHORTCUTS = [
    ("Ctrl+F", "focuses the search box", "window",
     "_shortcut_focus_search"),
    ("Ctrl+E", "exports the STELLA manifest", "window",
     "_shortcut_export_stella"),
    ("Delete", "removes the selected rows in My List", "list",
     "remove_selected"),
]


def _shortcuts_help_text() -> str:
    return " ".join(f"{key} {desc}." for key, desc, _s, _a in SHORTCUTS)


# Plain-words index for the How to Use window (U22). No em-dashes,
# no "&" in any visible text (U33, U11).
HOW_TO_USE_SECTIONS = [
    ("Search",
     "Type a few words about the kind of dataset you want, then press "
     "Search. Dataset Scout looks on GitHub and Hugging Face and shows "
     "the matches in the table, newest first. Double-click a row to "
     "open it in your browser. Click a column heading to sort by it. "
     "Recent searches are kept in the search box drop-down."),
    ("Categories and filters",
     "Pick a category chip to narrow the search: Post-training, "
     "Instruction tuning, Reasoning, or Training datasets. Active only "
     "keeps results updated recently. Use the stars box to keep only "
     "well liked projects: it counts GitHub stars and Hugging Face "
     "likes. The license box keeps only results with that license; "
     "results with no license shown are hidden while it is set."),
    ("Websites (add/remove)",
     "The Sources tab lists the websites the Search tab can open. Add "
     "a website with its name and address. Put {q} where your search "
     "words go. Remove any website you do not use, or restore the "
     "defaults."),
    ("My List and collections",
     "Add results to My List with the buttons under the search table, "
     "or add a link you found yourself with the Add a link I found "
     "button. Use the List box to keep named lists, like one for "
     "reasoning and one for math. Remove rows you do not want; Undo "
     "remove brings them back. Clear all asks first. Drag rows to "
     "reorder them. Right-click a row for more actions."),
    ("States",
     "Each saved link has a state: Found, Downloaded, Converted, or "
     "In STELLA. It follows the real pipeline. Change it with the "
     "Edit button, or right-click a row and pick Mark as. Download "
     "and convert sets it for you."),
    ("Check links",
     "Check links visits every link in My List and marks it working, "
     "dead, or unknown. You can stop the check part way through with "
     "the Stop button. Links already checked stay marked."),
    ("STELLA-ready check",
     "Select rows and press Check STELLA-ready to see whether a "
     "dataset is actually usable: Scout looks at the files it holds "
     "and says STELLA-ready when a .jsonl file is already in "
     "STELLA's shape, Needs conversion when the data needs work, or "
     "No data files when there is nothing to use. It checks one "
     "dataset at a time, only when you ask."),
    ("Download and convert",
     "Select rows in My List and press Download and convert. Scout "
     "downloads the dataset files and turns them into .jsonl files "
     "in STELLA's shape, saved under your Dataset Scout folder in "
     "stella-ready. Rows it cannot map are skipped, and the report "
     "says how many and why. Parquet files cannot be converted "
     "here: the report names them and says so."),
    ("Export for STELLA",
     "Export My List as a Markdown list, JSON, or a STELLA manifest. "
     "Every export says that downloaded data must be converted to "
     "STELLA's .jsonl shape before a STELLA project can use it. "
     "Import a list reads an export back in. Send to STELLA writes "
     "the manifest into STELLA's home folder on this PC. STELLA "
     "itself reads dataset files by path, so the converted .jsonl "
     "files from Download and convert are what it trains on."),
    ("GitHub token",
     "GitHub allows more searches per hour with a token. Paste one in "
     "the Sources tab and press Save token. It is stored on this PC "
     "only and never written to a log. The status bar shows how many "
     "GitHub searches are left."),
    ("Keyboard shortcuts", _shortcuts_help_text()),
    ("Check for updates",
     "Check for updates in the Help menu looks at the GitHub "
     "releases and says whether a newer version is out."),
    ("Debug report",
     "Copy debug report in the Sources tab copies the recent log to "
     "the clipboard, with any token removed, so you can share it when "
     "asking for help."),
]

HOW_TO_USE_TEXT = "\n\n".join(
    f"{title}\n{body}" for title, body in HOW_TO_USE_SECTIONS
)


class HowToUseWindow(QWidget):
    """A separate closable window with the How to Use index (U22)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("How to Use Dataset Scout")
        self.setMinimumSize(600, 450)
        self.resize(650, 500)
        if ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        layout = QVBoxLayout(self)
        heading = QLabel("How to Use Dataset Scout")
        heading.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(heading)
        self.text = QTextBrowser()
        self.text.setPlainText(HOW_TO_USE_TEXT)
        layout.addWidget(self.text, stretch=1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)


class AddLinkDialog(QDialog):
    """The hand-add form in its own window (T22, just-in-time GUI).

    The main window keeps references to the fields (found_url,
    found_title, found_category, found_note) so the add logic
    stays in one place.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a link I found")
        self.setMinimumSize(500, 300)
        layout = QFormLayout(self)
        self.found_url = QLineEdit()
        self.found_url.setPlaceholderText("https://...")
        layout.addRow("Link:", self.found_url)
        self.found_title = QLineEdit()
        self.found_title.setPlaceholderText("A short name for it")
        layout.addRow("Title:", self.found_title)
        self.found_category = QComboBox()
        self.found_category.setEditable(True)
        for key, (label, _suffix) in core.CATEGORIES.items():
            self.found_category.addItem(label, key)
        layout.addRow("Category:", self.found_category)
        layout.addRow("", _explain(
            "Pick one of the five, or type your own category."))
        self.found_note = QLineEdit()
        self.found_note.setPlaceholderText(
            "Anything to remember about it (optional)")
        layout.addRow("Note:", self.found_note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow("", buttons)


class EditItemDialog(QDialog):
    """Edit a saved link: title, category, notes, state (T12)."""

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit this link")
        self.setMinimumSize(500, 300)
        layout = QFormLayout(self)
        layout.addRow("Link:", QLabel(item.get("url") or ""))
        self.title_field = QLineEdit(item.get("title") or "")
        layout.addRow("Title:", self.title_field)
        self.category_field = QComboBox()
        self.category_field.setEditable(True)
        for key, (label, _suffix) in core.CATEGORIES.items():
            self.category_field.addItem(label, key)
        current = core.category_label(item.get("category") or "all")
        self.category_field.setCurrentText(current)
        layout.addRow("Category:", self.category_field)
        layout.addRow("", _explain(
            "Pick one of the five, or type your own category."))
        self.notes_field = QLineEdit(item.get("notes") or "")
        layout.addRow("Note:", self.notes_field)
        self.state_field = QComboBox()
        for key, label in core.WORKFLOW_STATES.items():
            self.state_field.addItem(label, key)
        index = self.state_field.findData(item.get("state") or "found")
        if index >= 0:
            self.state_field.setCurrentIndex(index)
        layout.addRow("State:", self.state_field)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow("", buttons)

    def values(self) -> dict:
        """The edited values, with the category as a key."""
        return {
            "title": self.title_field.text().strip(),
            "category": core.category_key_for_label(
                self.category_field.currentText()),
            "notes": self.notes_field.text().strip(),
            "state": self.state_field.currentData(),
        }


class DownloadDialog(QDialog):
    """Progress window for download and convert (T35).

    Owns the DownloadWorker. The log says what happened to each
    dataset in plain words; the Close button waits until the run
    is done or stopped.
    """

    def __init__(self, items: list, out_dir, token: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download and convert")
        self.setMinimumSize(600, 400)
        self.items = items
        self.out_dir = out_dir
        self.worker: DownloadWorker | None = None
        self._last_stage = ""

        layout = QVBoxLayout(self)
        word = "dataset" if len(items) == 1 else "datasets"
        header = QLabel(
            f"Downloading {len(items)} {word} and converting them "
            f"to STELLA .jsonl files in {out_dir}.")
        header.setWordWrap(True)
        layout.addWidget(header)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, stretch=1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar)

        row = QHBoxLayout()
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_run)
        row.addWidget(self.stop_button)
        self.open_button = QPushButton("Open the folder")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_folder)
        row.addWidget(self.open_button)
        self.close_button = QPushButton("Close")
        self.close_button.setEnabled(False)
        self.close_button.clicked.connect(self.accept)
        row.addWidget(self.close_button)
        row.addStretch(1)
        layout.addLayout(row)

    def start(self):
        """Start the worker. Call after the dialog is shown."""
        self.worker = DownloadWorker(self.items, self.out_dir,
                                     token=core.get_github_token(),
                                     parent=self)
        self.worker.progress.connect(self._on_progress)
        self.worker.item_done.connect(self._on_item_done)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

    def stop_run(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            self.stop_button.setEnabled(False)
            self._log("Stopping after the file running now...")

    def open_folder(self):
        QDesktopServices.openUrl(str(self.out_dir))

    def closeEvent(self, event):  # noqa: N802 - Qt naming
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            self.worker.wait(30000)
        super().closeEvent(event)

    def _log(self, text: str):
        self.log_view.append(text)

    def _on_progress(self, index: int, stage: str, got: int, total: int):
        if total and total > 0:
            self.bar.setMaximum(100)
            self.bar.setValue(min(100, int(got * 100 / total)))
        else:
            self.bar.setMaximum(0)  # busy: the server did not say how big
        # Log each stage once, not once per chunk.
        if stage != self._last_stage:
            self._last_stage = stage
            self._log(stage)

    def _on_item_done(self, index: int, ok: bool, message: str,
                      outputs: list, rows: int, state: str):
        title = ""
        # The item dicts are the same objects the main window holds,
        # so updating the state here updates My List too.
        for _idx, item in self.items:
            if _idx == index:
                title = item.get("title") or ""
                item["state"] = state
                break
        self._log(f"{title}: {message}")
        self.bar.setMaximum(100)
        self.bar.setValue(100 if ok else 0)

    def _on_all_done(self, stopped: bool):
        self.stop_button.setEnabled(False)
        self.close_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.bar.setMaximum(100)
        self.bar.setValue(100)
        self._log("Stopped part way through." if stopped else "Done.")


# ------------------------------------------------------------ single instance

class SingleInstance:
    """One GUI window at a time (U27).

    A QLockFile in SCOUT_HOME says who owns the window; a QLocalServer
    lets a second launch ask the running copy to come forward instead
    of starting a second window. A stale lock from a crashed run is
    taken over: the lock file is judged stale 1 second after it was
    written when its owner is dead (probed 2026-11-13: a live owner
    stays protected by the pid check, a dead owner is taken over).
    The CLI never touches this guard: it does not import the GUI, so
    it keeps working while the window is open.
    """

    def __init__(self, home: Path | None = None):
        self.home = home or core.ensure_home()
        self.lock_path = self.home / "app.lock"
        self.lock = QLockFile(str(self.lock_path))
        self.lock.setStaleLockTime(1000)
        digest = hashlib.md5(str(self.home).encode("utf-8")).hexdigest()
        self.server_name = f"dataset-scout-{digest[:12]}"
        self.server: QLocalServer | None = None
        self._sockets: list[QLocalSocket] = []
        self._on_raise = None

    def try_take(self) -> bool:
        """True if this process now owns the single-instance lock."""
        if self.lock.tryLock(0):
            return True
        # The owner may have crashed: drop a stale lock and retry.
        # (Only safe to call while NOT holding the lock.)
        if self.lock.removeStaleLockFile():
            return self.lock.tryLock(0)
        return False

    def release(self) -> None:
        if self.server is not None:
            self.server.close()
            self.server = None
        self.lock.unlock()

    def start_server(self, on_raise) -> bool:
        """Listen for raise requests from later launches."""
        self._on_raise = on_raise
        # A crashed run leaves its socket behind; remove it first or
        # listen fails with "Address in use" (probed 2026-11-13).
        QLocalServer.removeServer(self.server_name)
        self.server = QLocalServer()
        self.server.newConnection.connect(self._accept_connection)
        return self.server.listen(self.server_name)

    def _accept_connection(self):
        if self.server is None:
            return
        while True:
            sock = self.server.nextPendingConnection()
            if sock is None:
                return
            self._sockets.append(sock)
            sock.readyRead.connect(
                lambda s=sock: self._read_message(s))  # noqa: E731
            sock.disconnected.connect(
                lambda s=sock: self._drop_socket(s))  # noqa: E731
            self._read_message(sock)  # the message may already be waiting

    def _read_message(self, sock: QLocalSocket):
        data = bytes(sock.readAll())
        if b"raise" in data and self._on_raise is not None:
            self._on_raise()

    def _drop_socket(self, sock: QLocalSocket):
        if sock in self._sockets:
            self._sockets.remove(sock)
        sock.deleteLater()

    def notify_running(self) -> bool:
        """Ask the running copy to raise its window.

        True if it answered. Retries for a few seconds: a first
        instance that has taken the lock but not yet started its
        server answers once it is up.
        """
        for _ in range(10):
            sock = QLocalSocket()
            sock.connectToServer(self.server_name)
            if sock.waitForConnected(200):
                sock.write(b"raise")
                sock.flush()
                sock.waitForBytesWritten(200)
                sock.disconnectFromServer()
                return True
            time.sleep(0.2)
        return False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dataset Scout")
        self.setMinimumSize(900, 600)
        # Default size fits his 1366x768 screen (U5); a saved geometry
        # from a previous run wins when it still fits a screen.
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self._restore_geometry()
        if ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        self.results: list[dict] = []
        self.my_list: list[dict] = core.load_list()
        self._active_collection, _names = core.load_collections()
        self.search_worker: SearchWorker | None = None
        self.check_worker: LinkCheckWorker | None = None
        self.stella_worker: StellaCheckWorker | None = None
        self.update_worker: UpdateCheckWorker | None = None
        self.howto_window: HowToUseWindow | None = None
        self._check_total = 0
        self._check_done_count = 0
        self._undo_stack: list[list[tuple[int, dict]]] = []
        self._last_query: str | None = None
        self._last_category: str | None = None
        self._results_sorting = True
        self._results_sort = (R_UPDATED, Qt.SortOrder.DescendingOrder)
        self._list_sorting = False
        self._showing_skeleton = False
        self._stella_target = "results"

        self._build_menus()

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._build_search_tab()
        self._build_list_tab()
        self._build_sources_tab()
        self._refresh_list_tab()
        self._refresh_site_buttons()
        self._build_shortcuts()

        # The busy light (U16): a gold dot, visible only while a
        # search, a link check, or a STELLA check is running. It is
        # a light, not a second status text: the status bar stays
        # the only status text surface.
        self.busy_dot = QLabel("")
        self.busy_dot.setObjectName("busy_dot")
        self.busy_dot.setFixedSize(12, 12)
        self.busy_dot.setStyleSheet(
            "background-color: #ffd166; border-radius: 6px;")
        self.busy_dot.setVisible(False)
        self.statusBar().addPermanentWidget(self.busy_dot)
        # The GitHub rate meter (T16) and the live stats strip
        # (T25). Neither is a status text surface: they show numbers.
        self.rate_label = QLabel("GitHub: searches left unknown")
        self.rate_label.setObjectName("rate_meter")
        self.statusBar().addPermanentWidget(self.rate_label)
        self.stats_label = QLabel("")
        self.stats_label.setObjectName("stats_strip")
        self.statusBar().addPermanentWidget(self.stats_label)
        self._refresh_stats()

        self.statusBar().showMessage(
            "Type a few words and press Search, or pick a category below."
        )

    # ------------------------------------------------------------ menus (T23, U38)

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(
            "Export as Markdown list",
            lambda: self.export_list("md"))
        file_menu.addAction(
            "Export as JSON", lambda: self.export_list("json"))
        file_menu.addAction(
            "Export STELLA manifest",
            lambda: self.export_list("stella"))
        file_menu.addAction("Import a list", self.import_list)
        file_menu.addAction("Send to STELLA", self.send_to_stella)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        edit_menu = self.menuBar().addMenu("Edit")
        edit_menu.addAction("Undo remove", self.undo_remove)
        edit_menu.addAction("Remove selected", self.remove_selected)
        edit_menu.addAction("Clear My List", self.clear_list)

        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(
            "Search tab", lambda: self.tabs.setCurrentIndex(0))
        view_menu.addAction(
            "My List tab", lambda: self.tabs.setCurrentIndex(1))
        view_menu.addAction(
            "Sources tab", lambda: self.tabs.setCurrentIndex(2))

        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction("How to Use", self.show_how_to_use)
        help_menu.addAction("Check for updates", self.check_for_updates)
        help_menu.addAction("About Dataset Scout", self._show_about)

    def _build_shortcuts(self):
        """Keyboard shortcuts (T19), built from the SHORTCUTS registry."""
        for key, _desc, scope, action in SHORTCUTS:
            parent = self.list_table if scope == "list" else self
            QShortcut(QKeySequence(key), parent, getattr(self, action))

    def _shortcut_focus_search(self):
        self.search_box.setFocus()

    def _shortcut_export_stella(self):
        self.export_list("stella")

    def _show_about(self):
        QMessageBox.about(
            self, "About Dataset Scout",
            f"Dataset Scout {core.VERSION}\n\n"
            "Find training datasets for STELLA, build lists of links.\n"
            "Searches GitHub and Hugging Face live, checks whether "
            "datasets are STELLA-ready, and converts downloads to "
            "STELLA .jsonl files.")

    # ------------------------------------------------------------ update check (T21)

    def check_for_updates(self):
        if self.update_worker is not None and self.update_worker.isRunning():
            return
        self.statusBar().showMessage("Checking for updates...")
        self.update_worker = UpdateCheckWorker(self)
        self.update_worker.finished.connect(self._update_check_done)
        self.update_worker.start()

    def _update_check_done(self, tag):
        if not tag:
            QMessageBox.information(
                self, "Could not check",
                "Dataset Scout could not reach GitHub to check for "
                "updates. Try again later.")
            self.statusBar().showMessage("Update check failed.")
            return
        current = core.parse_version(core.VERSION)
        latest = core.parse_version(tag)
        if latest <= current:
            QMessageBox.information(
                self, "Up to date",
                f"Dataset Scout {core.VERSION} is the latest version.")
            self.statusBar().showMessage("Dataset Scout is up to date.")
        else:
            QMessageBox.information(
                self, "Update available",
                f"Version {tag} is out. You have {core.VERSION}. "
                "Get it from the GITDATA releases page on GitHub.")
            self.statusBar().showMessage(
                f"Version {tag} is out. You have {core.VERSION}.")

    # ------------------------------------------------------------ status bar numbers

    def _refresh_rate(self):
        """The GitHub rate meter (T16)."""
        remaining = apis.GITHUB_RATE.get("remaining")
        if remaining is None:
            self.rate_label.setText("GitHub: searches left unknown")
        elif remaining == 1:
            self.rate_label.setText("GitHub: 1 search left")
        else:
            self.rate_label.setText(
                f"GitHub: {remaining} searches left")

    def _refresh_stats(self):
        """The live stats strip (T25, U37)."""
        if not hasattr(self, "stats_label"):
            return  # the tabs are built before the status bar widgets
        links = len(self.my_list)
        ok = sum(1 for item in self.my_list
                 if item.get("status") == "ok")
        dead = sum(1 for item in self.my_list
                   if item.get("status") == "dead")
        searches = core.searches_run()
        self.stats_label.setText(
            f"Links: {links}  Working: {ok}  Dead: {dead}  "
            f"Searches: {searches}")

    # ------------------------------------------------------------ geometry

    def _restore_geometry(self):
        """Restore the saved window position and size (U5).

        Only when the saved rectangle still touches a screen; a window
        saved on a screen that is gone falls back to the default size.
        """
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        values = [
            settings.value(f"window/{key}", type=int)
            for key in ("x", "y", "width", "height")
        ]
        if any(value is None for value in values):
            return
        rect = QRect(*values)
        screens = QGuiApplication.screens()
        if screens and not any(
                screen.availableGeometry().intersects(rect)
                for screen in screens):
            return
        self.setGeometry(rect)

    def closeEvent(self, event):  # noqa: N802 - Qt naming
        self._stop_all_workers()
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        rect = self.geometry()
        settings.setValue("window/x", rect.x())
        settings.setValue("window/y", rect.y())
        settings.setValue("window/width", rect.width())
        settings.setValue("window/height", rect.height())
        settings.sync()
        super().closeEvent(event)

    def _stop_all_workers(self):
        """Stop every running worker and wait for it (T3).

        A worker stuck in a network call finishes when the call
        times out (20 seconds at most). A worker that still will
        not stop is terminated rather than left running past the
        window.
        """
        for worker in (self.search_worker, self.check_worker,
                       self.stella_worker, self.update_worker):
            if worker is None:
                continue
            if hasattr(worker, "request_stop"):
                worker.request_stop()
            if worker.isRunning():
                worker.wait(25000)
            if worker.isRunning():
                core.log("A worker would not stop; terminating it.",
                         level="WARNING")
                worker.terminate()
                worker.wait(5000)

    def bring_to_front(self):
        """Raise this window (used by the single-instance guard, U27)."""
        self.show()
        self.raise_()
        self.activateWindow()

    def show_how_to_use(self):
        """Open the How to Use window, or raise it if already open."""
        if self.howto_window is None:
            self.howto_window = HowToUseWindow()
            self.howto_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.howto_window.destroyed.connect(self._howto_closed)
        self.howto_window.show()
        self.howto_window.raise_()
        self.howto_window.activateWindow()

    def _howto_closed(self, _obj=None):
        self.howto_window = None

    def _set_busy(self, busy: bool):
        self.busy_dot.setVisible(busy)

    # ------------------------------------------------------------ search tab

    def _build_search_tab(self):
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)

        heading = QLabel("Dataset finder for STELLA")
        heading.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(heading)
        layout.addWidget(QLabel(
            "Find training datasets, save the links, check them, "
            "convert them for STELLA."
        ))

        row = QHBoxLayout()
        self.search_box = QComboBox()
        self.search_box.setEditable(True)
        self.search_box.lineEdit().setPlaceholderText(
            "What kind of dataset? (example: math word problems)")
        self.search_box.lineEdit().returnPressed.connect(self.start_search)
        self.search_box.activated.connect(self._history_activated)
        self._refresh_history_box()
        row.addWidget(self.search_box, stretch=1)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.start_search)
        row.addWidget(self.search_button)
        self.stop_search_button = QPushButton("Stop")
        self.stop_search_button.setEnabled(False)
        self.stop_search_button.clicked.connect(self.stop_search)
        row.addWidget(self.stop_search_button)
        layout.addLayout(row)
        layout.addWidget(_explain(
            "Searches GitHub and Hugging Face live. Results come "
            "back newest first. Recent searches are kept in the "
            "search box drop-down."))

        chip_row = QHBoxLayout()
        chip_row.addWidget(QLabel("Category:"))
        self.chip_buttons: dict[str, QPushButton] = {}
        for key, (label, _suffix) in core.CATEGORIES.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == "all")
            button.clicked.connect(self._chip_clicked)
            self.chip_buttons[key] = button
            chip_row.addWidget(button)
        chip_row.addStretch(1)
        layout.addLayout(chip_row)

        filter_row = QHBoxLayout()
        self.active_check = QCheckBox("Active only (updated recently)")
        self.active_check.setChecked(True)
        filter_row.addWidget(self.active_check)
        self.window_combo = QComboBox()
        self.window_combo.addItems(list(core.ACTIVE_WINDOWS.keys()))
        self.window_combo.setCurrentText("12 months")
        filter_row.addWidget(QLabel("Updated in the last:"))
        filter_row.addWidget(self.window_combo)
        filter_row.addWidget(QLabel("At least this many stars or likes:"))
        self.stars_combo = QComboBox()
        self.stars_combo.addItems(str(n) for n in core.MIN_STARS_CHOICES)
        filter_row.addWidget(self.stars_combo)
        filter_row.addWidget(QLabel("License:"))
        self.license_combo = QComboBox()
        self.license_combo.addItem("Any license", "")
        for choice in LICENSE_CHOICES[1:]:
            self.license_combo.addItem(choice, choice)
        filter_row.addWidget(self.license_combo)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)
        layout.addWidget(_explain(
            "Active only keeps results updated recently. Stars counts "
            "GitHub stars and Hugging Face likes. A license filter "
            "hides results with no license shown."))

        self.results_empty_label = QLabel(
            "No results yet. Type a few words and press Search.")
        self.results_empty_label.setStyleSheet("color: #9fb0cc;")
        layout.addWidget(self.results_empty_label)

        self.results_table = QTableWidget(0, len(RESULT_COLUMNS))
        self.results_table.setHorizontalHeaderLabels(RESULT_COLUMNS)
        self.results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.results_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.setSortingEnabled(True)
        # This PySide6 build defaults a fresh header to column 0
        # descending; the default view is newest first, so say so.
        self.results_table.horizontalHeader().setSortIndicator(
            R_UPDATED, Qt.SortOrder.DescendingOrder)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setMinimumHeight(220)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.horizontalHeader().sortIndicatorChanged.connect(
            self._results_sorted)
        self.results_table.doubleClicked.connect(self._open_result)
        self.results_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.results_table.customContextMenuRequested.connect(
            self._results_context_menu)
        layout.addWidget(self.results_table, stretch=1)

        add_row = QHBoxLayout()
        self.add_selected_button = QPushButton("Add selected to My List")
        self.add_selected_button.clicked.connect(self.add_selected)
        add_row.addWidget(self.add_selected_button)
        self.add_all_button = QPushButton("Add all results to My List")
        self.add_all_button.clicked.connect(self.add_all_results)
        add_row.addWidget(self.add_all_button)
        self.stella_check_button = QPushButton("Check STELLA-ready")
        self.stella_check_button.clicked.connect(
            lambda: self.start_stella_check("results"))
        add_row.addWidget(self.stella_check_button)
        add_row.addStretch(1)
        layout.addLayout(add_row)

        sites_box = QGroupBox("Search on other websites")
        sites_layout = QVBoxLayout(sites_box)
        sites_layout.addWidget(QLabel(
            "These open the same search in your browser. "
            "Add or remove websites in the Sources tab."
        ))
        self.site_buttons_row = QHBoxLayout()
        self.site_buttons_row.addStretch(1)
        sites_layout.addLayout(self.site_buttons_row)
        layout.addWidget(sites_box)

        # The hand-add form lives in its own window now (T22): the
        # Search tab stays about searching.
        self.add_link_dialog = AddLinkDialog(self)
        self.found_url = self.add_link_dialog.found_url
        self.found_title = self.add_link_dialog.found_title
        self.found_category = self.add_link_dialog.found_category
        self.found_note = self.add_link_dialog.found_note
        self.found_add_button = QPushButton("Add a link I found")
        self.found_add_button.clicked.connect(self.show_add_link_dialog)
        layout.addWidget(self.found_add_button)

        scroll.setWidget(inner)
        tab_layout.addWidget(scroll)
        self.tabs.addTab(tab, "Search")

    def _results_sorted(self, section: int, order):
        self._results_sorting = True
        if section >= 0:
            self._results_sort = (section, order)

    def _refresh_history_box(self):
        """Refill the search box drop-down from recent searches."""
        current = self.search_box.currentText()
        self.search_box.blockSignals(True)
        self.search_box.clear()
        for entry in core.load_history():
            self.search_box.addItem(entry["query"])
        self.search_box.setCurrentText(current)
        self.search_box.blockSignals(False)

    def _history_activated(self, index: int):
        """A picked recent search: restore its category, run it."""
        entries = core.load_history()
        if 0 <= index < len(entries):
            category = entries[index].get("category") or "all"
            for key, button in self.chip_buttons.items():
                button.setChecked(key == category)
            self.start_search()

    def show_add_link_dialog(self):
        """Open the hand-add form (T22)."""
        if self.add_link_dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_found_link()

    def _chip_clicked(self):
        sender = self.sender()
        for key, button in self.chip_buttons.items():
            button.setChecked(button is sender)

    def selected_category(self) -> str:
        for key, button in self.chip_buttons.items():
            if button.isChecked():
                return key
        return "all"

    def start_search(self):
        if self.search_worker is not None and self.search_worker.isRunning():
            return
        category = self.selected_category()
        typed = self.search_box.currentText()
        query = core.build_query(typed, category)
        core.save_history(typed, category)
        self._refresh_history_box()
        self._last_query = query
        self._last_category = category
        cached = core.load_search_cache(query, category)
        if cached is not None:
            self._search_done(cached, [], cached=True)
            return
        self.search_button.setEnabled(False)
        self.stop_search_button.setEnabled(True)
        self._set_busy(True)
        self._show_skeleton()
        self.statusBar().showMessage(f"Searching for: {query}")
        core.log(f"Search started: {query!r} (category {category})")
        self.search_worker = SearchWorker(
            query, category, core.get_github_token()
        )
        self.search_worker.finished_ok.connect(self._search_done)
        self.search_worker.finished_error.connect(self._search_failed)
        self.search_worker.finished_stopped.connect(self._search_stopped)
        self.search_worker.start()

    def stop_search(self):
        """Stop the running search (T3, U26)."""
        if self.search_worker is not None and self.search_worker.isRunning():
            self.search_worker.request_stop()
            self.stop_search_button.setEnabled(False)
            self.statusBar().showMessage("Stopping the search...")

    def _search_stopped(self):
        self.search_button.setEnabled(True)
        self.stop_search_button.setEnabled(False)
        self._set_busy(False)
        self._clear_skeleton()
        self.statusBar().showMessage("Search stopped.")

    def _show_skeleton(self):
        """Grey placeholder rows while a search runs (T27)."""
        table = self.results_table
        table.setSortingEnabled(False)
        table.setRowCount(5)
        for row in range(5):
            cell = _cell("Loading...")
            cell.setForeground(Qt.GlobalColor.gray)
            table.setItem(row, R_TITLE, cell)
        self._showing_skeleton = True
        self.results_empty_label.setVisible(False)

    def _clear_skeleton(self):
        if self._showing_skeleton:
            self._showing_skeleton = False
            self.results_table.setRowCount(0)

    def _update_results_empty_label(self):
        empty = (self.results_table.rowCount() == 0
                 and not self._showing_skeleton)
        self.results_empty_label.setVisible(empty)

    def _search_done(self, results: list[dict],
                     failures: list | None = None, cached: bool = False):
        self.search_button.setEnabled(True)
        self.stop_search_button.setEnabled(False)
        self._set_busy(False)
        self._clear_skeleton()
        months = core.ACTIVE_WINDOWS[self.window_combo.currentText()]
        min_stars = int(self.stars_combo.currentText())
        license_filter = self.license_combo.currentData() or ""
        total_before = len(results)
        filtered = core.filter_results(
            results,
            active_only=self.active_check.isChecked(),
            months=months,
            min_stars=min_stars,
            license_filter=license_filter,
        )
        # Newest first (T11): GitHub and Hugging Face results merged
        # by updated date, so the README claim is true now.
        filtered.sort(key=lambda item: item.get("updated") or "",
                      reverse=True)
        self.results = filtered
        self._show_results()
        if not cached and self._last_query is not None:
            core.save_search_cache(self._last_query,
                                   self._last_category or "all", results)
            core.bump_search_count()
        self._refresh_rate()
        self._refresh_stats()
        count = len(self.results)
        hidden = total_before - count
        word = "result" if count == 1 else "results"
        message = f"Found {count} {word}"
        if hidden:
            message += f" ({hidden} hidden by filters)"
        if cached:
            message += " (from the last hour, no new search)"
        message += ". Pick rows and add them to My List."
        failures = failures or []
        if failures:
            # Only one source can have failed here (both failing takes
            # the error path); name it in plain words (U32).
            failed = failures[0][0]
            other = "Hugging Face" if failed == "GitHub" else "GitHub"
            message += (
                f" {failed} search did not answer, "
                f"showing {other} results only."
            )
        self.statusBar().showMessage(message)
        core.log(f"Search finished: {count} results after filters")

    def _search_failed(self, message: str):
        self.search_button.setEnabled(True)
        self.stop_search_button.setEnabled(False)
        self._set_busy(False)
        self._clear_skeleton()
        self._refresh_rate()
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "Search failed", message)

    def _show_results(self):
        table = self.results_table
        table.setSortingEnabled(False)
        table.setRowCount(len(self.results))
        for row, item in enumerate(self.results):
            downloads = core._safe_int(item.get("downloads"))
            table.setItem(row, R_TITLE,
                          _cell(item.get("title", ""), row_index=row))
            table.setItem(row, R_SOURCE, _cell(item.get("source", "")))
            table.setItem(row, R_STARS,
                          _cell(str(item.get("stars", 0)),
                                sort_value=core._safe_int(
                                    item.get("stars"))))
            table.setItem(row, R_DOWNLOADS,
                          _cell(str(downloads) if downloads else "",
                                sort_value=downloads))
            table.setItem(row, R_UPDATED,
                          _cell(core.format_date(item.get("updated")),
                                sort_value=item.get("updated") or ""))
            table.setItem(row, R_DESC,
                          _cell(item.get("description", "")))
            table.setItem(row, R_LICENSE,
                          _cell(item.get("license", "")))
            table.setItem(row, R_FORMAT, _cell(item.get("format", "")))
            status = item.get("stella_status") or ""
            label = core.STELLA_STATUS_LABELS.get(status, status) \
                if status else ""
            stella_cell = _cell(label)
            if item.get("stella_files"):
                tip = f"{label}: {', '.join(item['stella_files'][:5])}"
                if item.get("stella_size"):
                    tip += (f" ({core.format_size(item['stella_size'])} "
                            "total)")
                stella_cell.setToolTip(tip)
            table.setItem(row, R_STELLA, stella_cell)
        table.setSortingEnabled(self._results_sorting)
        if self._results_sorting:
            # Re-apply the sort the user last chose (newest first
            # by default): re-enabling sorting would otherwise fall
            # back to this build's fresh-header default, title
            # descending, and break the newest-first order.
            table.sortByColumn(*self._results_sort)
        table.resizeColumnsToContents()
        # A long description must not shove the other columns off
        # the window; the cell still shows the start of the text.
        if table.columnWidth(R_DESC) > 300:
            table.setColumnWidth(R_DESC, 300)
        self._update_results_empty_label()

    def _selected_results(self) -> list[dict]:
        # Visual rows are not list indexes once the user sorts, so
        # each title cell carries its item index (ROW_ROLE).
        rows = set()
        for index in self.results_table.selectedIndexes():
            cell = self.results_table.item(index.row(), R_TITLE)
            if cell is None:
                continue
            stored = cell.data(ROW_ROLE)
            if isinstance(stored, int) and 0 <= stored < len(self.results):
                rows.add(stored)
        return [self.results[row] for row in sorted(rows)]

    def _results_context_menu(self, pos):
        if not self._selected_results():
            return
        menu = QMenu(self)
        menu.addAction("Add to My List", self.add_selected)
        menu.addAction("Open in browser", self._open_result)
        menu.addAction("Check STELLA-ready",
                       lambda: self.start_stella_check("results"))
        menu.exec(self.results_table.viewport().mapToGlobal(pos))

    def start_stella_check(self, target: str):
        """Check selected rows for STELLA-ready data files (T29)."""
        if self.stella_worker is not None and self.stella_worker.isRunning():
            return
        if target == "results":
            picked = []
            seen = set()
            for index in self.results_table.selectedIndexes():
                cell = self.results_table.item(index.row(), R_TITLE)
                if cell is None:
                    continue
                stored = cell.data(ROW_ROLE)
                if (isinstance(stored, int)
                        and 0 <= stored < len(self.results)
                        and stored not in seen):
                    seen.add(stored)
                    picked.append((stored, self.results[stored]))
        else:
            picked = [(row, self.my_list[row])
                      for row in self._selected_list_rows()]
        if not picked:
            self.statusBar().showMessage(
                "Nothing selected. Click a row first.")
            return
        self._stella_target = target
        self._set_busy(True)
        self.stella_check_button.setEnabled(False)
        self.list_stella_button.setEnabled(False)
        word = "dataset" if len(picked) == 1 else "datasets"
        self.statusBar().showMessage(
            f"Checking {len(picked)} {word} for STELLA-ready files...")
        self.stella_worker = StellaCheckWorker(
            picked, token=core.get_github_token(), parent=self)
        self.stella_worker.item_checked.connect(self._stella_checked)
        self.stella_worker.all_done.connect(self._stella_check_done)
        self.stella_worker.start()

    def _stella_checked(self, index: int, status: str, detail: str,
                        files: list, total_size):
        if self._stella_target == "results":
            if 0 <= index < len(self.results):
                item = self.results[index]
                item["stella_status"] = status
                item["stella_files"] = list(files)
                item["stella_size"] = total_size or 0
                self._update_result_stella_cell(index)
        else:
            if 0 <= index < len(self.my_list):
                item = self.my_list[index]
                item["stella_status"] = status
                item["stella_files"] = list(files)
                item["stella_size"] = total_size or 0
                self._update_list_stella_cell(index)

    def _update_result_stella_cell(self, index: int):
        item = self.results[index]
        for row in range(self.results_table.rowCount()):
            cell = self.results_table.item(row, R_TITLE)
            if cell is not None and cell.data(ROW_ROLE) == index:
                label = core.STELLA_STATUS_LABELS.get(
                    item.get("stella_status") or "", "")
                stella = _cell(label)
                if item.get("stella_files"):
                    tip = (f"{label}: "
                           f"{', '.join(item['stella_files'][:5])}")
                    if item.get("stella_size"):
                        tip += (f" ({core.format_size(item['stella_size'])} "
                                "total)")
                    stella.setToolTip(tip)
                self.results_table.setItem(row, R_STELLA, stella)
                return

    def _stella_check_done(self, stopped: bool):
        self._set_busy(False)
        self.stella_check_button.setEnabled(True)
        self.list_stella_button.setEnabled(True)
        if self._stella_target == "list":
            core.save_list(self.my_list)
        self.statusBar().showMessage(
            "STELLA check stopped." if stopped else "STELLA check done.")

    def add_selected(self):
        chosen = self._selected_results()
        if not chosen:
            self.statusBar().showMessage(
                "Nothing selected. Click a row first, or use Add all results."
            )
            return
        added = 0
        for item in chosen:
            self.my_list, was_added = core.add_to_list(self.my_list, item)
            added += 1 if was_added else 0
        core.save_list(self.my_list)
        self._refresh_list_tab()
        self.statusBar().showMessage(f"Added {added} to My List.")

    def add_all_results(self):
        added = 0
        for item in self.results:
            self.my_list, was_added = core.add_to_list(self.my_list, item)
            added += 1 if was_added else 0
        core.save_list(self.my_list)
        self._refresh_list_tab()
        self.statusBar().showMessage(f"Added {added} to My List.")

    def _open_result(self):
        chosen = self._selected_results()
        if chosen and chosen[0].get("url"):
            QDesktopServices.openUrl(chosen[0]["url"])

    def _refresh_site_buttons(self):
        layout = self.site_buttons_row
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for site in core.load_sources():
            button = QPushButton(f"Search on {site['name']}")
            button.clicked.connect(
                lambda _checked=False, s=site: self._open_site(s)
            )
            layout.addWidget(button)
        layout.addStretch(1)

    def _open_site(self, site: dict):
        query = core.build_query(
            self.search_box.currentText(), self.selected_category()
        )
        url = core.build_site_url(site["url"], query)
        QDesktopServices.openUrl(url)
        self.statusBar().showMessage(f"Opened {site['name']} in the browser.")

    def add_found_link(self):
        url = self.found_url.text().strip()
        if not url:
            self.statusBar().showMessage("Paste a link first.")
            return
        if "://" not in url:
            url = "https://" + url
        item = {
            "title": self.found_title.text().strip() or url,
            "url": url,
            "category": core.category_key_for_label(
                self.found_category.currentText()),
            "source": "Found by hand",
            "stars": 0,
            "updated": "",
            "license": "",
            "notes": self.found_note.text().strip(),
        }
        self.my_list, added = core.add_to_list(self.my_list, item)
        core.save_list(self.my_list)
        self._refresh_list_tab()
        if added:
            self.found_url.clear()
            self.found_title.clear()
            self.found_note.clear()
            self.statusBar().showMessage("Added to My List.")
        else:
            self.statusBar().showMessage("That link is already in My List.")

    # ------------------------------------------------------------ my list tab

    def _build_list_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel(
            "Your saved links. Double-click a row to open it. "
            "Export when the list is ready for STELLA."
        ))
        format_note = QLabel(core.STELLA_FORMAT_NOTE)
        format_note.setWordWrap(True)
        layout.addWidget(format_note)

        # Collections (T30): named lists, one active at a time.
        collection_row = QHBoxLayout()
        collection_row.addWidget(QLabel("List:"))
        self.collection_combo = QComboBox()
        self.collection_combo.currentTextChanged.connect(
            self._switch_collection)
        collection_row.addWidget(self.collection_combo, stretch=1)
        self.collection_new_button = QPushButton("New list")
        self.collection_new_button.clicked.connect(self.new_collection)
        collection_row.addWidget(self.collection_new_button)
        self.collection_rename_button = QPushButton("Rename")
        self.collection_rename_button.clicked.connect(self.rename_collection)
        collection_row.addWidget(self.collection_rename_button)
        self.collection_delete_button = QPushButton("Delete list")
        self.collection_delete_button.clicked.connect(self.delete_collection)
        collection_row.addWidget(self.collection_delete_button)
        layout.addLayout(collection_row)
        self._refresh_collection_combo()

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Find in this list:"))
        self.list_filter_box = QLineEdit()
        self.list_filter_box.setPlaceholderText(
            "Type to filter by title or link")
        self.list_filter_box.textChanged.connect(self._apply_list_filter)
        filter_row.addWidget(self.list_filter_box, stretch=1)
        filter_row.addWidget(QLabel("State:"))
        self.state_filter_combo = QComboBox()
        self.state_filter_combo.addItem("All states", "")
        for key, label in core.WORKFLOW_STATES.items():
            self.state_filter_combo.addItem(label, key)
        self.state_filter_combo.currentIndexChanged.connect(
            self._apply_list_filter)
        filter_row.addWidget(self.state_filter_combo)
        layout.addLayout(filter_row)

        self.list_empty_label = QLabel(
            "This list is empty. Add results from the Search tab, "
            "or import a list.")
        self.list_empty_label.setStyleSheet("color: #9fb0cc;")
        layout.addWidget(self.list_empty_label)

        self.list_table = QTableWidget(0, len(LIST_COLUMNS))
        self.list_table.setHorizontalHeaderLabels(LIST_COLUMNS)
        self.list_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.list_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list_table.setAlternatingRowColors(True)
        self.list_table.setMinimumHeight(200)
        # Sorting is on so header clicks work, but with no indicator
        # set: the list shows in the user's own order until they
        # click a heading. (A fresh header defaults to column 0
        # descending in this build, which would sort the list by
        # title on the first refresh.)
        self.list_table.setSortingEnabled(True)
        self.list_table.horizontalHeader().setSortIndicator(
            -1, Qt.SortOrder.AscendingOrder)
        self.list_table.horizontalHeader().setStretchLastSection(True)
        self.list_table.horizontalHeader().sortIndicatorChanged.connect(
            self._list_sorted)
        self.list_table.doubleClicked.connect(self._open_list_item)
        self.list_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_table.customContextMenuRequested.connect(
            self._list_context_menu)
        # Drag rows to reorder (T28). Sorting stays off until the
        # user clicks a heading, so a drag lands where it is dropped.
        self.list_table.setDragEnabled(True)
        self.list_table.setAcceptDrops(True)
        self.list_table.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self.list_table.setDragDropOverwriteMode(False)
        self.list_table.model().rowsMoved.connect(self._list_rows_moved)
        layout.addWidget(self.list_table, stretch=1)

        row = QHBoxLayout()
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self.remove_selected)
        row.addWidget(self.remove_button)
        self.clear_button = QPushButton("Clear all")
        self.clear_button.clicked.connect(self.clear_list)
        row.addWidget(self.clear_button)
        self.undo_button = QPushButton("Undo remove")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self.undo_remove)
        row.addWidget(self.undo_button)
        self.check_button = QPushButton("Check links")
        self.check_button.clicked.connect(self.start_link_check)
        row.addWidget(self.check_button)
        self.stop_check_button = QPushButton("Stop")
        self.stop_check_button.setEnabled(False)
        self.stop_check_button.clicked.connect(self.stop_link_check)
        row.addWidget(self.stop_check_button)
        self.copy_button = QPushButton("Copy all links")
        self.copy_button.clicked.connect(self.copy_all_links)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        layout.addLayout(row)

        action_row = QHBoxLayout()
        self.edit_button = QPushButton("Edit selected")
        self.edit_button.clicked.connect(self.edit_selected)
        action_row.addWidget(self.edit_button)
        self.list_stella_button = QPushButton("Check STELLA-ready")
        self.list_stella_button.clicked.connect(
            lambda: self.start_stella_check("list"))
        action_row.addWidget(self.list_stella_button)
        self.download_button = QPushButton("Download and convert")
        self.download_button.clicked.connect(self.start_download)
        action_row.addWidget(self.download_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("Export My List as:"))
        self.export_md_button = QPushButton("Markdown list")
        self.export_md_button.clicked.connect(
            lambda: self.export_list("md"))
        export_row.addWidget(self.export_md_button)
        self.export_json_button = QPushButton("JSON")
        self.export_json_button.clicked.connect(
            lambda: self.export_list("json"))
        export_row.addWidget(self.export_json_button)
        self.export_stella_button = QPushButton("STELLA manifest")
        self.export_stella_button.clicked.connect(
            lambda: self.export_list("stella"))
        export_row.addWidget(self.export_stella_button)
        self.import_button = QPushButton("Import a list")
        self.import_button.clicked.connect(self.import_list)
        export_row.addWidget(self.import_button)
        self.send_button = QPushButton("Send to STELLA")
        self.send_button.clicked.connect(self.send_to_stella)
        export_row.addWidget(self.send_button)
        export_row.addStretch(1)
        layout.addLayout(export_row)

        self.list_tab_index = self.tabs.addTab(tab, "My List (0)")

    def _list_sorted(self, section: int, _order):
        # Section -1 means the indicator was cleared (a drag, or a
        # move): the list is back in the user's own order.
        self._list_sorting = section >= 0

    # ------------------------------------------------------------ collections (T30)

    def _refresh_collection_combo(self):
        active, names = core.load_collections()
        self.collection_combo.blockSignals(True)
        self.collection_combo.clear()
        self.collection_combo.addItems(names)
        self.collection_combo.setCurrentText(active)
        self.collection_combo.blockSignals(False)

    def _save_active_items(self, data: dict):
        for col in data["collections"]:
            if col["name"] == self._active_collection:
                col["items"] = [core.normalize_item(item)
                                for item in self.my_list]

    def _switch_collection(self, name: str):
        if not name or name == self._active_collection:
            return
        _active, names = core.load_collections()
        if name not in names:
            return
        data = core.load_list_data()
        self._save_active_items(data)
        data["active"] = name
        core.save_collections(name, data["collections"])
        self._active_collection = name
        self.my_list = core.load_list()
        self._undo_stack.clear()
        self.undo_button.setEnabled(False)
        self._refresh_list_tab()
        self.statusBar().showMessage(f"Switched to {name}.")

    def new_collection(self):
        name, ok = QInputDialog.getText(
            self, "New list", "Name for the new list:")
        if not ok:
            return
        name = name.strip()
        if not name:
            self.statusBar().showMessage("Give the list a name first.")
            return
        _active, names = core.load_collections()
        if name in names:
            self.statusBar().showMessage(
                f"There is already a list called {name}.")
            return
        data = core.load_list_data()
        self._save_active_items(data)
        data["collections"].append({"name": name, "items": []})
        data["active"] = name
        core.save_collections(name, data["collections"])
        self._active_collection = name
        self.my_list = []
        self._refresh_collection_combo()
        self._refresh_list_tab()
        self.statusBar().showMessage(f"Created the list {name}.")

    def rename_collection(self):
        name, ok = QInputDialog.getText(
            self, "Rename list", "New name:",
            text=self._active_collection)
        if not ok:
            return
        name = name.strip()
        if not name or name == self._active_collection:
            return
        _active, names = core.load_collections()
        if name in names:
            self.statusBar().showMessage(
                f"There is already a list called {name}.")
            return
        data = core.load_list_data()
        for col in data["collections"]:
            if col["name"] == self._active_collection:
                col["name"] = name
        self._save_active_items(data)
        data["active"] = name
        core.save_collections(name, data["collections"])
        self._active_collection = name
        self._refresh_collection_combo()
        self.statusBar().showMessage(f"Renamed the list to {name}.")

    def delete_collection(self):
        _active, names = core.load_collections()
        if len(names) <= 1:
            self.statusBar().showMessage(
                "The last list cannot be deleted.")
            return
        answer = QMessageBox.question(
            self, "Delete list",
            f"Delete the list {self._active_collection} and its "
            f"{len(self.my_list)} links?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        data = core.load_list_data()
        data["collections"] = [col for col in data["collections"]
                               if col["name"] != self._active_collection]
        new_active = data["collections"][0]["name"]
        data["active"] = new_active
        core.save_collections(new_active, data["collections"])
        self._active_collection = new_active
        self.my_list = core.load_list()
        self._undo_stack.clear()
        self.undo_button.setEnabled(False)
        self._refresh_collection_combo()
        self._refresh_list_tab()
        self.statusBar().showMessage(
            f"Deleted the list. Now showing {new_active}.")

    # ------------------------------------------------------------ list filter (T19, T30)

    def _apply_list_filter(self):
        """Hide list rows that do not match the filter box or state."""
        text = self.list_filter_box.text().strip().lower()
        wanted_state = self.state_filter_combo.currentData() or ""
        for row in range(self.list_table.rowCount()):
            cell = self.list_table.item(row, L_TITLE)
            if cell is None:
                continue
            stored = cell.data(ROW_ROLE)
            if not isinstance(stored, int) or stored >= len(self.my_list):
                self.list_table.setRowHidden(row, True)
                continue
            item = self.my_list[stored]
            if wanted_state and \
                    (item.get("state") or "found") != wanted_state:
                self.list_table.setRowHidden(row, True)
                continue
            if text:
                hay = (f"{item.get('title', '')} "
                       f"{item.get('url', '')}").lower()
                self.list_table.setRowHidden(row, text not in hay)
            else:
                self.list_table.setRowHidden(row, False)

    def _refresh_list_tab(self):
        table = self.list_table
        table.setSortingEnabled(False)
        table.setRowCount(len(self.my_list))
        for row, item in enumerate(self.my_list):
            status = item.get("status", "")
            title = item["title"] + (f" [{status}]" if status else "")
            table.setItem(row, L_TITLE, _cell(title, row_index=row))
            table.setItem(row, L_CATEGORY,
                          _cell(core.category_label(
                              item.get("category", "all"))))
            table.setItem(row, L_SOURCE, _cell(item.get("source", "")))
            table.setItem(row, L_STARS,
                          _cell(str(item.get("stars", 0)),
                                sort_value=core._safe_int(
                                    item.get("stars"))))
            table.setItem(row, L_UPDATED,
                          _cell(core.format_date(item.get("updated")),
                                sort_value=item.get("updated") or ""))
            table.setItem(row, L_LINK, _cell(item.get("url", "")))
            table.setItem(row, L_NOTES, _cell(item.get("notes", "")))
            table.setItem(row, L_STATE,
                          _cell(core.WORKFLOW_STATES.get(
                              item.get("state") or "found", "Found")))
            stella_status = item.get("stella_status") or ""
            label = core.STELLA_STATUS_LABELS.get(
                stella_status, stella_status) if stella_status else ""
            stella_cell = _cell(label)
            if item.get("stella_files"):
                tip = f"{label}: {', '.join(item['stella_files'][:5])}"
                if item.get("stella_size"):
                    tip += (f" ({core.format_size(item['stella_size'])} "
                            "total)")
                stella_cell.setToolTip(tip)
            table.setItem(row, L_STELLA, stella_cell)
        if not self._list_sorting:
            # Clear the indicator BEFORE re-enabling sorting, or the
            # re-enable re-sorts by the stale column.
            table.horizontalHeader().setSortIndicator(
                -1, Qt.SortOrder.AscendingOrder)
        table.setSortingEnabled(True)
        table.resizeColumnsToContents()
        # A long link must not shove the State and STELLA columns
        # off the window; the cell still shows the start of it.
        if table.columnWidth(L_LINK) > 320:
            table.setColumnWidth(L_LINK, 320)
        self.tabs.setTabText(
            self.list_tab_index, f"My List ({len(self.my_list)})"
        )
        self.list_empty_label.setVisible(len(self.my_list) == 0)
        self._apply_list_filter()
        self._refresh_stats()

    def _selected_list_rows(self) -> list[int]:
        # Visual rows are not list indexes once the user sorts, so
        # each title cell carries its item index (ROW_ROLE).
        rows = set()
        for index in self.list_table.selectedIndexes():
            cell = self.list_table.item(index.row(), L_TITLE)
            if cell is None:
                continue
            stored = cell.data(ROW_ROLE)
            if isinstance(stored, int) and 0 <= stored < len(self.my_list):
                rows.add(stored)
        return sorted(rows)

    def _open_list_item(self):
        rows = self._selected_list_rows()
        if rows and self.my_list[rows[0]].get("url"):
            QDesktopServices.openUrl(self.my_list[rows[0]]["url"])

    def _list_context_menu(self, pos):
        rows = self._selected_list_rows()
        if not rows:
            return
        menu = QMenu(self)
        menu.addAction("Open in browser", self._open_list_item)
        menu.addAction("Edit", self.edit_selected)
        menu.addAction("Copy link", self._copy_selected_link)
        mark = menu.addMenu("Mark as")
        for key, label in core.WORKFLOW_STATES.items():
            mark.addAction(
                label, lambda _checked=False, k=key: self._mark_state(k))
        menu.addAction("Move up", lambda: self._move_selected(-1))
        menu.addAction("Move down", lambda: self._move_selected(1))
        menu.addAction("Remove", self.remove_selected)
        menu.exec(self.list_table.viewport().mapToGlobal(pos))

    def _copy_selected_link(self):
        rows = self._selected_list_rows()
        if rows:
            QGuiApplication.clipboard().setText(
                self.my_list[rows[0]]["url"])
            self.statusBar().showMessage("Link copied.")

    def _mark_state(self, state: str):
        rows = self._selected_list_rows()
        for row in rows:
            self.my_list[row]["state"] = state
        if rows:
            core.save_list(self.my_list)
            self._refresh_list_tab()
            label = core.WORKFLOW_STATES.get(state, state)
            self.statusBar().showMessage(f"Marked as {label}.")

    def _move_selected(self, delta: int):
        """Move the selected rows up or down (T28, library 027:
        dragging is never the only path to reorder)."""
        rows = self._selected_list_rows()
        if not rows:
            return
        if delta < 0 and rows[0] == 0:
            return
        if delta > 0 and rows[-1] == len(self.my_list) - 1:
            return
        order = rows if delta < 0 else list(reversed(rows))
        for row in order:
            self.my_list[row], self.my_list[row + delta] = (
                self.my_list[row + delta], self.my_list[row])
        core.save_list(self.my_list)
        self._list_sorting = False
        self._refresh_list_tab()
        for row in rows:
            self.list_table.selectRow(row + delta)

    def _list_rows_moved(self, _parent, _start, _end, _dest, _row):
        """A drag finished: save the new row order (T28)."""
        selected_urls = {self.my_list[row]["url"]
                         for row in self._selected_list_rows()}
        order = []
        for visual in range(self.list_table.rowCount()):
            cell = self.list_table.item(visual, L_TITLE)
            if cell is None:
                continue
            stored = cell.data(ROW_ROLE)
            if isinstance(stored, int) and 0 <= stored < len(self.my_list):
                order.append(self.my_list[stored])
        if len(order) == len(self.my_list):
            self.my_list = order
            core.save_list(self.my_list)
        # A drag drops where it lands; sorting turns back off so the
        # new order stays put.
        self._list_sorting = False
        self._refresh_list_tab()
        # The dropped rows stay selected in their new home (library 027).
        for visual in range(self.list_table.rowCount()):
            cell = self.list_table.item(visual, L_TITLE)
            if cell is None:
                continue
            stored = cell.data(ROW_ROLE)
            if (isinstance(stored, int)
                    and self.my_list[stored]["url"] in selected_urls):
                self.list_table.selectRow(visual)

    def _update_list_row_status(self, index: int, status: str):
        """Update one row's title cell in place (T17, U28)."""
        for row in range(self.list_table.rowCount()):
            cell = self.list_table.item(row, L_TITLE)
            if cell is not None and cell.data(ROW_ROLE) == index:
                title = self.my_list[index]["title"]
                if status:
                    title += f" [{status}]"
                cell.setText(title)
                return

    def _update_list_stella_cell(self, index: int):
        item = self.my_list[index]
        for row in range(self.list_table.rowCount()):
            cell = self.list_table.item(row, L_TITLE)
            if cell is not None and cell.data(ROW_ROLE) == index:
                label = core.STELLA_STATUS_LABELS.get(
                    item.get("stella_status") or "", "")
                stella = _cell(label)
                if item.get("stella_files"):
                    tip = (f"{label}: "
                           f"{', '.join(item['stella_files'][:5])}")
                    if item.get("stella_size"):
                        tip += (f" ({core.format_size(item['stella_size'])} "
                                "total)")
                    stella.setToolTip(tip)
                self.list_table.setItem(row, L_STELLA, stella)
                return

    def edit_selected(self):
        """Edit the selected link in place (T12)."""
        rows = self._selected_list_rows()
        if not rows:
            self.statusBar().showMessage("Nothing selected to edit.")
            return
        item = self.my_list[rows[0]]
        dialog = EditItemDialog(item, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if values["title"]:
            item["title"] = values["title"]
        item["category"] = values["category"]
        item["notes"] = values["notes"]
        item["state"] = values["state"]
        core.save_list(self.my_list)
        self._refresh_list_tab()
        self.statusBar().showMessage("Saved the changes.")

    def remove_selected(self):
        rows = self._selected_list_rows()
        if not rows:
            self.statusBar().showMessage("Nothing selected to remove.")
            return
        word = "link" if len(rows) == 1 else "links"
        answer = QMessageBox.question(
            self, "Remove from My List",
            f"Remove {len(rows)} {word} from My List?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = [(row, self.my_list[row]) for row in rows]
        self._undo_stack.append(removed)
        self.undo_button.setEnabled(True)
        keep = {row for row in rows}
        self.my_list = [item for index, item in enumerate(self.my_list)
                        if index not in keep]
        core.save_list(self.my_list)
        self._refresh_list_tab()
        self.statusBar().showMessage(f"Removed {len(rows)} from My List.")

    def undo_remove(self):
        """Bring back the last removed rows, where they were (T18)."""
        if not self._undo_stack:
            return
        removed = self._undo_stack.pop()
        for row, item in sorted(removed):
            self.my_list.insert(min(row, len(self.my_list)), item)
        self.undo_button.setEnabled(bool(self._undo_stack))
        core.save_list(self.my_list)
        self._refresh_list_tab()
        self.statusBar().showMessage("Removal undone.")

    def clear_list(self):
        if not self.my_list:
            return
        answer = QMessageBox.question(
            self, "Clear My List",
            "Remove every link from My List?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._undo_stack.append(
            [(row, item) for row, item in enumerate(self.my_list)])
        self.undo_button.setEnabled(True)
        self.my_list = []
        core.save_list(self.my_list)
        self._refresh_list_tab()
        self.statusBar().showMessage("My List is empty now.")

    def start_link_check(self):
        if not self.my_list:
            self.statusBar().showMessage("My List is empty. Nothing to check.")
            return
        if self.check_worker is not None and self.check_worker.isRunning():
            return
        for index, item in enumerate(self.my_list):
            item["status"] = "checking"
            self._update_list_row_status(index, "checking")
        self.check_button.setEnabled(False)
        self.stop_check_button.setEnabled(True)
        self._set_busy(True)
        self._check_total = len(self.my_list)
        self._check_done_count = 0
        self.statusBar().showMessage(
            f"Checking 0 of {self._check_total}..."
        )
        self.check_worker = LinkCheckWorker(
            [item["url"] for item in self.my_list]
        )
        self.check_worker.item_checked.connect(self._link_checked)
        self.check_worker.all_done.connect(self._link_check_done)
        self.check_worker.start()

    def stop_link_check(self):
        """Stop the link check after the link it is on now (U26)."""
        if self.check_worker is not None and self.check_worker.isRunning():
            self.check_worker.request_stop()
            self.stop_check_button.setEnabled(False)

    def _link_checked(self, index: int, status: str):
        if 0 <= index < len(self.my_list):
            self.my_list[index]["status"] = status
            self._update_list_row_status(index, status)
        self._check_done_count += 1
        self.statusBar().showMessage(
            f"Checking {self._check_done_count} of {self._check_total}..."
        )

    def _link_check_done(self, stopped: bool = False):
        self.check_button.setEnabled(True)
        self.stop_check_button.setEnabled(False)
        self._set_busy(False)
        if stopped:
            # Links never reached go back to unmarked: the check is
            # not running any more, so "checking" would be a lie.
            for index, item in enumerate(self.my_list):
                if item.get("status") == "checking":
                    item["status"] = ""
                    self._update_list_row_status(index, "")
        core.save_list(self.my_list)
        self._refresh_stats()
        if stopped:
            self.statusBar().showMessage(
                f"Stopped, {self._check_done_count} of "
                f"{self._check_total} checked."
            )
            return
        ok = sum(1 for item in self.my_list if item.get("status") == "ok")
        dead = sum(1 for item in self.my_list if item.get("status") == "dead")
        unknown = len(self.my_list) - ok - dead
        self.statusBar().showMessage(
            f"Link check done: {ok} working, {dead} dead, "
            f"{unknown} unknown."
        )

    def copy_all_links(self):
        if not self.my_list:
            self.statusBar().showMessage("My List is empty. Nothing to copy.")
            return
        text = "\n".join(item["url"] for item in self.my_list)
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage(
            f"Copied {len(self.my_list)} links to the clipboard."
        )

    def export_list(self, fmt: str):
        if not self.my_list:
            self.statusBar().showMessage(
                "My List is empty. Add some links first."
            )
            return
        if fmt == "md":
            default = "dataset-scout-list.md"
            file_filter = "Markdown (*.md)"
            text = core.export_markdown(self.my_list)
        elif fmt == "json":
            default = "dataset-scout-list.json"
            file_filter = "JSON (*.json)"
            text = core.export_json(self.my_list)
        else:
            default = "stella-manifest.json"
            file_filter = "JSON (*.json)"
            text = core.export_stella_manifest(self.my_list)
        path, _selected = QFileDialog.getSaveFileName(
            self, "Save export", default, file_filter
        )
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            core.log_error(f"Export failed: {exc}")
            QMessageBox.warning(self, "Export failed",
                                f"Could not write the file: {exc}")
            return
        core.log(f"Exported {len(self.my_list)} items as {fmt} to {path}")
        self.statusBar().showMessage(f"Exported {len(self.my_list)} links.")

    def import_list(self):
        """Read a Markdown, JSON, or STELLA export back in (T13)."""
        path, _selected = QFileDialog.getOpenFileName(
            self, "Import a list", "",
            "Dataset Scout lists (*.md *.json);;All files (*)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
            items = core.import_file_text(text)
        except (OSError, ValueError) as exc:
            core.log_error(f"Import failed: {exc}")
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        added = 0
        for item in items:
            self.my_list, was_added = core.add_to_list(self.my_list, item)
            added += 1 if was_added else 0
        core.save_list(self.my_list)
        self._refresh_list_tab()
        skipped = len(items) - added
        message = f"Imported {added} links."
        if skipped:
            message += f" {skipped} were already in the list."
        self.statusBar().showMessage(message)

    def send_to_stella(self):
        """Write the manifest into STELLA's home folder (T33)."""
        if not self.my_list:
            self.statusBar().showMessage(
                "My List is empty. Add some links first.")
            return
        home = core.stella_home()
        if home is None:
            QMessageBox.information(
                self, "STELLA is not installed here",
                "Dataset Scout could not find a STELLA folder on this "
                "PC. Export the STELLA manifest instead, and use it "
                "once STELLA is installed.")
            return
        path = home / "dataset-scout-manifest.json"
        try:
            path.write_text(
                core.export_stella_manifest(self.my_list),
                encoding="utf-8")
        except OSError as exc:
            core.log_error(f"Send to STELLA failed: {exc}")
            QMessageBox.warning(
                self, "Send to STELLA failed",
                f"Could not write the file: {exc}")
            return
        core.log(f"Sent {len(self.my_list)} items to STELLA at {path}")
        self.statusBar().showMessage(
            f"Sent {len(self.my_list)} links to STELLA.")

    def start_download(self):
        """Download selected datasets and convert them (T35)."""
        rows = self._selected_list_rows()
        if not rows:
            self.statusBar().showMessage(
                "Nothing selected. Click a row first.")
            return
        items = [(row, self.my_list[row]) for row in rows]
        out_dir = core.home_dir() / "stella-ready"
        dialog = DownloadDialog(items, out_dir, parent=self)
        dialog.start()
        dialog.exec()
        core.save_list(self.my_list)
        self._refresh_list_tab()
        self.statusBar().showMessage(
            "Download and convert finished. See the folder for files.")

    # ------------------------------------------------------------ sources tab

    def _build_sources_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(QLabel(
            "Websites to search on. Each one opens the same search "
            "in your browser from the Search tab."
        ))
        self.sources_table = QTableWidget(0, 3)
        self.sources_table.setHorizontalHeaderLabels(
            ["Website", "Address", ""])
        self.sources_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.sources_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sources_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.sources_table, stretch=1)

        add_box = QGroupBox("Add a website")
        add_layout = QFormLayout(add_box)
        self.site_name = QLineEdit()
        self.site_name.setPlaceholderText("Example: Kaggle Datasets")
        add_layout.addRow("Name:", self.site_name)
        self.site_url = QLineEdit()
        self.site_url.setPlaceholderText(
            "https://example.com/search?q={q} ({q} is where your search words go)"
        )
        add_layout.addRow("Address:", self.site_url)
        self.site_add_button = QPushButton("Add website")
        self.site_add_button.clicked.connect(self.add_source)
        add_layout.addRow("", self.site_add_button)
        add_layout.addRow("", _explain(
            "The address can hold {q} where your search words go, "
            "or be a plain address."))
        layout.addWidget(add_box)

        defaults_row = QHBoxLayout()
        self.restore_button = QPushButton("Restore default websites")
        self.restore_button.clicked.connect(self.restore_sources)
        defaults_row.addWidget(self.restore_button)
        defaults_row.addStretch(1)
        layout.addLayout(defaults_row)

        token_box = QGroupBox("GitHub token (optional)")
        token_layout = QFormLayout(token_box)
        self.token_field = QLineEdit()
        self.token_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_field.setText(core.get_github_token())
        token_layout.addRow("Token:", self.token_field)
        token_layout.addRow("", QLabel(
            "Stored on this PC only. Without one, GitHub allows "
            "fewer searches per hour."
        ))
        self.token_save_button = QPushButton("Save token")
        self.token_save_button.clicked.connect(self.save_token)
        token_layout.addRow("", self.token_save_button)
        layout.addWidget(token_box)

        debug_row = QHBoxLayout()
        self.debug_button = QPushButton("Copy debug report")
        self.debug_button.clicked.connect(self.copy_debug_report)
        debug_row.addWidget(self.debug_button)
        debug_row.addWidget(QLabel(
            "Copies the recent log with any token removed, for help."
        ))
        debug_row.addStretch(1)
        layout.addLayout(debug_row)

        self.tabs.addTab(tab, "Sources")
        self._refresh_sources_table()

    def _refresh_sources_table(self):
        sites = core.load_sources()
        table = self.sources_table
        # Remove the old Remove buttons first: setCellWidget does NOT
        # delete the widget it replaces, so a plain repopulate leaves
        # the old buttons visible on top of the new ones (doubled
        # Remove buttons, caught in verification 2026-11-13).
        for row in range(table.rowCount()):
            old = table.cellWidget(row, 2)
            if old is not None:
                table.removeCellWidget(row, 2)
                # Hide at once (safe even when this runs inside the
                # button's own clicked handler); deleteLater alone
                # leaves the old button painted until the loop next
                # spins, which a screenshot — and a fast eye — can
                # catch as a doubled Remove button.
                old.hide()
                old.deleteLater()
        table.setRowCount(len(sites))
        for row, site in enumerate(sites):
            table.setItem(row, 0, QTableWidgetItem(site["name"]))
            table.setItem(row, 1, QTableWidgetItem(site["url"]))
            remove = QPushButton("Remove")
            remove.clicked.connect(
                lambda _checked=False, name=site["name"]: self.remove_source(name)
            )
            table.setCellWidget(row, 2, remove)
        table.resizeColumnsToContents()

    def add_source(self):
        name = self.site_name.text().strip()
        url = self.site_url.text().strip()
        if not name or not url:
            self.statusBar().showMessage(
                "Give the website a name and an address first."
            )
            return
        sites = core.load_sources()
        sites.append({"name": name, "url": url})
        core.save_sources(sites)
        self.site_name.clear()
        self.site_url.clear()
        self._refresh_sources_table()
        self._refresh_site_buttons()
        self.statusBar().showMessage(f"Added {name}.")

    def remove_source(self, name: str):
        sites = [s for s in core.load_sources() if s["name"] != name]
        core.save_sources(sites)
        self._refresh_sources_table()
        self._refresh_site_buttons()
        self.statusBar().showMessage(f"Removed {name}.")

    def restore_sources(self):
        core.restore_default_sources()
        self._refresh_sources_table()
        self._refresh_site_buttons()
        self.statusBar().showMessage("Default websites restored.")

    def save_token(self):
        core.set_github_token(self.token_field.text())
        self.statusBar().showMessage(
            "Token saved on this PC. It is never written to a log."
        )

    def copy_debug_report(self):
        QGuiApplication.clipboard().setText(core.debug_report())
        self.statusBar().showMessage("Debug report copied to the clipboard.")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Dataset Scout")
    app.setStyleSheet(DARK_STYLESHEET)
    # Single instance (U27): a second launch asks the running window
    # to come forward and exits, instead of starting a second copy.
    guard = SingleInstance()
    if not guard.try_take():
        if guard.notify_running():
            return 0
        # No server answered: the owner may have died between taking
        # the lock and starting its server. The notify retries gave
        # the lock file time to age past the stale window, so a
        # second try takes a stale lock over cleanly.
        if not guard.try_take():
            return 0
    window = MainWindow()
    guard.start_server(window.bring_to_front)
    window.show()
    return app.exec()
