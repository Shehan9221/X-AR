import time

import cv2
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QImage

from xar.gui import config
from xar.gui.adapter import get_adapter


class CaptureWorker(QThread):
    """Reads frames and runs pose detection off the GUI thread.

    This worker never touches widgets; it only emits signals.
    """

    frame_ready = pyqtSignal(QImage, object)          # frame, PoseResult
    fps_updated = pyqtSignal(float)
    position_updated = pyqtSignal(int, int)           # current_ms, total_ms
    media_info = pyqtSignal(int, int, int, float)     # width, height, duration_ms, fps
    source_finished = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, source_type="webcam", source_value=0, use_mock=True, parent=None):
        super().__init__(parent)
        self.source_type = source_type                # "webcam" | "file"
        self.source_value = source_value
        self.use_mock = use_mock

        self._running = False
        self._paused = False
        self._seek_request = None
        self._adapter = None
        self._cap = None
        self._engine_ts = 0
        self._duration_ms = 0
        self._frame_interval_s = 1.0 / config.DEFAULT_FPS
        self._fps_count = 0
        self._fps_t0 = time.monotonic()
        # Back-pressure: only one frame may sit in the GUI event queue.  Without
        # this a fast video + a slow paint can queue thousands of full-size
        # QImages and exhaust memory.
        self._frame_pending = False

    # -- public API (called from the GUI thread) -----------------

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def is_paused(self):
        return self._paused

    def seek(self, position_ms):
        self._seek_request = int(position_ms)

    def frame_consumed(self):
        """Called by the GUI after it has handled a frame (back-pressure)."""
        self._frame_pending = False

    def stop(self):
        """Ask the thread to finish.  Does not block; the caller waits."""
        self._running = False
        self.requestInterruption()

    def stop_and_wait(self, timeout_ms=5000):
        """Stop and block until the thread has really exited."""
        self.stop()
        return self.wait(timeout_ms)

    # -- worker thread -------------------------------------------

    def run(self):
        self._running = True
        try:
            if not self._open_capture():
                return
            if not self._running:
                return

            self._adapter = get_adapter(self.use_mock)
            self._adapter.reset()
            self._publish_media_info()

            while self._running and not self.isInterruptionRequested():
                if self._seek_request is not None:
                    self._apply_seek()

                if self._paused:
                    # Keep draining the camera so pause/resume stays responsive.
                    if self.source_type == "webcam":
                        self._cap.grab()
                    self.msleep(30)
                    continue

                started = time.perf_counter()
                ok, frame = self._cap.read()
                if not ok:
                    if self.source_type == "file":
                        if self._duration_ms:
                            self.position_updated.emit(self._duration_ms, self._duration_ms)
                        self.source_finished.emit()
                    else:
                        self.error_occurred.emit(
                            "Lost camera connection. It may be in use by another application."
                        )
                    break

                self._engine_ts += max(1, int(self._frame_interval_s * 1000))
                result = self._adapter.process_frame(frame, self._engine_ts)

                if not self._running:
                    break
                if not self._frame_pending:
                    self._frame_pending = True
                    self.frame_ready.emit(self._to_qimage(frame), result)

                self._fps_count += 1
                now = time.monotonic()
                if now - self._fps_t0 >= 1.0:
                    self.fps_updated.emit(self._fps_count / (now - self._fps_t0))
                    self._fps_count = 0
                    self._fps_t0 = now

                if self.source_type == "file":
                    pos = int(self._cap.get(cv2.CAP_PROP_POS_MSEC))
                    self.position_updated.emit(pos, self._duration_ms)
                    elapsed = time.perf_counter() - started
                    remaining = self._frame_interval_s - elapsed
                    if remaining > 0:
                        self.msleep(int(remaining * 1000))

        except Exception as exc:  # never let the worker kill the app silently
            self.error_occurred.emit(f"Processing error: {exc}")
        finally:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
            # The adapter is a shared, process-wide object: drop our reference
            # but leave the MediaPipe graphs alive for the next video.
            self._adapter = None

    # -- helpers -------------------------------------------------

    def _open_capture(self):
        if self.source_type == "webcam":
            self._cap = cv2.VideoCapture(int(self.source_value), cv2.CAP_DSHOW)
            if not self._cap.isOpened():
                # CAP_DSHOW is Windows-specific; retry with the backend default.
                self._cap.release()
                self._cap = cv2.VideoCapture(int(self.source_value))
            if not self._cap.isOpened():
                self.error_occurred.emit(
                    f"No camera found for device {self.source_value}."
                )
                return False
            fps = self._cap.get(cv2.CAP_PROP_FPS)
            if fps and fps > 1:
                self._frame_interval_s = 1.0 / fps
            return True

        self._cap = cv2.VideoCapture(self.source_value)
        if not self._cap.isOpened():
            self.error_occurred.emit(
                f"Could not open video file:\n{self.source_value}"
            )
            return False

        # Validate that the file actually decodes, otherwise a corrupt file is
        # silently treated as an empty video.
        ok, _ = self._cap.read()
        if not ok:
            self.error_occurred.emit(
                f"Unreadable video file:\n{self.source_value}"
            )
            return False
        self._cap.set(cv2.CAP_PROP_POS_MSEC, 0)

        fps = self._cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 1 or fps > 240:
            fps = config.DEFAULT_FPS
        self._frame_interval_s = 1.0 / fps

        frames = self._cap.get(cv2.CAP_PROP_FRAME_COUNT)
        self._duration_ms = int(frames / fps * 1000) if frames and frames > 0 else 0
        return True

    def _publish_media_info(self):
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = 1.0 / self._frame_interval_s
        self.media_info.emit(w, h, self._duration_ms, fps)

    def _apply_seek(self):
        target = self._seek_request
        self._seek_request = None
        if self._cap is None or self.source_type != "file":
            return
        self._cap.set(cv2.CAP_PROP_POS_MSEC, target)
        if self._adapter is not None:
            self._adapter.reset()
        self.position_updated.emit(target, self._duration_ms)

    @staticmethod
    def _to_qimage(frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        image = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        return image.copy()
