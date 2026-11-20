"""Dataset Scout window — one window, three tabs, plain words.

Tabs: Search | My List (n) | Sources.
The status bar is the ONLY status surface in the window.
"""

from __future__ import annotations

import copy
import hashlib
import html
import sys
import threading
import time
from concurrent.futures import (
    FIRST_COMPLETED, ThreadPoolExecutor, wait,
)
from pathlib import Path

from PySide6.QtCore import (
    QLockFile, QPoint, QRect, QSettings, QSize, QThread, QTimer, Qt, QUrl,
    Signal,
)
from PySide6.QtGui import (
    QActionGroup, QColor, QDesktopServices, QGuiApplication, QIcon,
    QKeySequence, QPainter, QShortcut,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLayout, QLineEdit,
    QListWidget, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from . import (apis, core, download, history, parquet, score, selftest,
               stella_link, watch)
from . import queue as dlqueue

HERE = Path(__file__).resolve().parent.parent
ICON_PATH = HERE / "assets" / "scout-256.png"

# QSettings home for the saved window geometry (U5).
SETTINGS_ORG = "BrokeNBored"
SETTINGS_APP = "DatasetScout"

DEFAULT_WIDTH = 1100
DEFAULT_HEIGHT = 700

RESULT_COLUMNS = ["Title", "Source", "Stars", "Downloads", "Updated",
                  "Description", "License", "Format", "STELLA", "Score"]
LIST_COLUMNS = ["Title", "Category", "Source", "Stars", "Updated",
                "Link", "Notes", "State", "STELLA", "Score"]

# Results table column indexes.
(R_TITLE, R_SOURCE, R_STARS, R_DOWNLOADS, R_UPDATED, R_DESC,
 R_LICENSE, R_FORMAT, R_STELLA, R_SCORE) = range(len(RESULT_COLUMNS))
# List table column indexes.
(L_TITLE, L_CATEGORY, L_SOURCE, L_STARS, L_UPDATED, L_LINK,
 L_NOTES, L_STATE, L_STELLA, L_SCORE) = range(len(LIST_COLUMNS))

# Item data roles: UserRole holds the sort value, UserRole + 1 the
# index of the row's item in self.results / self.my_list.
SORT_ROLE = Qt.ItemDataRole.UserRole
ROW_ROLE = Qt.ItemDataRole.UserRole + 1

# Row heights for the density control (T53, 064): comfortable is
# the default, compact is the opt-in for scanning long lists.
DENSITY_ROW_HEIGHTS = {"comfortable": 48, "compact": 32}

# Score column cap (v0.1.6 T11): the widest score text is
# "100 Worth a look"; anything wider is empty space taken from
# Title and Description.
SCORE_COLUMN_MAX = 132

def _display_version(tag) -> str:
    """A version as the About box shows it (v0.1.6 T19): no
    leading "v", so the update popup, the restart prompt, and
    the status bar all say it the same way."""
    text = str(tag or "")
    return text[1:] if text.startswith("v") else text


def _download_dir() -> Path:
    """Where downloads are saved (v0.1.6 T38): the folder the
    user picked in the Download window, remembered in the
    config, or the stella-ready folder under the Scout home."""
    saved = core.load_config().get("download_dir")
    if saved:
        return Path(str(saved))
    return core.home_dir() / "stella-ready"


def _save_download_dir(path) -> None:
    """Remember the download destination (v0.1.6 T38)."""
    config = core.load_config()
    config["download_dir"] = str(path)
    core.save_config(config)


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
/* A visible focus ring (023 Focus-first navigation): a gold border
   on :focus. outline renders 0 px in this PySide6 build, so the
   border is the ring. */
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QTextEdit:focus,
QTextBrowser:focus, QListWidget:focus, QTableWidget:focus,
QCheckBox:focus, QTabBar::tab:focus {
    border: 2px solid #ffd166;
}
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


def _score_cell(item: dict):
    """The quality score cell for one item (T57): the number sorts,
    the band says what it means, the tooltip shows the working."""
    scored = score.score_item(item)
    cell = _cell(f"{scored['score']} {scored['label']}",
                 sort_value=scored["score"])
    cell.setToolTip(score.describe_score(scored))
    return cell


def _scaled_font(widget, factor: float):
    """A copy of the widget's font, scaled by factor (053).

    Text sizes come from the font, never from fixed pixels, so
    system text scaling (GNOME Large Text) grows the app with it
    and the hierarchy (heading bigger than body) holds at any size.
    """
    font = widget.font()
    size = font.pointSizeF()
    if size > 0:
        font.setPointSizeF(max(6.0, size * factor))
    return font


def _explain(text: str) -> QLabel:
    """A small muted explanation line under a field (U20)."""
    label = QLabel(text)
    label.setStyleSheet("color: #9fb0cc;")
    label.setFont(_scaled_font(label, 0.85))
    label.setWordWrap(True)
    return label


def _heading(text: str) -> QLabel:
    """A bold section heading, sized from the font (053)."""
    label = QLabel(text)
    font = _scaled_font(label, 1.3)
    font.setBold(True)
    label.setFont(font)
    label.setWordWrap(True)
    return label


def _looks_like_url(text: str) -> bool:
    """A light plausibility check for a web address (044).

    Advisory only: it warns, it never blocks a save, because a
    host the check does not know (an intranet name, localhost)
    may still be real.
    """
    text = text.strip()
    if not text or " " in text:
        return False
    host = text.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    return "." in host


def _plain_read_error(exc: BaseException) -> str:
    """A file-read failure in plain words (v0.1.4 T24): no errno,
    no full paths. A ValueError from the importer already carries
    plain words ("That JSON file holds no links."), so it is kept;
    a decoding failure gets a plain line of its own."""
    if isinstance(exc, FileNotFoundError):
        return "That file is gone."
    if isinstance(exc, PermissionError):
        return "Could not read that file. Check who can open it."
    if isinstance(exc, UnicodeDecodeError):
        return "That file is not text Scout can read."
    if isinstance(exc, ValueError):
        return str(exc)
    return "Could not read that file."


def _plain_write_error(exc: BaseException) -> str:
    """A file-write failure in plain words (v0.1.4 T24): no errno,
    no full paths."""
    if isinstance(exc, FileNotFoundError):
        return "That folder is gone. Pick another place to save."
    if isinstance(exc, PermissionError):
        return "Could not write there. Pick another place."
    if isinstance(exc, FileExistsError):
        return "Something is already in the way there. Pick another place."
    return "Could not write there."


def _inline_error() -> QLabel:
    """An inline validation message beside a field (044).

    Empty and hidden until a check fails. Words plus color, never
    color alone (031).
    """
    label = QLabel("")
    label.setStyleSheet("color: #ff9a9a;")
    label.setWordWrap(True)
    label.setVisible(False)
    return label


class FlowLayout(QLayout):
    """A layout that wraps its widgets onto new rows (012).

    The standard Qt flow-layout example: widgets flow left to
    right and wrap when the row is full, so a long row of site
    buttons never pushes the tab wider than the window.
    """

    def __init__(self, parent=None, margin=0, spacing=6):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing
        self._items: list = []

    def __del__(self):
        while self.takeAt(0) is not None:
            pass

    def addItem(self, item):  # noqa: N802 - Qt naming
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):  # noqa: N802 - Qt naming
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):  # noqa: N802 - Qt naming
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802 - Qt naming
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt naming
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt naming
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):  # noqa: N802 - Qt naming
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):  # noqa: N802 - Qt naming
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802 - Qt naming
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margin = self.contentsMargins().left()
        size += QSize(2 * margin, 2 * margin)
        return size

    def _do_layout(self, rect, test_only: bool) -> int:
        x = rect.x()
        y = rect.y()
        line_height = 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            next_x = x + item.sizeHint().width() + self._spacing
            if next_x - self._spacing > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + self._spacing
                next_x = x + item.sizeHint().width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y()


class CollapsibleSection(QWidget):
    """A section with a header that hides or shows its content
    (012 progressive disclosure, U38 collapsible headers).

    Expanded by default: nothing is hidden from a first-time
    user; collapsing is the user's choice when a tab feels busy.
    With a key, the choice is remembered in the config (T47),
    like row density: collapsing a section and restarting keeps
    it collapsed.
    """

    def __init__(self, title: str, parent=None, key: str | None = None,
                 default_expanded: bool = True):
        super().__init__(parent)
        self._title = title
        self._key = key
        # A remembered choice always wins (T47); the default only
        # shapes the first view (v0.1.6 T30).
        self._expanded = bool(default_expanded)
        if key is not None:
            saved = core.load_config().get("sections") or {}
            if isinstance(saved, dict) and key in saved:
                self._expanded = bool(saved[key])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = QPushButton()
        self.toggle.setStyleSheet("text-align: left; font-weight: bold;")
        self.toggle.setToolTip("Show or hide this group of buttons.")
        self.toggle.clicked.connect(self._flip)
        layout.addWidget(self.toggle)
        self.content = QWidget()
        layout.addWidget(self.content)
        self._refresh()

    def set_content_layout(self, content_layout):
        """Put the section's buttons (or any layout) inside."""
        self.content.setLayout(content_layout)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool):
        self._expanded = bool(expanded)
        self._refresh()
        if self._key is not None:
            config = core.load_config()
            sections = config.get("sections")
            if not isinstance(sections, dict):
                sections = {}
            sections[self._key] = self._expanded
            config["sections"] = sections
            core.save_config(config)

    def _flip(self):
        self.set_expanded(not self._expanded)

    def _refresh(self):
        self.toggle.setText(
            ("▾ " if self._expanded else "▸ ") + self._title)
        self.content.setVisible(self._expanded)


class LinkHealthBar(QWidget):
    """One small bar: working vs dead vs unknown links (037).

    The numbers live in the stats strip text beside it, so the
    bar never carries meaning by color alone (068).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(120, 12)
        self._ok = 0
        self._dead = 0
        self._unknown = 0
        self.setToolTip("No links saved yet.")

    def set_counts(self, ok: int, dead: int, unknown: int):
        self._ok = ok
        self._dead = dead
        self._unknown = unknown
        self.setToolTip(
            f"Working: {ok}, dead: {dead}, unknown: {unknown}")
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt naming
        painter = QPainter(self)
        width = self.width()
        height = self.height()
        painter.fillRect(0, 0, width, height, QColor("#0f1a30"))
        total = self._ok + self._dead + self._unknown
        if total == 0:
            painter.fillRect(0, 0, width, height, QColor("#2a3f66"))
            return
        segments = ((self._ok, "#5aa469"),
                    (self._dead, "#c65b5b"),
                    (self._unknown, "#6b7a99"))
        nonzero = [(count, color) for count, color in segments
                   if count > 0]
        x = 0
        for position, (count, color) in enumerate(nonzero):
            if position == len(nonzero) - 1:
                segment_width = width - x
            else:
                segment_width = int(width * count / total)
            painter.fillRect(x, 0, segment_width, height, QColor(color))
            x += segment_width


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
                 per_page: int = 30, page: int = 1, parent=None):
        super().__init__(parent)
        self.query = query
        self.category = category
        self.token = token
        self.per_page = per_page
        # Load more (v0.1.3 T22): GitHub takes the page number;
        # Hugging Face ignores paging, so the limit grows and the
        # window dedups the overlap.
        self.page = page
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
            except Exception as exc:  # noqa: BLE001 - a dead source is a failure, not a silent thread death
                core.log_error(f"{name} search failed: {exc}")
                with lock:
                    failures.append((name, str(exc) or "did not answer"))
                return
            with lock:
                results.extend(found)

        threads = [
            threading.Thread(
                target=run_one,
                args=("GitHub", lambda: apis.search_github(
                    self.query, self.category, token=self.token,
                    per_page=self.per_page, page=self.page)),
                daemon=True),
            threading.Thread(
                target=run_one,
                args=("Hugging Face", lambda: apis.search_huggingface(
                    self.query, self.category,
                    limit=self.per_page * self.page)),
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
            reasons = [reason for _name, reason in failures]
            if reasons[0] == reasons[1]:
                # Both sources failed for the same cause (v0.1.4
                # T23): say it once, in plain words, instead of
                # naming each source with the same sentence twice.
                self.finished_error.emit(reasons[0])
                return
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
    either way. item_checked carries the URL, not a list index:
    results are matched to items by identity, so a row removed or
    moved mid-check cannot shift statuses onto the wrong item
    (v0.1.3 T3).
    """

    item_checked = Signal(str, str)
    all_done = Signal(bool)

    def __init__(self, urls: list[str], parent=None, workers: int = 4):
        super().__init__(parent)
        self.urls = urls
        self.workers = workers
        self._stop_event = threading.Event()
        self._graceful_stop_event = threading.Event()

    def request_stop(self) -> None:
        """Ask the worker to stop at once (v0.1.4 T27).

        Links still being checked are dropped, never waited for:
        waiting for an in-flight request to time out took ~10 s.
        Only finished checks are reported, so the stopped count
        is honest.
        """
        self._stop_event.set()

    def request_graceful_stop(self) -> None:
        """Ask the worker to finish the checks in flight and then
        stop (the close path, v0.1.3 T16): a check already
        running is allowed to land, so its status survives the
        close on disk. Unlike request_stop, this never drops a
        check — the close path waits for the network call to
        finish or time out instead.
        """
        self._graceful_stop_event.set()

    def run(self) -> None:  # noqa: N802 - Qt naming
        stopped = False
        executor = ThreadPoolExecutor(max_workers=self.workers)
        try:
            futures = {executor.submit(apis.check_link, url): url
                       for url in self.urls}

            def emit(url: str, future):
                try:
                    status = future.result()
                except Exception as exc:  # noqa: BLE001 - a bad link must not kill the run
                    core.log_error(f"Link check failed for {url}: {exc}")
                    status = "unknown"
                self.item_checked.emit(url, status)

            # wait() with a short timeout, not as_completed: the
            # stop flag is checked every 50 ms, so Stop drops the
            # in-flight checks at once instead of waiting for the
            # next one to finish (v0.1.4 T27). Finished checks
            # are emitted, never thrown away (v0.1.3 T16).
            pending = set(futures)
            while pending:
                if self._stop_event.is_set():
                    stopped = True
                    break
                done, pending = wait(pending, timeout=0.05,
                                     return_when=FIRST_COMPLETED)
                for future in done:
                    emit(futures[future], future)
            if self._graceful_stop_event.is_set():
                # The close path asked for a graceful stop: every
                # check in flight finished and was reported, but
                # the run still ended early, so it counts as
                # stopped (v0.1.3 T16).
                stopped = True
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        self.all_done.emit(stopped)


class StellaCheckWorker(QThread):
    """Checks selected items for STELLA-ready data files (T29).

    One item at a time, on demand only: the APIs are rate
    limited, so a whole results page is never checked at once.
    item_checked carries (url, status, detail, files, size): the
    URL, not a list index, so a badge lands on the item that was
    actually checked (v0.1.3 T3).
    """

    item_checked = Signal(str, str, str, list, object)
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
            self.item_checked.emit(item.get("url") or "",
                                   found["status"],
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
        try:
            tag = apis.latest_release_tag()
        except Exception as exc:  # noqa: BLE001 - the check must always answer, never hang
            core.log_error(f"Update check failed: {exc}")
            tag = None
        self.finished.emit(tag)


class OnlineCheckWorker(QThread):
    """Checks whether the internet answers, off the UI thread (T67)."""

    finished = Signal(bool)

    def run(self) -> None:  # noqa: N802 - Qt naming
        try:
            online = apis.is_online(force=True)
        except Exception as exc:  # noqa: BLE001 - the check must always answer
            core.log_error(f"Online check failed: {exc}")
            online = False
        self.finished.emit(online)


class WatchWorker(QThread):
    """Re-runs due watches off the UI thread (T63).

    Each watch is a deep copy: the window merges the finished
    watch back into its store, so the worker thread never touches
    the window's own data. A watch whose search fails on both
    sources comes back unchanged, so a failed run never wipes
    the baseline it compares against.
    """

    watch_finished = Signal(dict, list, list)  # watch, new items, failures
    all_done = Signal()

    def __init__(self, watches: list, token: str = "", parent=None):
        super().__init__(parent)
        self.watches = watches
        self.token = token
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # noqa: N802 - Qt naming
        for watch_item in self.watches:
            if self._stop_event.is_set():
                break
            results, failures = watch.run_watch_search(
                watch_item["query"],
                watch_item.get("category") or "all",
                token=self.token)
            if len(failures) == 2:
                self.watch_finished.emit(watch_item, [], failures)
                continue
            new_items = watch.apply_watch_results(watch_item, results)
            self.watch_finished.emit(watch_item, new_items, failures)
        self.all_done.emit()


class QueueWorker(QThread):
    """Runs the download queue, one item at a time (T66).

    Each entry downloads through the same download_and_convert
    path as a one-off download, so a queued item gets the same
    resume behaviour as a single download. entry_done carries
    (entry id, ok, message, outputs, rows, state).
    """

    progress = Signal(str, str, int, int)
    entry_started = Signal(str)
    entry_done = Signal(str, bool, str, list, int, str)
    all_done = Signal(bool)

    def __init__(self, entries: list, out_dir, token: str = "",
                 parent=None):
        super().__init__(parent)
        self.entries = entries
        self.out_dir = out_dir
        self.token = token
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # noqa: N802 - Qt naming
        stopped = False
        for entry in self.entries:
            if self._stop_event.is_set():
                stopped = True
                break
            entry_id = entry["id"]
            self.entry_started.emit(entry_id)

            def say(stage: str, got: int = 0, total=None, eid=entry_id):
                self.progress.emit(eid, stage, got,
                                   -1 if total is None else total)

            try:
                report = download.download_and_convert(
                    entry["item"], self.out_dir, token=self.token,
                    progress=say, stop=self._stop_event)
            except Exception as exc:  # noqa: BLE001 - one bad item must not kill the run
                core.log_error(
                    f"Queued download failed for "
                    f"{entry['item'].get('url')}: {exc}")
                report = {"ok": False, "message": str(exc),
                          "outputs": [], "rows": 0, "state": "found"}
            if not report["ok"] and self._stop_event.is_set():
                # Paused mid-download (T28): the half-downloaded
                # file stays on disk, so the dialog re-queues
                # this entry instead of marking it failed.
                report = dict(report, state="stopped")
            self.entry_done.emit(entry_id, report["ok"],
                                 report["message"], report["outputs"],
                                 report["rows"], report["state"])
        self.all_done.emit(stopped or self._stop_event.is_set())


# ------------------------------------------------------------ how to use

# One registry for every keyboard shortcut (library 028): the
# shortcuts and the How to Use text are both built from this, so
# the help can never drift out of date with the bindings.
# (key, plain-words description, scope, action name on MainWindow)
# An action of None means the key is built into the widget (or is
# a mouse gesture): documented here, bound nowhere (T52).
SHORTCUTS = [
    ("Ctrl+1", "shows the Search tab", "window",
     "_shortcut_tab_search"),
    ("Ctrl+2", "shows the My List tab", "window",
     "_shortcut_tab_list"),
    ("Ctrl+3", "shows the Sources tab", "window",
     "_shortcut_tab_sources"),
    ("Ctrl+Tab", "shows the next tab", "window",
     "_shortcut_tab_next"),
    ("Ctrl+Shift+Tab", "shows the previous tab", "window",
     "_shortcut_tab_prev"),
    ("Ctrl+F", "focuses the search box", "window",
     "_shortcut_focus_search"),
    ("Ctrl+E", "exports the STELLA manifest", "window",
     "_shortcut_export_stella"),
    ("Delete", "removes the selected rows in My List", "list",
     "remove_selected"),
    ("?", "shows the list of keyboard shortcuts", "window",
     "show_shortcuts_overlay"),
    ("Enter", "shows the picked result's details", "results", None),
    ("Ctrl+A", "selects every row in the table", "tables", None),
    ("Double-click", "opens the picked row in your browser",
     "tables", None),
]


def _shortcuts_help_text() -> str:
    return " ".join(f"{key} {desc}." for key, desc, _s, _a in SHORTCUTS)


def _fill_category_combo(combo) -> None:
    """Fill a Category combo from the data (v0.1.6 T29).

    "All" is the search filter, not a category, so it is never
    offered here: the choices are the real categories from
    core.CATEGORIES, and the combo stays editable so a custom
    category can still be typed. Categories are DATA: nothing
    here hard-codes a category name."""
    for key, (label, _suffix) in core.CATEGORIES.items():
        if key == "all":
            continue
        combo.addItem(label, key)


def _category_list_text() -> str:
    """The preset category names, from the data (categories are
    DATA: the copy must never hard-code their names or count)."""
    names = [label for key, (label, _suffix) in core.CATEGORIES.items()
             if key != "all"]
    if len(names) <= 1:
        return ", ".join(names)
    return ", ".join(names[:-1]) + ", or " + names[-1]


# Plain-words index for the How to Use window (U22). No em-dashes,
# no "&" in any visible text (U33, U11).
HOW_TO_USE_SECTIONS = [
    ("Search",
     "Type a few words about the kind of dataset you want, then press "
     "Search. Dataset Scout looks on GitHub and Hugging Face and shows "
     "the matches in the table, newest first. Double-click a row to "
     "open it in your browser. Click a column heading to sort by it. "
     "Find in these results narrows the rows on screen without a new "
     "search. Recent searches are kept in the search box drop-down. "
     "Press Load more results at the bottom of the table to see the "
     "next page."),
    ("Categories and filters",
     "Pick a category chip to narrow the search: "
     + _category_list_text() + ". "
     "Active only keeps results updated recently. Use the stars box to "
     "keep only well liked projects: it counts GitHub stars and "
     "Hugging Face likes. The license box keeps only results with that "
     "license; results with no license shown are hidden while it is "
     "set."),
    ("Websites (add/remove)",
     "The Sources tab lists the websites the Search tab can open. Add "
     "a website with its name and address. Put {q} where your search "
     "words go. Remove any website you do not use, or restore the "
     "defaults."),
    ("My List and collections",
     "Add results to My List with the buttons under the search table, "
     "or add a link you found yourself with the Add a link I found "
     "button on the Search tab, the My List tab, or the File menu. "
     "Use the List box to keep named lists, like one for reasoning "
     "and one for math. Right-click a row to move it to another list, "
     "or to open its converted file once it is converted. Remove "
     "rows you do not want; Undo remove brings them back. Clear all "
     "asks first. Drag rows to reorder them. The Score column keeps "
     "the quality score each link had when it was saved."),
    ("States",
     "Each saved link has a state: Found, Downloaded, Converted, or "
     "In STELLA. It follows the real pipeline. Change it with the "
     "Edit button, or right-click a row and pick Mark as. Download "
     "and convert sets it for you. The Edit window also fixes a "
     "mistyped link, and Undo edit takes back a change made there."),
    ("Check links",
     "Check links visits every link in My List and marks it working, "
     "dead, or unknown. You can stop the check part way through with "
     "the Stop button. Links already checked stay marked. Remove the "
     "dead ones clears every dead row at once, after asking."),
    ("STELLA-ready check",
     "Select rows and press Check STELLA-ready to see whether a "
     "dataset is actually usable. With nothing selected, it checks "
     "everything, like Check links does. Scout looks at the files "
     "and says STELLA-ready when a .jsonl file is already in "
     "STELLA's shape, Needs conversion when the data needs work, or "
     "No data files when there is nothing to use. It can also say "
     "No usable fields: the files were read, but no prompt and "
     "answer pairing was found in them, so there is nothing Scout "
     "can turn into training rows. It checks one "
     "dataset at a time, only when you ask."),
    ("Download and convert",
     "Select rows in My List and press Download and convert. Scout "
     "downloads the dataset files and turns them into .jsonl files "
     "in STELLA's shape, saved under your Dataset Scout folder in "
     "stella-ready. Rows it cannot map are skipped, and the report "
     "says how many and why. Parquet files convert too: the reader "
     "is installed with the app. If the reader is somehow missing, "
     "the report says so as an error and tells you to run the "
     "installer again to add it."),
    ("Download history",
     "Download history in the File menu lists what was converted, "
     "when, and how many rows, newest first. Pick an entry to open "
     "the folder its files are in."),
    ("Download queue",
     "Queue several datasets to download and convert instead of "
     "doing them one at a time: select rows in My List and add them "
     "to the queue. Start, pause, and resume the queue any time; it "
     "remembers where it got to, even after you close and reopen "
     "the app."),
    ("Export for STELLA",
     "Export My List as a Markdown list, JSON, or a STELLA manifest. "
     "Every export says that downloaded data must be converted to "
     "STELLA's .jsonl shape before a STELLA project can use it. "
     "Import a list reads an export back in. Send to STELLA writes "
     "the manifest into STELLA's home folder on this PC. STELLA "
     "itself reads dataset files by path, so the converted .jsonl "
     "files from Download and convert are what it trains on."),
    ("Send to a STELLA project",
     "After Download and convert, Send to a STELLA project puts "
     "the converted file into one of your STELLA projects, ready "
     "to train on. Scout backs up the project file first and "
     "checks its work after writing, and says plainly if a project "
     "cannot be used."),
    ("Watched searches",
     "Watch a search to hear about new datasets: press Watch this "
     "search, and Scout re-runs it when the app opens and every 30 "
     "minutes while it is open, flagging datasets that appeared "
     "since the last run. Watching is off unless you turn it on "
     "for a search."),
    ("Quality score",
     "Each result has a score from 0 to 100, from how recently it "
     "was updated, how many stars and downloads it has, whether "
     "its license is clear, and whether its shape fits STELLA. "
     "The bands are: Worth a look means 80 or more, Promising "
     "means 50 to 79, Maybe means 25 to 49, and Probably skip "
     "means under 25. "
     "It is a guide, not a verdict."),
    ("Offline",
     "With no network, Scout says so at the top of the Search tab. "
     "You can still browse cached results, work My List, manage "
     "the queue, and convert files already on your PC. Search, "
     "link checks, and downloads wait until you are back online."),
    ("GitHub token",
     "GitHub allows more searches per minute with a token. You make "
     "one on GitHub: open the GitHub token page with the button next "
     "to the token field in the Sources tab, sign in, and create a "
     "token there. Paste it in "
     "the Sources tab and press Save token. It is stored on this PC "
     "only and never written to a log. The status bar shows how many "
     "GitHub searches are left."),
    ("Row spacing",
     "The View menu has Comfortable rows and Compact rows. "
     "Comfortable rows are roomy and easy to read. Compact rows "
     "are tighter, so more rows fit on screen at once: use "
     "Compact on a small window or with a long list."),
    ("Keyboard shortcuts", _shortcuts_help_text()),
    ("Check for updates",
     "Dataset Scout checks for a new version on its own when it "
     "starts, and says nothing when you are up to date. When a new "
     "version is out, a popup asks whether to get it. The download "
     "is checked against the checksum from the release before "
     "anything installs. After the download, a second popup asks "
     "restart now to complete the update, yes or no. Restart now "
     "restarts Dataset Scout into the new version. Later is safe: "
     "the update finishes the next time Dataset Scout starts. You "
     "can also pick Check for updates in the Help menu at any time; "
     "that check reports in the status bar. If you are on v0.1.4 or "
     "older, this update is a one-time hand install: download the "
     "zip and run the installer, like the first install. After "
     "that, Dataset Scout updates itself from inside the app."),
    ("Self-test",
     "Run self-test in the Help menu checks the app end to end: "
     "search, sniff, and a sample conversion. It prints pass or fail "
     "in plain words."),
    ("Debug report",
     "Copy debug report in the Sources tab copies the recent log to "
     "the clipboard, with any token removed, so you can share it when "
     "asking for help."),
]

HOW_TO_USE_TEXT = "\n\n".join(
    f"{title}\n{body}" for title, body in HOW_TO_USE_SECTIONS
)


class HowToUseWindow(QWidget):
    """A separate closable window with the How to Use index (U22).

    The section titles are a clickable index on the left (012):
    pick one to read just that section, or "All sections" for the
    whole guide in one scroll. "All sections" is the default, so
    the window opens as it always did.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("How to Use Dataset Scout")
        self.setMinimumSize(600, 450)
        self.resize(650, 500)
        if ICON_PATH.is_file():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        layout = QVBoxLayout(self)
        layout.addWidget(_heading("How to Use Dataset Scout"))
        splitter = QSplitter()
        self.index = QListWidget()
        self.index.addItem("All sections")
        for title, _body in HOW_TO_USE_SECTIONS:
            self.index.addItem(title)
        self.index.setToolTip(
            "Pick a section to read it, or All sections for the guide.")
        self.index.currentRowChanged.connect(self._show_section)
        splitter.addWidget(self.index)
        self.text = QTextBrowser()
        splitter.addWidget(self.text)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, stretch=1)
        close_button = QPushButton("Close")
        close_button.setToolTip("Close the How to Use window.")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)
        self.index.setCurrentRow(0)

    def _show_section(self, row: int):
        if row <= 0:
            self.text.setPlainText(HOW_TO_USE_TEXT)
            return
        title, body = HOW_TO_USE_SECTIONS[row - 1]
        self.text.setPlainText(f"{title}\n\n{body}")


class ShortcutsDialog(QDialog):
    """The ? overlay (065): every shortcut, grouped by context.

    Built from the SHORTCUTS registry, so it can never drift out
    of date with the bindings. Reachable from the ? key and from
    the Help menu: the users who need it most do not know ? exists.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setMinimumSize(420, 300)
        layout = QVBoxLayout(self)
        layout.addWidget(_heading("Keyboard shortcuts"))
        self.text = QTextBrowser()
        self.text.setPlainText(self._build_text())
        layout.addWidget(self.text, stretch=1)
        close_button = QPushButton("Close")
        close_button.setToolTip("Close the shortcuts list.")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

    @staticmethod
    def _build_text() -> str:
        groups = [("window", "Anywhere in the window"),
                  ("results", "In the search results"),
                  ("list", "In My List"),
                  ("tables", "In any table")]
        parts = []
        for scope, title in groups:
            rows = [f"{key}  {desc}"
                    for key, desc, entry_scope, _a in SHORTCUTS
                    if entry_scope == scope]
            if rows:
                parts.append(title + "\n" + "\n".join(rows))
        return "\n\n".join(parts)


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
        self.found_url.editingFinished.connect(self._validate_url)
        layout.addRow("Link:", self.found_url)
        self.url_error = _inline_error()
        layout.addRow("", self.url_error)
        self.found_title = QLineEdit()
        self.found_title.setPlaceholderText("A short name for it")
        layout.addRow("Title:", self.found_title)
        self.found_category = QComboBox()
        self.found_category.setEditable(True)
        _fill_category_combo(self.found_category)
        # The default is a real category from the data (T29),
        # never "All": the first real category in the list.
        if self.found_category.count():
            self.found_category.setCurrentIndex(0)
        layout.addRow("Category:", self.found_category)
        layout.addRow("", _explain(
            "Pick one, or type your own category."))
        self.found_note = QLineEdit()
        self.found_note.setPlaceholderText(
            "Anything to remember about it (optional)")
        layout.addRow("Note:", self.found_note)
        buttons = QDialogButtonBox()
        add_button = buttons.addButton(
            "Add link", QDialogButtonBox.ButtonRole.AcceptRole)
        add_button.setToolTip("Add this link to My List.")
        # Enter stays in its field (v0.1.6 T26): no button is the
        # dialog default, so pressing Enter in a text field does
        # not file the link half-finished. The buttons still work
        # by click, and Esc still closes.
        add_button.setAutoDefault(False)
        add_button.setDefault(False)
        cancel_button = buttons.addButton(
            "Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_button.setToolTip("Close without adding anything.")
        cancel_button.setAutoDefault(False)
        cancel_button.setDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow("", buttons)

    def keyPressEvent(self, event):  # noqa: N802 - Qt naming
        # Enter stays in its field (v0.1.6 T26): while a text
        # field has focus, Return and Enter do nothing, so a
        # half-finished link is never filed by habit. Esc still
        # closes through the normal dialog handling.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and isinstance(self.focusWidget(), QLineEdit):
            event.accept()
            return
        super().keyPressEvent(event)

    def _validate_url(self, force: bool = False) -> bool:
        """Inline validation (044): on blur, beside the field.

        Only after the user has typed: an empty field that merely
        lost focus gets no error. A link the check rejects is
        blocked (v0.1.4 T22): warning about it and adding it
        anyway planted unopenable links in the list.
        """
        text = self.found_url.text().strip()
        touched = force or self.found_url.isModified()
        error = ""
        if touched:
            if not text:
                error = "Paste a link first."
            elif not _looks_like_url(text):
                error = "That does not look like a link. Check it."
        self.url_error.setText(error)
        self.url_error.setVisible(bool(error))
        return bool(text) and _looks_like_url(text)

    def accept(self):  # noqa: N802 - Qt naming
        # Stay open on a missing or bad-looking link, with the
        # message beside the field, instead of closing and
        # complaining in the status bar.
        if not self._validate_url(force=True):
            return
        super().accept()


class EditItemDialog(QDialog):
    """Edit a saved link: title, category, notes, state (T12).

    The link itself is editable too (T58): a mistyped address is
    fixed in place, keeping the notes, state, and list position.
    taken_urls are the other rows' links, so an edit cannot
    duplicate a link that is already in the list.
    """

    def __init__(self, item: dict, parent=None, taken_urls=()):
        super().__init__(parent)
        self.setWindowTitle("Edit this link")
        self.setMinimumSize(500, 300)
        self._taken_urls = {core.normalize_url(url)
                            for url in taken_urls}
        # The category and state as saved (v0.1.3 T31/T43): an
        # untouched save must keep them, even when the category
        # is not one of the built-ins or the state is not one of
        # the known steps.
        self._original_category = item.get("category") or "all"
        self._original_state = item.get("state") or "found"
        self._original_url = item.get("url") or ""
        layout = QFormLayout(self)
        self.url_field = QLineEdit(self._original_url)
        self.url_field.setPlaceholderText("https://...")
        self.url_field.editingFinished.connect(self._validate_url)
        layout.addRow("Link:", self.url_field)
        self.url_error = _inline_error()
        layout.addRow("", self.url_error)
        layout.addRow("", _explain(
            "Fix a mistyped address here. The notes, state, and "
            "place in the list stay put."))
        self.title_field = QLineEdit(item.get("title") or "")
        layout.addRow("Title:", self.title_field)
        self.title_error = _inline_error()
        layout.addRow("", self.title_error)
        layout.addRow("", _explain("The name shown in My List."))
        self.category_field = QComboBox()
        self.category_field.setEditable(True)
        _fill_category_combo(self.category_field)
        current = core.category_label(self._original_category)
        self.category_field.setCurrentText(current)
        layout.addRow("Category:", self.category_field)
        layout.addRow("", _explain(
            "Pick one, or type your own category."))
        self.notes_field = QLineEdit(item.get("notes") or "")
        layout.addRow("Note:", self.notes_field)
        layout.addRow("", _explain("Anything to remember about it."))
        self.state_field = QComboBox()
        for key, label in core.WORKFLOW_STATES.items():
            self.state_field.addItem(label, key)
        index = self.state_field.findData(self._original_state)
        if index >= 0:
            self.state_field.setCurrentIndex(index)
        else:
            # An unrecognised state is shown as-is and kept on
            # save, not silently reset to "found".
            self.state_field.addItem(self._original_state,
                                     self._original_state)
            self.state_field.setCurrentIndex(
                self.state_field.count() - 1)
        layout.addRow("State:", self.state_field)
        layout.addRow("", _explain(
            "Where this link is in the pipeline: found, downloaded, "
            "converted, or in STELLA."))
        buttons = QDialogButtonBox()
        save_button = buttons.addButton(
            "Save changes", QDialogButtonBox.ButtonRole.AcceptRole)
        save_button.setToolTip("Save the changes to this link.")
        # Enter stays in its field (v0.1.6 T26), as in the Add
        # dialog: no default button, Esc still closes.
        save_button.setAutoDefault(False)
        save_button.setDefault(False)
        cancel_button = buttons.addButton(
            "Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_button.setToolTip("Close without saving anything.")
        cancel_button.setAutoDefault(False)
        cancel_button.setDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow("", buttons)

    def keyPressEvent(self, event):  # noqa: N802 - Qt naming
        # Enter stays in its field (v0.1.6 T26), as in the Add
        # dialog.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and isinstance(self.focusWidget(), QLineEdit):
            event.accept()
            return
        super().keyPressEvent(event)

    def _validate_url(self, force: bool = False) -> bool:
        """Inline validation, the same check as the add form (044).

        Only after the user has typed: an untouched field gets no
        error. A link the check rejects blocks the save, as on
        the add form (v0.1.4 T22); an empty link and a link
        already in the list block it too.
        """
        text = self.url_field.text().strip()
        touched = force or self.url_field.isModified()
        error = ""
        blocked = False
        if touched:
            if not text:
                error = "Paste a link first."
                blocked = True
            elif (core.normalize_url(text) in self._taken_urls
                    and text != self._original_url):
                error = "That link is already in My List."
                blocked = True
            elif not _looks_like_url(text):
                error = "That does not look like a link. Check it."
                blocked = True
        self.url_error.setText(error)
        self.url_error.setVisible(bool(error))
        return not blocked

    def _validate_title(self) -> bool:
        """A blank title blocks the save (v0.1.4 T26): saving one
        used to say "Saved the changes." while silently keeping
        the old title."""
        if self.title_field.text().strip():
            self.title_error.setText("")
            self.title_error.setVisible(False)
            return True
        self.title_error.setText("Give the link a title.")
        self.title_error.setVisible(True)
        return False

    def accept(self):  # noqa: N802 - Qt naming
        # Stay open on an empty link, a duplicate, or a blank
        # title, with the message beside the field, instead of
        # closing and complaining in the status bar.
        if not self._validate_url(force=True):
            return
        if not self._validate_title():
            return
        super().accept()

    def values(self) -> dict:
        """The edited values, with the category as a key.

        An untouched category keeps its saved key (v0.1.3 T31):
        a custom category like "my_cat" is not renamed to
        "my-cat" by saving.
        """
        text = self.category_field.currentText()
        if text == core.category_label(self._original_category):
            category = self._original_category
        else:
            category = core.category_key_for_label(text)
        return {
            "title": self.title_field.text().strip(),
            "url": self.url_field.text().strip(),
            "category": category,
            "notes": self.notes_field.text().strip(),
            "state": self.state_field.currentData(),
        }


class DownloadDialog(QDialog):
    """Progress window for download and convert (T35).

    Owns the DownloadWorker. The log says what happened to each
    dataset in plain words; the Close button waits until the run
    is done or stopped.
    """

    def __init__(self, items: list, out_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download and convert")
        self.setMinimumSize(600, 400)
        self.items = items
        self.out_dir = out_dir
        self.worker: DownloadWorker | None = None
        self._last_stage = ""
        self._done_ok = 0
        self._done_rows = 0
        # Converted outputs per item index (T64): the STELLA
        # handoff offers these files to a STELLA project.
        self._outputs_by_index: dict[int, list] = {}

        layout = QVBoxLayout(self)
        # No raw path, right pronoun (v0.1.6 T27): one dataset is
        # "it", several are "them", and the place is named the way
        # the rest of the app names it.
        if len(items) == 1:
            header_text = (
                "Downloading 1 dataset and converting it to STELLA "
                ".jsonl files in your Dataset Scout folder.")
        else:
            header_text = (
                f"Downloading {len(items)} datasets and converting "
                "them to STELLA .jsonl files in your Dataset Scout "
                "folder.")
        self.header_label = QLabel(header_text)
        self.header_label.setWordWrap(True)
        layout.addWidget(self.header_label)
        # The destination picker (v0.1.6 T38): where the converted
        # files go. Remembered between runs; the queue uses the
        # same remembered folder. Picking a folder mid-run sends
        # the datasets that have not started yet to the new place.
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Saving to:"))
        self.dest_label = QLabel(str(self.out_dir))
        self.dest_label.setToolTip(
            "The folder the converted files are saved in.")
        dest_row.addWidget(self.dest_label, stretch=1)
        self.choose_folder_button = QPushButton("Choose folder")
        self.choose_folder_button.setToolTip(
            "Save the converted files in a different folder.")
        self.choose_folder_button.clicked.connect(self._choose_folder)
        dest_row.addWidget(self.choose_folder_button)
        layout.addLayout(dest_row)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, stretch=1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar)

        row = QHBoxLayout()
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip(
            "Stop the download running now.")
        self.stop_button.clicked.connect(self.stop_run)
        row.addWidget(self.stop_button)
        self.open_button = QPushButton("Open the folder")
        self.open_button.setToolTip("Open the folder with the converted files.")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_folder)
        row.addWidget(self.open_button)
        self.stella_button = QPushButton("Send to a STELLA project")
        self.stella_button.setToolTip(
            "Add a converted file to a STELLA project, ready to train on.")
        self.stella_button.setEnabled(False)
        self.stella_button.clicked.connect(self._send_to_stella_project)
        row.addWidget(self.stella_button)
        self.close_button = QPushButton("Close")
        self.close_button.setToolTip("Close this window.")
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
            self._log("Stopping...")

    def _choose_folder(self):
        """Pick the destination folder (v0.1.6 T38)."""
        picked = QFileDialog.getExistingDirectory(
            self, "Choose where converted files go",
            str(self.out_dir))
        if not picked:
            return
        self.out_dir = Path(picked)
        _save_download_dir(self.out_dir)
        self.dest_label.setText(str(self.out_dir))
        if self.worker is not None and self.worker.isRunning():
            # Datasets that have not started yet go to the new
            # folder; the one running now finishes where it began.
            self.worker.out_dir = self.out_dir
            self._log(
                f"Saving the rest to {self.out_dir}.")
        else:
            self._log(f"Saving to {self.out_dir}.")

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.out_dir)))

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
        url = ""
        source = ""
        # The item dicts are the same objects the main window holds,
        # so updating the state here updates My List too.
        for _idx, item in self.items:
            if _idx == index:
                title = item.get("title") or ""
                url = item.get("url") or ""
                source = item.get("source") or ""
                item["state"] = state
                break
        self._outputs_by_index[index] = list(outputs)
        self._log(f"{title}: {message}")
        self.bar.setMaximum(100)
        self.bar.setValue(100 if ok else 0)
        if ok:
            self._done_ok += 1
            self._done_rows += rows
            # The download history (T61): what was converted, when,
            # and how many rows. A history write must never break
            # the download flow, so a failure is logged, not raised.
            try:
                history.record_conversion(title, url, source, rows,
                                          outputs)
            except OSError as exc:
                core.log_error(f"Could not record the download: {exc}")

    def _on_all_done(self, stopped: bool):
        self.stop_button.setEnabled(False)
        self.close_button.setEnabled(True)
        # "Open the folder" only when there is something in it
        # (v0.1.4 T30): after a failed run it opened an empty
        # folder.
        self.open_button.setEnabled(
            any(self._outputs_by_index.values()))
        self.stella_button.setEnabled(
            any(self._outputs_by_index.values()))
        # The bar shows the share that converted (v0.1.4 T16): a
        # run that converted nothing ends at 0, not full.
        self.bar.setMaximum(100)
        total = len(self.items)
        self.bar.setValue(
            int(100 * self._done_ok / total) if total else 0)
        if stopped:
            self._log("Stopped part way through.")
        elif self._done_ok == 0:
            # No checkmark and no full bar on a zero-success run:
            # a refused or stopped run is not a success to
            # celebrate (v0.1.4 T16).
            self._log("Nothing converted.")
        else:
            # The completion moment (008): a checkmark plus a
            # one-line summary of what the run produced.
            word = "dataset" if self._done_ok == 1 else "datasets"
            self._log(f"✓ Done: {self._done_ok} of {len(self.items)} "
                      f"{word} converted, {self._done_rows} rows kept.")

    def _send_to_stella_project(self):
        """Offer a converted file to a STELLA project (T64)."""
        paths = [path for outputs in self._outputs_by_index.values()
                 for path in outputs]
        if not paths:
            return
        dialog = StellaLinkDialog(paths, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.result is not None:
            self._log(dialog.result["message"])
        if dialog.linked_path:
            # The linked dataset's item is in STELLA now: its
            # state follows the pipeline (the main window saves
            # the list when this dialog closes).
            for index, outputs in self._outputs_by_index.items():
                if dialog.linked_path not in outputs:
                    continue
                for _idx, item in self.items:
                    if _idx == index:
                        item["state"] = "in-stella"


class WatchDialog(QDialog):
    """The watched searches (T63): what is watched, what is new."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Watched searches")
        self.setMinimumSize(600, 400)
        self.watches: list[dict] = []
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Scout re-runs these searches while the app is open and "
            "flags datasets that appeared since the last run. "
            "Nothing runs when the app is closed.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.watch_list = QListWidget()
        self.watch_list.setToolTip(
            "The searches Scout watches. Pick one to see what is new.")
        self.watch_list.currentRowChanged.connect(self._watch_picked)
        layout.addWidget(self.watch_list, stretch=1)
        self.new_label = QLabel("Pick a watch to see what is new.")
        layout.addWidget(self.new_label)
        self.new_list = QListWidget()
        self.new_list.setToolTip("Datasets that appeared since the last run.")
        layout.addWidget(self.new_list, stretch=1)
        row = QHBoxLayout()
        self.check_now_button = QPushButton("Check now")
        self.check_now_button.setToolTip(
            "Re-run every watched search right now.")
        self.check_now_button.clicked.connect(self._check_now)
        row.addWidget(self.check_now_button)
        self.add_new_button = QPushButton("Add new items to My List")
        self.add_new_button.setToolTip(
            "Save the new datasets from the picked watch to My List.")
        self.add_new_button.clicked.connect(self._add_new_to_list)
        row.addWidget(self.add_new_button)
        self.pause_button = QPushButton("Pause this watch")
        self.pause_button.setToolTip(
            "Stop re-running the picked watch until you resume it.")
        self.pause_button.clicked.connect(self._toggle_paused)
        row.addWidget(self.pause_button)
        self.remove_button = QPushButton("Stop watching")
        self.remove_button.setToolTip(
            "Forget the picked search. It will not be re-run again.")
        self.remove_button.clicked.connect(self._remove_watch)
        row.addWidget(self.remove_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        row.addWidget(close_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        """Reload the watches from disk and refill the lists.

        The picked watch stays picked (T43): the selection is
        restored by watch id, so Pause turns into Resume on the
        same row instead of looking broken.
        """
        picked_id = None
        picked = self._selected_watch()
        if picked is not None:
            picked_id = picked.get("id")
        self.watches = watch.load_watches()
        self.watch_list.blockSignals(True)
        self.watch_list.clear()
        for item in self.watches:
            self.watch_list.addItem(self._watch_row_text(item))
        restore = -1
        if picked_id is not None:
            for row, item in enumerate(self.watches):
                if item.get("id") == picked_id:
                    restore = row
                    break
        self.watch_list.setCurrentRow(restore)
        self.watch_list.blockSignals(False)
        self._watch_selected()

    @staticmethod
    def _watch_row_text(item: dict) -> str:
        """One row: the search, its new count while unseen (T42),
        and (paused) when it is paused."""
        text = (f"{item['query']} "
                f"({core.category_label(item.get('category') or 'all')})")
        count = len(item.get("new") or [])
        if count and not item.get("new_seen", True):
            word = "dataset" if count == 1 else "datasets"
            text += f"  {count} new {word}"
        if not item.get("enabled", True):
            text += " (paused)"
        return text

    def _selected_watch(self) -> dict | None:
        row = self.watch_list.currentRow()
        if 0 <= row < len(self.watches):
            return self.watches[row]
        return None

    def _refresh_buttons(self):
        """Enable only the buttons that have something to act on
        (T44): a dead button that silently does nothing looks
        broken."""
        picked = self._selected_watch()
        self.check_now_button.setEnabled(bool(self.watches))
        self.add_new_button.setEnabled(
            picked is not None and bool(picked.get("new")))
        self.pause_button.setEnabled(picked is not None)
        self.remove_button.setEnabled(picked is not None)

    def _watch_picked(self, _row: int = -1):
        """The user picked a watch: its new items are seen now
        (T42), so the flag clears while the items stay listed."""
        picked = self._selected_watch()
        if (picked is not None and picked.get("new")
                and not picked.get("new_seen", True)):
            picked["new_seen"] = True
            watch.save_watches(self.watches)
            row = self.watch_list.currentRow()
            item = self.watch_list.item(row)
            if item is not None:
                item.setText(self._watch_row_text(picked))
        self._watch_selected()

    def _watch_selected(self):
        picked = self._selected_watch()
        self.new_list.clear()
        if picked is None:
            if not self.watches:
                # Empty state teaches (T48, 031): how a watch
                # comes to exist, in the line the eye lands on.
                self.new_label.setText(
                    "No watched searches yet. Run a search, then "
                    "press Watch this search to watch it.")
            else:
                self.new_label.setText(
                    "Pick a watch to see what is new.")
            self.pause_button.setText("Pause this watch")
            self._refresh_buttons()
            return
        self.pause_button.setText(
            "Resume this watch" if not picked.get("enabled", True)
            else "Pause this watch")
        new_items = picked.get("new") or []
        last = picked.get("last_run") or ""
        when = core.format_date(last) if last else "never"
        if picked.get("just_started") and not new_items:
            # The first run set the starting point (v0.1.6 T21):
            # say so, instead of claiming nothing was new since a
            # last run that never happened.
            count = picked.get("baseline_count") or 0
            self.new_label.setText(
                f"Watch started: {count} results saved as the "
                "starting point. New ones will show here.")
        elif new_items:
            word = "dataset" if len(new_items) == 1 else "datasets"
            self.new_label.setText(
                f"{len(new_items)} new {word} since the last run "
                f"({when}).")
            for item in new_items:
                self.new_list.addItem(
                    item.get("title") or item.get("url") or "")
        else:
            self.new_label.setText(
                f"Nothing new since the last run ({when}).")
        self._refresh_buttons()

    def _check_now(self):
        parent = self.parent()
        if parent is None or not hasattr(parent, "_run_due_watches"):
            return
        if getattr(parent, "_online", None) is False:
            # Offline Check now must say so (T46), not run
            # silently into nothing.
            parent.statusBar().showMessage(
                "You are offline. Watched searches wait until you "
                "are back online.")
            return
        parent._run_due_watches(force=True)

    def _add_new_to_list(self):
        picked = self._selected_watch()
        parent = self.parent()
        if picked is None or parent is None:
            return
        added = 0
        for item in picked.get("new") or []:
            parent.my_list, was_added = core.add_to_list(
                parent.my_list, item)
            added += 1 if was_added else 0
        if added:
            parent._save_list()
            parent._refresh_list_tab()
        picked["new"] = []
        picked["new_seen"] = True
        watch.save_watches(self.watches)
        self.refresh()
        parent.statusBar().showMessage(f"Added {added} to My List.")

    def _toggle_paused(self):
        picked = self._selected_watch()
        if picked is None:
            return
        picked["enabled"] = not picked.get("enabled", True)
        watch.save_watches(self.watches)
        core.log(f"Watch {'resumed' if picked['enabled'] else 'paused'}: "
                 f"{picked.get('query')!r}.")
        self.refresh()

    def _remove_watch(self):
        picked = self._selected_watch()
        if picked is None:
            return
        answer = QMessageBox.question(
            self, "Stop watching this search?",
            f"Scout will stop re-running {picked['query']!r}.",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            # Say what was kept (T35): a stale message describing
            # something the user did not do is a lie by omission.
            parent = self.parent()
            if parent is not None and hasattr(parent, "statusBar"):
                parent.statusBar().showMessage(
                    f"Kept watching {picked['query']!r}.")
            return
        watch.save_watches(
            watch.remove_watch(self.watches, picked["id"]))
        core.log(f"Watch deleted: {picked.get('query')!r}.")
        self.refresh()


class QueueDialog(QDialog):
    """The bulk download queue (T66): several datasets, pause,
    resume after closing and reopening the app."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download queue")
        self.setMinimumSize(650, 450)
        # The remembered download destination (v0.1.6 T38): the
        # same folder the Download window saves to.
        self.out_dir = _download_dir()
        self.worker: QueueWorker | None = None
        # Set when Pause is pressed (v0.1.6 T24): the row settles
        # at once and no more stage lines print for that item.
        self._pause_requested = False
        self.entries: list[dict] = []
        # The last stage line logged (v0.1.6 T7): progress chunks
        # repeat the same stage, and the log shows it once, like
        # the single Download window already does.
        self._last_stage = ""
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Queued datasets download and convert one at a time. "
            "Pause stops the download now and queues that dataset "
            "again; Start picks up where it left off, even after "
            "closing the app.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.queue_table = QTableWidget(0, 3)
        self.queue_table.setHorizontalHeaderLabels(
            ["Dataset", "State", "Rows"])
        self.queue_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.horizontalHeader().setStretchLastSection(True)
        self.queue_table.itemSelectionChanged.connect(
            self._refresh_buttons)
        layout.addWidget(self.queue_table, stretch=1)
        # The empty state teaches how the queue fills (T48):
        # refresh() shows this when there is nothing queued.
        self.empty_label = QLabel(
            "Nothing in the queue yet. Select rows in My List and "
            "press Add selected to queue.")
        self.empty_label.setStyleSheet("color: #9fb0cc;")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(120)
        layout.addWidget(self.log_view)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        row = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.start_button.setToolTip(
            "Download and convert the waiting datasets, in order.")
        self.start_button.clicked.connect(self.start_queue)
        row.addWidget(self.start_button)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setToolTip(
            "Stop the download now. Start resumes where it left off.")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.pause_queue)
        row.addWidget(self.pause_button)
        self.retry_button = QPushButton("Retry failed")
        self.retry_button.setToolTip(
            "Queue the failed downloads again, so Start can try them.")
        self.retry_button.clicked.connect(self._retry_failed)
        row.addWidget(self.retry_button)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.setToolTip("Take the picked rows out of the queue.")
        self.remove_button.clicked.connect(self._remove_selected)
        row.addWidget(self.remove_button)
        self.clear_button = QPushButton("Clear finished")
        self.clear_button.setToolTip(
            "Take done and failed rows out of the queue.")
        self.clear_button.clicked.connect(self._clear_finished)
        row.addWidget(self.clear_button)
        close_button = QPushButton("Close")
        close_button.setToolTip("Close this window. The queue is saved.")
        close_button.clicked.connect(self.accept)
        row.addWidget(close_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        """Reload the queue from disk and refill the table.

        While a run is in progress the in-memory entries are the
        truth (v0.1.4 T14): reloading from disk would reset the
        running entry to queued and log a false "stopped part way
        through" line on a normal run, and "Downloading" would
        never show. Only the table is redrawn mid-run; the
        interrupted count is for a fresh load after a crash or a
        close mid-run.
        """
        if self.worker is not None and self.worker.isRunning():
            entries = self.entries
            interrupted = 0
        else:
            entries, interrupted = dlqueue.load_queue()
            self.entries = entries
        if interrupted:
            word = "download" if interrupted == 1 else "downloads"
            self._log(f"{interrupted} {word} stopped part way "
                      "through and queued again.")
        table = self.queue_table
        table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            table.setItem(row, 0, _cell(
                entry["item"].get("title") or ""))
            table.setItem(row, 1, _cell(
                dlqueue.STATE_LABELS.get(entry["state"],
                                         entry["state"])))
            rows = entry.get("rows") or 0
            table.setItem(row, 2, _cell(str(rows) if rows else ""))
        table.resizeColumnsToContents()
        self.empty_label.setVisible(len(entries) == 0)
        self._refresh_buttons()

    def _refresh_buttons(self):
        """Enable only the buttons that have something to act on
        (T44). Pause is left alone: the run owns it."""
        running = self.worker is not None and self.worker.isRunning()
        states = [entry["state"] for entry in self.entries]
        self.start_button.setEnabled(
            not running and dlqueue.QUEUED in states)
        self.retry_button.setEnabled(dlqueue.FAILED in states)
        self.clear_button.setEnabled(
            dlqueue.DONE in states or dlqueue.FAILED in states)
        self.remove_button.setEnabled(
            bool(self.queue_table.selectedIndexes()))

    def _retry_failed(self):
        """Queue failed entries again (T45): the picked failed
        rows, or every failed row when none are picked."""
        rows = {index.row() for index in
                self.queue_table.selectedIndexes()}
        picked_failed = [self.entries[row]["id"] for row in sorted(rows)
                         if 0 <= row < len(self.entries)
                         and self.entries[row]["state"] == dlqueue.FAILED]
        ids = picked_failed or None
        count = dlqueue.retry_failed(self.entries, ids)
        if not count:
            self._log("No failed downloads to retry.")
            return
        dlqueue.save_queue(self.entries)
        self.refresh()
        word = "download" if count == 1 else "downloads"
        self._log(f"{count} failed {word} queued again. "
                  "Press Start to try them.")

    def _log(self, text: str):
        self.log_view.append(text)

    def _entry(self, entry_id: str) -> dict | None:
        for entry in self.entries:
            if entry["id"] == entry_id:
                return entry
        return None

    def start_queue(self):
        if self.worker is not None and self.worker.isRunning():
            return
        parent = self.parent()
        if parent is not None and getattr(parent, "_online", None) is False:
            self._log("You are offline. Downloads need the internet.")
            return
        queued = [entry for entry in self.entries
                  if entry["state"] == dlqueue.QUEUED]
        if not queued:
            self._log("Nothing is waiting in the queue.")
            return
        self.start_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self._pause_requested = False
        self.out_dir = _download_dir()
        core.log(f"Download queue started: {len(queued)} waiting.")
        self.worker = QueueWorker(queued, self.out_dir,
                                  token=core.get_github_token(),
                                  parent=self)
        self.worker.progress.connect(self._on_progress)
        self.worker.entry_started.connect(self._on_entry_started)
        self.worker.entry_done.connect(self._on_entry_done)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

    def pause_queue(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            self.pause_button.setEnabled(False)
            self._pause_requested = True
            # Settle the row in the same beat (v0.1.6 T24): the
            # running row reads Paused now, not Downloading until
            # the worker's done signal lands. The saved entry
            # follows when the run reports it stopped.
            for row, entry in enumerate(self.entries):
                if entry["state"] == dlqueue.RUNNING:
                    cell = self.queue_table.item(row, 1)
                    if cell is not None:
                        cell.setText("Paused")
            self._log("Pausing the download now...")

    def closeEvent(self, event):  # noqa: N802 - Qt naming
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            self.worker.wait(30000)
        super().closeEvent(event)

    def _on_progress(self, _entry_id: str, stage: str,
                     got: int, total: int):
        if self._pause_requested:
            # Paused (v0.1.6 T24): no more Downloading lines for
            # this item once Pause has landed.
            return
        if total and total > 0:
            self.bar.setMaximum(100)
            self.bar.setValue(min(100, int(got * 100 / total)))
        else:
            self.bar.setMaximum(0)
        # Log each stage once, not once per chunk (v0.1.6 T7):
        # one 17 MB download used to print the same Downloading
        # line 126 times, burying the lines that matter. Real
        # events (started, done, failures, the summary) go through
        # their own handlers and always print.
        if stage != self._last_stage:
            self._last_stage = stage
            self._log(stage)

    def _on_entry_started(self, entry_id: str):
        entry = self._entry(entry_id)
        if entry is not None:
            entry["state"] = dlqueue.RUNNING
            dlqueue.save_queue(self.entries)
            self.refresh()
        # A new entry's first stage prints even when its text
        # matches the previous entry's last stage (T7).
        self._last_stage = ""

    def _on_entry_done(self, entry_id: str, ok: bool, message: str,
                       outputs: list, rows: int, state: str):
        entry = self._entry(entry_id)
        if entry is not None:
            if state == "stopped":
                # Paused mid-download (T28): back to waiting, so
                # Start resumes it from the half-downloaded file.
                entry["state"] = dlqueue.QUEUED
                entry["error"] = ""
            else:
                entry["state"] = dlqueue.DONE if ok else dlqueue.FAILED
                entry["outputs"] = list(outputs)
                entry["rows"] = rows
                entry["error"] = "" if ok else message
                # The list item's own state follows the pipeline too.
                entry["item"]["state"] = state
                if ok:
                    # The download history (T61): a queued
                    # conversion is recorded like a single one.
                    try:
                        history.record_conversion(
                            entry["item"].get("title") or "",
                            entry["item"].get("url") or "",
                            entry["item"].get("source") or "",
                            rows, outputs)
                    except OSError as exc:
                        core.log_error(
                            f"Could not record the download: {exc}")
            dlqueue.save_queue(self.entries)
        title = entry["item"].get("title") if entry else ""
        if state == "stopped":
            # A clean pause is a pause (v0.1.4 T14), not a failed
            # download: the report's "Nothing could be
            # downloaded." belongs to a real failure, not to the
            # user pressing Pause.
            self._log(f"{title}: Paused. Start resumes the download.")
        else:
            self._log(f"{title}: {message}")
        self.bar.setMaximum(100)
        self.bar.setValue(100 if ok else 0)
        self.refresh()
        parent = self.parent()
        if parent is not None and entry is not None \
                and state != "stopped":
            # The My List row follows the queue run (v0.1.4 T15):
            # the queue holds its own copy of the item, so the
            # list row is updated by URL, exactly as a single
            # download updates it.
            find = getattr(parent, "_find_list_index", None)
            if callable(find) and hasattr(parent, "my_list"):
                list_index = find(entry["item"].get("url") or "")
                if list_index >= 0:
                    parent.my_list[list_index]["state"] = state
                    save = getattr(parent, "_save_list", None)
                    if callable(save):
                        save()
        if parent is not None and hasattr(parent, "_refresh_list_tab"):
            parent._refresh_list_tab()

    def _on_all_done(self, stopped: bool):
        self.pause_button.setEnabled(False)
        self.bar.setMaximum(100)
        # The bar shows the share that finished (v0.1.4 T16): a
        # run that finished nothing ends at 0, not full.
        total = dlqueue.counts(self.entries)
        all_entries = total["total"]
        self.bar.setValue(
            int(100 * total["done"] / all_entries) if all_entries else 0)
        # Start only comes back when something is waiting (T44):
        # after a full run it would otherwise sit enabled while
        # doing nothing but logging "Nothing is waiting."
        self._refresh_buttons()
        if stopped:
            self._log("Paused. Start resumes the queue.")
            core.log("Download queue paused.")
        else:
            self._log(f"Queue done: {total['done']} done, "
                      f"{total['failed']} failed, "
                      f"{total['queued']} still waiting.")
            core.log(f"Download queue finished: {total['done']} done, "
                     f"{total['failed']} failed, "
                     f"{total['queued']} still waiting.")

    def _remove_selected(self):
        rows = {index.row() for index in
                self.queue_table.selectedIndexes()}
        if not rows:
            return
        ids = {self.entries[row]["id"] for row in rows
               if 0 <= row < len(self.entries)}
        self.entries = [entry for entry in self.entries
                        if entry["id"] not in ids]
        dlqueue.save_queue(self.entries)
        self.refresh()

    def _clear_finished(self):
        self.entries = dlqueue.clear_finished(self.entries)
        dlqueue.save_queue(self.entries)
        self.refresh()


class StellaLinkDialog(QDialog):
    """Add a converted file to a STELLA project (T64)."""

    def __init__(self, jsonl_paths: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Send to a STELLA project")
        self.setMinimumSize(500, 300)
        self.paths = list(jsonl_paths)
        self.linked = False
        self.linked_path: str | None = None
        self.result: dict | None = None
        layout = QFormLayout(self)
        self.file_combo = QComboBox()
        for path in self.paths:
            self.file_combo.addItem(Path(path).name, str(path))
        layout.addRow("Dataset file:", self.file_combo)
        self.profile_combo = QComboBox()
        for profile in stella_link.list_profiles():
            self.profile_combo.addItem(profile["display_name"],
                                       profile["name"])
        layout.addRow("STELLA profile:", self.profile_combo)
        self.project_field = QLineEdit()
        if self.paths:
            self.project_field.setText(
                stella_link.stella_slug(Path(self.paths[0]).stem))
        layout.addRow("Project name:", self.project_field)
        layout.addRow("", _explain(
            "A new project is created when the name does not exist "
            "yet. The dataset file is copied into the project, so "
            "the project is ready to train on."))
        self.replace_check = QCheckBox(
            "Replace the dataset if the project already has one")
        layout.addRow("", self.replace_check)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #ff9a9a;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addRow("", self.error_label)
        buttons = QDialogButtonBox()
        self.link_button = buttons.addButton(
            "Add to STELLA project",
            QDialogButtonBox.ButtonRole.AcceptRole)
        self.link_button.setToolTip(
            "Add the dataset file to the STELLA project.")
        cancel_button = buttons.addButton(
            "Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_button.setToolTip("Close without changing STELLA.")
        buttons.accepted.connect(self.link)
        buttons.rejected.connect(self.reject)
        layout.addRow("", buttons)
        if core.stella_home() is None:
            self._show_error(
                "STELLA is not installed on this PC, so there is no "
                "STELLA project to add the dataset to.")
            self.link_button.setEnabled(False)
        elif self.profile_combo.count() == 0:
            self._show_error(
                "STELLA has no profiles yet. Create one in STELLA "
                "first, then send the dataset again.")
            self.link_button.setEnabled(False)

    def _show_error(self, text: str):
        self.error_label.setText(text)
        self.error_label.setVisible(True)

    def link(self) -> dict:
        """Link the picked file into the picked project.

        Returns the link result. On success the dialog closes;
        on failure the plain-words message shows under the form
        and the dialog stays open.
        """
        path = self.file_combo.currentData()
        profile = self.profile_combo.currentData()
        project = self.project_field.text().strip()
        if not path or not profile or not project:
            result = {"ok": False,
                      "message": "Pick a file, a profile, and a "
                                 "project name first."}
            self.result = result
            self._show_error(result["message"])
            return result
        result = stella_link.link_dataset(
            path, profile, project,
            replace=self.replace_check.isChecked(),
            display_name=project)
        self.result = result
        if result["ok"]:
            self.linked = True
            self.linked_path = str(path)
            core.log(f"Sent to STELLA project: {result['message']}")
            self.accept()
        else:
            self._show_error(result["message"])
        return result


class HistoryDialog(QDialog):
    """Download history (T61): what was converted, when, how many
    rows. Newest first. Opening the folder takes the user back to
    the converted files."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download history")
        self.setMinimumSize(640, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Every download and convert that produced files, newest "
            "first."))
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(
            ["Dataset", "Converted", "Rows", "Files"])
        self.history_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.doubleClicked.connect(self.open_folder)
        layout.addWidget(self.history_table, stretch=1)
        self.empty_label = QLabel(
            "Nothing converted yet. Download and convert a dataset "
            "and it shows up here.")
        self.empty_label.setStyleSheet("color: #9fb0cc;")
        layout.addWidget(self.empty_label)
        row = QHBoxLayout()
        self.open_button = QPushButton("Open the folder")
        self.open_button.setToolTip(
            "Open the folder with this conversion's files.")
        self.open_button.clicked.connect(self.open_folder)
        row.addWidget(self.open_button)
        self.clear_button = QPushButton("Clear history")
        self.clear_button.setToolTip(
            "Forget every entry in this history. The converted "
            "files stay on disk.")
        self.clear_button.clicked.connect(self.clear_history)
        row.addWidget(self.clear_button)
        close_button = QPushButton("Close")
        close_button.setToolTip("Close this window.")
        close_button.clicked.connect(self.accept)
        row.addWidget(close_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        """Refill the table from the saved history."""
        self.entries = history.load_history()
        table = self.history_table
        table.setSortingEnabled(False)
        table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            table.setItem(row, 0, _cell(entry.get("title") or ""))
            table.setItem(row, 1,
                          _cell(core.format_date(entry.get("when"))))
            table.setItem(row, 2,
                          _cell(str(entry.get("rows") or 0),
                                sort_value=core._safe_int(
                                    entry.get("rows"))))
            files = entry.get("files") or []
            if len(files) == 1:
                files_text = files[0]
            elif files:
                files_text = f"{len(files)} files"
            else:
                files_text = ""
            files_cell = _cell(files_text)
            if len(files) > 1:
                files_cell.setToolTip(", ".join(files))
            table.setItem(row, 3, files_cell)
        table.setSortingEnabled(True)
        table.resizeColumnsToContents()
        self.empty_label.setVisible(not self.entries)
        self.open_button.setEnabled(bool(self.entries))
        self.clear_button.setEnabled(bool(self.entries))

    def _selected_entry(self) -> dict | None:
        rows = {index.row()
                for index in self.history_table.selectedIndexes()}
        if not rows:
            return None
        row = sorted(rows)[0]
        if 0 <= row < len(self.entries):
            return self.entries[row]
        return None

    def open_folder(self):
        """Open the picked conversion's folder, if it is still there."""
        entry = self._selected_entry()
        if entry is None:
            return
        folder = entry.get("folder") or ""
        if folder and Path(folder).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            QMessageBox.information(
                self, "Folder not found",
                "That folder is gone. The files may have been moved "
                "or deleted.")

    def clear_history(self):
        """Forget every entry, after asking (U23, U31)."""
        if not self.entries:
            return
        answer = QMessageBox.question(
            self, "Clear download history",
            "Forget every entry in the download history? The "
            "converted files stay on disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        history.clear_history()
        self.refresh()


class SelfTestWorker(QThread):
    """Runs the self-test off the UI thread (T64, U30).

    The search and sniff checks use the network and take seconds;
    the window stays responsive while they run.
    """

    done = Signal(list)

    def run(self) -> None:  # noqa: N802 - Qt naming
        try:
            checks = selftest.run_checks()
        except Exception as exc:  # noqa: BLE001 - a crashed self-test is a failed report, not a crash
            checks = [{"name": "Self-test", "ok": False,
                       "detail": f"could not run: {exc}"}]
        self.done.emit(checks)


class SelfTestDialog(QDialog):
    """The self-test in the app (T64): the same three checks as
    `scout-cli --self-test`, with the plain-words result."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Self-test")
        self.setMinimumSize(560, 320)
        self.worker: SelfTestWorker | None = None
        layout = QVBoxLayout(self)
        # Word wrap on the header (v0.1.6 T15): at the dialog's
        # own 560 px default the sentence was cut mid-word. It is
        # the dialog's only long label; the report below is a text
        # view, which wraps on its own.
        self.header_label = QLabel(
            "The self-test checks search, sniff, and conversion end "
            "to end. It takes a few seconds and changes nothing.")
        self.header_label.setWordWrap(True)
        layout.addWidget(self.header_label)
        self.report_view = QTextEdit()
        self.report_view.setReadOnly(True)
        layout.addWidget(self.report_view, stretch=1)
        row = QHBoxLayout()
        self.run_button = QPushButton("Run the self-test")
        self.run_button.setToolTip(
            "Run the self-test again.")
        self.run_button.clicked.connect(self.start_test)
        row.addWidget(self.run_button)
        close_button = QPushButton("Close")
        close_button.setToolTip("Close this window.")
        close_button.clicked.connect(self.accept)
        row.addWidget(close_button)
        row.addStretch(1)
        layout.addLayout(row)

    def start_test(self):
        """Run the checks in the background and show the report."""
        if self.worker is not None and self.worker.isRunning():
            return
        self.run_button.setEnabled(False)
        self.report_view.setPlainText("Running the self-test...")
        self.worker = SelfTestWorker(parent=self)
        self.worker.done.connect(self._show_report)
        self.worker.start()

    def _show_report(self, checks: list):
        self.run_button.setEnabled(True)
        self.report_view.setPlainText(selftest.format_report(checks))
        passed = sum(1 for check in checks if check.get("ok"))
        core.log(f"Self-test (GUI): {passed} of {len(checks)} "
                 "checks passed.")

    def closeEvent(self, event):  # noqa: N802 - Qt naming
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(30000)
        super().closeEvent(event)


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
        # What the list file looked like when this window last read
        # it (T13): a save refuses when the file changed elsewhere,
        # and merges instead of overwriting. _list_known_urls tells
        # a link added elsewhere from one removed here.
        self._list_generation = core.list_generation()
        self._list_known_urls = {
            core.normalize_url(item.get("url") or "")
            for item in self.my_list}
        # Checks running now ("link" and/or "stella"): while one
        # runs, the list and the search stay put (T3).
        self._active_checks: set[str] = set()
        self.search_worker: SearchWorker | None = None
        self.check_worker: LinkCheckWorker | None = None
        self.stella_worker: StellaCheckWorker | None = None
        self.update_worker: UpdateCheckWorker | None = None
        # True while a running update check is the silent startup
        # one (v0.1.5 U1): up to date or unreachable stays quiet.
        # A manual check during a running silent one flips this
        # off, so the in-flight answer still gets its receipt.
        self._update_check_silent = False
        self.watch_worker: WatchWorker | None = None
        self._online_worker: OnlineCheckWorker | None = None
        # Online state (T67): None until the first check answers.
        self._online: bool | None = None
        self._watch_dialog: WatchDialog | None = None
        self._queue_dialog: QueueDialog | None = None
        self._history_dialog: HistoryDialog | None = None
        self._selftest_dialog: SelfTestDialog | None = None
        # Watch timer (T63): re-runs due watches while the app is
        # open. Started by start_background_checks, never here, so
        # constructing the window never touches the network.
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(30 * 60 * 1000)
        self._watch_timer.timeout.connect(self._run_due_watches)
        self.howto_window: HowToUseWindow | None = None
        self._check_total = 0
        self._check_done_count = 0
        # STELLA check progress (v0.1.3 T18): mirrors the link check.
        self._stella_total = 0
        self._stella_done_count = 0
        # The search page for Load more (v0.1.3 T22).
        self._search_page = 1
        # The last removed website, for Undo (v0.1.3 T19):
        # (site dict, position) or None.
        self._removed_site = None
        self._undo_stack: list[list[tuple[int, dict]]] = []
        # Edits waiting to be undone (T59): (the item as it was
        # before the edit, after-edit normalised URL). The
        # after-edit URL finds the row again if the list moved.
        self._edit_undo_stack: list[tuple[dict, str]] = []
        self._last_query: str | None = None
        self._last_category: str | None = None
        self._results_sorting = True
        self._results_sort = (R_UPDATED, Qt.SortOrder.DescendingOrder)
        self._list_sorting = False
        self._showing_skeleton = False
        self._stella_target = "results"
        self._searched_once = False
        # A line the empty results label shows instead of its
        # usual text (v0.1.4 T18/T29): a failed or offline search
        # says so, instead of showing the never-searched hint.
        self._results_empty_note = ""
        # The last search's unfiltered results and how many each
        # filter hid (v0.1.6 T23): "Show them anyway" re-filters
        # these in place, with no new search.
        self._last_raw_results: list[dict] = []
        self._hidden_breakdown: dict = {}
        # Whether the update popup on screen came from a manual
        # check (v0.1.6 T17): a manual check leaves a receipt.
        self._update_offer_manual = False
        # The checklist's completion beat (T36) starts as shown:
        # a returning user whose setup is already done must not
        # see the list flash on every launch. It clears the first
        # time a refresh finds the list incomplete, so a new user
        # still gets the beat when they finish.
        self._checklist_done_shown = True
        # Keys the menu bar already binds (T45): _build_shortcuts
        # skips them, so one key never fires two handlers.
        self._menu_shortcut_keys: set[str] = set()
        # Worker-completion dialogs waiting behind an open modal
        # (T60, U18): shown in order once the modal closes.
        self._dialog_queue: list = []
        self._dialog_queue_timer = QTimer(self)
        self._dialog_queue_timer.setInterval(500)
        self._dialog_queue_timer.timeout.connect(
            self._drain_dialog_queue)
        # Elapsed-time ticker for long searches (T52).
        self._search_started_at = 0.0
        self._search_elapsed_timer = QTimer(self)
        self._search_elapsed_timer.setInterval(1000)
        self._search_elapsed_timer.timeout.connect(
            self._tick_search_elapsed)

        self._build_menus()

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._build_search_tab()
        self._build_list_tab()
        self._build_sources_tab()
        self.tabs.currentChanged.connect(self._refit_tables)
        self._refresh_list_tab()
        self._refresh_site_buttons()
        self._build_shortcuts()
        self._apply_density()

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
        # The finish signal (T57, 008): a checkmark that stays put
        # after a long job ends, until the next job starts.
        self.finish_label = QLabel("")
        self.finish_label.setObjectName("finish_label")
        self.finish_label.setStyleSheet("color: #ffd166;")
        self.statusBar().addPermanentWidget(self.finish_label)
        # The GitHub rate meter (T16) and the live stats strip
        # (T25). Neither is a status text surface: they show numbers.
        self.rate_label = QLabel("GitHub: quota unknown")
        self.rate_label.setObjectName("rate_meter")
        self.statusBar().addPermanentWidget(self.rate_label)
        self.stats_label = QLabel("")
        self.stats_label.setObjectName("stats_strip")
        self.statusBar().addPermanentWidget(self.stats_label)
        # One small bar on the stats strip (T55, 037): working vs
        # dead vs unknown, with a plain takeaway beside it.
        self.health_bar = LinkHealthBar()
        self.statusBar().addPermanentWidget(self.health_bar)
        self.health_takeaway = QLabel("")
        self.statusBar().addPermanentWidget(self.health_takeaway)
        self._refresh_stats()
        self._refresh_checklist()
        self._layout_status_bar()

        self.statusBar().showMessage(
            "Type a few words and press Search, or pick a category below."
        )
        # Say when the saved list was damaged and what was recovered
        # (T4): a corrupt file, or rows that could not be read.
        notice = core.list_load_notice()
        if notice:
            self.statusBar().showMessage(notice)

    def _layout_status_bar(self):
        """Give the status text priority over the permanent
        widgets (T33).

        At the default width the rate meter, stats, health bar,
        and takeaway together clip the message mid-word (and the
        reset time with it). The takeaway and the bar hide first
        as the window narrows: their numbers already live in the
        stats strip, so nothing is lost, and the message gets
        the room.
        """
        if not hasattr(self, "health_takeaway"):
            return
        width = self.width()
        self.health_takeaway.setVisible(width >= 1300)
        self.health_bar.setVisible(width >= 1200)

    def _refit_tables(self, _index: int = -1):
        """Re-fit both tables' columns to the real viewport
        (v0.1.6 T10/T11): the construction-time fit sees
        provisional geometry, so showing the window and switching
        tabs fit again with true sizes."""
        if hasattr(self, "results_table") and self.results:
            self._fit_results_columns()
        if hasattr(self, "list_table"):
            self._fit_list_columns()

    def showEvent(self, event):  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._refit_tables()

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._layout_status_bar()
        # A narrower window re-fits the results columns (T31):
        # the fit from the last search assumed the old width.
        if hasattr(self, "results_table") and self.results:
            self._fit_results_columns()
        if hasattr(self, "list_table") and self.my_list:
            self._fit_list_columns()

    # ------------------------------------------------------------ menus (T23, U38)

    def _menu_action(self, menu, text, slot, tip=None, shortcut=None):
        """One menu item, with its teaching text (040, 065).

        The tip shows in the status bar while the item is hovered.
        A shortcut binds the key to the menu item and is recorded,
        so _build_shortcuts does not bind the same key twice.
        """
        action = menu.addAction(text, slot)
        if tip:
            action.setStatusTip(tip)
            action.setToolTip(tip)
        if shortcut:
            action.setShortcut(shortcut)
            self._menu_shortcut_keys.add(shortcut)
        return action

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("File")
        self._menu_action(
            file_menu, "Export as Markdown list",
            lambda: self.export_list("md"),
            "Save My List as a Markdown file you can open anywhere.")
        self._menu_action(
            file_menu, "Export as JSON",
            lambda: self.export_list("json"),
            "Save My List as a JSON file for other programs.")
        self._menu_action(
            file_menu, "Export STELLA manifest",
            lambda: self.export_list("stella"),
            "Save a STELLA manifest for this list. Ctrl+E does this too.",
            shortcut="Ctrl+E")
        self._menu_action(
            file_menu, "Import a list", self.import_list,
            "Read a list you exported back into My List.")
        self._menu_action(
            file_menu, "Add a link I found", self.show_add_link_dialog,
            "Add a link you found yourself to My List.")
        self._menu_action(
            file_menu, "Send to STELLA", self.send_to_stella,
            "Write the STELLA manifest into the STELLA folder on this PC.")
        self._menu_action(
            file_menu, "Convert a file on this PC",
            self.convert_local_file,
            "Convert a dataset file already on this PC to STELLA "
            ".jsonl. Works offline.")
        self._menu_action(
            file_menu, "Watched searches", self.show_watches,
            "See the searches Scout re-runs for you, and what is new.")
        self._menu_action(
            file_menu, "Download history", self.show_history,
            "See what was converted, when, and how many rows.")
        file_menu.addSeparator()
        self._menu_action(
            file_menu, "Exit", self.close, "Close Dataset Scout.")

        edit_menu = self.menuBar().addMenu("Edit")
        # The Undo items track the same enable state as the My
        # List tab buttons (v0.1.6 T13): disabled while their
        # stack is empty. _sync_undo_actions keeps them in step.
        self.undo_remove_action = self._menu_action(
            edit_menu, "Undo remove", self.undo_remove,
            "Bring back the rows removed most recently.")
        self.undo_remove_action.setEnabled(False)
        self.undo_edit_action = self._menu_action(
            edit_menu, "Undo edit", self.undo_edit,
            "Undo the last change made in the Edit window.")
        self.undo_edit_action.setEnabled(False)
        self._menu_action(
            edit_menu, "Remove selected", self.remove_selected,
            "Remove the picked rows from My List. Delete does this too.",
            shortcut="Delete")
        self._menu_action(
            edit_menu, "Clear My List", self.clear_list,
            "Remove every link from My List after asking first.")

        view_menu = self.menuBar().addMenu("View")
        self._menu_action(
            view_menu, "Search tab",
            lambda: self.tabs.setCurrentIndex(0),
            "Show the Search tab.")
        self._menu_action(
            view_menu, "My List tab",
            lambda: self.tabs.setCurrentIndex(1),
            "Show My List.")
        self._menu_action(
            view_menu, "Sources tab",
            lambda: self.tabs.setCurrentIndex(2),
            "Show the Sources tab.")
        view_menu.addSeparator()
        # Row density (T53, 064): comfortable default, compact opt-in.
        density_group = QActionGroup(self)
        density_group.setExclusive(True)
        self.density_comfortable_action = self._menu_action(
            view_menu, "Comfortable rows",
            lambda: self.set_density("comfortable"),
            "Use roomy rows that are easy to read.")
        self.density_comfortable_action.setCheckable(True)
        density_group.addAction(self.density_comfortable_action)
        self.density_compact_action = self._menu_action(
            view_menu, "Compact rows",
            lambda: self.set_density("compact"),
            "Use tight rows to see more at once.")
        self.density_compact_action.setCheckable(True)
        density_group.addAction(self.density_compact_action)

        help_menu = self.menuBar().addMenu("Help")
        self._menu_action(
            help_menu, "How to Use", self.show_how_to_use,
            "Open the guide to using Dataset Scout.")
        self._menu_action(
            help_menu, "Keyboard shortcuts",
            self.show_shortcuts_overlay,
            "Show every keyboard shortcut. Press ? any time.",
            shortcut="?")
        self._menu_action(
            help_menu, "Check for updates", self.check_for_updates,
            "Look on GitHub for a newer version of Dataset Scout.")
        self._menu_action(
            help_menu, "Run self-test", self.show_selftest,
            "Check search, sniff, and conversion end to end, and "
            "show the result in plain words.")
        self._menu_action(
            help_menu, "About Dataset Scout", self._show_about,
            "Show the version of Dataset Scout.")

    def _build_shortcuts(self):
        """Keyboard shortcuts (T19), built from the SHORTCUTS registry.

        Keys the menu bar already binds are skipped here: one key,
        one handler. The input-field guard (028) is Qt's own: a
        text field swallows its typing keys before a shortcut sees
        them, so ? and Delete keep working while the user types.
        """
        for key, _desc, scope, action in SHORTCUTS:
            if action is None:
                # Documented only (T52): the widget already owns
                # the key, or it is a mouse gesture, not a key.
                continue
            if key in self._menu_shortcut_keys:
                continue
            parent = self.list_table if scope == "list" else self
            QShortcut(QKeySequence(key), parent, getattr(self, action))

    def show_shortcuts_overlay(self):
        """Open the ? overlay (065), built from the registry."""
        ShortcutsDialog(self).exec()

    def _shortcut_focus_search(self):
        # Show the box as well as focus it (v0.1.6 T12): from
        # another tab, focusing an invisible field left the user
        # typing into a hidden box with no visible change.
        self.tabs.setCurrentIndex(0)
        self.search_box.setFocus()

    def _shortcut_tab_search(self):
        self.tabs.setCurrentIndex(0)

    def _shortcut_tab_list(self):
        self.tabs.setCurrentIndex(1)

    def _shortcut_tab_sources(self):
        self.tabs.setCurrentIndex(2)

    def _shortcut_tab_next(self):
        count = self.tabs.count()
        if count:
            self.tabs.setCurrentIndex(
                (self.tabs.currentIndex() + 1) % count)

    def _shortcut_tab_prev(self):
        count = self.tabs.count()
        if count:
            self.tabs.setCurrentIndex(
                (self.tabs.currentIndex() - 1) % count)

    def _shortcut_export_stella(self):
        self.export_list("stella")

    def _about_text(self) -> str:
        """The About box text, including the persistent record of
        a list recovery when one happened (T53): the smallest
        honest place for it, since the status bar is the only
        status surface and its notice is transient."""
        text = (
            f"Dataset Scout {core.VERSION}\n\n"
            "Find training datasets for STELLA, build lists of links.\n"
            "Searches GitHub and Hugging Face live, checks whether "
            "datasets are STELLA-ready, and converts downloads to "
            "STELLA .jsonl files.")
        record = core.load_config().get("list_recovered")
        if isinstance(record, dict) and record.get("links") is not None:
            count = core._safe_int(record.get("links"))
            word = "link" if count == 1 else "links"
            when = core.format_date(record.get("date") or "")
            text += (
                f"\n\nYour saved list was damaged once and loaded "
                f"from the backup copy instead ({count} {word} "
                f"recovered{', ' + when if when else ''}).")
        return text

    def _show_about(self):
        QMessageBox.about(self, "About Dataset Scout", self._about_text())

    # ------------------------------------------------------------ update check (T21)

    def check_for_updates(self, silent: bool = False):
        """Ask GitHub for the latest release, off the UI thread.

        silent is the startup check (v0.1.5 U1): up to date or
        unreachable stays quiet, and only a newer release speaks.
        The manual Help-menu check keeps its status-bar receipt
        for the up-to-date and unreachable cases.
        """
        if self.update_worker is not None and self.update_worker.isRunning():
            if not silent:
                # A manual check during a running silent one
                # upgrades it: the in-flight answer still gets
                # its receipt instead of staying quiet.
                self._update_check_silent = False
            return
        if not silent:
            if not self._require_online("Checking for updates"):
                return
            self.statusBar().showMessage("Checking for updates...")
        self._update_check_silent = silent
        self.update_worker = UpdateCheckWorker(self)
        self.update_worker.finished.connect(self._update_check_done)
        self.update_worker.start()

    def _update_check_done(self, tag):
        # The meter refreshes too: an update check reads the core
        # quota, and the meter must not show it as searches (T14).
        self._refresh_rate()
        silent = self._update_check_silent
        if not tag:
            # Unreachable (v0.1.5 U1): the silent startup check
            # stays quiet — the worker already logged the failure.
            # The manual check keeps its status-bar receipt.
            if not silent:
                self.statusBar().showMessage(
                    "Could not check for updates. Try again later.")
            return
        current = core.parse_version(core.VERSION)
        latest = core.parse_version(tag)
        if latest <= current:
            if not silent:
                self.statusBar().showMessage("Dataset Scout is up to date.")
        else:
            # A newer release (v0.1.5 U2): a popup asks whether
            # to get it, on both the startup and manual paths.
            self._update_offer_manual = not silent
            self._offer_update(tag)

    def _offer_update(self, tag):
        """The update-found popup (v0.1.5 U2): plain words, two
        buttons. "Get it" hands off to the updater; "Not now"
        just dismisses, and the app asks again next launch."""
        manual = self._update_offer_manual

        def show_popup():
            box = QMessageBox(self)
            box.setWindowTitle("A new version is out")
            box.setText(
                f"Dataset Scout {_display_version(tag)} is out. "
                f"You have {_display_version(core.VERSION)}.\n\n"
                "Want to get the new version now?")
            get_button = box.addButton(
                "Get it", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if manual:
                # A manual check leaves a receipt (v0.1.6 T17):
                # "Checking for updates..." must not be left
                # standing after the popup is dismissed.
                self.statusBar().showMessage(
                    f"Dataset Scout {_display_version(tag)} is out.")
            if box.clickedButton() is get_button:
                from scout import updater
                updater.start_update(self, tag)
                if manual:
                    self.statusBar().showMessage(
                        f"Dataset Scout {_display_version(tag)} "
                        "is out.")

        # A worker-completion dialog (T60, U18): it never stacks
        # on an open modal, it waits its turn.
        self._queue_modal_dialog(
            show_popup,
            "A new version is out. It will show once the open "
            "dialog closes.")

    # ------------------------------------------------------------ status bar numbers

    def _refresh_rate(self):
        """The GitHub rate meter (T16): the search quota, per minute.

        Only search replies update the search bucket (T14), and the
        reset time the code already captures is shown too.
        """
        remaining = apis.GITHUB_RATE.get("search_remaining")
        reset = apis.GITHUB_RATE.get("search_reset")
        if remaining is None:
            # Short on purpose (T33): the meter shares the bar
            # with the message, and a long meter clips both.
            self.rate_label.setText("GitHub: quota unknown")
            return
        if remaining == 1:
            text = "GitHub: 1 left"
        else:
            text = f"GitHub: {remaining} left"
        if reset is not None:
            text += f", resets {reset.astimezone().strftime('%H:%M')}"
        self.rate_label.setText(text)

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
        if hasattr(self, "health_bar"):
            # One bar, one message (T55, 037): the bar shows the
            # split, the takeaway beside it says what it means.
            unknown = links - ok - dead
            self.health_bar.set_counts(ok, dead, unknown)
            if links == 0:
                takeaway = "No links saved yet"
            elif ok == 0 and dead == 0:
                takeaway = "No links checked yet"
            elif dead == 0:
                takeaway = "Every checked link works"
            elif ok >= dead:
                takeaway = "Most links work"
            else:
                takeaway = "Many links are dead"
            self.health_takeaway.setText(takeaway)

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
        # Let the queued check results land before saving (T16):
        # the workers' signals wait in the queue while the loop is
        # blocked in wait(), and are dropped at exit if they are
        # never delivered. Then save the list (T11): closing must
        # keep the session's changes, not just the window geometry.
        QApplication.processEvents()
        self._save_list()
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
                       self.stella_worker, self.update_worker,
                       self.watch_worker, self._online_worker):
            if worker is None:
                continue
            if hasattr(worker, "request_graceful_stop"):
                # The close path lets in-flight work land
                # (v0.1.3 T16): a link check already running
                # finishes and its status survives on disk. The
                # Stop button's at-once drop (v0.1.4 T27) is for
                # the user pressing Stop, not for closing.
                worker.request_graceful_stop()
            elif hasattr(worker, "request_stop"):
                worker.request_stop()
            if worker.isRunning():
                worker.wait(25000)
            if worker.isRunning():
                core.log("A worker would not stop; terminating it.",
                         level="WARNING")
                worker.terminate()
                worker.wait(5000)

    # ------------------------------------------------------------ saving (T11, T13)

    def _note_list_loaded(self) -> None:
        """Record what the list file looks like after a load."""
        self._list_generation = core.list_generation()
        self._list_known_urls = {
            core.normalize_url(item.get("url") or "")
            for item in self.my_list}

    def _save_list_failed(self, exc: OSError) -> None:
        """A failed list save, in plain words (T11)."""
        core.log_error(f"Could not save My List: {exc}")
        QMessageBox.warning(
            self, "Could not save My List",
            "Dataset Scout could not save My List, so changes from "
            "this session may be lost when the window closes. The "
            "list is still on screen. Try again, or export a copy "
            "from the File menu.")
        self.statusBar().showMessage("Could not save My List.")

    def _save_list(self) -> bool:
        """Save My List, telling the user in plain words if it fails.

        If the file changed on disk since this window last read it
        (a CLI save while the GUI was open), the other copy's new
        links are merged in instead of overwritten (T13).
        """
        try:
            core.save_list(self.my_list,
                           expected_generation=self._list_generation)
        except core.ListChangedError:
            return self._save_list_merged()
        except OSError as exc:
            self._save_list_failed(exc)
            return False
        self._note_list_loaded()
        return True

    def _save_list_merged(self) -> bool:
        """Save after the file changed elsewhere: keep both copies.

        Links the window has seen before and no longer holds stay
        removed; links it never saw (added elsewhere) are kept.
        """
        try:
            disk_items = core.load_list()
        except OSError:
            disk_items = []
        mine = {core.normalize_url(item.get("url") or "")
                for item in self.my_list}
        added = 0
        for item in disk_items:
            key = core.normalize_url(item.get("url") or "")
            if key and key not in mine \
                    and key not in self._list_known_urls:
                self.my_list.append(item)
                added += 1
        try:
            core.save_list(self.my_list)
        except OSError as exc:
            self._save_list_failed(exc)
            return False
        self._note_list_loaded()
        if added:
            self._refresh_list_tab()
            word = "link" if added == 1 else "links"
            self.statusBar().showMessage(
                f"The list had changed elsewhere, so {added} {word} "
                "from the other copy were kept as well.")
        return True

    def _save_collections(self, active: str, collections: list) -> bool:
        """Save the collections file, warning plainly on failure."""
        try:
            core.save_collections(active, collections)
        except OSError as exc:
            core.log_error(f"Could not save the lists: {exc}")
            QMessageBox.warning(
                self, "Could not save the lists",
                "Dataset Scout could not save the lists, so changes "
                "from this session may be lost when the window "
                "closes.")
            self.statusBar().showMessage("Could not save the lists.")
            return False
        self._note_list_loaded()
        return True

    def _try_save(self, call, title: str, message: str) -> bool:
        """Run a small save, warning plainly when it fails (T11)."""
        try:
            call()
        except OSError as exc:
            core.log_error(f"{title}: {exc}")
            QMessageBox.warning(self, title, message)
            self.statusBar().showMessage(message)
            return False
        return True

    # ------------------------------------------------------------ check lock (T3)

    def _check_running(self) -> bool:
        """True while a link check or STELLA check is running."""
        return bool(self._active_checks)

    def _refresh_check_lock(self):
        """Lock list and search controls while a check runs.

        Remove, reorder, collection switch, and a new search would
        move the ground under a running check, so they wait: the
        buttons go quiet, and the guarded methods say so too (menu
        items and shortcuts bypass the buttons).
        """
        locked = bool(self._active_checks)
        for widget in (self.remove_button, self.clear_button,
                       self.collection_combo, self.collection_new_button,
                       self.collection_rename_button,
                       self.collection_delete_button):
            widget.setEnabled(not locked)
        self.undo_button.setEnabled(
            (not locked) and bool(self._undo_stack))
        if hasattr(self, "undo_edit_button"):
            self.undo_edit_button.setEnabled(
                (not locked) and bool(self._edit_undo_stack))
        self._sync_undo_actions()
        if hasattr(self, "remove_dead_button"):
            self._refresh_dead_button()
        searching = (self.search_worker is not None
                     and self.search_worker.isRunning())
        self.search_button.setEnabled(not locked and not searching)
        self.list_table.setDragEnabled(not locked)

    def _sync_undo_actions(self):
        """Keep the Edit menu's Undo items in step with the My
        List tab buttons (v0.1.6 T13): the menu copies the
        buttons' enabled state, which the stacks and the check
        lock already compute."""
        if hasattr(self, "undo_remove_action") \
                and hasattr(self, "undo_button"):
            self.undo_remove_action.setEnabled(
                self.undo_button.isEnabled())
        if hasattr(self, "undo_edit_action") \
                and hasattr(self, "undo_edit_button"):
            self.undo_edit_action.setEnabled(
                self.undo_edit_button.isEnabled())

    def _find_list_index(self, url: str) -> int:
        """The index of a list item by URL, or -1 when it is gone."""
        for index, item in enumerate(self.my_list):
            if item.get("url") == url:
                return index
        key = core.normalize_url(url)
        for index, item in enumerate(self.my_list):
            if core.normalize_url(item.get("url") or "") == key:
                return index
        return -1

    def _find_result_index(self, url: str) -> int:
        """The index of a result by URL, or -1 when it is gone."""
        for index, item in enumerate(self.results):
            if item.get("url") == url:
                return index
        key = core.normalize_url(url)
        for index, item in enumerate(self.results):
            if core.normalize_url(item.get("url") or "") == key:
                return index
        return -1

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

    # ------------------------------------------------------------ dialog queue (T60, U18)

    def _any_modal_open(self) -> bool:
        """True when a modal window is open (T60, U18).

        isHidden, not isVisible: a shown modal counts even when
        its parent window is not on screen yet.
        """
        return any(
            widget.isModal() and not widget.isHidden()
            for widget in QApplication.topLevelWidgets()
        )

    def _queue_modal_dialog(self, show_fn, waiting_message: str):
        """Show a worker-completion dialog, or queue it (003).

        Modal dialogs never stack (U18): when a modal is open, the
        dialog waits its turn and the status bar says honestly
        what it is waiting for.
        """
        if not self._any_modal_open():
            show_fn()
            return

        def show_then_clear():
            show_fn()
            # The waiting message described this dialog (v0.1.6
            # T5): once the dialog has been shown and dismissed,
            # the message must not stand there saying a dialog is
            # still coming. Clear it only when it is still the
            # current message, so a newer message is never eaten.
            if self.statusBar().currentMessage() == waiting_message:
                self.statusBar().clearMessage()

        self._dialog_queue.append(show_then_clear)
        self.statusBar().showMessage(waiting_message)
        if not self._dialog_queue_timer.isActive():
            self._dialog_queue_timer.start()

    def _drain_dialog_queue(self):
        """Show queued dialogs once no modal is open."""
        if self._any_modal_open():
            return
        while self._dialog_queue:
            show_fn = self._dialog_queue.pop(0)
            show_fn()
            if self._any_modal_open():
                return  # the shown dialog is modal; wait for it
        self._dialog_queue_timer.stop()

    # ------------------------------------------------------------ completion moment (T57, 008)

    def _mark_finished(self, summary: str):
        """The completion moment: a checkmark plus a one-line
        summary. Static, no animation: the checkmark stays until
        the next job starts."""
        self.finish_label.setText("✓ Done")
        self.statusBar().showMessage(f"✓ {summary}")

    def _clear_finished(self):
        self.finish_label.setText("")

    # ------------------------------------------------------------ density (T53, 064)

    def _density(self) -> str:
        """The saved row density: comfortable unless compact was picked."""
        value = core.load_config().get("density")
        return value if value in DENSITY_ROW_HEIGHTS else "comfortable"

    def _apply_density(self):
        """Set both tables' row heights from the saved density."""
        height = DENSITY_ROW_HEIGHTS[self._density()]
        for table in (self.results_table, self.list_table):
            table.verticalHeader().setDefaultSectionSize(height)
            # The header needs less height than a data row
            # (v0.1.6 T16): at the row height it ate a full row's
            # worth of the results table's viewport.
            table.horizontalHeader().setMinimumHeight(30)
            for row in range(table.rowCount()):
                table.setRowHeight(row, height)
        self._sync_density_menu()

    def set_density(self, name: str):
        """Pick a row density and remember it for next time."""
        if name not in DENSITY_ROW_HEIGHTS:
            return
        config = core.load_config()
        config["density"] = name
        core.save_config(config)
        self._apply_density()

    def _sync_density_menu(self):
        if not hasattr(self, "density_comfortable_action"):
            return
        compact = self._density() == "compact"
        self.density_compact_action.setChecked(compact)
        self.density_comfortable_action.setChecked(not compact)

    # ------------------------------------------------------------ first-run checklist (T56, 020)

    def _refresh_checklist(self):
        """Tick the first-run checklist from real state.

        Installed is honestly ticked from the start. The list goes
        away once a search has run and a link is saved; the token
        step is optional, so it never holds the list open.
        Dismissed means dismissed.
        """
        if not hasattr(self, "checklist_box"):
            return
        config = core.load_config()
        if config.get("checklist_dismissed"):
            self.checklist_box.setVisible(False)
            return
        searched = self._searched_once or core.searches_run() > 0
        data = core.load_list_data()
        saved = any(col.get("items")
                    for col in data.get("collections", []))
        has_token = bool(core.get_github_token())
        if searched and saved:
            if not getattr(self, "_checklist_done_shown", False):
                # The completion moment (T36): show the final
                # ticked list for a beat before hiding it, so
                # finishing setup feels finished. The flag resets
                # whenever the list is incomplete again.
                self._checklist_done_shown = True
                self.checklist_box.setVisible(True)
                for key, label in self.checklist_labels.items():
                    text = self.checklist_texts[key]
                    done = key in ("installed", "search", "save") \
                        or (key == "token" and has_token)
                    mark = "✓ " if done else "○ "
                    label.setText(mark + text)
                QTimer.singleShot(
                    1500, self._hide_checklist_after_completion)
                return
            self.checklist_box.setVisible(False)
            return
        self._checklist_done_shown = False
        self.checklist_box.setVisible(True)
        states = {"installed": True, "search": searched,
                  "save": saved, "token": has_token}
        for key, label in self.checklist_labels.items():
            text = self.checklist_texts[key]
            mark = "✓ " if states[key] else "○ "
            label.setText(mark + text)

    def _hide_checklist_after_completion(self):
        """Hide the ticked checklist once its beat is over (T36)."""
        if hasattr(self, "checklist_box"):
            self.checklist_box.setVisible(False)

    def _dismiss_checklist(self):
        """Hide the first-run checklist for good."""
        config = core.load_config()
        config["checklist_dismissed"] = True
        core.save_config(config)
        self.checklist_box.setVisible(False)

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
        # Tight on purpose (T31): at 1100x700 the results, the
        # Add buttons, and Load more must show without scrolling.
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        heading = _heading("Dataset finder for STELLA")
        layout.addWidget(heading)
        # No separate subtitle line (v0.1.6 T16): what the tab is
        # for is said by the heading and the How to Use guide, and
        # the line's height belongs to the results table.

        # The offline note (T67): shown only when the internet
        # cannot be reached, so offline work stays honest.
        self.offline_banner = QLabel(
            "You are offline. Search shows saved results only, "
            "and downloads need the internet. Your lists and "
            "files on this PC still work.")
        self.offline_banner.setObjectName("offline_banner")
        self.offline_banner.setWordWrap(True)
        self.offline_banner.setStyleSheet("color: #ffd166;")
        self.offline_banner.setVisible(False)
        layout.addWidget(self.offline_banner)

        # The first-run checklist (T56, 020): three steps to a first
        # result, the first honestly ticked, gone when done. A
        # wrapping flow (T31): one row at 1100 px, two at 900, so
        # the steps stop squeezing the results off a 1100x700
        # window on a new user's first search.
        self.checklist_box = QGroupBox("First steps")
        # Less chrome (T31): the group-box margin and padding cost
        # ~20 px the results need at 1100x700.
        self.checklist_box.setStyleSheet(
            "QGroupBox { margin-top: 4px; padding-top: 4px; }")
        checklist_layout = FlowLayout(self.checklist_box)
        self.checklist_texts = {
            "installed": "Dataset Scout is installed and running",
            "search": "Run your first search",
            "save": "Save a link to My List",
            "token": "Add a GitHub token (optional)",
        }
        self.checklist_labels = {}
        for key, text in self.checklist_texts.items():
            label = QLabel("○ " + text)
            self.checklist_labels[key] = label
            checklist_layout.addWidget(label)
        dismiss_button = QPushButton("Hide this list")
        dismiss_button.setToolTip("Hide the first steps list for good.")
        dismiss_button.clicked.connect(self._dismiss_checklist)
        checklist_layout.addWidget(dismiss_button)
        layout.addWidget(self.checklist_box)

        row = QHBoxLayout()
        self.search_box = QComboBox()
        self.search_box.setEditable(True)
        self.search_box.setToolTip(
            "Type what you want to find. Ctrl+F jumps back here.")
        self.search_box.lineEdit().setPlaceholderText(
            "What kind of dataset? (example: math word problems)")
        self.search_box.lineEdit().returnPressed.connect(self.start_search)
        self.search_box.activated.connect(self._history_activated)
        self._refresh_history_box()
        row.addWidget(self.search_box, stretch=1)
        self.search_button = QPushButton("Search")
        self.search_button.setToolTip(
            "Search GitHub and Hugging Face for the words in the box.")
        self.search_button.clicked.connect(self.start_search)
        row.addWidget(self.search_button)
        self.stop_search_button = QPushButton("Stop")
        self.stop_search_button.setToolTip(
            "Stop the search running now. Results found so far stay.")
        self.stop_search_button.setEnabled(False)
        self.stop_search_button.clicked.connect(self.stop_search)
        row.addWidget(self.stop_search_button)
        self.watch_button = QPushButton("Watch this search")
        self.watch_button.setToolTip(
            "Save this search as a watch: Scout re-runs it while "
            "the app is open and flags new datasets.")
        self.watch_button.clicked.connect(self.watch_current_search)
        row.addWidget(self.watch_button)
        # Elapsed time on long searches (T52, 011): past ~10 seconds
        # the search says how long it has been going.
        self.search_elapsed_label = QLabel("")
        row.addWidget(self.search_elapsed_label)
        # Find in these results shares the search row (v0.1.6
        # T16): it had a row of its own under the table's chrome,
        # and the results table paid for it. It still narrows the
        # on-screen rows by title, description, or source (T55)
        # with no new search.
        row.addWidget(QLabel("Find in these results:"))
        self.results_filter_box = QLineEdit()
        self.results_filter_box.setPlaceholderText(
            "Type to filter by title, description, or source")
        self.results_filter_box.setToolTip(
            "Type a few letters to keep only matching results.")
        self.results_filter_box.textChanged.connect(
            self._apply_results_filter)
        row.addWidget(self.results_filter_box, stretch=1)
        layout.addLayout(row)
        # The two explainer lines that used to sit under the
        # search row and the filter row are folded into the
        # Details pane's resting text (v0.1.6 T16): the words stay
        # on the tab, the stacked lines do not.

        chip_row = QHBoxLayout()
        chip_row.addWidget(QLabel("Category:"))
        self.chip_buttons: dict[str, QPushButton] = {}
        for key, (label, _suffix) in core.CATEGORIES.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == "all")
            if key == "all":
                button.setToolTip("Search every kind of dataset.")
            else:
                button.setToolTip(f"Search only in {label}.")
            button.clicked.connect(self._chip_clicked)
            self.chip_buttons[key] = button
            chip_row.addWidget(button)
        chip_row.addStretch(1)
        layout.addLayout(chip_row)

        filter_row = QHBoxLayout()
        self.active_check = QCheckBox("Active only (updated recently)")
        self.active_check.setChecked(True)
        self.active_check.setToolTip(
            "Keep only datasets updated recently in the results.")
        filter_row.addWidget(self.active_check)
        self.window_combo = QComboBox()
        self.window_combo.addItems(list(core.ACTIVE_WINDOWS.keys()))
        self.window_combo.setCurrentText("12 months")
        self.window_combo.setToolTip(
            "Only show results updated in the last few months.")
        # The window choice is remembered between runs (T65), like
        # the row density: the default stays Active-only, 12 months,
        # exactly as before, and only a choice the user made wins.
        _saved_filters = core.load_config()
        _saved_window = _saved_filters.get("active_window")
        if _saved_window in core.ACTIVE_WINDOWS:
            self.window_combo.setCurrentText(_saved_window)
        self.active_check.setChecked(
            bool(_saved_filters.get("active_only", True)))
        self.active_check.toggled.connect(self._save_search_filters)
        self.window_combo.currentTextChanged.connect(
            self._save_search_filters)
        filter_row.addWidget(QLabel("Updated in the last:"))
        filter_row.addWidget(self.window_combo)
        filter_row.addWidget(QLabel("At least this many stars or likes:"))
        self.stars_combo = QComboBox()
        self.stars_combo.addItems(str(n) for n in core.MIN_STARS_CHOICES)
        self.stars_combo.setToolTip(
            "Keep only results with at least this many stars or likes.")
        filter_row.addWidget(self.stars_combo)
        filter_row.addWidget(QLabel("License:"))
        self.license_combo = QComboBox()
        self.license_combo.addItem("Any license", "")
        for choice in LICENSE_CHOICES[1:]:
            self.license_combo.addItem(choice, choice)
        self.license_combo.setToolTip(
            "Keep only results with this license, if one is shown.")
        filter_row.addWidget(self.license_combo)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        self.results_empty_label = QLabel(
            "No results yet. Type a few words and press Search.")
        self.results_empty_label.setStyleSheet("color: #9fb0cc;")
        layout.addWidget(self.results_empty_label)

        # Which filter hid results, and the way back (v0.1.6
        # T23): shown only when a search hid something. The
        # button relaxes the hiding filter for this search only;
        # the saved default does not change.
        hidden_row = QHBoxLayout()
        self.filter_hidden_label = QLabel("")
        self.filter_hidden_label.setWordWrap(True)
        self.filter_hidden_label.setVisible(False)
        hidden_row.addWidget(self.filter_hidden_label, stretch=1)
        self.show_hidden_button = QPushButton("Show them anyway")
        self.show_hidden_button.setToolTip(
            "Show the hidden results for this search. Your saved "
            "filter choices do not change.")
        self.show_hidden_button.setVisible(False)
        self.show_hidden_button.clicked.connect(self._show_hidden_anyway)
        hidden_row.addWidget(self.show_hidden_button)
        layout.addLayout(hidden_row)

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
        self.results_table.setMinimumHeight(180)
        # No stretch-last-section (v0.1.6 T11): it handed every
        # spare pixel to Score, the column that needs it least.
        # _fit_results_columns hands the space to Description.
        self.results_table.horizontalHeader().setStretchLastSection(False)
        self.results_table.horizontalHeader().sortIndicatorChanged.connect(
            self._results_sorted)
        self.results_table.doubleClicked.connect(self._open_result)
        self.results_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.results_table.customContextMenuRequested.connect(
            self._results_context_menu)
        # Details on demand (T50, 051): a click or Enter shows the
        # picked result's full description, files, and license below.
        self.results_table.itemSelectionChanged.connect(
            self._show_result_detail)
        detail_shortcut = QShortcut(QKeySequence("Return"),
                                    self.results_table,
                                    self._show_result_detail)
        detail_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        layout.addWidget(self.results_table, stretch=1)

        # The Details pane (v0.1.6 T16): a plain label beside the
        # view, not a group box. The group box's squeezed title
        # overlapped its own border at both window sizes, and its
        # chrome cost height the results table needs. The view
        # keeps a smaller share than the old 80 px for the same
        # reason.
        detail_row = QHBoxLayout()
        detail_label = QLabel("Details")
        detail_label.setStyleSheet("font-weight: bold;")
        detail_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        detail_row.addWidget(detail_label)
        self.detail_view = QTextBrowser()
        # Links in the pane open in the browser (T49), in the
        # theme's light blue so they read on the dark surface.
        self.detail_view.setOpenExternalLinks(True)
        self.detail_view.document().setDefaultStyleSheet(
            "a { color: #7ee0ff; }")
        self.detail_view.setMaximumHeight(44)
        detail_row.addWidget(self.detail_view, stretch=1)
        layout.addLayout(detail_row)
        self._show_result_detail()

        add_row = QHBoxLayout()
        self.add_selected_button = QPushButton("Add selected to My List")
        self.add_selected_button.setToolTip(
            "Add the rows you picked to My List for later work.")
        self.add_selected_button.clicked.connect(self.add_selected)
        add_row.addWidget(self.add_selected_button)
        self.add_all_button = QPushButton("Add all results to My List")
        self.add_all_button.setToolTip(
            "Add every result on this page to My List at once.")
        self.add_all_button.clicked.connect(self.add_all_results)
        add_row.addWidget(self.add_all_button)
        self.stella_check_button = QPushButton("Check STELLA-ready")
        self.stella_check_button.setToolTip(
            "Check the picked rows, or every row when nothing is picked.")
        self.stella_check_button.clicked.connect(
            lambda: self.start_stella_check("results"))
        add_row.addWidget(self.stella_check_button)
        self.stop_stella_button = QPushButton("Stop")
        self.stop_stella_button.setToolTip(
            "Stop the STELLA check. Rows checked so far stay marked.")
        self.stop_stella_button.setEnabled(False)
        self.stop_stella_button.clicked.connect(self.stop_stella_check)
        add_row.addWidget(self.stop_stella_button)
        # Load more and the hand-add button share this row
        # (v0.1.6 T16): they used to take a row each, and the
        # results table paid for both.
        self.load_more_button = QPushButton("Load more results")
        self.load_more_button.setToolTip(
            "Fetch the next page of results and add them below.")
        self.load_more_button.setEnabled(False)
        self.load_more_button.clicked.connect(self.load_more_results)
        add_row.addWidget(self.load_more_button)
        add_row.addStretch(1)
        layout.addLayout(add_row)

        # The other-websites buttons on one row (v0.1.6 T16): the
        # group box and its explainer took about 90 px for what is
        # a label and a row of buttons. The buttons' own tooltips
        # still say they open the search in the browser, and the
        # label's tooltip keeps the Sources pointer.
        sites_row = QHBoxLayout()
        sites_label = QLabel("Search on other websites:")
        sites_label.setToolTip(
            "These open the same search in your browser. "
            "Add or remove websites in the Sources tab.")
        sites_row.addWidget(sites_label)
        # A wrapping flow layout (T51, 012): 12 sites measured
        # 1,413 px of buttons in a row; now they wrap instead.
        self.site_buttons_row = FlowLayout()
        sites_row.addLayout(self.site_buttons_row, stretch=1)
        layout.addLayout(sites_row)

        # The hand-add form lives in its own window now (T22): the
        # Search tab stays about searching.
        self.add_link_dialog = AddLinkDialog(self)
        self.found_url = self.add_link_dialog.found_url
        self.found_title = self.add_link_dialog.found_title
        self.found_category = self.add_link_dialog.found_category
        self.found_note = self.add_link_dialog.found_note
        self.found_add_button = QPushButton("Add a link I found")
        self.found_add_button.setToolTip(
            "Add a link you found yourself to My List.")
        self.found_add_button.clicked.connect(self.show_add_link_dialog)
        # In the action row, before its stretch (v0.1.6 T16).
        add_row.insertWidget(add_row.count() - 1,
                             self.found_add_button)

        scroll.setWidget(inner)
        tab_layout.addWidget(scroll)
        self.tabs.addTab(tab, "Search")

    def _results_sorted(self, section: int, order):
        self._results_sorting = True
        if section >= 0:
            self._results_sort = (section, order)
        # Sorting moves items between visual rows, and row hiding
        # follows the visual row, not the item: re-apply the
        # filter so it keeps hiding the right rows (T55).
        if hasattr(self, "results_filter_box"):
            self._apply_results_filter()

    def _save_search_filters(self):
        """Remember the Active-only choice and window (T65)."""
        config = core.load_config()
        config["active_only"] = self.active_check.isChecked()
        config["active_window"] = self.window_combo.currentText()
        core.save_config(config)

    def _apply_results_filter(self):
        """Hide result rows that do not match the filter box (T55)."""
        if self._showing_skeleton:
            # Placeholder rows carry no item: leave them alone
            # while the search runs.
            return
        text = self.results_filter_box.text().strip().lower()
        for row in range(self.results_table.rowCount()):
            cell = self.results_table.item(row, R_TITLE)
            if cell is None:
                continue
            stored = cell.data(ROW_ROLE)
            if not isinstance(stored, int) or stored >= len(self.results):
                self.results_table.setRowHidden(row, True)
                continue
            if not text:
                self.results_table.setRowHidden(row, False)
                continue
            item = self.results[stored]
            hay = (f"{item.get('title', '')} "
                   f"{item.get('description', '')} "
                   f"{item.get('source', '')} "
                   f"{item.get('url', '')}").lower()
            self.results_table.setRowHidden(row, text not in hay)
        self._update_results_empty_label()

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
        if self._check_running():
            # A new search replaces the results a STELLA check is
            # working through (T3).
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
        if self.search_worker is not None and self.search_worker.isRunning():
            return
        if not self.search_box.currentText().strip():
            # An empty box searches nothing (v0.1.4 T5): it used to
            # run a search for the generic word "dataset".
            self.statusBar().showMessage("Type a few words first.")
            return
        category = self.selected_category()
        typed = self.search_box.currentText()
        query = core.build_query(typed, category)
        try:
            core.save_history(typed, category)
        except OSError as exc:
            core.log_error(f"Could not save the search history: {exc}")
        self._refresh_history_box()
        self._last_query = query
        self._last_category = category
        cached = core.load_search_cache(query, category)
        if cached is not None:
            self._search_done(cached, [], cached=True)
            if self._online is False:
                # Offline (T67): the saved results are what there
                # is, and the note says so.
                self.statusBar().showMessage(
                    "You are offline. Showing saved results from "
                    "the last hour.")
            return
        if self._online is False:
            # Offline with nothing saved for this search: say so
            # instead of failing (T67). List work still works.
            # The old rows go too (v0.1.4 T18): leaving the
            # previous search's results under the new query reads
            # as the new search's answer.
            self.results = []
            self._results_empty_note = (
                "You are offline, and there is no saved search for "
                "this yet. Try again when you are back online.")
            self.load_more_button.setEnabled(False)
            self._show_results()
            self.statusBar().showMessage(
                "You are offline, and there is no saved search for "
                "this yet. Try again when you are back online.")
            return
        self.search_button.setEnabled(False)
        self.stop_search_button.setEnabled(True)
        # The results the Add buttons would act on are about to be
        # replaced, so they wait (v0.1.3 T17).
        self.add_selected_button.setEnabled(False)
        self.add_all_button.setEnabled(False)
        self.stella_check_button.setEnabled(False)
        self.load_more_button.setEnabled(False)
        self._search_page = 1
        self._set_busy(True)
        self._clear_finished()
        self._show_skeleton()
        self.statusBar().showMessage(f"Searching for: {query}")
        core.log(f"Search started: {query!r} (category {category})")
        self._search_started_at = time.monotonic()
        self.search_elapsed_label.setText("")
        self._search_elapsed_timer.start()
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

    def _tick_search_elapsed(self):
        """Past ~10 seconds, say how long the search has run (011)."""
        elapsed = int(time.monotonic() - self._search_started_at)
        if elapsed >= 10:
            self.search_elapsed_label.setText(
                f"Searching for {elapsed} seconds...")

    def _stop_search_clock(self):
        self._search_elapsed_timer.stop()
        self.search_elapsed_label.setText("")

    def _search_stopped(self):
        self.search_button.setEnabled(True)
        self.stop_search_button.setEnabled(False)
        self.add_selected_button.setEnabled(True)
        self.add_all_button.setEnabled(True)
        self.stella_check_button.setEnabled(True)
        self._set_busy(False)
        self._clear_skeleton()
        self._stop_search_clock()
        self.statusBar().showMessage("Search stopped.")

    def _show_skeleton(self):
        """Grey placeholder rows while a search runs (T27).

        Every column is filled (T52, 011): the placeholder must
        match the real table's geometry, or it causes the shift
        it was meant to prevent.
        """
        table = self.results_table
        table.setSortingEnabled(False)
        table.setRowCount(5)
        for row in range(5):
            for col in range(len(RESULT_COLUMNS)):
                text = "Loading..." if col == R_TITLE else "…"
                cell = _cell(text)
                cell.setForeground(Qt.GlobalColor.gray)
                table.setItem(row, col, cell)
        self._showing_skeleton = True
        # A new search supersedes the last failure's note (T29).
        self._results_empty_note = ""
        self.results_empty_label.setVisible(False)

    def _clear_skeleton(self):
        if self._showing_skeleton:
            self._showing_skeleton = False
            self.results_table.setRowCount(0)
        # The skeleton hid the empty-state text; show it again now
        # that the table is empty (T46). Stopped and failed searches
        # end here, so without this the table sits blank.
        self._update_results_empty_label()

    def _update_results_empty_label(self):
        empty = (self.results_table.rowCount() == 0
                 and not self._showing_skeleton)
        if empty:
            if self._results_empty_note:
                # A failed or offline search says so (v0.1.4
                # T18/T29), instead of the never-searched hint.
                self.results_empty_label.setText(
                    self._results_empty_note)
                self.results_empty_label.setVisible(True)
                return
            # Never-searched and zero-results are different states
            # (T46, 031): a search that found nothing suggests
            # broadening, instead of repeating the first-run hint.
            if self._searched_once:
                self.results_empty_label.setText(
                    "No results found. Try fewer words, a different "
                    "category, or a wider date range.")
            else:
                self.results_empty_label.setText(
                    "No results yet. Type a few words and press Search.")
            self.results_empty_label.setVisible(True)
            return
        # Rows exist but the filter hides every one (T55): say so,
        # instead of showing an empty table with no explanation.
        if (not self._showing_skeleton
                and hasattr(self, "results_filter_box")
                and self.results_filter_box.text().strip()
                and all(self.results_table.isRowHidden(row)
                        for row in range(self.results_table.rowCount()))):
            self.results_empty_label.setText(
                "No results match that filter. Clear the box to see "
                "them all.")
            self.results_empty_label.setVisible(True)
            return
        self.results_empty_label.setVisible(False)

    def _filter_breakdown(self, results: list[dict]) -> dict:
        """How many results each active filter hid (v0.1.6 T23).

        Counted in the same order core.filter_results applies
        them (stars, then license, then Active only), so the
        counts add up to the hidden total and the biggest one
        is the filter doing the hiding."""
        min_stars = int(self.stars_combo.currentText())
        wanted = (self.license_combo.currentData() or "").strip().lower()
        months = core.ACTIVE_WINDOWS[self.window_combo.currentText()]
        after_stars = [
            item for item in results
            if isinstance(item, dict)
            and core._safe_int(item.get("stars")) >= min_stars]
        if wanted:
            after_license = [
                item for item in after_stars
                if str(item.get("license") or "").strip().lower()
                == wanted]
        else:
            after_license = after_stars
        if self.active_check.isChecked():
            after_active = [
                item for item in after_license
                if core.is_active(item.get("updated"), months)]
        else:
            after_active = after_license
        return {
            "stars": len(results) - len(after_stars),
            "license": len(after_stars) - len(after_license),
            "active": len(after_license) - len(after_active),
        }

    def _update_filter_hidden_ui(self, hidden: int):
        """Name the filter that hid results, and offer the way
        back (v0.1.6 T23). Hidden when nothing was hidden."""
        if hidden <= 0 or not hasattr(self, "filter_hidden_label"):
            if hasattr(self, "filter_hidden_label"):
                self.filter_hidden_label.setVisible(False)
                self.show_hidden_button.setVisible(False)
            return
        breakdown = self._hidden_breakdown
        dominant = max(
            ("active", "stars", "license"),
            key=lambda name: breakdown.get(name, 0))
        count = breakdown.get(dominant, 0)
        if dominant == "active" and count:
            why = (f"Active only (updated in the last "
                   f"{self.window_combo.currentText()}) hid "
                   f"{count} of them.")
        elif dominant == "stars" and count:
            why = (f"The stars filter (at least "
                   f"{self.stars_combo.currentText()} stars or "
                   f"likes) hid {count} of them.")
        elif dominant == "license" and count:
            why = f"The license filter hid {count} of them."
        else:
            why = "The filters above hid them."
        word = "result" if hidden == 1 else "results"
        self.filter_hidden_label.setText(
            f"{hidden} {word} hidden by filters. {why}")
        self.filter_hidden_label.setVisible(True)
        self.show_hidden_button.setVisible(True)

    def _show_hidden_anyway(self):
        """Relax the hiding filter for this search (v0.1.6 T23).

        One click, this search only: the control changes with
        its signals blocked, so the saved choice (and the
        Active-only default) is exactly as it was, and the next
        launch starts from the saved filters again."""
        breakdown = self._hidden_breakdown
        dominant = max(
            ("active", "stars", "license"),
            key=lambda name: breakdown.get(name, 0))
        if breakdown.get(dominant, 0) <= 0:
            dominant = "active"
        if dominant == "active":
            self.active_check.blockSignals(True)
            self.active_check.setChecked(False)
            self.active_check.blockSignals(False)
            note = "Active only is off for this search."
        elif dominant == "stars":
            self.stars_combo.blockSignals(True)
            self.stars_combo.setCurrentText("0")
            self.stars_combo.blockSignals(False)
            note = "The stars filter is off for this search."
        else:
            self.license_combo.blockSignals(True)
            self.license_combo.setCurrentIndex(0)
            self.license_combo.blockSignals(False)
            note = "The license filter is off for this search."
        raw = self._last_raw_results
        months = core.ACTIVE_WINDOWS[self.window_combo.currentText()]
        filtered = core.filter_results(
            raw,
            active_only=self.active_check.isChecked(),
            months=months,
            min_stars=int(self.stars_combo.currentText()),
            license_filter=self.license_combo.currentData() or "",
        )
        filtered.sort(key=core.updated_sort_key, reverse=True)
        self.results = filtered
        self._hidden_breakdown = self._filter_breakdown(raw)
        hidden = len(raw) - len(filtered)
        self._show_results()
        self._update_filter_hidden_ui(hidden)
        count = len(filtered)
        word = "result" if count == 1 else "results"
        self.statusBar().showMessage(
            f"Showing {count} {word}. {note}")
        core.log("Search filters relaxed for this search: "
                 f"{note}")

    def _search_done(self, results: list[dict],
                     failures: list | None = None, cached: bool = False):
        self.search_button.setEnabled(True)
        self.stop_search_button.setEnabled(False)
        self.add_selected_button.setEnabled(True)
        self.add_all_button.setEnabled(True)
        self.stella_check_button.setEnabled(True)
        self._set_busy(False)
        self._clear_skeleton()
        self._stop_search_clock()
        self._searched_once = True
        self._results_empty_note = ""
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
        # by updated date, so the README claim is true now. The
        # parsed time is the key (v0.1.3 T37): raw date strings
        # misorder across timezones.
        filtered.sort(key=core.updated_sort_key, reverse=True)
        self.results = filtered
        self._show_results()
        if not cached and self._last_query is not None:
            try:
                core.save_search_cache(self._last_query,
                                       self._last_category or "all", results)
                core.bump_search_count()
            except OSError as exc:
                core.log_error(
                    f"Could not save the search cache: {exc}")
        self._refresh_rate()
        self._refresh_stats()
        count = len(self.results)
        self.load_more_button.setEnabled(count > 0)
        hidden = total_before - count
        self._last_raw_results = list(results)
        self._hidden_breakdown = self._filter_breakdown(results)
        self._update_filter_hidden_ui(hidden)
        word = "result" if count == 1 else "results"
        message = f"Found {count} {word}"
        if hidden:
            message += f" ({hidden} hidden by filters)"
        if cached:
            message += " (from the last hour, no new search)"
        # No "Pick rows" suffix when there is nothing to pick
        # (v0.1.3 T41).
        if count:
            message += ". Pick rows and add them to My List."
        else:
            message += "."
        failures = failures or []
        if failures:
            # Only one source can have failed here (both failing takes
            # the error path); name it in plain words (U32).
            failed = failures[0][0]
            reason = failures[0][1] if len(failures[0]) > 1 else ""
            if "did not accept that token" in reason:
                # A rejected token is named on screen, in the
                # log's own words (v0.1.4 T6): the cause and the
                # fix are known, so hiding them behind "did not
                # answer" leaves the user guessing.
                message += f" {reason}"
            else:
                other = "Hugging Face" if failed == "GitHub" else "GitHub"
                message += (
                    f" {failed} search did not answer, "
                    f"showing {other} results only."
                )
        self.statusBar().showMessage(message)
        core.log(f"Search finished: {count} results after filters")
        self._refresh_checklist()

    def _search_failed(self, message: str):
        self.search_button.setEnabled(True)
        self.stop_search_button.setEnabled(False)
        self.add_selected_button.setEnabled(True)
        self.add_all_button.setEnabled(True)
        self.stella_check_button.setEnabled(True)
        self._set_busy(False)
        # The results label says the search failed (v0.1.4 T29),
        # instead of the never-searched text.
        self._results_empty_note = (
            "That search failed. Check the connection and try again.")
        self._clear_skeleton()
        self._stop_search_clock()
        self._refresh_rate()
        self.statusBar().showMessage(message)
        # A failed search may mean the internet went away (T67):
        # re-check in the background so the offline note appears.
        self._check_online()
        # The both-sources-failed case keeps its popup (T60), but
        # the popup waits behind any open modal instead of stacking.
        self._queue_modal_dialog(
            lambda: QMessageBox.warning(self, "Search failed", message),
            "Search failed. The details will show when the open "
            "window closes.")

    def load_more_results(self):
        """Fetch the next page of results and append them (T22)."""
        if self.search_worker is not None and self.search_worker.isRunning():
            return
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
        if self._last_query is None:
            return
        self._search_page += 1
        self.load_more_button.setEnabled(False)
        self.stop_search_button.setEnabled(True)
        self._set_busy(True)
        self.statusBar().showMessage(
            f"Loading more results (page {self._search_page})...")
        self.search_worker = SearchWorker(
            self._last_query, self._last_category or "all",
            core.get_github_token(), page=self._search_page,
        )
        self.search_worker.finished_ok.connect(self._load_more_done)
        self.search_worker.finished_error.connect(self._load_more_failed)
        self.search_worker.finished_stopped.connect(
            self._load_more_stopped)
        self.search_worker.start()

    def _load_more_done(self, results: list[dict],
                        failures: list | None = None):
        self._set_busy(False)
        self.stop_search_button.setEnabled(False)
        self.search_worker = None
        months = core.ACTIVE_WINDOWS[self.window_combo.currentText()]
        min_stars = int(self.stars_combo.currentText())
        license_filter = self.license_combo.currentData() or ""
        filtered = core.filter_results(
            results,
            active_only=self.active_check.isChecked(),
            months=months,
            min_stars=min_stars,
            license_filter=license_filter,
        )
        self._last_raw_results.extend(results)
        seen = {core.normalize_url(item.get("url") or "")
                for item in self.results}
        new = [item for item in filtered
               if core.normalize_url(item.get("url") or "") not in seen]
        # Keep the user's place: the scroll position and the
        # selection survive the refresh.
        scroll = self.results_table.verticalScrollBar().value()
        selected = {core.normalize_url(item.get("url") or "")
                    for item in self._selected_results()}
        self.results.extend(new)
        self.results.sort(key=core.updated_sort_key, reverse=True)
        self._show_results()
        self.results_table.verticalScrollBar().setValue(scroll)
        for row in range(self.results_table.rowCount()):
            cell = self.results_table.item(row, R_TITLE)
            if cell is None:
                continue
            stored = cell.data(ROW_ROLE)
            if (isinstance(stored, int)
                    and 0 <= stored < len(self.results)
                    and core.normalize_url(
                        self.results[stored].get("url") or "")
                    in selected):
                self.results_table.selectRow(row)
        if new:
            word = "result" if len(new) == 1 else "results"
            self.statusBar().showMessage(
                f"Loaded {len(new)} more {word}.")
            self.load_more_button.setEnabled(True)
        else:
            self.statusBar().showMessage("No more results.")
            self.load_more_button.setEnabled(False)
        self._refresh_rate()
        self._refresh_stats()

    def _load_more_failed(self, message: str):
        self._set_busy(False)
        self.stop_search_button.setEnabled(False)
        self.search_worker = None
        # The page did not load: the next try asks for it again.
        self._search_page = max(1, self._search_page - 1)
        self.load_more_button.setEnabled(True)
        self.statusBar().showMessage(message)

    def _load_more_stopped(self):
        self._set_busy(False)
        self.stop_search_button.setEnabled(False)
        self.search_worker = None
        self._search_page = max(1, self._search_page - 1)
        self.load_more_button.setEnabled(True)
        self.statusBar().showMessage("Stopped.")

    def _show_results(self):
        table = self.results_table
        table.setSortingEnabled(False)
        table.setRowCount(len(self.results))
        saved_keys = self._saved_url_keys()
        for row, item in enumerate(self.results):
            downloads = core._safe_int(item.get("downloads"))
            # Already saved rows say so (v0.1.6 T37): the add
            # itself still dedupes silently, as before.
            title_text = item.get("title", "")
            if core.normalize_url(item.get("url") or "") in saved_keys:
                title_text = f"{title_text} (In My List)"
            table.setItem(row, R_TITLE,
                          _cell(title_text, row_index=row))
            table.setItem(row, R_SOURCE, _cell(item.get("source", "")))
            table.setItem(row, R_STARS,
                          _cell(str(item.get("stars", 0)),
                                sort_value=core._safe_int(
                                    item.get("stars"))))
            table.setItem(row, R_DOWNLOADS,
                          _cell(str(downloads) if downloads else "",
                                sort_value=downloads))
            # v0.1.3 T37: the Updated column sorts by the parsed
            # time, so a timezone offset cannot misorder the rows.
            parsed = core.parse_iso_date(item.get("updated"))
            table.setItem(row, R_UPDATED,
                          _cell(core.format_date(item.get("updated")),
                                sort_value=(parsed.timestamp()
                                            if parsed is not None
                                            else 0.0)))
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
            # The quality score (T65): the number sorts, the band
            # says what it means, the tooltip shows the working.
            scored = score.score_item(item)
            score_cell = _cell(
                f"{scored['score']} {scored['label']}",
                sort_value=scored["score"])
            score_cell.setToolTip(score.describe_score(scored))
            table.setItem(row, R_SCORE, score_cell)
        table.setSortingEnabled(self._results_sorting)
        if self._results_sorting:
            # Re-apply the sort the user last chose (newest first
            # by default): re-enabling sorting would otherwise fall
            # back to this build's fresh-header default, title
            # descending, and break the newest-first order.
            table.sortByColumn(*self._results_sort)
        table.resizeColumnsToContents()
        # A long title or description must not shove the other
        # columns off the window; the cells still show the start
        # of the text.
        if table.columnWidth(R_TITLE) > 260:
            table.setColumnWidth(R_TITLE, 260)
        if table.columnWidth(R_DESC) > 300:
            table.setColumnWidth(R_DESC, 300)
        self._fit_results_columns()
        # A typed filter applies to the fresh results too (T55);
        # it refreshes the empty label as well.
        self._apply_results_filter()

    def _fit_results_columns(self):
        """Fit every column inside the table's width (T31, and
        v0.1.6 T10/T11).

        The fit recomputes from content widths every time, so a
        fit run against provisional geometry (before the window
        is shown) never strands a column at a shrink floor: the
        show and tab-switch refits land on the same widths a live
        search produces. Score is capped near its content width
        ("100 Worth a look" plus padding); what is left after the
        fixed columns and Score is split between Title and
        Description, Description taking the larger share (T11:
        the space Score used to waste goes to the reading
        columns). At the default 1100x700 the table fits its
        viewport with no horizontal scrollbar and no clipped
        header, and the 1366x768 fit stays exact."""
        table = self.results_table
        available = table.viewport().width() or table.width()
        if available < 400:
            return  # not laid out yet; the caps in _show_results apply
        table.resizeColumnsToContents()
        if table.columnWidth(R_TITLE) > 260:
            table.setColumnWidth(R_TITLE, 260)
        if table.columnWidth(R_DESC) > 300:
            table.setColumnWidth(R_DESC, 300)
        if table.columnWidth(R_SCORE) > SCORE_COLUMN_MAX:
            table.setColumnWidth(R_SCORE, SCORE_COLUMN_MAX)
        fixed = sum(table.columnWidth(col)
                    for col in range(len(RESULT_COLUMNS))
                    if col not in (R_TITLE, R_DESC))
        budget = available - fixed
        if budget < 200:
            # Very narrow: Score gives up its padding too, and
            # Title and Description keep readable floors; below
            # this the table scrolls, as it always has at tiny
            # widths.
            if table.columnWidth(R_SCORE) > 104:
                table.setColumnWidth(R_SCORE, 104)
                budget = available - sum(
                    table.columnWidth(col)
                    for col in range(len(RESULT_COLUMNS))
                    if col not in (R_TITLE, R_DESC))
        title = min(260, max(84, int(budget * 0.42)))
        desc = budget - title
        if desc > 560 and title < 260:
            # A very wide window: Description stops growing at a
            # readable measure and Title takes the rest, up to
            # its cap; anything beyond that stays with
            # Description rather than Score.
            move = min(260 - title, desc - 560)
            title += move
            desc -= move
        table.setColumnWidth(R_TITLE, title)
        table.setColumnWidth(R_DESC, max(60, desc))

    def _saved_url_keys(self) -> set:
        """The normalised links already in My List (T37)."""
        return {core.normalize_url(item.get("url") or "")
                for item in self.my_list}

    def _refresh_result_markers(self):
        """Refresh the In My List markers in place (v0.1.6 T37):
        called whenever My List changes, so a row saved or
        removed updates its marker without a new search."""
        if not hasattr(self, "results_table"):
            return
        saved_keys = self._saved_url_keys()
        for row in range(self.results_table.rowCount()):
            cell = self.results_table.item(row, R_TITLE)
            if cell is None:
                continue
            stored = cell.data(ROW_ROLE)
            if not isinstance(stored, int) \
                    or not 0 <= stored < len(self.results):
                continue
            item = self.results[stored]
            title_text = item.get("title", "")
            if core.normalize_url(item.get("url") or "") in saved_keys:
                title_text = f"{title_text} (In My List)"
            if cell.text() != title_text:
                cell.setText(title_text)

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
        menu.addAction("Show details", self._show_result_detail)
        menu.addAction("Check STELLA-ready",
                       lambda: self.start_stella_check("results"))
        menu.exec(self.results_table.viewport().mapToGlobal(pos))

    def start_stella_check(self, target: str):
        """Check rows for STELLA-ready data files (T29).

        With nothing selected, check everything (T51): the link
        check already covers the whole list, and the old "Nothing
        selected" refusal hid the Ctrl+A workaround nobody knew.
        """
        if not self._require_online("Checking STELLA-ready"):
            return
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
            if target == "results":
                picked = list(enumerate(self.results))
            else:
                picked = list(enumerate(self.my_list))
        if not picked:
            self.statusBar().showMessage(
                "Nothing to check yet.")
            return
        self._stella_target = target
        self._set_busy(True)
        self._clear_finished()
        self.stella_check_button.setEnabled(False)
        self.list_stella_button.setEnabled(False)
        self.stop_stella_button.setEnabled(True)
        self.list_stop_stella_button.setEnabled(True)
        self._active_checks.add("stella")
        self._refresh_check_lock()
        # Per-item progress, mirroring the link check (v0.1.3 T18).
        self._stella_total = len(picked)
        self._stella_done_count = 0
        self.statusBar().showMessage(
            f"Checking 0 of {self._stella_total}...")
        self.stella_worker = StellaCheckWorker(
            picked, token=core.get_github_token(), parent=self)
        self.stella_worker.item_checked.connect(self._stella_checked)
        self.stella_worker.all_done.connect(self._stella_check_done)
        self.stella_worker.start()

    def stop_stella_check(self):
        """Stop the STELLA check after the dataset it is on (U26)."""
        if self.stella_worker is not None and self.stella_worker.isRunning():
            self.stella_worker.request_stop()
            self.stop_stella_button.setEnabled(False)
            self.list_stop_stella_button.setEnabled(False)

    def _stella_checked(self, url: str, status: str, detail: str,
                        files: list, total_size):
        # Matched by URL (T3): a result for an item that is gone is
        # dropped, never landed on whatever sits at that index now.
        if self._stella_target == "results":
            index = self._find_result_index(url)
            if index >= 0:
                item = self.results[index]
                item["stella_status"] = status
                item["stella_files"] = list(files)
                item["stella_size"] = total_size or 0
                self._update_result_stella_cell(index)
        else:
            index = self._find_list_index(url)
            if index >= 0:
                item = self.my_list[index]
                item["stella_status"] = status
                item["stella_files"] = list(files)
                item["stella_size"] = total_size or 0
                self._update_list_stella_cell(index)
        self._stella_done_count += 1
        self.statusBar().showMessage(
            f"Checking {self._stella_done_count} of "
            f"{self._stella_total}..."
        )

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
                # The score follows the badge (v0.1.4 T13): shape
                # is one of its four parts, so a stale score and
                # its "not checked yet" tooltip would contradict
                # the badge in the next column.
                self.results_table.setItem(row, R_SCORE,
                                           _score_cell(item))
                return

    def _stella_check_done(self, stopped: bool):
        self._set_busy(False)
        self.stella_check_button.setEnabled(True)
        self.list_stella_button.setEnabled(True)
        self.stop_stella_button.setEnabled(False)
        self.list_stop_stella_button.setEnabled(False)
        self._active_checks.discard("stella")
        self._refresh_check_lock()
        if self._stella_target == "list":
            self._save_list()
        self._refresh_rate()
        if stopped:
            self.statusBar().showMessage(
                f"Stopped, {self._stella_done_count} of "
                f"{self._stella_total} checked."
            )
            return
        # The completion moment (T57): count what the check
        # actually found (v0.1.4 T17). The worker holds the same
        # item dicts the check updated, so the counts are this
        # run's. Counting only "ready" read as failure when
        # everything was convertible, and hid the failures.
        picked = [item for _index, item in self.stella_worker.items] \
            if self.stella_worker is not None else []
        ready = sum(1 for item in picked
                    if item.get("stella_status") == "ready")
        convert = sum(1 for item in picked
                      if item.get("stella_status") == "convert")
        failed = sum(1 for item in picked
                     if item.get("stella_status") == "unknown")
        unusable = sum(1 for item in picked
                       if item.get("stella_status")
                       in ("none", "unsupported", "no-fields"))
        needs_reader = sum(1 for item in picked
                           if item.get("stella_status")
                           == "needs-reader")
        parts = [f"{ready} ready now", f"{convert} need conversion"]
        if failed:
            parts.append(f"{failed} could not be checked")
        if unusable:
            parts.append(f"{unusable} have no usable files")
        if needs_reader:
            parts.append(f"{needs_reader} need the Parquet reader")
        self._mark_finished(
            "STELLA check done: " + ", ".join(parts) + ".")

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
        self._save_list()
        self._refresh_list_tab()
        if added:
            core.log(f"Added {added} to My List.")
            self.statusBar().showMessage(f"Added {added} to My List.")
        else:
            # Same honest line as Add all (T37): "Added 0"
            # reports success for doing nothing.
            self.statusBar().showMessage(
                "Those links are already in My List.")

    def add_all_results(self):
        if not self.results:
            self.statusBar().showMessage(
                "No results to add yet. Run a search first.")
            return
        added = 0
        for item in self.results:
            self.my_list, was_added = core.add_to_list(self.my_list, item)
            added += 1 if was_added else 0
        self._save_list()
        self._refresh_list_tab()
        if added:
            core.log(f"Added {added} to My List.")
            self.statusBar().showMessage(f"Added {added} to My List.")
        else:
            # "Added 0" reports success for doing nothing (T37).
            self.statusBar().showMessage(
                "Those results are already in My List.")

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
            button.setToolTip(
                f"Open this search on {site['name']} in your browser.")
            button.clicked.connect(
                lambda _checked=False, s=site: self._open_site(s)
            )
            layout.addWidget(button)

    def _show_result_detail(self):
        """The detail view (T50, 051): the picked result's full
        description, files, sizes, and license, on demand."""
        chosen = self._selected_results()
        if not chosen:
            # The resting text also carries the two explainer
            # lines folded in from the tab (v0.1.6 T16).
            self.detail_view.setPlainText(
                "Click a row to see its full description, files, "
                "and license here. Searches GitHub and Hugging "
                "Face live, newest first; recent searches are kept "
                "in the search box drop-down. Active only keeps "
                "results updated recently. Stars counts GitHub "
                "stars and Hugging Face likes. A license filter "
                "hides results with no license shown.")
            return
        item = chosen[0]
        lines = [item.get("title") or "", "",
                 item.get("description") or "No description given.", "",
                 f"License: {item.get('license') or 'not shown'}"]
        lines.append(score.describe_score(score.score_item(item)))
        if item.get("format"):
            lines.append(f"Format: {item['format']}")
        files = item.get("stella_files") or []
        if files:
            # Cap the file list like the STELLA tooltip does (T41):
            # five files, one per line, then "and N more". The old
            # code joined ALL files on ONE line: 178,948 chars for
            # one dataset.
            if len(files) <= 5:
                lines.append("Files: " + ", ".join(files))
            else:
                lines.append("Files:")
                lines.extend(f"  {name}" for name in files[:5])
                rest = len(files) - 5
                word = "file" if rest == 1 else "files"
                lines.append(f"  and {rest} more {word}.")
            if item.get("stella_size"):
                lines.append(
                    f"Size: {core.format_size(item['stella_size'])} total")
        else:
            lines.append("Files: not checked yet. Press Check "
                         "STELLA-ready to see the files.")
        if item.get("updated"):
            lines.append(f"Updated: {core.format_date(item['updated'])}")
        # The link is clickable (T49): the pane renders HTML, so
        # the address opens in the browser instead of needing a
        # copy by hand. Everything else is escaped plain text.
        body = "<br>".join(html.escape(line) for line in lines)
        if item.get("url"):
            url = item["url"]
            shown = html.escape(url)
            body += (f"<br>Link: <a href=\"{html.escape(url, quote=True)}\">"
                     f"{shown}</a>")
        self.detail_view.setHtml(body)

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
        if not _looks_like_url(url):
            # The dialog blocks this too; this is the second net
            # (v0.1.4 T22): a link the check rejects is never added.
            self.statusBar().showMessage(
                "That does not look like a link. Check it.")
            return
        item = {
            "title": self.found_title.text().strip() or url,
            "url": url,
            "category": core.category_key_for_label(
                self.found_category.currentText()),
            # The source follows the URL (v0.1.4 T3): a pasted
            # Hugging Face or GitHub link is checked and
            # downloaded like a searched one, instead of being
            # stuck as "Found by hand" forever.
            "source": core.source_for_url(url),
            "stars": 0,
            "updated": "",
            "license": "",
            "notes": self.found_note.text().strip(),
        }
        self.my_list, added = core.add_to_list(self.my_list, item)
        self._save_list()
        self._refresh_list_tab()
        if added:
            core.log(f"Added a link to My List: {item['title']}")
            self.found_url.clear()
            self.found_title.clear()
            self.found_note.clear()
            self.statusBar().showMessage("Added to My List.")
        else:
            self.statusBar().showMessage("That link is already in My List.")

    # ------------------------------------------------------------ my list tab

    def _build_list_tab(self):
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        # A scroll area (T49): the tab holds together at small
        # heights instead of squeezing its rows.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.addWidget(QLabel(
            "Your saved links. Double-click a row to open it. "
            "Export when the list is ready for STELLA."
        ))
        format_note = QLabel(core.STELLA_FORMAT_NOTE_SHORT)
        format_note.setWordWrap(True)
        layout.addWidget(format_note)

        # Add a link I found, reachable from My List too (T60):
        # it used to live only on the Search tab, below the fold.
        add_link_row = QHBoxLayout()
        self.list_add_link_button = QPushButton("Add a link I found")
        self.list_add_link_button.setToolTip(
            "Add a link you found yourself to My List.")
        self.list_add_link_button.clicked.connect(self.show_add_link_dialog)
        add_link_row.addWidget(self.list_add_link_button)
        add_link_row.addStretch(1)
        layout.addLayout(add_link_row)

        # Collections (T30): named lists, one active at a time.
        collection_row = QHBoxLayout()
        collection_row.addWidget(QLabel("List:"))
        self.collection_combo = QComboBox()
        self.collection_combo.setToolTip(
            "Pick which named list to show.")
        self.collection_combo.currentTextChanged.connect(
            self._switch_collection)
        collection_row.addWidget(self.collection_combo, stretch=1)
        self.collection_new_button = QPushButton("New list")
        self.collection_new_button.setToolTip(
            "Start a new named list to keep links separate.")
        self.collection_new_button.clicked.connect(self.new_collection)
        collection_row.addWidget(self.collection_new_button)
        self.collection_rename_button = QPushButton("Rename")
        self.collection_rename_button.setToolTip(
            "Give the list showing now a different name.")
        self.collection_rename_button.clicked.connect(self.rename_collection)
        collection_row.addWidget(self.collection_rename_button)
        self.collection_delete_button = QPushButton("Delete list")
        self.collection_delete_button.setToolTip(
            "Delete the list showing now and all its links.")
        self.collection_delete_button.clicked.connect(self.delete_collection)
        collection_row.addWidget(self.collection_delete_button)
        layout.addLayout(collection_row)
        self._refresh_collection_combo()
        layout.addWidget(_explain(
            "Lists keep your links grouped. Switch lists, make a "
            "new one, rename it, or delete it."))

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Find in this list:"))
        self.list_filter_box = QLineEdit()
        self.list_filter_box.setPlaceholderText(
            "Type to filter by title or link")
        self.list_filter_box.setToolTip(
            "Type a few letters to keep only matching rows.")
        self.list_filter_box.textChanged.connect(self._apply_list_filter)
        filter_row.addWidget(self.list_filter_box, stretch=1)
        filter_row.addWidget(QLabel("State:"))
        self.state_filter_combo = QComboBox()
        self.state_filter_combo.setToolTip(
            "Keep only links in this state.")
        self.state_filter_combo.addItem("All states", "")
        for key, label in core.WORKFLOW_STATES.items():
            self.state_filter_combo.addItem(label, key)
        self.state_filter_combo.currentIndexChanged.connect(
            self._apply_list_filter)
        filter_row.addWidget(self.state_filter_combo)
        layout.addLayout(filter_row)
        layout.addWidget(_explain(
            "Type to narrow this list by title or link. State keeps "
            "only links in one step of the pipeline."))

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
        # No stretch-last-section (v0.1.6 T11): it handed every
        # spare pixel to Score. _fit_list_columns hands the space
        # to Title instead.
        self.list_table.horizontalHeader().setStretchLastSection(False)
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

        # The 14 buttons in three collapsible sections (T49, 012,
        # 049): grouped by job, so the tab reads as three choices
        # instead of one wall of buttons.
        row = QHBoxLayout()
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.setToolTip(
            "Remove the picked rows from My List. Delete does this too.")
        self.remove_button.clicked.connect(self.remove_selected)
        row.addWidget(self.remove_button)
        self.clear_button = QPushButton("Clear all")
        self.clear_button.setToolTip(
            "Remove every link from My List after asking first.")
        self.clear_button.clicked.connect(self.clear_list)
        row.addWidget(self.clear_button)
        self.undo_button = QPushButton("Undo remove")
        self.undo_button.setToolTip(
            "Bring back the rows removed most recently.")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self.undo_remove)
        row.addWidget(self.undo_button)
        self.check_button = QPushButton("Check links")
        self.check_button.setToolTip(
            "Visit every link in My List and mark working or dead.")
        self.check_button.clicked.connect(self.start_link_check)
        row.addWidget(self.check_button)
        self.stop_check_button = QPushButton("Stop")
        self.stop_check_button.setToolTip(
            "Stop the link check. Links checked so far stay marked.")
        self.stop_check_button.setEnabled(False)
        self.stop_check_button.clicked.connect(self.stop_link_check)
        row.addWidget(self.stop_check_button)
        self.remove_dead_button = QPushButton("Remove the dead ones")
        self.remove_dead_button.setToolTip(
            "Remove every row the link check marked dead.")
        self.remove_dead_button.setEnabled(False)
        self.remove_dead_button.clicked.connect(self.remove_dead)
        row.addWidget(self.remove_dead_button)
        self.copy_button = QPushButton("Copy all links")
        self.copy_button.setToolTip(
            "Copy every link in My List to the clipboard.")
        self.copy_button.clicked.connect(self.copy_all_links)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        self.remove_section = CollapsibleSection(
            "Remove and check", key="list-remove")
        self.remove_section.set_content_layout(row)
        layout.addWidget(self.remove_section)

        action_row = QHBoxLayout()
        self.edit_button = QPushButton("Edit selected")
        self.edit_button.setToolTip(
            "Change the title, category, notes, or state of one link.")
        self.edit_button.clicked.connect(self.edit_selected)
        action_row.addWidget(self.edit_button)
        self.undo_edit_button = QPushButton("Undo edit")
        self.undo_edit_button.setToolTip(
            "Undo the last change made in the Edit window.")
        self.undo_edit_button.setEnabled(False)
        self.undo_edit_button.clicked.connect(self.undo_edit)
        action_row.addWidget(self.undo_edit_button)
        self.list_stella_button = QPushButton("Check STELLA-ready")
        self.list_stella_button.setToolTip(
            "Check the picked rows, or every row when nothing is picked.")
        self.list_stella_button.clicked.connect(
            lambda: self.start_stella_check("list"))
        action_row.addWidget(self.list_stella_button)
        self.list_stop_stella_button = QPushButton("Stop")
        self.list_stop_stella_button.setToolTip(
            "Stop the STELLA check. Rows checked so far stay marked.")
        self.list_stop_stella_button.setEnabled(False)
        self.list_stop_stella_button.clicked.connect(self.stop_stella_check)
        action_row.addWidget(self.list_stop_stella_button)
        self.download_button = QPushButton("Download and convert")
        self.download_button.setToolTip(
            "Download the picked datasets and convert them for STELLA.")
        # The primary action of its section (v0.1.6 T30): the
        # gold fill the theme uses for the active choice, so it
        # stands out from the same-weight buttons around it.
        self.download_button.setStyleSheet(
            "background-color: #ffd166; color: #16233d; "
            "font-weight: bold; border-color: #ffd166;")
        self.download_button.clicked.connect(self.start_download)
        action_row.addWidget(self.download_button)
        self.add_to_queue_button = QPushButton("Add selected to queue")
        self.add_to_queue_button.setToolTip(
            "Queue the picked datasets to download later, in a batch.")
        self.add_to_queue_button.clicked.connect(self.add_selected_to_queue)
        action_row.addWidget(self.add_to_queue_button)
        self.queue_button = QPushButton("Download queue")
        self.queue_button.setToolTip(
            "Open the download queue: start, pause, and resume it.")
        self.queue_button.clicked.connect(self.show_queue)
        action_row.addWidget(self.queue_button)
        action_row.addStretch(1)
        self.edit_section = CollapsibleSection(
            "Edit and convert", key="list-edit", default_expanded=False)
        self.edit_section.set_content_layout(action_row)
        layout.addWidget(self.edit_section)

        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("Export My List as:"))
        self.export_md_button = QPushButton("Markdown list")
        self.export_md_button.setToolTip(
            "Save My List as a Markdown file you can open anywhere.")
        self.export_md_button.clicked.connect(
            lambda: self.export_list("md"))
        export_row.addWidget(self.export_md_button)
        self.export_json_button = QPushButton("JSON")
        self.export_json_button.setToolTip(
            "Save My List as a JSON file for other programs.")
        self.export_json_button.clicked.connect(
            lambda: self.export_list("json"))
        export_row.addWidget(self.export_json_button)
        self.export_stella_button = QPushButton("STELLA manifest")
        self.export_stella_button.setToolTip(
            "Save a STELLA manifest for this list. Ctrl+E does this too.")
        self.export_stella_button.clicked.connect(
            lambda: self.export_list("stella"))
        export_row.addWidget(self.export_stella_button)
        self.import_button = QPushButton("Import a list")
        self.import_button.setToolTip(
            "Read a list you exported back into My List.")
        self.import_button.clicked.connect(self.import_list)
        export_row.addWidget(self.import_button)
        self.send_button = QPushButton("Send to STELLA")
        self.send_button.setToolTip(
            "Write the STELLA manifest into the STELLA folder on this PC.")
        self.send_button.clicked.connect(self.send_to_stella)
        export_row.addWidget(self.send_button)
        self.open_converted_button = QPushButton(
            "Open the converted folder")
        self.open_converted_button.setToolTip(
            "Open the folder with the converted .jsonl files.")
        self.open_converted_button.clicked.connect(
            self.open_converted_folder)
        export_row.addWidget(self.open_converted_button)
        export_row.addStretch(1)
        self.export_section = CollapsibleSection(
            "Export", key="list-export", default_expanded=False)
        self.export_section.set_content_layout(export_row)
        layout.addWidget(self.export_section)
        layout.addWidget(_explain(
            "Export saves this list as a file. Import reads one "
            "back in. Send to STELLA writes the manifest where "
            "STELLA can find it."))

        scroll.setWidget(inner)
        tab_layout.addWidget(scroll)
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
                # Merge, do not overwrite (T13): links added on disk
                # since this window loaded (a CLI save) are kept,
                # while links this window removed stay removed.
                mine = {core.normalize_url(item.get("url") or "")
                        for item in self.my_list}
                merged = [core.normalize_item(item)
                          for item in self.my_list]
                for disk_item in col.get("items") or []:
                    if not isinstance(disk_item, dict):
                        continue
                    key = core.normalize_url(disk_item.get("url") or "")
                    if key and key not in mine \
                            and key not in self._list_known_urls:
                        merged.append(core.normalize_item(disk_item))
                col["items"] = merged

    def _switch_collection(self, name: str):
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            self._refresh_collection_combo()
            return
        if not name or name == self._active_collection:
            return
        _active, names = core.load_collections()
        if name not in names:
            return
        data = core.load_list_data()
        self._save_active_items(data)
        data["active"] = name
        self._save_collections(name, data["collections"])
        self._active_collection = name
        self.my_list = core.load_list()
        self._note_list_loaded()
        self._undo_stack.clear()
        self.undo_button.setEnabled(False)
        self._edit_undo_stack.clear()
        self.undo_edit_button.setEnabled(False)
        self._refresh_list_tab()
        self.statusBar().showMessage(f"Switched to {name}.")

    def new_collection(self):
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
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
        self._save_collections(name, data["collections"])
        self._active_collection = name
        self.my_list = []
        self._note_list_loaded()
        self._refresh_collection_combo()
        self._refresh_list_tab()
        self.statusBar().showMessage(f"Created the list {name}.")

    def rename_collection(self):
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
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
        # Save the active list BEFORE the rename (v0.1.3 T36): the
        # order _switch_collection already uses. Saving after
        # would match nothing, and in-memory changes would be lost.
        self._save_active_items(data)
        for col in data["collections"]:
            if col["name"] == self._active_collection:
                col["name"] = name
        data["active"] = name
        self._save_collections(name, data["collections"])
        self._active_collection = name
        self._refresh_collection_combo()
        self.statusBar().showMessage(f"Renamed the list to {name}.")

    def delete_collection(self):
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
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
            self.statusBar().showMessage(
                f"Kept the list {self._active_collection}.")
            return
        data = core.load_list_data()
        data["collections"] = [col for col in data["collections"]
                               if col["name"] != self._active_collection]
        new_active = data["collections"][0]["name"]
        data["active"] = new_active
        self._save_collections(new_active, data["collections"])
        self._active_collection = new_active
        self.my_list = core.load_list()
        self._note_list_loaded()
        self._undo_stack.clear()
        self.undo_button.setEnabled(False)
        self._edit_undo_stack.clear()
        self.undo_edit_button.setEnabled(False)
        self._refresh_collection_combo()
        self._refresh_list_tab()
        self.statusBar().showMessage(
            f"Deleted the list. Now showing {new_active}.")

    def move_selected_to_collection(self, name: str):
        """Move the selected rows to another list (T63).

        A link saved to the wrong list is no longer stuck there.
        The move is one save: the rows leave this list and join
        the other, skipping links the other list already holds.
        """
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
        if not name or name == self._active_collection:
            return
        _active, names = core.load_collections()
        if name not in names:
            return
        rows = self._selected_list_rows()
        if not rows:
            self.statusBar().showMessage("Nothing selected to move.")
            return
        moved = [self.my_list[row] for row in rows]
        keep = {row for row in rows}
        self.my_list = [item for index, item in enumerate(self.my_list)
                        if index not in keep]
        data = core.load_list_data()
        # Save the reduced list first (the _switch_collection
        # order): _save_active_items merges in anything added on
        # disk since this window loaded.
        self._save_active_items(data)
        added = 0
        for col in data["collections"]:
            if col["name"] != name:
                continue
            target = col.get("items") or []
            keys = {core.normalize_url(entry.get("url") or "")
                    for entry in target if isinstance(entry, dict)}
            for item in moved:
                key = core.normalize_url(item.get("url") or "")
                if key and key not in keys:
                    target.append(core.normalize_item(item))
                    keys.add(key)
                    added += 1
            col["items"] = target
        if not self._save_collections(self._active_collection,
                                      data["collections"]):
            # The save failed: put the rows back where they were
            # instead of losing them.
            for row, item in sorted(zip(rows, moved)):
                self.my_list.insert(min(row, len(self.my_list)), item)
            self._refresh_list_tab()
            return
        self._refresh_list_tab()
        word = "link" if added == 1 else "links"
        if added < len(moved):
            skipped = len(moved) - added
            self.statusBar().showMessage(
                f"Moved {added} {word} to {name}. {skipped} "
                f"{'was' if skipped == 1 else 'were'} already there.")
        else:
            self.statusBar().showMessage(f"Moved {added} {word} to {name}.")

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
            # v0.1.3 T37: the Updated column sorts by the parsed
            # time, so a timezone offset cannot misorder the rows.
            parsed = core.parse_iso_date(item.get("updated"))
            table.setItem(row, L_UPDATED,
                          _cell(core.format_date(item.get("updated")),
                                sort_value=(parsed.timestamp()
                                            if parsed is not None
                                            else 0.0)))
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
            # The quality score (T57): computed from the same fields
            # the search table scores, so it stays in step with the
            # result the link came from, and with the STELLA check.
            table.setItem(row, L_SCORE, _score_cell(item))
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
        # One long title did the same (T32): cap Title the way
        # Link already is, and Notes with it.
        if table.columnWidth(L_TITLE) > 320:
            table.setColumnWidth(L_TITLE, 320)
        if table.columnWidth(L_NOTES) > 200:
            table.setColumnWidth(L_NOTES, 200)
        self._fit_list_columns()
        self.tabs.setTabText(
            self.list_tab_index, f"My List ({len(self.my_list)})"
        )
        self.list_empty_label.setVisible(len(self.my_list) == 0)
        self._refresh_result_markers()
        self._refresh_dead_button()
        self._apply_list_filter()
        self._refresh_stats()
        self._refresh_checklist()
        self._sync_undo_actions()

    def _fit_list_columns(self):
        """Fit the My List columns inside the table's width
        (v0.1.6 T11): Score is capped near its content width,
        overflow comes out of Notes, then Link, then Title, and
        spare width goes to Title first (up to its 320 px cap),
        then Link and Notes (their existing caps), so the freed
        Score space lands on the column that truncates dataset
        names today."""
        table = self.list_table
        available = table.viewport().width() or table.width()
        if available < 400:
            return  # not laid out yet; the caps above still apply
        # Recompute from content, like the results fit (T10/T11):
        # a fit against provisional geometry must not strand a
        # column at a shrink floor once the window is shown.
        table.resizeColumnsToContents()
        if table.columnWidth(L_LINK) > 320:
            table.setColumnWidth(L_LINK, 320)
        if table.columnWidth(L_TITLE) > 320:
            table.setColumnWidth(L_TITLE, 320)
        if table.columnWidth(L_NOTES) > 200:
            table.setColumnWidth(L_NOTES, 200)
        if table.columnWidth(L_SCORE) > SCORE_COLUMN_MAX:
            table.setColumnWidth(L_SCORE, SCORE_COLUMN_MAX)
        total = sum(table.columnWidth(col)
                    for col in range(len(LIST_COLUMNS)))
        over = total - available
        for col, floor in ((L_NOTES, 80), (L_LINK, 180),
                           (L_TITLE, 150), (L_NOTES, 56),
                           (L_LINK, 140), (L_TITLE, 110)):
            if over <= 0:
                break
            give = min(over, table.columnWidth(col) - floor)
            if give > 0:
                table.setColumnWidth(col, table.columnWidth(col) - give)
                over -= give
        if over < 0:
            slack = -over
            for col, cap in ((L_TITLE, 320), (L_LINK, 320),
                             (L_NOTES, 200)):
                if slack <= 0:
                    break
                give = min(slack, cap - table.columnWidth(col))
                if give > 0:
                    table.setColumnWidth(
                        col, table.columnWidth(col) + give)
                    slack -= give

    def _refresh_dead_button(self):
        """Enable "Remove the dead ones" only when the check has
        actually marked rows dead (T62), and no check is running."""
        if not hasattr(self, "remove_dead_button"):
            return
        self.remove_dead_button.setEnabled(
            any(item.get("status") == "dead" for item in self.my_list)
            and not self._check_running())

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
        menu = self._build_list_context_menu(rows)
        menu.exec(self.list_table.viewport().mapToGlobal(pos))

    def _build_list_context_menu(self, rows: list[int]) -> QMenu:
        """The right-click menu for the selected list rows.

        Built separately from showing it, so the entries can be
        checked without opening a modal menu.
        """
        menu = QMenu(self)
        menu.addAction("Open in browser", self._open_list_item)
        menu.addAction("Edit", self.edit_selected)
        menu.addAction("Copy link", self._copy_selected_link)
        mark = menu.addMenu("Mark as")
        for key, label in core.WORKFLOW_STATES.items():
            mark.addAction(
                label, lambda _checked=False, k=key: self._mark_state(k))
        # A way back to the converted files (T56): only when this
        # row's conversion folder holds .jsonl files.
        if len(rows) == 1:
            item = self.my_list[rows[0]]
            files = self._converted_files(item)
            if files:
                if len(files) == 1:
                    menu.addAction(
                        "Open converted file",
                        lambda _checked=False, p=files[0]:
                            self._open_converted_file(p))
                else:
                    open_menu = menu.addMenu("Open converted file")
                    for path in files:
                        open_menu.addAction(
                            path.name,
                            lambda _checked=False, p=path:
                                self._open_converted_file(p))
                menu.addAction(
                    "Open converted folder",
                    lambda _checked=False, it=item:
                        self._open_converted_folder(it))
        # Move between collections (T63): only when another list
        # exists to move to.
        _active, names = core.load_collections()
        others = [name for name in names
                  if name != self._active_collection]
        if others:
            move = menu.addMenu("Move to list")
            for name in others:
                move.addAction(
                    name, lambda _checked=False, n=name:
                        self.move_selected_to_collection(n))
        menu.addAction("Move up", lambda: self._move_selected(-1))
        menu.addAction("Move down", lambda: self._move_selected(1))
        menu.addAction("Remove", self.remove_selected)
        return menu

    def _converted_folder(self, item: dict) -> Path:
        """Where this item's converted files live (T56).

        The same folder download_and_convert writes into, derived
        from the title, source, and URL, so it needs no stored path.
        """
        return (core.home_dir() / "stella-ready"
                / download.dataset_folder_name(item))

    def _converted_files(self, item: dict) -> list[Path]:
        """The converted .jsonl files for one item, if any (T56)."""
        folder = self._converted_folder(item)
        if not folder.is_dir():
            return []
        return sorted(folder.glob("*.jsonl"))

    def _open_converted_file(self, path: Path):
        """Open one converted .jsonl with the system's viewer."""
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.statusBar().showMessage(
                "That file is gone. Convert the dataset again to "
                "make it.")

    def _open_converted_folder(self, item: dict):
        """Open the folder with an item's converted files (T56)."""
        folder = self._converted_folder(item)
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        else:
            self.statusBar().showMessage(
                "There are no converted files for that link yet.")

    def open_converted_folder(self):
        """Open the converted-files folder itself (v0.1.6 T39):
        one button on My List, instead of depending on the
        Download window still being open."""
        folder = _download_dir()
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        else:
            self.statusBar().showMessage(
                "There is no converted folder yet. Download and "
                "convert a dataset first.")

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
            self._save_list()
            self._refresh_list_tab()
            label = core.WORKFLOW_STATES.get(state, state)
            self.statusBar().showMessage(f"Marked as {label}.")

    def _move_selected(self, delta: int):
        """Move the selected rows up or down (T28, library 027:
        dragging is never the only path to reorder)."""
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
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
        self._save_list()
        self._list_sorting = False
        self._refresh_list_tab()
        for row in rows:
            self.list_table.selectRow(row + delta)

    def _list_rows_moved(self, _parent, _start, _end, _dest, _row):
        """A drag finished: save the new row order (T28)."""
        if self._check_running():
            # A drag cannot move rows under a running check; put
            # the table back the way the list really is.
            self._refresh_list_tab()
            return
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
            self._save_list()
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
                # The score follows the STELLA check too (T57):
                # shape is one of its four parts.
                self.list_table.setItem(row, L_SCORE, _score_cell(item))
                return

    def edit_selected(self):
        """Edit the selected link in place (T12)."""
        rows = self._selected_list_rows()
        if not rows:
            self.statusBar().showMessage("Nothing selected to edit.")
            return
        item = self.my_list[rows[0]]
        taken = [other.get("url") or ""
                 for index, other in enumerate(self.my_list)
                 if index != rows[0]]
        dialog = EditItemDialog(item, self, taken_urls=taken)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        # Undo for edits (T59): the item as it was, before the
        # change lands. Only a real change is undoable.
        before = copy.deepcopy(item)
        if values["title"]:
            item["title"] = values["title"]
        # A fixed link (T58): the same https:// repair the add
        # form applies, so a bare host stays a working link.
        new_url = (values.get("url") or "").strip()
        if new_url and "://" not in new_url:
            new_url = "https://" + new_url
        if new_url:
            item["url"] = new_url
        item["category"] = values["category"]
        item["notes"] = values["notes"]
        item["state"] = values["state"]
        if item != before:
            self._edit_undo_stack.append(
                (before, core.normalize_url(item.get("url") or "")))
            self.undo_edit_button.setEnabled(True)
            core.log(f"Edited a link in My List: {item.get('title')}")
        self._save_list()
        self._refresh_list_tab()
        self.statusBar().showMessage("Saved the changes.")

    def undo_edit(self):
        """Undo the last edit: title, link, category, notes, or
        state goes back to what it was (T59)."""
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
        if not self._edit_undo_stack:
            # Invoked with nothing to undo (v0.1.6 T13): say so
            # instead of staying silent.
            self.statusBar().showMessage("Nothing to undo.")
            return
        before, after_url = self._edit_undo_stack.pop()
        self.undo_edit_button.setEnabled(bool(self._edit_undo_stack))
        # Find the edited row again by its after-edit link: the
        # list may have moved since the edit. No position fallback:
        # another row may sit in the old slot now, and undo must
        # never overwrite the wrong link.
        index = self._find_list_index(after_url) if after_url else -1
        if index < 0:
            self.statusBar().showMessage(
                "That link is gone. Nothing to undo.")
            return
        self.my_list[index] = before
        self._save_list()
        self._refresh_list_tab()
        self.statusBar().showMessage("Edit undone.")

    def remove_selected(self):
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
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
            # Say what was kept (T35): leaving the previous
            # message would describe something the user did not do.
            self.statusBar().showMessage(
                f"Kept {len(rows)} {word} in My List.")
            return
        removed = [(row, self.my_list[row]) for row in rows]
        self._undo_stack.append(removed)
        self.undo_button.setEnabled(True)
        keep = {row for row in rows}
        self.my_list = [item for index, item in enumerate(self.my_list)
                        if index not in keep]
        self._save_list()
        self._refresh_list_tab()
        core.log(f"Removed {len(rows)} from My List.")
        self.statusBar().showMessage(f"Removed {len(rows)} from My List.")

    def undo_remove(self):
        """Bring back the last removed rows, where they were (T18)."""
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
        if not self._undo_stack:
            # Invoked with nothing to undo (v0.1.6 T13): say so
            # instead of staying silent.
            self.statusBar().showMessage("Nothing to undo.")
            return
        removed = self._undo_stack.pop()
        for row, item in sorted(removed):
            self.my_list.insert(min(row, len(self.my_list)), item)
        self.undo_button.setEnabled(bool(self._undo_stack))
        self._save_list()
        self._refresh_list_tab()
        self.statusBar().showMessage("Removal undone.")

    def clear_list(self):
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
        if not self.my_list:
            # An enabled button that silently does nothing looks
            # broken (T34): say so, like every other empty-state
            # button does.
            self.statusBar().showMessage(
                "My List is empty. Nothing to clear.")
            return
        answer = QMessageBox.question(
            self, "Clear My List",
            "Remove every link from My List?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            count = len(self.my_list)
            word = "link" if count == 1 else "links"
            self.statusBar().showMessage(
                f"Kept My List as it is ({count} {word}).")
            return
        cleared = len(self.my_list)
        self._undo_stack.append(
            [(row, item) for row, item in enumerate(self.my_list)])
        self.undo_button.setEnabled(True)
        self.my_list = []
        self._save_list()
        self._refresh_list_tab()
        core.log(f"Cleared My List: {cleared} removed.")
        self.statusBar().showMessage("My List is empty now.")

    def remove_dead(self):
        """Remove every row the link check marked dead (T62).

        The obvious next step after "2 dead": one action instead
        of picking the rows one by one. Undo remove brings them
        back, like any other removal.
        """
        if self._check_running():
            self.statusBar().showMessage(
                "Wait for the check to finish first.")
            return
        rows = [index for index, item in enumerate(self.my_list)
                if item.get("status") == "dead"]
        if not rows:
            self.statusBar().showMessage(
                "No dead links in My List.")
            return
        word = "link" if len(rows) == 1 else "links"
        answer = QMessageBox.question(
            self, "Remove the dead ones",
            f"Remove {len(rows)} dead {word} from My List?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.statusBar().showMessage(
                f"Kept {len(rows)} dead {word} in My List.")
            return
        removed = [(row, self.my_list[row]) for row in rows]
        self._undo_stack.append(removed)
        self.undo_button.setEnabled(True)
        keep = {row for row in rows}
        self.my_list = [item for index, item in enumerate(self.my_list)
                        if index not in keep]
        self._save_list()
        self._refresh_list_tab()
        core.log(f"Removed {len(rows)} dead {word} from My List.")
        self.statusBar().showMessage(
            f"Removed {len(rows)} dead {word} from My List.")

    def start_link_check(self):
        if not self.my_list:
            self.statusBar().showMessage("My List is empty. Nothing to check.")
            return
        if not self._require_online("Checking links"):
            return
        if self.check_worker is not None and self.check_worker.isRunning():
            return
        for index, item in enumerate(self.my_list):
            item["status"] = "checking"
            self._update_list_row_status(index, "checking")
        self.check_button.setEnabled(False)
        self.stop_check_button.setEnabled(True)
        self._set_busy(True)
        self._clear_finished()
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
        self._active_checks.add("link")
        self._refresh_check_lock()
        self.check_worker.start()

    def stop_link_check(self):
        """Stop the link check at once (U26, v0.1.4 T27)."""
        if self.check_worker is not None and self.check_worker.isRunning():
            self.check_worker.request_stop()
            self.stop_check_button.setEnabled(False)

    def _link_checked(self, url: str, status: str):
        # Matched by URL (T3): a result for an item that is gone is
        # dropped, never landed on whatever sits at that index now.
        index = self._find_list_index(url)
        if index >= 0:
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
        self._active_checks.discard("link")
        self._refresh_check_lock()
        if stopped:
            # Links never reached go back to unmarked: the check is
            # not running any more, so "checking" would be a lie.
            for index, item in enumerate(self.my_list):
                if item.get("status") == "checking":
                    item["status"] = ""
                    self._update_list_row_status(index, "")
        self._save_list()
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
        self._mark_finished(
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
            # Atomic (v0.1.3 T21): a crash mid-write never leaves a
            # half-written export behind.
            core.write_text_atomic(Path(path), text)
        except OSError as exc:
            core.log_error(f"Export failed: {exc}")
            QMessageBox.warning(self, "Export failed",
                                _plain_write_error(exc))
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
            # utf-8-sig strips a byte-order mark (v0.1.3 T35).
            text = Path(path).read_text(encoding="utf-8-sig")
            items = core.import_file_text(text)
        except (OSError, ValueError) as exc:
            core.log_error(f"Import failed: {exc}")
            QMessageBox.warning(self, "Import failed",
                                _plain_read_error(exc))
            return
        added = 0
        for item in items:
            self.my_list, was_added = core.add_to_list(self.my_list, item)
            added += 1 if was_added else 0
        self._save_list()
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
            # Atomic (v0.1.3 T21): a crash mid-write never leaves a
            # half-written manifest behind.
            core.write_text_atomic(
                path, core.export_stella_manifest(self.my_list))
        except OSError as exc:
            core.log_error(f"Send to STELLA failed: {exc}")
            QMessageBox.warning(
                self, "Send to STELLA failed",
                _plain_write_error(exc))
            return
        core.log(f"Sent {len(self.my_list)} items to STELLA at {path}")
        self.statusBar().showMessage(
            f"Sent {len(self.my_list)} links to STELLA.")

    def start_download(self):
        """Download selected datasets and convert them (T35)."""
        if not self._require_online("Downloading"):
            return
        rows = self._selected_list_rows()
        if not rows:
            self.statusBar().showMessage(
                "Nothing selected. Click a row first.")
            return
        items = [(row, self.my_list[row]) for row in rows]
        out_dir = _download_dir()
        self._clear_finished()
        dialog = DownloadDialog(items, out_dir, parent=self)
        core.log(f"Download and convert started: {len(items)} "
                 "dataset(s).")
        dialog.start()
        dialog.exec()
        self._save_list()
        self._refresh_list_tab()
        # The finish line matches what actually converted
        # (v0.1.6 T6): the dialog has been honest since v0.1.4
        # T16, and the main window must not contradict it the
        # moment it closes. Zero converted gets the dialog's own
        # honest result, with no checkmark and no folder promise;
        # some converted names the count.
        converted = dialog._done_ok
        if converted == 0:
            self._clear_finished()
            self.statusBar().showMessage(
                "Nothing converted. See the download window for why.")
            core.log("Download and convert finished: nothing "
                     "converted.")
        else:
            word = "dataset" if converted == 1 else "datasets"
            self._mark_finished(
                f"Download and convert finished: {converted} "
                f"{word} converted, {dialog._done_rows} rows kept. "
                "See the folder for files.")
            core.log(f"Download and convert finished: {converted} "
                     f"dataset(s) converted, {dialog._done_rows} "
                     "rows kept.")

    # ------------------------------------------------------------ offline, watch, queue (T63, T66, T67)

    def start_background_checks(self):
        """Start the background work main() owns: the online probe,
        then due watches, plus the silent startup update check.
        Called once, after the window shows."""
        self._check_online()
        self._watch_timer.start()
        # Silent startup update check (v0.1.5 U1): off the UI
        # thread, after the window shows, so launch is never
        # blocked. Silent when up to date or unreachable; only
        # a newer release speaks, with the popup (U2).
        self.check_for_updates(silent=True)

    def _check_online(self):
        """Probe the internet in the background (T67)."""
        if self._online_worker is not None \
                and self._online_worker.isRunning():
            return
        self._online_worker = OnlineCheckWorker(self)
        self._online_worker.finished.connect(self._on_online_checked)
        self._online_worker.start()

    def _on_online_checked(self, online: bool):
        self._set_online(online)
        if online:
            self._run_due_watches()

    def _set_online(self, online: bool):
        """Record the online state and show or hide the note."""
        self._online = online
        self.offline_banner.setVisible(not online)
        if not online:
            core.log("Offline: the internet did not answer.",
                     level="WARNING")

    def _require_online(self, action: str) -> bool:
        """True when the internet is up (or not yet checked). When
        it is known to be down, say so in plain words and False."""
        if self._online is False:
            self.statusBar().showMessage(
                f"You are offline. {action} needs the internet.")
            return False
        return True

    # ------------------------------------------------------------ watch (T63)

    def watch_current_search(self):
        """Save the search in the box as a watch (opt-in)."""
        typed = self.search_box.currentText().strip()
        if not typed:
            # An empty box watches nothing (v0.1.4 T5): it used to
            # silently create a persistent watch for the generic
            # word "dataset", re-running every 30 minutes.
            self.statusBar().showMessage("Type a few words first.")
            return
        category = self.selected_category()
        query = core.build_query(typed, category)
        watches = watch.load_watches()
        watches, added = watch.add_watch(
            watches, watch.new_watch(query, category))
        if not added:
            self.statusBar().showMessage(
                "That search is already watched.")
            return
        watch.save_watches(watches)
        core.log(f"Watch created: {query!r} (category {category}).")
        self.statusBar().showMessage(
            f"Watching {query!r}. Scout will check for new datasets "
            "while the app is open.")

    def show_watches(self):
        """Open the watched searches window."""
        if self._watch_dialog is None:
            self._watch_dialog = WatchDialog(self)
            self._watch_dialog.finished.connect(self._watches_closed)
        else:
            self._watch_dialog.refresh()
        self._watch_dialog.show()
        self._watch_dialog.raise_()
        self._watch_dialog.activateWindow()

    def _watches_closed(self, _result=None):
        self._watch_dialog = None

    def _run_due_watches(self, force: bool = False):
        """Re-run watches that are due (T63).

        Only while the app is open, and only watches the user
        opted in. With force, every enabled watch runs now (the
        Watch window's Check now button). Nothing runs offline.
        """
        if self.watch_worker is not None and self.watch_worker.isRunning():
            return
        if self._online is False:
            # A forced run is a button press (T46): silence reads
            # as a dead button, so say the run is waiting.
            if force:
                self.statusBar().showMessage(
                    "You are offline. Watched searches wait until "
                    "you are back online.")
            return
        watches = watch.load_watches()
        if force:
            due = [item for item in watches
                   if item.get("enabled", True)]
        else:
            due = watch.due_watches(watches)
        if not due:
            if force:
                self.statusBar().showMessage(
                    "No watched searches to check right now.")
            return
        import copy

        self.watch_worker = WatchWorker(
            copy.deepcopy(due), token=core.get_github_token(),
            parent=self)
        self.watch_worker.watch_finished.connect(self._on_watch_finished)
        self.watch_worker.all_done.connect(self._on_watches_done)
        self.watch_worker.start()

    def _on_watch_finished(self, finished_watch: dict, new_items: list,
                           failures: list):
        """Merge one finished watch back into the store."""
        watches = watch.load_watches()
        stored = watch.find_watch(watches, finished_watch.get("id", ""))
        if stored is None:
            return
        if failures and len(failures) == 2:
            # Both sources failed: the baseline is untouched, so
            # the watch stays due and tries again later.
            core.log(f"Watch {finished_watch.get('query')!r} could not "
                     "run: both sources failed.", level="WARNING")
            return
        first_run = not stored.get("last_run")
        stored.update(finished_watch)
        watch.save_watches(watches)
        # The run's outcome is logged (v0.1.6 T14), new or not.
        core.log(f"Watch {finished_watch.get('query')!r} ran: "
                 f"{len(new_items)} new dataset(s).")
        # The outcome also lands in the main window (v0.1.6 T21),
        # not only inside the watch dialog.
        if first_run:
            count = finished_watch.get("baseline_count")
            if count is None:
                count = len(finished_watch.get("last_seen") or [])
            self.statusBar().showMessage(
                f"Watch started: {count} results saved as the "
                "starting point. New ones will show here.")
        elif new_items:
            count = len(new_items)
            word = "dataset" if count == 1 else "datasets"
            self.statusBar().showMessage(
                f"Watch {finished_watch.get('query')!r} found {count} "
                f"new {word}. See Watched searches in the File menu.")
        else:
            self.statusBar().showMessage(
                f"Watch {finished_watch.get('query')!r} ran: "
                "nothing new.")

    def _on_watches_done(self):
        if self._watch_dialog is not None:
            self._watch_dialog.refresh()

    # ------------------------------------------------------------ queue (T66)

    def add_selected_to_queue(self):
        """Queue the picked My List rows for bulk download."""
        rows = self._selected_list_rows()
        if not rows:
            self.statusBar().showMessage(
                "Nothing selected. Click a row first.")
            return
        entries, _interrupted = dlqueue.load_queue()
        added = 0
        for row in rows:
            entries, was_added = dlqueue.enqueue(
                entries, self.my_list[row])
            added += 1 if was_added else 0
        dlqueue.save_queue(entries)
        if added:
            word = "dataset" if added == 1 else "datasets"
            core.log(f"Added {added} {word} to the download queue.")
            self.statusBar().showMessage(
                f"Added {added} {word} to the download queue.")
        else:
            self.statusBar().showMessage(
                "Those datasets are already in the queue.")
        if self._queue_dialog is not None:
            self._queue_dialog.refresh()

    def show_queue(self):
        """Open the download queue window."""
        if self._queue_dialog is None:
            self._queue_dialog = QueueDialog(self)
            self._queue_dialog.finished.connect(self._queue_closed)
        else:
            self._queue_dialog.refresh()
        self._queue_dialog.show()
        self._queue_dialog.raise_()
        self._queue_dialog.activateWindow()

    def _queue_closed(self, _result=None):
        self._queue_dialog = None

    def show_history(self):
        """Open the download history window (T61)."""
        if self._history_dialog is None:
            self._history_dialog = HistoryDialog(self)
            self._history_dialog.finished.connect(self._history_closed)
        else:
            self._history_dialog.refresh()
        self._history_dialog.show()
        self._history_dialog.raise_()
        self._history_dialog.activateWindow()

    def _history_closed(self, _result=None):
        self._history_dialog = None

    def show_selftest(self):
        """Open the self-test window and run it (T64)."""
        if self._selftest_dialog is None:
            self._selftest_dialog = SelfTestDialog(self)
            self._selftest_dialog.finished.connect(self._selftest_closed)
        self._selftest_dialog.show()
        self._selftest_dialog.raise_()
        self._selftest_dialog.activateWindow()
        self._selftest_dialog.start_test()

    def _selftest_closed(self, _result=None):
        self._selftest_dialog = None

    # ------------------------------------------------------------ local convert (T67)

    def convert_local_file(self):
        """Convert a dataset file already on this PC (T67).

        No network needed: the file is read from disk and the
        STELLA .jsonl is written into the stella-ready folder.
        """
        path, _selected = QFileDialog.getOpenFileName(
            self, "Convert a file on this PC", "",
            "Dataset files (*.jsonl *.json *.csv *.tsv *.parquet);;"
            "All files (*)")
        if not path:
            return
        source = Path(path)
        out_dir = core.home_dir() / "stella-ready"
        # Folder-qualified output names (v0.1.4 T1), the same as
        # the download path: folder-a/train.jsonl and
        # folder-b/train.jsonl become folder-a-train.jsonl and
        # folder-b-train.jsonl, so the second convert never
        # destroys the first file's rows.
        display = (f"{source.parent.name}/{source.name}"
                   if source.parent.name else source.name)
        out_path = out_dir / download.output_name(display)
        try:
            ok, message, rows = download._convert_file(
                source, out_path, display)
        except Exception as exc:  # noqa: BLE001 - a bad file is a message, not a crash
            core.log_error(f"Local convert failed: {exc}")
            QMessageBox.warning(self, "Convert failed",
                                f"Could not convert that file: {exc}")
            return
        if not ok:
            QMessageBox.warning(self, "Convert failed", message)
            return
        core.log(f"Converted a local file: {source} -> {out_path}")
        # The download history (T61): a local convert counts too.
        try:
            history.record_conversion(
                source.name, "", "File on this PC", rows, [out_path])
        except OSError as exc:
            core.log_error(f"Could not record the download: {exc}")
        self.statusBar().showMessage(
            f"Converted {display}: {rows} rows in {out_path.name}.")
        if core.stella_home() is not None:
            answer = QMessageBox.question(
                self, "Add it to a STELLA project?",
                f"{out_path.name} is converted. Add it to a STELLA "
                "project now, ready to train on?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                StellaLinkDialog([str(out_path)], parent=self).exec()

    # ------------------------------------------------------------ sources tab

    def _build_sources_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(QLabel(
            "Websites to search on. Each one opens the same search "
            "in your browser from the Search tab."
        ))
        # Empty state (T46, 031): guidance when every site is gone.
        # Above the table (T54): the guidance is the useful part,
        # and it used to sit below a large dead box.
        self.sources_empty_label = QLabel(
            "No websites here. Add one below, or restore the defaults.")
        self.sources_empty_label.setStyleSheet("color: #9fb0cc;")
        self.sources_empty_label.setWordWrap(True)
        layout.addWidget(self.sources_empty_label)
        self.sources_table = QTableWidget(0, 4)
        self.sources_table.setHorizontalHeaderLabels(
            ["Website", "Address", "Remove", "Edit"])
        self.sources_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.sources_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        # The Remove column is sized to its button (T39): the old
        # stretchLastSection gave it ~60% of the table while the
        # addresses the user needs to read were squeezed.
        header = self.sources_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        # Double-click a row to edit it (v0.1.6 T34).
        self.sources_table.doubleClicked.connect(
            self._source_double_clicked)
        # No stretch (v0.1.6 T28): the table takes the height its
        # rows need (refresh caps it), and the space it used to
        # reserve goes to the rest of the tab.
        layout.addWidget(self.sources_table)
        layout.addStretch(1)
        # The website being edited in the form, if any (T34).
        self._editing_site = None

        add_box = QGroupBox("Add a website")
        add_layout = QFormLayout(add_box)
        self.site_name = QLineEdit()
        self.site_name.setPlaceholderText("Example: Kaggle Datasets")
        self.site_name.editingFinished.connect(self._validate_site_name)
        self.site_name.textChanged.connect(
            lambda: self._clear_error_when_fixed(
                self.site_name, self.site_name_error,
                lambda text: bool(text.strip())))
        add_layout.addRow("Name:", self.site_name)
        self.site_name_error = _inline_error()
        add_layout.addRow("", self.site_name_error)
        self.site_url = QLineEdit()
        self.site_url.setPlaceholderText(
            "https://example.com/search?q={q} ({q} is where your search words go)"
        )
        self.site_url.editingFinished.connect(self._validate_site_url)
        self.site_url.textChanged.connect(
            lambda: self._clear_error_when_fixed(
                self.site_url, self.site_url_error, _looks_like_url))
        add_layout.addRow("Address:", self.site_url)
        self.site_url_error = _inline_error()
        add_layout.addRow("", self.site_url_error)
        self.site_add_button = QPushButton("Add website")
        self.site_add_button.setToolTip(
            "Add this website to the Search tab buttons.")
        self.site_add_button.clicked.connect(self.add_source)
        add_layout.addRow("", self.site_add_button)
        add_layout.addRow("", _explain(
            "The address can hold {q} where your search words go, "
            "or be a plain address."))
        layout.addWidget(add_box)

        defaults_row = QHBoxLayout()
        self.restore_button = QPushButton("Restore default websites")
        self.restore_button.setToolTip(
            "Bring back the websites Dataset Scout started with.")
        self.restore_button.clicked.connect(self.restore_sources)
        defaults_row.addWidget(self.restore_button)
        self.undo_site_button = QPushButton("Undo remove")
        self.undo_site_button.setToolTip(
            "Bring back the website removed most recently.")
        self.undo_site_button.setEnabled(False)
        self.undo_site_button.clicked.connect(self.undo_remove_source)
        defaults_row.addWidget(self.undo_site_button)
        defaults_row.addStretch(1)
        layout.addLayout(defaults_row)

        token_box = QGroupBox("GitHub token (optional)")
        token_layout = QFormLayout(token_box)
        self.token_field = QLineEdit()
        self.token_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_field.setText(core.get_github_token())
        self.token_field.setToolTip(
            "Paste a GitHub token to raise the search limit. Optional.")
        self.token_field.editingFinished.connect(self._validate_token)
        token_layout.addRow("Token:", self.token_field)
        self.token_error = _inline_error()
        token_layout.addRow("", self.token_error)
        token_layout.addRow("", QLabel(
            "Stored on this PC only. Without one, GitHub allows "
            "fewer searches per minute."
        ))
        token_layout.addRow("", QLabel(
            "New to tokens? You make one on GitHub: sign in there, "
            "create a token on the token page, then paste it here."
        ))
        self.token_page_button = QPushButton("Open GitHub token page")
        self.token_page_button.setToolTip(
            "Open the GitHub token settings in your browser, where "
            "you can make a token.")
        self.token_page_button.clicked.connect(self._open_token_page)
        token_layout.addRow("", self.token_page_button)
        self.token_save_button = QPushButton("Save token")
        self.token_save_button.setToolTip(
            "Save this token on this PC for more GitHub searches.")
        self.token_save_button.clicked.connect(self.save_token)
        token_layout.addRow("", self.token_save_button)
        layout.addWidget(token_box)

        debug_row = QHBoxLayout()
        self.debug_button = QPushButton("Copy debug report")
        self.debug_button.setToolTip(
            "Copy the recent log, with any token removed, for help.")
        self.debug_button.clicked.connect(self.copy_debug_report)
        debug_row.addWidget(self.debug_button)
        debug_row.addWidget(QLabel(
            "Copies the recent log with any token removed, for help."
        ))
        debug_row.addStretch(1)
        layout.addLayout(debug_row)

        self.tabs.addTab(tab, "Sources")
        self._refresh_sources_table()

    # ------------------------------------------------------------ inline validation (T48, 044)

    def _validate_site_name(self, force: bool = False) -> bool:
        """On blur, beside the field. Only after the user typed."""
        text = self.site_name.text().strip()
        touched = force or self.site_name.isModified()
        error = ""
        if touched and not text:
            error = "Give the website a name."
        self.site_name_error.setText(error)
        self.site_name_error.setVisible(bool(error))
        return not error

    def _validate_site_url(self, force: bool = False) -> bool:
        """On blur, beside the field. On add, a bad-looking or
        empty address blocks the add (see add_source)."""
        text = self.site_url.text().strip()
        touched = force or self.site_url.isModified()
        error = ""
        if touched:
            if not text:
                error = "Give the website an address."
            elif not _looks_like_url(text):
                error = ("That address does not look like a web "
                         "address. Check it.")
        self.site_url_error.setText(error)
        self.site_url_error.setVisible(bool(error))
        return not error

    def _validate_token(self, force: bool = False) -> bool:
        """On blur, beside the field. Advisory: a token the check
        does not recognise still saves."""
        text = self.token_field.text().strip()
        touched = force or self.token_field.isModified()
        error = ""
        if touched and text:
            if len(text) < 10:
                error = "That token looks too short. Paste the whole token."
            elif " " in text:
                error = "A token has no spaces. Check what you pasted."
        self.token_error.setText(error)
        self.token_error.setVisible(bool(error))
        return not error

    def _clear_error_when_fixed(self, field, error_label, ok):
        """While typing, only ever clear an error, never set one:
        flagging a half-typed field is the hostile pattern (044)."""
        if error_label.isVisible() and ok(field.text()):
            error_label.setVisible(False)

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
        # The old Edit buttons go too, for the same reason.
        for row in range(table.rowCount()):
            old_edit = table.cellWidget(row, 3)
            if old_edit is not None:
                table.removeCellWidget(row, 3)
                old_edit.hide()
                old_edit.deleteLater()
        table.setRowCount(len(sites))
        for row, site in enumerate(sites):
            table.setItem(row, 0, QTableWidgetItem(site["name"]))
            table.setItem(row, 1, QTableWidgetItem(site["url"]))
            remove = QPushButton("Remove")
            remove.setToolTip(
                f"Remove {site['name']} from the Search tab buttons.")
            remove.clicked.connect(
                lambda _checked=False, name=site["name"]: self.remove_source(name)
            )
            table.setCellWidget(row, 2, remove)
            edit = QPushButton("Edit")
            edit.setToolTip(
                f"Load {site['name']} into the form below to fix it.")
            edit.clicked.connect(
                lambda _checked=False, name=site["name"]:
                    self.edit_source(name)
            )
            table.setCellWidget(row, 3, edit)
        table.resizeColumnsToContents()
        # Size the table to its rows (v0.1.6 T28): header plus one
        # row height per website, with a sane maximum so a long
        # list still scrolls inside the table.
        row_height = table.rowHeight(0) if sites else 30
        if row_height <= 0:
            row_height = 30
        header_height = table.horizontalHeader().height() or 30
        wanted = header_height + row_height * max(1, len(sites)) + 8
        table.setMaximumHeight(min(320, wanted))
        self.sources_empty_label.setVisible(len(sites) == 0)

    def edit_source(self, name: str):
        """Load a saved website into the form to fix it (T34).

        The Add button becomes Save changes; saving goes through
        the same validation as adding (T2), so a fixed address
        that still does not look like a link is not saved."""
        sites = core.load_sources()
        for site in sites:
            if site["name"] == name:
                self._editing_site = name
                self.site_name.setText(site["name"])
                self.site_url.setText(site["url"])
                self.site_name.setModified(False)
                self.site_url.setModified(False)
                self.site_add_button.setText("Save changes")
                self.statusBar().showMessage(
                    f"Editing {name}. Fix the name or address, "
                    "then press Save changes.")
                return

    def _source_double_clicked(self, index):
        """Double-click a websites row to edit it (T34)."""
        row = index.row()
        cell = self.sources_table.item(row, 0)
        if cell is not None:
            self.edit_source(cell.text())

    def _open_token_page(self):
        """Open the GitHub token settings in the browser (T35)."""
        QDesktopServices.openUrl(
            QUrl("https://github.com/settings/tokens"))
        self.statusBar().showMessage(
            "Opened the GitHub token page in your browser.")

    def add_source(self):
        name = self.site_name.text().strip()
        url = self.site_url.text().strip()
        self._validate_site_name(force=True)
        url_ok = self._validate_site_url(force=True)
        if not name or not url:
            self.statusBar().showMessage(
                "Give the website a name and an address first."
            )
            return
        if not url_ok:
            # A bad-looking address is not added (v0.1.6 T2): the
            # validator used to be called and its result thrown
            # away, so a junk address was saved and given a Search
            # button. Keep the form as typed so it can be fixed,
            # and show the same inline message the Add a link
            # dialog shows for the same input.
            self.site_url_error.setText(
                "That does not look like a link. Check it.")
            self.site_url_error.setVisible(True)
            return
        sites = core.load_sources()
        editing = getattr(self, "_editing_site", None)
        replaced = False
        if editing:
            for site in sites:
                if site["name"] == editing:
                    site["name"] = name
                    site["url"] = url
                    replaced = True
                    break
        if not replaced:
            sites.append({"name": name, "url": url})
        if not self._try_save(
                lambda: core.save_sources(sites),
                "Could not save the websites",
                "Could not save the websites. Try again."):
            return
        self._editing_site = None
        self.site_add_button.setText("Add website")
        self.site_name.clear()
        self.site_url.clear()
        # The cleared form is a fresh form (v0.1.4 T8): clear()
        # leaves the modified flag set, so re-validating here
        # showed red "Give the website a name." errors under a
        # success message. Reset the flags and hide any errors
        # instead of validating.
        self.site_name.setModified(False)
        self.site_url.setModified(False)
        self.site_name_error.setText("")
        self.site_name_error.setVisible(False)
        self.site_url_error.setText("")
        self.site_url_error.setVisible(False)
        self._refresh_sources_table()
        self._refresh_site_buttons()
        if replaced:
            core.log(f"Edited website {name}.")
            self.statusBar().showMessage(f"Saved {name}.")
        else:
            core.log(f"Added website {name}.")
            self.statusBar().showMessage(f"Added {name}.")

    def remove_source(self, name: str):
        sites = core.load_sources()
        position = next((index for index, site in enumerate(sites)
                         if site["name"] == name), None)
        if position is None:
            return
        # Ask first (v0.1.3 T19): removing a website is easy to do
        # by accident, and the safe answer is the default.
        answer = QMessageBox.question(
            self, "Remove website",
            f"Remove {name} from the websites?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.statusBar().showMessage(f"Kept {name}.")
            return
        removed = sites.pop(position)
        if not self._try_save(
                lambda: core.save_sources(sites),
                "Could not save the websites",
                "Could not save the websites. Try again."):
            return
        self._removed_site = (removed, position)
        self.undo_site_button.setEnabled(True)
        self._refresh_sources_table()
        self._refresh_site_buttons()
        core.log(f"Removed website {name}.")
        self.statusBar().showMessage(f"Removed {name}.")

    def undo_remove_source(self):
        """Bring back the website removed most recently (T19)."""
        if self._removed_site is None:
            return
        removed, position = self._removed_site
        self._removed_site = None
        self.undo_site_button.setEnabled(False)
        sites = core.load_sources()
        if any(site["name"] == removed["name"] for site in sites):
            return
        sites.insert(min(position, len(sites)), removed)
        if not self._try_save(
                lambda: core.save_sources(sites),
                "Could not save the websites",
                "Could not save the websites. Try again."):
            return
        self._refresh_sources_table()
        self._refresh_site_buttons()
        self.statusBar().showMessage(f"Added {removed['name']} back.")

    def restore_sources(self):
        # Ask first (v0.1.3 T19): restoring drops every website
        # the user added, and the safe answer is the default.
        answer = QMessageBox.question(
            self, "Restore default websites",
            "Bring back the websites Dataset Scout started with? "
            "Websites you added will be removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.statusBar().showMessage(
                "Kept your websites as they are.")
            return
        if not self._try_save(
                core.restore_default_sources,
                "Could not save the websites",
                "Could not save the websites. Try again."):
            return
        # The restore supersedes any remove waiting to be undone.
        self._removed_site = None
        self.undo_site_button.setEnabled(False)
        self._refresh_sources_table()
        self._refresh_site_buttons()
        self.statusBar().showMessage("Default websites restored.")

    def save_token(self):
        # Advisory only: a token the check does not recognise
        # still saves (044 never blocks on a guess). But the
        # status line says so in one honest line (v0.1.4 T21):
        # claiming plain success while the field shows a red
        # error was a contradiction.
        valid = self._validate_token(force=True)
        if not self._try_save(
                lambda: core.set_github_token(self.token_field.text()),
                "Could not save the token",
                "Could not save the token. Try again."):
            return
        # The save is logged, never the token (v0.1.6 T14): the
        # log scrubbing would catch the value anyway, and the
        # line does not contain it in the first place.
        core.log("GitHub token saved.")
        error = self.token_error.text().strip()
        if not valid and error:
            lowered = error[0].lower() + error[1:]
            self.statusBar().showMessage(f"Saved, but {lowered}")
        else:
            self.statusBar().showMessage(
                "Token saved on this PC. It is never written to a log."
            )
        self._refresh_checklist()

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
    # Orphaned download work folders (v0.1.6 T9): only the owning
    # instance cleans, and only at startup, so a live download's
    # own work folder is never touched.
    download.cleanup_orphan_tmp_folders()
    window = MainWindow()
    guard.start_server(window.bring_to_front)
    window.show()
    # Background checks (T63, T67, v0.1.5 U1): the online probe,
    # then due watches, plus the silent startup update check.
    # Started here, not in the window's __init__, so
    # constructing the window never touches the network.
    window.start_background_checks()
    return app.exec()
