"""Dataset Scout window — one window, three tabs, plain words.

Tabs: Search | My List (n) | Sources.
The status bar is the ONLY status surface in the window.
"""

from __future__ import annotations

import hashlib
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QRect, QSettings, QThread, Qt, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from . import apis, core

HERE = Path(__file__).resolve().parent.parent
ICON_PATH = HERE / "assets" / "scout-256.png"

# QSettings home for the saved window geometry (U5).
SETTINGS_ORG = "BrokeNBored"
SETTINGS_APP = "DatasetScout"

DEFAULT_WIDTH = 1100
DEFAULT_HEIGHT = 700

RESULT_COLUMNS = ["Title", "Source", "Stars", "Updated", "Description", "License"]
LIST_COLUMNS = ["Title", "Category", "Source", "Stars", "Updated", "Link", "Notes"]


class SearchWorker(QThread):
    """Runs the live searches off the UI thread.

    Both sources are always tried, even if one fails (U32: never
    silently skip). finished_ok carries the results plus the list
    of failed sources as (name, reason) pairs, so the window can
    say plainly which source did not answer. finished_error fires
    only when BOTH sources fail.
    """

    finished_ok = Signal(list, list)
    finished_error = Signal(str)

    def __init__(self, query: str, category: str, token: str,
                 per_page: int = 30, parent=None):
        super().__init__(parent)
        self.query = query
        self.category = category
        self.token = token
        self.per_page = per_page

    def run(self) -> None:  # noqa: N802 - Qt naming
        results: list[dict] = []
        failures: list[tuple[str, str]] = []
        try:
            results = apis.search_github(
                self.query, self.category, token=self.token,
                per_page=self.per_page,
            )
        except (apis.SearchError, core.RateLimitedError) as exc:
            core.log_error(f"GitHub search failed: {exc}")
            failures.append(("GitHub", str(exc)))
        try:
            results += apis.search_huggingface(
                self.query, self.category, limit=self.per_page
            )
        except apis.SearchError as exc:
            core.log_error(f"Hugging Face search failed: {exc}")
            failures.append(("Hugging Face", str(exc)))
        if len(failures) == 2:
            detail = "; ".join(
                f"{name}: {reason}" for name, reason in failures
            )
            self.finished_error.emit(f"Search failed. {detail}")
            return
        self.finished_ok.emit(results, failures)


class LinkCheckWorker(QThread):
    """Checks every link in My List off the UI thread.

    all_done carries True when the run was stopped part way (U26);
    links already checked stay marked either way.
    """

    item_checked = Signal(int, str)
    all_done = Signal(bool)

    def __init__(self, urls: list[str], parent=None):
        super().__init__(parent)
        self.urls = urls
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        """Ask the worker to stop after the link it is on now."""
        self._stop_event.set()

    def run(self) -> None:  # noqa: N802 - Qt naming
        stopped = False
        for index, url in enumerate(self.urls):
            if self._stop_event.is_set():
                stopped = True
                break
            try:
                status = apis.check_link(url)
            except Exception as exc:  # noqa: BLE001 - a bad link must not kill the run
                core.log_error(f"Link check failed for {url}: {exc}")
                status = "unknown"
            self.item_checked.emit(index, status)
        self.all_done.emit(stopped)


# ------------------------------------------------------------ how to use

# Plain-words index for the How to Use window (U22). No em-dashes,
# no "&" in any visible text (U33, U11).
HOW_TO_USE_SECTIONS = [
    ("Search",
     "Type a few words about the kind of dataset you want, then press "
     "Search. Dataset Scout looks on GitHub and Hugging Face and shows "
     "the matches in the table. Double-click a row to open it in your "
     "browser."),
    ("Categories and filters",
     "Pick a category chip to narrow the search: Post-training, "
     "Instruction tuning, Reasoning, or Training datasets. Active only "
     "keeps results updated recently. Use the stars box to keep only "
     "well liked projects."),
    ("Websites (add/remove)",
     "The Sources tab lists the websites the Search tab can open. Add "
     "a website with its name and address. Put {q} where your search "
     "words go. Remove any website you do not use, or restore the "
     "defaults."),
    ("My List",
     "Add results to My List with the buttons under the search table, "
     "or add a link you found yourself with the form at the bottom of "
     "the Search tab. Remove rows you do not want. Clear all asks "
     "first."),
    ("Check links",
     "Check links visits every link in My List and marks it working, "
     "dead, or unknown. You can stop the check part way through with "
     "the Stop button. Links already checked stay marked."),
    ("Export for STELLA",
     "Export My List as a Markdown list, JSON, or a STELLA manifest. "
     "Every export says that downloaded data must be converted to "
     "STELLA's .jsonl shape before a STELLA project can use it. "
     "Dataset Scout only finds links. It does not download or convert "
     "the data."),
    ("GitHub token",
     "GitHub allows more searches per hour with a token. Paste one in "
     "the Sources tab and press Save token. It is stored on this PC "
     "only and never written to a log."),
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
        self.search_worker: SearchWorker | None = None
        self.check_worker: LinkCheckWorker | None = None
        self.howto_window: HowToUseWindow | None = None
        self._check_total = 0
        self._check_done_count = 0

        help_menu = self.menuBar().addMenu("Help")
        howto_action = help_menu.addAction("How to Use")
        howto_action.triggered.connect(self.show_how_to_use)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._build_search_tab()
        self._build_list_tab()
        self._build_sources_tab()
        self._refresh_list_tab()
        self._refresh_site_buttons()

        # The busy light (U16): visible only while a search or a link
        # check is running. It is a light, not a second status text —
        # the status bar stays the only status text surface.
        self.busy_dot = QLabel("\u25cf")
        self.busy_dot.setObjectName("busy_dot")
        self.busy_dot.setVisible(False)
        self.statusBar().addPermanentWidget(self.busy_dot)

        self.statusBar().showMessage(
            "Type a few words and press Search, or pick a category below."
        )

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
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        rect = self.geometry()
        settings.setValue("window/x", rect.x())
        settings.setValue("window/y", rect.y())
        settings.setValue("window/width", rect.width())
        settings.setValue("window/height", rect.height())
        settings.sync()
        super().closeEvent(event)

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
        layout = QVBoxLayout(tab)

        heading = QLabel("Dataset finder for STELLA")
        heading.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(heading)
        layout.addWidget(QLabel(
            "Find training datasets, save the links, export the list. "
            "STELLA uses the list after you convert downloads to its "
            ".jsonl shape."
        ))

        row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("What kind of dataset? (example: math word problems)")
        self.search_box.returnPressed.connect(self.start_search)
        row.addWidget(self.search_box, stretch=1)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.start_search)
        row.addWidget(self.search_button)
        layout.addLayout(row)

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
        filter_row.addWidget(QLabel("At least this many stars:"))
        self.stars_combo = QComboBox()
        self.stars_combo.addItems(str(n) for n in core.MIN_STARS_CHOICES)
        filter_row.addWidget(self.stars_combo)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        self.results_table = QTableWidget(0, len(RESULT_COLUMNS))
        self.results_table.setHorizontalHeaderLabels(RESULT_COLUMNS)
        self.results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.results_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.doubleClicked.connect(self._open_result)
        layout.addWidget(self.results_table, stretch=1)

        add_row = QHBoxLayout()
        self.add_selected_button = QPushButton("Add selected to My List")
        self.add_selected_button.clicked.connect(self.add_selected)
        add_row.addWidget(self.add_selected_button)
        self.add_all_button = QPushButton("Add all results to My List")
        self.add_all_button.clicked.connect(self.add_all_results)
        add_row.addWidget(self.add_all_button)
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

        found_box = QGroupBox("Add a link I found")
        found_layout = QFormLayout(found_box)
        self.found_url = QLineEdit()
        self.found_url.setPlaceholderText("https://...")
        found_layout.addRow("Link:", self.found_url)
        self.found_title = QLineEdit()
        self.found_title.setPlaceholderText("A short name for it")
        found_layout.addRow("Title:", self.found_title)
        self.found_category = QComboBox()
        for key, (label, _suffix) in core.CATEGORIES.items():
            self.found_category.addItem(label, key)
        found_layout.addRow("Category:", self.found_category)
        self.found_note = QLineEdit()
        self.found_note.setPlaceholderText("Anything to remember about it (optional)")
        found_layout.addRow("Note:", self.found_note)
        self.found_add_button = QPushButton("Add this link to My List")
        self.found_add_button.clicked.connect(self.add_found_link)
        found_layout.addRow("", self.found_add_button)
        layout.addWidget(found_box)

        self.tabs.addTab(tab, "Search")

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
        query = core.build_query(self.search_box.text(), category)
        self.search_button.setEnabled(False)
        self._set_busy(True)
        self.statusBar().showMessage(f"Searching for: {query}")
        core.log(f"Search started: {query!r} (category {category})")
        self.search_worker = SearchWorker(
            query, category, core.get_github_token()
        )
        self.search_worker.finished_ok.connect(self._search_done)
        self.search_worker.finished_error.connect(self._search_failed)
        self.search_worker.start()

    def _search_done(self, results: list[dict],
                     failures: list | None = None):
        self.search_button.setEnabled(True)
        self._set_busy(False)
        months = core.ACTIVE_WINDOWS[self.window_combo.currentText()]
        min_stars = int(self.stars_combo.currentText())
        self.results = core.filter_results(
            results,
            active_only=self.active_check.isChecked(),
            months=months,
            min_stars=min_stars,
        )
        self._show_results()
        count = len(self.results)
        word = "result" if count == 1 else "results"
        message = f"Found {count} {word}. Pick rows and add them to My List."
        failures = failures or []
        if failures:
            # Only one source can have failed here (both failing takes
            # the error path); name it in plain words (U32).
            failed = failures[0][0]
            other = "Hugging Face" if failed == "GitHub" else "GitHub"
            message = (
                f"Found {count} {word}. {failed} search did not answer, "
                f"showing {other} results only."
            )
        self.statusBar().showMessage(message)
        core.log(f"Search finished: {count} results after filters")

    def _search_failed(self, message: str):
        self.search_button.setEnabled(True)
        self._set_busy(False)
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "Search failed", message)

    def _show_results(self):
        table = self.results_table
        table.setRowCount(len(self.results))
        for row, item in enumerate(self.results):
            values = [
                item.get("title", ""),
                item.get("source", ""),
                str(item.get("stars", 0)),
                core.format_date(item.get("updated")),
                item.get("description", ""),
                item.get("license", ""),
            ]
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(value))
        table.resizeColumnsToContents()

    def _selected_results(self) -> list[dict]:
        rows = {index.row() for index in self.results_table.selectedIndexes()}
        return [self.results[row] for row in sorted(rows)
                if 0 <= row < len(self.results)]

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
            self.search_box.text(), self.selected_category()
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
            "category": self.found_category.currentData(),
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

        self.list_table = QTableWidget(0, len(LIST_COLUMNS))
        self.list_table.setHorizontalHeaderLabels(LIST_COLUMNS)
        self.list_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.list_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list_table.horizontalHeader().setStretchLastSection(True)
        self.list_table.doubleClicked.connect(self._open_list_item)
        layout.addWidget(self.list_table, stretch=1)

        row = QHBoxLayout()
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self.remove_selected)
        row.addWidget(self.remove_button)
        self.clear_button = QPushButton("Clear all")
        self.clear_button.clicked.connect(self.clear_list)
        row.addWidget(self.clear_button)
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
        export_row.addStretch(1)
        layout.addLayout(export_row)

        self.list_tab_index = self.tabs.addTab(tab, "My List (0)")

    def _refresh_list_tab(self):
        table = self.list_table
        table.setRowCount(len(self.my_list))
        for row, item in enumerate(self.my_list):
            status = item.get("status", "")
            title = item["title"] + (f" [{status}]" if status else "")
            values = [
                title,
                core.CATEGORIES.get(item.get("category", "all"),
                                    ("All", ""))[0],
                item.get("source", ""),
                str(item.get("stars", 0)),
                core.format_date(item.get("updated")),
                item.get("url", ""),
                item.get("notes", ""),
            ]
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(value))
        table.resizeColumnsToContents()
        self.tabs.setTabText(
            self.list_tab_index, f"My List ({len(self.my_list)})"
        )

    def _selected_list_rows(self) -> list[int]:
        return sorted({index.row()
                       for index in self.list_table.selectedIndexes()})

    def _open_list_item(self):
        rows = self._selected_list_rows()
        if rows and self.my_list[rows[0]].get("url"):
            QDesktopServices.openUrl(self.my_list[rows[0]]["url"])

    def remove_selected(self):
        rows = self._selected_list_rows()
        if not rows:
            self.statusBar().showMessage("Nothing selected to remove.")
            return
        urls = {self.my_list[row]["url"] for row in rows}
        self.my_list = [item for item in self.my_list
                        if item["url"] not in urls]
        core.save_list(self.my_list)
        self._refresh_list_tab()
        self.statusBar().showMessage(f"Removed {len(rows)} from My List.")

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
        for item in self.my_list:
            item["status"] = "checking"
        self._refresh_list_tab()
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
            self._refresh_list_tab()
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
            for item in self.my_list:
                if item.get("status") == "checking":
                    item["status"] = ""
            self._refresh_list_tab()
        core.save_list(self.my_list)
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
