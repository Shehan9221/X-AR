import math

from PyQt5.QtCore import QPointF, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QWidget

from xar.gui import config

# Standard MediaPipe pose connections (33 landmarks).
POSE_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8),
    (9, 10),
    (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21),
    (17, 19), (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (11, 23), (12, 24), (23, 24), (23, 25), (24, 26),
    (25, 27), (26, 28), (27, 29), (28, 30), (29, 31), (30, 32),
    (27, 31), (28, 32),
]

# Face-mesh midline points, forehead -> chin.
MIDLINE_INDICES = [10, 168, 4, 164, 0, 17, 152]

# Trunk reference lines -> (label, colour key, overlay flag).
TRUNK_LINE_SPECS = {
    "msl": ("MSL", "trunk_msl", "show_trunk_msl"),
    "asis": ("ASIS", "asis", "show_asis_line"),
    "hip_level": ("Hip level", "hip_level", "show_hip_level"),
}


class VideoOverlay(QWidget):
    """Transparent overlay painted with QPainter on top of the video frame.

    The widget geometry is set to exactly the letterboxed video rectangle, so
    normalized landmark coordinates map directly onto widget coordinates.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)

        self.show_skeleton = True
        self.show_landmarks = True
        self.show_head_line = True
        self.show_shoulder_line = True
        self.show_hip_line = True
        self.show_trunk_msl = True
        self.show_asis_line = True
        self.show_hip_level = True
        self.show_angle_labels = True

        self.pose_result = None
        self._label_font = QFont("Segoe UI", 9, QFont.DemiBold)

    # -- data / settings ----------------------------------------

    def set_pose_result(self, pose_result):
        self.pose_result = pose_result
        self.update()

    def set_overlay_flags(self, **flags):
        for name, value in flags.items():
            if hasattr(self, name):
                setattr(self, name, bool(value))
        self.update()

    # -- painting -----------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setFont(self._label_font)

        result = self.pose_result
        if result is None:
            return

        has_pose = bool(result.landmarks)
        if not has_pose and not result.head_landmarks:
            self._draw_label(painter, 16, 16, "Pose not detected", (154, 154, 154))
            return

        if self.show_skeleton and has_pose:
            self._draw_skeleton(painter, result.landmarks)
        if self.show_landmarks and has_pose:
            self._draw_landmarks(painter, result.landmarks)
        if self.show_head_line:
            self._draw_head_lines(painter, result)
        if has_pose:
            self._draw_trunk_reference_lines(painter, result)
        if self.show_shoulder_line and has_pose:
            self._draw_trunk_line(painter, result, 11, 12, "Shoulder",
                                  result.trunk_level_status, result.trunk_level_deg)
        if self.show_hip_line and has_pose:
            self._draw_trunk_line(painter, result, 23, 24, "Hips",
                                  result.trunk_rotation_status, None)

        if self.show_angle_labels:
            self._draw_angle_summary(painter, result)

    # -- individual overlays ------------------------------------

    def _draw_skeleton(self, painter, landmarks):
        pen = QPen(QColor(*config.OVERLAY_COLORS["skeleton"]))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        for a, b in POSE_CONNECTIONS:
            pa, pb = self._point(landmarks, a), self._point(landmarks, b)
            if pa and pb:
                painter.drawLine(pa, pb)

    def _draw_landmarks(self, painter, landmarks):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(*config.OVERLAY_COLORS["landmark"]))
        for lm in landmarks:
            if lm[2] > 0.4:
                painter.drawEllipse(self._to_px(lm[0], lm[1]), 2.2, 2.2)
        painter.setBrush(Qt.NoBrush)

    def _draw_head_lines(self, painter, result):
        face = result.head_landmarks

        # Interpupillary line: irises first, eye corners as a fallback.
        ipl = self._segment_from_face(face, 468, 473) or self._segment_from_face(face, 33, 263)
        if ipl:
            self._draw_line(painter, ipl, config.OVERLAY_COLORS["head_ipl"], 2.0)
            self._draw_label(painter, ipl[1].x() + 6, ipl[1].y(),
                             "IPL", config.OVERLAY_COLORS["head_ipl"])

        # Medial sagittal line: fit a straight line through the face midline.
        msl = self._face_midline(face)
        if msl:
            self._draw_line(painter, msl, config.OVERLAY_COLORS["head_msl"], 2.0)
            self._draw_label(painter, msl[1].x() + 6, msl[1].y(),
                             "MSL", config.OVERLAY_COLORS["head_msl"])

    def _draw_trunk_reference_lines(self, painter, result):
        """ASIS, mid-sagittal and hip-level lines from the torso reference."""
        # ASIS and hip level sit close together, so their labels are placed on
        # opposite sides of the line to avoid overlapping.
        label_offsets = {"msl": (6, 0), "asis": (6, -20), "hip_level": (6, 4)}
        for line in result.trunk_lines:
            spec = TRUNK_LINE_SPECS.get(line.kind)
            if spec is None:
                continue
            label, color_key, flag = spec
            if not getattr(self, flag, True):
                continue
            p1 = self._to_px(*line.p1)
            p2 = self._to_px(*line.p2)
            color = config.OVERLAY_COLORS[color_key]
            self._draw_line(painter, (p1, p2), color, 2.0)
            dx, dy = label_offsets.get(line.kind, (6, 0))
            self._draw_label(painter, p2.x() + dx, p2.y() + dy, label, color)

    def _draw_trunk_line(self, painter, result, a, b, name, status, angle):
        pts = self._segment(result.landmarks, a, b)
        if not pts:
            return
        color = config.OVERLAY_COLORS["shoulder" if a == 11 else "hip"]
        self._draw_line(painter, pts, color, 2.5)

        if self.show_angle_labels:
            if status == "not_detected":
                angle = None
            if angle is None:
                dx = pts[1].x() - pts[0].x()
                dy = pts[1].y() - pts[0].y()
                angle = math.degrees(math.atan2(dy, dx))
                # Normalise to (-90, 90] so a horizontal line reads ~0°, not ~180°.
                if angle > 90:
                    angle -= 180
                elif angle < -90:
                    angle += 180
            text = f"{name} {angle:+.1f}\N{DEGREE SIGN}"
        else:
            text = name
        mid = QPointF((pts[0].x() + pts[1].x()) / 2, (pts[0].y() + pts[1].y()) / 2)
        self._draw_label(painter, mid.x() + 8, mid.y() - 8, text,
                         config.STATUS_COLORS_RGB.get(status, (255, 255, 255)))

    def _draw_angle_summary(self, painter, result):
        """Small HUD with the three measured angles."""
        rows = [
            ("Head rot", result.head_rotation_deg, result.head_rotation_status),
            ("Head tilt", result.head_tilt_deg, result.head_tilt_status),
            ("Trunk rot", result.trunk_rotation_deg, result.trunk_rotation_status),
            ("Trunk level", result.trunk_level_deg, result.trunk_level_status),
        ]
        y = 14
        for label, value, status in rows:
            if status == "not_detected":
                text = f"{label}: --"
                color = config.STATUS_COLORS_RGB["not_detected"]
            else:
                text = f"{label}: {value:+.1f}\N{DEGREE SIGN}"
                color = config.STATUS_COLORS_RGB.get(status, (255, 255, 255))
            rect = self._draw_label(painter, self.width() - 8, y, text, color,
                                    align_right=True)
            y += rect.height() + 4

    # -- helpers ------------------------------------------------

    def _to_px(self, x, y):
        return QPointF(x * self.width(), y * self.height())

    def _point(self, landmarks, index):
        if index >= len(landmarks):
            return None
        lm = landmarks[index]
        if lm[2] <= 0.35:
            return None
        return self._to_px(lm[0], lm[1])

    def _segment(self, landmarks, a, b):
        pa, pb = self._point(landmarks, a), self._point(landmarks, b)
        if pa is None or pb is None:
            return None
        return pa, pb

    def _segment_from_face(self, face, a, b):
        if a not in face or b not in face:
            return None
        pa, pb = face[a], face[b]
        if pa[2] <= 0.1 or pb[2] <= 0.1:
            return None
        return self._to_px(pa[0], pa[1]), self._to_px(pb[0], pb[1])

    def _face_midline(self, face):
        points = [face[i] for i in MIDLINE_INDICES if i in face and face[i][2] > 0.1]
        if len(points) < 2:
            return None

        # Least-squares fit x = a*y + b so the line follows the real face axis.
        n = len(points)
        sy = sum(p[1] for p in points)
        sx = sum(p[0] for p in points)
        syy = sum(p[1] * p[1] for p in points)
        sxy = sum(p[0] * p[1] for p in points)
        denom = n * syy - sy * sy
        if abs(denom) < 1e-9:
            return None
        a = (n * sxy - sx * sy) / denom
        b = (sx - a * sy) / n

        ys = [p[1] for p in points]
        y_top, y_bottom = min(ys) - 0.02, max(ys) + 0.02
        top = (a * y_top + b, y_top)
        bottom = (a * y_bottom + b, y_bottom)
        return self._to_px(*top), self._to_px(*bottom)

    def _draw_line(self, painter, segment, color, width):
        pen = QPen(QColor(*color))
        pen.setWidthF(width)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(segment[0], segment[1])
        painter.setBrush(QColor(*color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(segment[0], 3.0, 3.0)
        painter.drawEllipse(segment[1], 3.0, 3.0)
        painter.setBrush(Qt.NoBrush)

    def _draw_label(self, painter, x, y, text, color, align_right=False):
        metrics = painter.fontMetrics()
        pad_x, pad_y = 5, 3
        width = metrics.horizontalAdvance(text) + pad_x * 2
        height = metrics.height() + pad_y * 2
        if align_right:
            x -= width
        if x < 4:
            x = 4
        if x + width > self.width() - 4:
            x = max(4, self.width() - width - 4)
        if y + height > self.height() - 4:
            y = max(4, self.height() - height - 4)

        rect = QRect(int(x), int(y), int(width), int(height))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 20, 20, 190))
        painter.drawRoundedRect(rect, 4, 4)

        painter.setPen(QColor(*color))
        painter.drawText(rect.adjusted(pad_x, 0, -pad_x, 0),
                         Qt.AlignVCenter | Qt.AlignLeft, text)
        return rect


class VideoDisplay(QWidget):
    double_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("videoContainer")
        self.setMinimumSize(480, 320)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

        self.current_frame = None
        self.scaled_pixmap = None
        self.overlay = VideoOverlay(self)
        self.overlay.show()
        self.overlay.raise_()

    def set_frame(self, qimage, pose_result=None):
        if qimage is not None:
            self.current_frame = qimage
            self.update_pixmap()
        if pose_result is not None:
            self.overlay.set_pose_result(pose_result)
        self.update()

    def clear(self):
        self.current_frame = None
        self.scaled_pixmap = None
        self.overlay.set_pose_result(None)
        self.overlay.setGeometry(0, 0, 0, 0)
        self.update()

    def update_pixmap(self):
        if not self.current_frame:
            return
        pixmap = QPixmap.fromImage(self.current_frame)
        scaled = pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.scaled_pixmap = scaled
        rect = self._frame_rect()
        self.overlay.setGeometry(rect)
        self.overlay.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_pixmap()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        if self.scaled_pixmap:
            rect = self._frame_rect()
            painter.drawPixmap(rect, self.scaled_pixmap)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def _frame_rect(self):
        if not self.scaled_pixmap:
            return QRect(0, 0, self.width(), self.height())
        x = (self.width() - self.scaled_pixmap.width()) // 2
        y = (self.height() - self.scaled_pixmap.height()) // 2
        return QRect(x, y, self.scaled_pixmap.width(), self.scaled_pixmap.height())

    def set_overlay_flags(self, **flags):
        self.overlay.set_overlay_flags(**flags)
