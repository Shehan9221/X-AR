"""Centralised configuration — thresholds, colours, constants."""

import os

# ── Directories ──────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Engine selection ─────────────────────────────────────────
# False -> real core engine loaded from code/pose_detection (tracks real people)
# True  -> MockPoseEngine (fake but smoothly moving skeleton, no MediaPipe needed)
# Can also be switched at runtime from View -> Use Mock Engine.
USE_MOCK = False

# Where the existing core implementation lives.  Adapter is the only module that
# reads this path — nothing else in the GUI imports core code.
CORE_MODULE_PATH = os.path.join(PROJECT_ROOT, "code", "pose_detection")
CORE_MODEL_PATH = os.path.join(PROJECT_ROOT, "pose_landmarker_full.task")

# Webcam preview is not mirrored; overlays therefore line up 1:1 with frames.
MIRROR_PREVIEW = False

# Core reports head rotation as an interpupillary asymmetry percentage.
# This factor converts that percentage into approximate degrees so the
# status cards can display a degree value.  Calibrate with your core if needed.
HEAD_ROTATION_PCT_PER_DEG = 1.67

# ── Status thresholds (degrees) ──────────────────────────────
# |value| <= ok          -> "ok"
# ok < |value| <= adjust -> "adjust"
# |value| > adjust       -> "adjust" (still needs correction)
THRESHOLDS = {
    "head_rotation": {"ok": 3.0, "adjust": 6.0},
    "head_tilt": {"ok": 3.0, "adjust": 6.0},
    "trunk_rotation": {"ok": 5.0, "adjust": 10.0},
    "trunk_level": {"ok": 3.0, "adjust": 6.0},
}

# ── Status colours ───────────────────────────────────────────
STATUS_COLORS_HEX = {
    "ok": "#22c55e",           # green
    "adjust": "#f59e0b",       # amber
    "not_detected": "#9a9a9a", # grey
}

# RGB tuples for QPainter overlay
STATUS_COLORS_RGB = {
    "ok": (34, 197, 94),
    "adjust": (245, 158, 11),
    "not_detected": (154, 154, 154),
}

# ── Overlay colours (RGB) ────────────────────────────────────
OVERLAY_COLORS = {
    "skeleton": (60, 220, 120),
    "landmark": (255, 255, 255),
    "head_msl": (255, 82, 82),
    "head_ipl": (0, 220, 255),
    "trunk_msl": (255, 82, 82),
    "asis": (0, 255, 0),
    "hip_level": (0, 170, 255),
    "shoulder": (255, 200, 0),
    "hip": (0, 170, 255),
    "label_bg": (20, 20, 20),
    "label_fg": (255, 255, 255),
}

# ── UI constants ─────────────────────────────────────────────
SIDE_PANEL_WIDTH = 280
DEFAULT_FPS = 30
APP_NAME = "X-AR"
APP_VERSION = "0.1.0"

# Toggle-switch colours
SWITCH_ON_RGB = (0, 168, 168)
SWITCH_OFF_RGB = (176, 176, 176)
