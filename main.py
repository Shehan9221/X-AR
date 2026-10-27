import functools
import os
import sys
import traceback

from xar.gui import config

# MediaPipe must be imported before PyQt5 on Windows, otherwise its native
# bindings fail to initialise.  Loading the core here also keeps every heavy
# import on the main thread (importing inside a QThread deadlocks).
_CORE_IMPORT_ERROR = None
try:
    from xar.gui.adapter import preload_core

    preload_core()
except Exception as exc:  # mock mode can still run without the core
    _CORE_IMPORT_ERROR = exc

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QAction, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QShortcut, QStatusBar, QVBoxLayout, QWidget,
)

from xar.gui.control_bar import ControlBar
from xar.gui.side_panel import SidePanel
from xar.gui.video_widget import VideoDisplay
from xar.gui.worker import CaptureWorker
from xar.gui.adapter import close_shared_adapter


# Never let a QThread that is still running be garbage collected: Qt aborts the
# process with "QThread: Destroyed while thread is still running".
_ABANDONED_WORKERS = []


def _guarded(slot):
    """Keep an exception in a Qt slot from aborting the whole process.

    PyQt5 calls qFatal() when a slot raises, which shows up as a hard crash
    (0xC0000409) with no traceback.  Log the error instead.
    """
    @functools.wraps(slot)
    def wrapper(self, *args, **kwargs):
        try:
            return slot(self, *args, **kwargs)
        except Exception:
            traceback.print_exc()
            return None
    return wrapper


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{config.APP_NAME} — Radiography Positioning Guidance")
        self.setMinimumSize(1024, 700)
        self.resize(1280, 800)

        self.worker = None
        # Workers that did not finish within the shutdown timeout are kept
        # here.  Dropping the last Python reference to a running QThread makes
        # Qt abort with "QThread: Destroyed while thread is still running".
        self._retiring_workers = []
        self._starting = False
        self.current_source = ("webcam", 0)
        self.use_mock = config.USE_MOCK
        self._fullscreen = False
        self._was_panel_visible = True

        self._build_ui()
        self._build_menus()
        self._load_stylesheet()
        self._apply_overlay_flags()

        QTimer.singleShot(0, lambda: self.start_source("webcam", 0))

    # -- construction -------------------------------------------

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.side_panel = SidePanel()
        self.side_panel.source_selected.connect(self.start_source)
        self.side_panel.browse_requested.connect(self.open_file)
        self.side_panel.overlay_changed.connect(self.video_display_set_flags)

        self.video_display = VideoDisplay()
        self.video_display.double_clicked.connect(self.toggle_fullscreen)

        self.control_bar = ControlBar()
        self.control_bar.play_requested.connect(self.play)
        self.control_bar.pause_requested.connect(self.pause)
        self.control_bar.stop_requested.connect(self.stop)
        self.control_bar.seek_requested.connect(self.seek)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        row_layout.addWidget(self.side_panel)
        row_layout.addWidget(self.video_display, 1)

        body_layout.addWidget(row, 1)
        body_layout.addWidget(self.control_bar)
        root.addWidget(body)

        self._build_status_bar()

        self.shortcut_play = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.shortcut_play.activated.connect(self.toggle_playback)
        self.shortcut_escape = QShortcut(QKeySequence("Escape"), self)
        self.shortcut_escape.activated.connect(self._escape_pressed)

    def _build_status_bar(self):
        status = QStatusBar()
        self.setStatusBar(status)
        self.source_label = QLabel("Source: —")
        self.resolution_label = QLabel("Resolution: —")
        self.fps_label = QLabel("FPS: —")
        self.engine_label = QLabel(
            "Engine: Mock" if self.use_mock else "Engine: Core"
        )
        status.addWidget(self.source_label)
        status.addWidget(self.resolution_label)
        status.addPermanentWidget(self.fps_label)
        status.addPermanentWidget(self.engine_label)
        self.status_bar = status

    def _build_menus(self):
        menubar = self.menuBar()

        media = menubar.addMenu("&Media")

        open_file = QAction("&Open File…", self)
        open_file.setShortcut(QKeySequence.Open)
        open_file.triggered.connect(self.open_file)
        media.addAction(open_file)

        open_webcam = QAction("Open &Webcam", self)
        open_webcam.triggered.connect(lambda: self.start_source("webcam", self.side_panel.selected_camera()))
        media.addAction(open_webcam)

        media.addSeparator()

        close_media = QAction("&Close", self)
        close_media.triggered.connect(self.stop)
        media.addAction(close_media)

        quit_app = QAction("&Quit", self)
        quit_app.setShortcut(QKeySequence.Quit)
        quit_app.triggered.connect(self.close)
        media.addAction(quit_app)

        view = menubar.addMenu("&View")

        self.panel_action = QAction("Show/Hide Side &Panel", self)
        self.panel_action.setCheckable(True)
        self.panel_action.setChecked(True)
        self.panel_action.setShortcut(QKeySequence("Ctrl+P"))
        self.panel_action.triggered.connect(self.toggle_side_panel)
        view.addAction(self.panel_action)

        fullscreen = QAction("&Fullscreen", self)
        fullscreen.setShortcut(QKeySequence("F11"))
        fullscreen.triggered.connect(self.toggle_fullscreen)
        view.addAction(fullscreen)

        view.addSeparator()

        self.mock_action = QAction("Use &Mock Engine", self)
        self.mock_action.setCheckable(True)
        self.mock_action.setChecked(self.use_mock)
        self.mock_action.triggered.connect(self.toggle_mock_engine)
        view.addAction(self.mock_action)

        help_menu = menubar.addMenu("&Help")
        about = QAction("&About X-AR", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)

    def _load_stylesheet(self):
        path = os.path.join(config.PROJECT_ROOT, "xar", "gui", "style.qss")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                self.setStyleSheet(handle.read())
        except OSError as exc:
            print(f"Could not load stylesheet: {exc}")

    # -- source lifecycle ---------------------------------------

    def start_source(self, source_type, source_value):
        if source_type == "file" and not source_value:
            return False
        # A seek or another source change must not re-enter this method while
        # the worker is being replaced.
        if self._starting:
            return False

        if not self.use_mock:
            if _CORE_IMPORT_ERROR is not None:
                QMessageBox.critical(
                    self, "X-AR",
                    f"Could not load the core engine:\n{_CORE_IMPORT_ERROR}",
                )
                return False
            try:
                preload_core()
            except Exception as exc:
                QMessageBox.critical(self, "X-AR", f"Could not load the core engine:\n{exc}")
                return False

        self._starting = True
        try:
            if not self._stop_worker():
                self.status_bar.showMessage(
                    "The previous video is still closing. Please try again.", 5000
                )
                return False

            self.current_source = (source_type, source_value)

            display_name = (
                os.path.basename(source_value) if source_type == "file" else "Webcam"
            )
            self.side_panel.set_source_mode(source_type)
            if source_type == "file":
                self.side_panel.set_file_label(source_value)
            self.source_label.setText(f"Source: {display_name}")
            self.resolution_label.setText("Resolution: —")
            self.fps_label.setText("FPS: —")
            self.control_bar.set_mode(source_type == "file")
            self.control_bar.reset()
            self.control_bar.set_playing(True)
            self.video_display.clear()
            self.setWindowTitle(f"{config.APP_NAME} — {display_name}")
            self.status_bar.showMessage(f"Opening {display_name}…")

            self.worker = CaptureWorker(
                source_type=source_type,
                source_value=source_value,
                use_mock=self.use_mock,
            )
            self.worker.frame_ready.connect(self.on_frame_ready)
            self.worker.fps_updated.connect(self.on_fps_updated)
            self.worker.media_info.connect(self.on_media_info)
            self.worker.position_updated.connect(self.on_position_updated)
            self.worker.source_finished.connect(self.on_source_finished)
            self.worker.error_occurred.connect(self.on_worker_error)
            self.worker.start()
            return True
        finally:
            self._starting = False

    def _stop_worker(self, wait_ms=5000):
        """Stop the current worker and free its resources safely.

        Returns True when the thread has really finished.  A running QThread
        must never lose its last reference, so a thread that does not stop in
        time is parked in *_retiring_workers* instead of being dropped.
        """
        worker, self.worker = self.worker, None
        if worker is None:
            self._collect_retired()
            return True

        # Do not let a worker that is going away update the UI any more.
        try:
            worker.disconnect()
        except TypeError:
            pass

        worker.stop()
        if worker.isRunning():
            worker.wait(wait_ms)

        if worker.isRunning():
            self._retiring_workers.append(worker)
            return False

        worker.deleteLater()
        self._collect_retired()
        return True

    def _collect_retired(self):
        still_running = []
        for worker in self._retiring_workers:
            try:
                alive = worker.isRunning()
            except RuntimeError:      # already deleted by Qt
                alive = False
            if alive:
                still_running.append(worker)
            else:
                worker.deleteLater()
        self._retiring_workers = still_running

    # -- transport ----------------------------------------------

    def play(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.resume()
            self.control_bar.set_playing(True)
        else:
            self.start_source(*self.current_source)

    def pause(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.pause()
            self.control_bar.set_playing(False)

    def toggle_playback(self):
        if self.worker is not None and self.worker.isRunning() and not self.worker.is_paused():
            self.pause()
        else:
            self.play()

    def stop(self):
        self._stop_worker()
        self.video_display.clear()
        self.control_bar.set_playing(False)
        self.control_bar.reset()
        self.status_bar.showMessage("Stopped", 3000)

    def seek(self, position_ms):
        if self.current_source[0] != "file" or self._starting:
            return
        worker = self.worker
        if worker is None or not worker.isRunning():
            # Restart a stopped/finished video, then jump to the requested time.
            if not self.start_source(*self.current_source):
                return
            worker = self.worker
        if worker is not None:
            worker.seek(position_ms)
            worker.resume()
            self.control_bar.set_playing(True)

    def toggle_side_panel(self):
        visible = not self.side_panel.isVisible()
        self.side_panel.setVisible(visible)
        self.panel_action.setChecked(visible)

    def toggle_fullscreen(self):
        if self._fullscreen:
            self.showNormal()
            self.menuBar().show()
            self.status_bar.show()
            self.control_bar.show()
            self.side_panel.setVisible(self._was_panel_visible)
            self.panel_action.setChecked(self._was_panel_visible)
            self._fullscreen = False
        else:
            self._was_panel_visible = self.side_panel.isVisible()
            self.menuBar().hide()
            self.status_bar.hide()
            self.control_bar.hide()
            self.side_panel.hide()
            self.showFullScreen()
            self._fullscreen = True

    def _escape_pressed(self):
        if self._fullscreen:
            self.toggle_fullscreen()

    def open_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open Video File",
            config.PROJECT_ROOT,
            "Video files (*.mp4 *.avi *.mkv *.mov);;All files (*.*)",
        )
        if filename:
            self.side_panel.set_file(filename)
        elif not self.side_panel.has_file():
            self.side_panel.set_source_mode(self.current_source[0])

    # -- worker signals -----------------------------------------

    @_guarded
    def on_frame_ready(self, image, result):
        try:
            self.video_display.set_frame(image, result)
            self.side_panel.update_status(result)
        finally:
            # Tell the worker it may send the next frame (back-pressure).
            worker = self.sender()
            if isinstance(worker, CaptureWorker):
                worker.frame_consumed()

    @_guarded
    def on_media_info(self, width, height, duration_ms, fps):
        if width and height:
            self.resolution_label.setText(f"Resolution: {width}×{height}")
        if duration_ms:
            self.control_bar.set_duration(duration_ms)
        self.status_bar.clearMessage()

    @_guarded
    def on_position_updated(self, current_ms, total_ms):
        self.control_bar.set_position(current_ms, total_ms)

    @_guarded
    def on_fps_updated(self, fps):
        self.fps_label.setText(f"FPS: {fps:.0f}")

    @_guarded
    def on_source_finished(self):
        self.control_bar.set_playing(False)
        self.status_bar.showMessage("End of video", 5000)

    @_guarded
    def on_worker_error(self, message):
        QMessageBox.warning(self, "X-AR", message)
        self.stop()

    # -- overlay ------------------------------------------------

    def _apply_overlay_flags(self):
        self.video_display.set_overlay_flags(**self.side_panel.overlay_flags())

    def video_display_set_flags(self, flags):
        self.video_display.set_overlay_flags(**flags)

    def toggle_mock_engine(self, checked):
        self.use_mock = bool(checked)
        self.engine_label.setText("Engine: Mock" if self.use_mock else "Engine: Core")
        if self.current_source[0] == "file" or self.worker is not None:
            self.start_source(*self.current_source)

    def show_about(self):
        QMessageBox.about(
            self,
            "About X-AR",
            f"{config.APP_NAME} {config.APP_VERSION}\n\n"
            "Radiography positioning guidance.\n"
            "Live pose overlay: head rotation, head tilt, trunk rotation and trunk level.\n\n"
            f"Engine: {'mock' if self.use_mock else 'core'}",
        )

    # -- shutdown -----------------------------------------------

    def closeEvent(self, event):
        self._stop_worker()
        for worker in self._retiring_workers:
            worker.wait(2000)
        still_running = any(worker.isRunning() for worker in self._retiring_workers)
        if still_running:
            _ABANDONED_WORKERS.extend(self._retiring_workers)
        self._retiring_workers = []
        # Never tear down MediaPipe while a thread may still be using it.
        if not still_running:
            close_shared_adapter()
        super().closeEvent(event)


_CRASH_LOG_HANDLE = None


def _enable_crash_log():
    """Dump a Python/native stack to a log file if the process dies."""
    global _CRASH_LOG_HANDLE
    import faulthandler
    try:
        path = os.path.join(
            os.environ.get("TEMP", config.PROJECT_ROOT), "xar_crash.log"
        )
        _CRASH_LOG_HANDLE = open(path, "a", buffering=1)
        faulthandler.enable(_CRASH_LOG_HANDLE)
    except OSError:
        _CRASH_LOG_HANDLE = None


def main():
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    _enable_crash_log()

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
