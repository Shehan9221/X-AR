from PyQt5.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QWidget,
)


class ControlBar(QWidget):
    """VLC-style transport bar: play/pause, stop, seek slider, time labels."""

    play_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    seek_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("controlBar")
        self.setFixedHeight(52)

        self._is_video = False
        self._playing = False
        self._dragging = False
        self._updating = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("controlBtn")
        self.play_btn.setToolTip("Play / Pause (Space)")
        self.play_btn.clicked.connect(self._on_play_clicked)
        layout.addWidget(self.play_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setObjectName("controlBtn")
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.clicked.connect(self.stop_requested)
        layout.addWidget(self.stop_btn)

        self._icon_play = self._make_icon("play")
        self._icon_pause = self._make_icon("pause")
        self._icon_stop = self._make_icon("stop")
        for button in (self.play_btn, self.stop_btn):
            button.setIconSize(QSize(18, 18))
        self.play_btn.setIcon(self._icon_play)
        self.stop_btn.setIcon(self._icon_stop)

        self.elapsed_label = QLabel("00:00")
        self.elapsed_label.setObjectName("timeLabel")
        layout.addWidget(self.elapsed_label)

        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setObjectName("seekSlider")
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setEnabled(False)
        self.seek_slider.sliderPressed.connect(self._on_slider_pressed)
        self.seek_slider.sliderReleased.connect(self._on_slider_released)
        self.seek_slider.valueChanged.connect(self._on_slider_value_changed)
        layout.addWidget(self.seek_slider, 1)

        self.total_label = QLabel("00:00")
        self.total_label.setObjectName("timeLabel")
        layout.addWidget(self.total_label)

        self.spacer = QWidget()
        self.spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.spacer.hide()
        layout.addWidget(self.spacer)

        self.live_label = QLabel("● LIVE")
        self.live_label.setObjectName("liveLabel")
        self.live_label.setAlignment(Qt.AlignCenter)
        self.live_label.hide()
        layout.addWidget(self.live_label)

        self.mode_label = QLabel("Webcam")
        self.mode_label.setObjectName("modeLabel")
        layout.addWidget(self.mode_label)

    # -- state --------------------------------------------------

    def set_mode(self, is_video):
        self._is_video = is_video
        self.seek_slider.setVisible(is_video)
        self.seek_slider.setEnabled(is_video)
        self.elapsed_label.setVisible(is_video)
        self.total_label.setVisible(is_video)
        self.spacer.setVisible(not is_video)
        self.live_label.setVisible(not is_video)
        self.mode_label.setText("Video" if is_video else "Webcam")

    def set_duration(self, duration_ms):
        self._updating = True
        self.seek_slider.setRange(0, max(0, int(duration_ms)))
        self._updating = False
        self.total_label.setText(self._format_time(duration_ms))

    def set_position(self, position_ms, duration_ms=None):
        if duration_ms is not None:
            self.set_duration(duration_ms)
        if not self._dragging:
            self._updating = True
            self.seek_slider.setValue(int(position_ms))
            self._updating = False
        self.elapsed_label.setText(self._format_time(position_ms))

    def set_playing(self, playing):
        self._playing = playing
        self.play_btn.setIcon(self._icon_pause if playing else self._icon_play)
        self.play_btn.setToolTip("Pause (Space)" if playing else "Play (Space)")

    def reset(self):
        # Programming the slider must not look like a user seek: otherwise
        # setValue(0) emits seek_requested() and the window restarts the video
        # (recursively, from inside start_source()/stop()).
        self._updating = True
        self.seek_slider.setValue(0)
        self._updating = False
        self.elapsed_label.setText("00:00")
        self.total_label.setText("00:00")

    # -- slider -------------------------------------------------

    def _on_play_clicked(self):
        if self._playing:
            self.pause_requested.emit()
        else:
            self.play_requested.emit()

    def _on_slider_pressed(self):
        self._dragging = True

    def _on_slider_released(self):
        self._dragging = False
        if self._is_video:
            self.seek_requested.emit(self.seek_slider.value())

    def _on_slider_value_changed(self, value):
        if not self._is_video or self._updating:
            return
        self.elapsed_label.setText(self._format_time(value))
        if not self._dragging:
            # Clicking the groove (rather than dragging the handle).
            self.seek_requested.emit(value)

    @staticmethod
    def _make_icon(kind):
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(235, 235, 235))
        if kind == "play":
            painter.drawPolygon(QPolygonF([QPointF(6, 4), QPointF(16, 10), QPointF(6, 16)]))
        elif kind == "pause":
            painter.drawRect(5, 4, 4, 12)
            painter.drawRect(12, 4, 4, 12)
        else:  # stop
            painter.drawRect(5, 5, 11, 11)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _format_time(ms):
        total_seconds = max(0, int(ms) // 1000)
        hours, rem = divmod(total_seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
