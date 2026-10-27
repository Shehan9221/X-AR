from PyQt5.QtCore import QEvent, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QWidget

from xar.gui import config


class _SwitchButton(QAbstractButton):
    """iOS-style pill switch (track + knob), painted with QPainter."""

    WIDTH = 36
    HEIGHT = 20
    MARGIN = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(0, 0, self.WIDTH, self.HEIGHT)
        track = QColor(*config.SWITCH_ON_RGB) if self.isChecked() else QColor(*config.SWITCH_OFF_RGB)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, self.HEIGHT / 2, self.HEIGHT / 2)

        diameter = self.HEIGHT - self.MARGIN * 2
        x = self.WIDTH - self.MARGIN - diameter if self.isChecked() else self.MARGIN
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QRectF(x, self.MARGIN, diameter, diameter))


class ToggleSwitch(QWidget):
    """Label + iOS-style switch.  Clicking either part toggles it."""

    toggled = pyqtSignal(bool)

    def __init__(self, text, checked=True, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.setFixedHeight(22)

        self.label = QLabel(text)
        self.label.setObjectName("toggleLabel")
        layout.addWidget(self.label, 1)

        self._switch = _SwitchButton()
        self._switch.setChecked(checked)
        self._switch.toggled.connect(self._on_switch_toggled)
        layout.addWidget(self._switch, 0, Qt.AlignRight)

        self.label.installEventFilter(self)
        self.label.setCursor(Qt.PointingHandCursor)

    def isChecked(self):
        return self._switch.isChecked()

    def setChecked(self, checked):
        blocked = self._switch.blockSignals(True)
        self._switch.setChecked(checked)
        self._switch.blockSignals(blocked)

    def _on_switch_toggled(self, checked):
        self.toggled.emit(checked)

    def eventFilter(self, obj, event):
        if obj is self.label and event.type() == QEvent.MouseButtonRelease:
            self._switch.toggle()
            return True
        return super().eventFilter(obj, event)
