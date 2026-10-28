from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget,
)

from xar.gui import config
from xar.gui.toggle_switch import ToggleSwitch


class StatusCard(QFrame):
    """A single measurement card: label, value, badge and short advice."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("statusCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardLabel")
        layout.addWidget(self.title_label)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.value_label = QLabel("--\N{DEGREE SIGN}")
        self.value_label.setObjectName("cardValue")
        row.addWidget(self.value_label)
        row.addStretch()

        self.badge = QLabel("Not detected")
        self.badge.setObjectName("statusBadge")
        self.badge.setProperty("status", "not_detected")
        self.badge.setAlignment(Qt.AlignCenter)
        row.addWidget(self.badge)
        layout.addLayout(row)

        self.status_text = QLabel("No detection")
        self.status_text.setObjectName("statusText")
        self.status_text.setWordWrap(True)
        layout.addWidget(self.status_text)

    def set_value(self, value, status, text):
        if status == "not_detected":
            self.value_label.setText("--\N{DEGREE SIGN}")
            self.badge.setText("Not detected")
        else:
            self.value_label.setText(f"{value:+.1f}\N{DEGREE SIGN}")
            self.badge.setText("OK" if status == "ok" else "Adjust")
        self.status_text.setText(text)
        self.badge.setProperty("status", status)
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)


class SidePanel(QWidget):
    """Left control panel: source, live status and overlay guidelines."""

    source_selected = pyqtSignal(str, object)   # "webcam" -> int index, "file" -> path
    browse_requested = pyqtSignal()
    overlay_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidePanel")
        self.setFixedWidth(config.SIDE_PANEL_WIDTH)

        self._file_path = None
        self._suppress = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("panelContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        layout.addWidget(self._build_source_group())
        layout.addWidget(self._build_status_group())
        layout.addWidget(self._build_guidelines_group())
        layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    # -- construction -------------------------------------------

    def _build_source_group(self):
        group = QGroupBox("Source")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(6)

        self.source_group = QButtonGroup(self)
        self.webcam_radio = QRadioButton("Webcam")
        self.file_radio = QRadioButton("Video File")
        self.source_group.addButton(self.webcam_radio)
        self.source_group.addButton(self.file_radio)
        self.webcam_radio.setChecked(True)
        layout.addWidget(self.webcam_radio)
        layout.addWidget(self.file_radio)

        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self.browse_requested)
        layout.addWidget(self.browse_btn)

        camera_row = QHBoxLayout()
        camera_row.setSpacing(8)
        camera_label = QLabel("Camera")
        camera_label.setObjectName("fieldLabel")
        camera_row.addWidget(camera_label)
        self.camera_combo = QComboBox()
        for index in range(4):
            self.camera_combo.addItem(f"Camera {index}", index)
        camera_row.addWidget(self.camera_combo, 1)
        layout.addLayout(camera_row)

        self.file_label = QLabel("No file selected")
        self.file_label.setObjectName("fileNameLabel")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        self.webcam_radio.toggled.connect(self._on_source_toggled)
        self.file_radio.toggled.connect(self._on_source_toggled)
        self.camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        return group

    def _build_status_group(self):
        group = QGroupBox("Status")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(6)

        self.head_card = StatusCard("Head Rotation")
        self.head_tilt_card = StatusCard("Head Tilt")
        self.trunk_rotation_card = StatusCard("Trunk Rotation")
        self.trunk_level_card = StatusCard("Trunk Level")
        layout.addWidget(self.head_card)
        layout.addWidget(self.head_tilt_card)
        layout.addWidget(self.trunk_rotation_card)
        layout.addWidget(self.trunk_level_card)
        return group

    def _build_guidelines_group(self):
        group = QGroupBox("Guidelines")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(2)

        self.skeleton_toggle = self._toggle("Skeleton overlay")
        self.landmarks_toggle = self._toggle("Landmark points")
        self.head_line_toggle = self._toggle("Head line (MSL / IPL)")
        self.shoulder_line_toggle = self._toggle("Shoulder line")
        self.hip_line_toggle = self._toggle("Hip line")
        self.trunk_msl_toggle = self._toggle("Trunk midline (MSL)")
        self.diaphragm_toggle = self._toggle("Diaphragm level")
        self.asis_toggle = self._toggle("ASIS line")
        self.iliac_crest_toggle = self._toggle("Iliac crest level")
        self.hip_level_toggle = self._toggle("Hip level")
        self.angle_labels_toggle = self._toggle("Angle labels")

        for widget in (
            self.skeleton_toggle, self.landmarks_toggle, self.head_line_toggle,
            self.shoulder_line_toggle, self.hip_line_toggle, self.trunk_msl_toggle,
            self.diaphragm_toggle, self.asis_toggle, self.iliac_crest_toggle,
            self.hip_level_toggle, self.angle_labels_toggle,
        ):
            layout.addWidget(widget)
            widget.toggled.connect(self._emit_overlay_flags)
        return group

    def _toggle(self, text):
        return ToggleSwitch(text, checked=True)

    # -- public API ---------------------------------------------

    def set_file(self, path):
        self.set_file_label(path)
        if not self.file_radio.isChecked():
            self._suppress = True
            self.file_radio.setChecked(True)
            self._suppress = False
        self.source_selected.emit("file", path)

    def set_file_label(self, path):
        """Update the file name without changing the active source."""
        self._file_path = path
        name = path.replace("\\", "/").rsplit("/", 1)[-1] if path else ""
        self.file_label.setText(name or "No file selected")
        self.file_label.setToolTip(path or "")

    def has_file(self):
        return bool(self._file_path)

    def set_source_mode(self, source_type):
        self._suppress = True
        self.webcam_radio.setChecked(source_type == "webcam")
        self.file_radio.setChecked(source_type == "file")
        self._suppress = False

    def selected_camera(self):
        return self.camera_combo.currentData()

    def overlay_flags(self):
        return {
            "show_skeleton": self.skeleton_toggle.isChecked(),
            "show_landmarks": self.landmarks_toggle.isChecked(),
            "show_head_line": self.head_line_toggle.isChecked(),
            "show_shoulder_line": self.shoulder_line_toggle.isChecked(),
            "show_hip_line": self.hip_line_toggle.isChecked(),
            "show_trunk_msl": self.trunk_msl_toggle.isChecked(),
            "show_diaphragm_line": self.diaphragm_toggle.isChecked(),
            "show_asis_line": self.asis_toggle.isChecked(),
            "show_iliac_crest": self.iliac_crest_toggle.isChecked(),
            "show_hip_level": self.hip_level_toggle.isChecked(),
            "show_angle_labels": self.angle_labels_toggle.isChecked(),
        }

    def update_status(self, result):
        self._update_head(result)
        self._update_head_tilt(result)
        self._update_trunk_rotation(result)
        self._update_trunk_level(result)

    # -- status cards -------------------------------------------

    def _update_head(self, result):
        status = result.head_rotation_status
        value = result.head_rotation_deg
        if status == "not_detected":
            self.head_card.set_value(0, status, "No face detected")
            return
        side = "left" if value > 0 else "right"
        if status == "ok":
            text = "Level" if abs(value) < 0.5 else f"Slight turn {side} {abs(value):.0f}\N{DEGREE SIGN}"
        else:
            text = f"Rotated {side} {abs(value):.0f}\N{DEGREE SIGN}"
        self.head_card.set_value(value, status, text)

    def _update_head_tilt(self, result):
        status = result.head_tilt_status
        value = result.head_tilt_deg
        if status == "not_detected":
            self.head_tilt_card.set_value(0, status, "No face detected")
            return
        side = "left" if value > 0 else "right"
        if status == "ok":
            text = "Level" if abs(value) < 0.5 else f"Slight tilt {side} {abs(value):.0f}\N{DEGREE SIGN}"
        else:
            correction = "right" if side == "left" else "left"
            text = (
                f"Tilted {side} {abs(value):.0f}\N{DEGREE SIGN} — "
                f"straighten to the {correction}"
            )
        self.head_tilt_card.set_value(value, status, text)

    def _update_trunk_rotation(self, result):
        status = result.trunk_rotation_status
        value = result.trunk_rotation_deg
        if status == "not_detected":
            self.trunk_rotation_card.set_value(0, status, "Trunk not detected")
            return
        side = "left" if value > 0 else "right"
        if status == "ok":
            text = "Square to detector"
        else:
            text = f"Rotated {side} {abs(value):.0f}\N{DEGREE SIGN}"
        self.trunk_rotation_card.set_value(value, status, text)

    def _update_trunk_level(self, result):
        status = result.trunk_level_status
        value = result.trunk_level_deg
        if status == "not_detected":
            self.trunk_level_card.set_value(0, status, "Trunk not detected")
            return
        if status == "ok":
            text = "Level"
        else:
            text = (
                "Left shoulder lower" if value > 0 else "Right shoulder lower"
            )
        self.trunk_level_card.set_value(value, status, text)

    # -- events -------------------------------------------------

    def _on_source_toggled(self, checked):
        if not checked or self._suppress:
            return
        if self.webcam_radio.isChecked():
            self.source_selected.emit("webcam", self.camera_combo.currentData())
        else:
            if self._file_path:
                self.source_selected.emit("file", self._file_path)
            else:
                self.browse_requested.emit()

    def _on_camera_changed(self, _index):
        if self.webcam_radio.isChecked() and not self._suppress:
            self.source_selected.emit("webcam", self.camera_combo.currentData())

    def _emit_overlay_flags(self, _checked=None):
        self.overlay_changed.emit(self.overlay_flags())
