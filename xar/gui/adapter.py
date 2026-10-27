"""Bridge between the GUI and the pose-detection core.

Only this file should ever import from core/. The GUI talks to PoseAdapter,
never to core classes directly.
"""

import importlib.util
import math
import os
import threading
import time
from dataclasses import dataclass, field
from importlib.machinery import SourceFileLoader
from typing import Dict, List, Tuple

from xar.gui import config


_CORE_MODULE = None
_ADAPTER = None
_ADAPTER_LOCK = threading.Lock()


def preload_core():
    """Import MediaPipe and the legacy core module on the calling thread.

    mediapipe's import deadlocks when it first happens inside a QThread on
    Windows, so the GUI calls this from the main thread before starting the
    capture worker.  Repeated calls are cheap.
    """
    global _CORE_MODULE
    if _CORE_MODULE is not None:
        return _CORE_MODULE

    # Import heavy dependencies here so the worker thread never imports them.
    import cv2  # noqa: F401
    import mediapipe  # noqa: F401
    from mediapipe import solutions  # noqa: F401
    from mediapipe.framework.formats import landmark_pb2  # noqa: F401
    from mediapipe.tasks import python  # noqa: F401
    from mediapipe.tasks.python import vision  # noqa: F401

    path = config.CORE_MODULE_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(f"Core implementation not found: {path}")
    loader = SourceFileLoader("xar_legacy_core", path)
    spec = importlib.util.spec_from_loader("xar_legacy_core", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    _CORE_MODULE = module
    return module


# ── Data transfer object ─────────────────────────────────────

@dataclass
class TrunkLine:
    """One anatomical reference line as normalized (x, y) endpoints."""
    kind: str  # "msl" | "asis" | "hip_level"
    p1: Tuple[float, float]
    p2: Tuple[float, float]


@dataclass
class PoseResult:
    """One frame's worth of pose analysis."""
    landmarks: List[Tuple[float, float, float]] = field(default_factory=list)  # normalized pose (x, y, visibility)
    head_landmarks: Dict[int, Tuple[float, float, float]] = field(default_factory=dict)  # face-mesh points by index
    trunk_lines: List[TrunkLine] = field(default_factory=list)  # ASIS / trunk MSL / hip level
    timestamp_ms: int = 0

    head_rotation_deg: float = 0.0
    head_tilt_deg: float = 0.0
    trunk_rotation_deg: float = 0.0
    trunk_level_deg: float = 0.0

    head_rotation_status: str = "not_detected"  # "ok" | "adjust" | "not_detected"
    head_tilt_status: str = "not_detected"
    trunk_rotation_status: str = "not_detected"
    trunk_level_status: str = "not_detected"


# ── Helpers ───────────────────────────────────────────────────

def _status(value: float, key: str) -> str:
    t = config.THRESHOLDS[key]
    av = abs(value)
    if av <= t["ok"]:
        return "ok"
    if av <= t["adjust"]:
        return "adjust"
    return "adjust"


# ── Trunk reference geometry ──────────────────────────────────
#
# These constants follow the core's TrunkAnatomicalLine helpers:
#   draw_asis_line(k=0.08), draw_iliac_crest_line(k=0.20),
#   draw_diaphragm_line(k=0.55) and draw_landmark_line(width_ratio=0.8).
# The MSL extension matches draw_mid_sagittal_line(extend_up=25,
# extend_down=100).

ASIS_K = 0.08
HIP_LEVEL_K = 0.0
TRUNK_LINE_WIDTH_RATIO = 0.8
MSL_EXTEND_UP_PX = 25
MSL_EXTEND_DOWN_PX = 100


def _torso_geometry_from_points(left_sho, right_sho, left_hip, right_hip, w, h):
    """Pixel-space torso reference from normalized (x, y) points.

    Same maths as ``TrunkAnatomicalLine.get_torso_geometry``; used by the mock
    engine so it can draw identical guidelines without importing MediaPipe.
    """
    hip_center = (
        (left_hip[0] + right_hip[0]) * w / 2,
        (left_hip[1] + right_hip[1]) * h / 2,
    )
    shoulder_center = (
        (left_sho[0] + right_sho[0]) * w / 2,
        (left_sho[1] + right_sho[1]) * h / 2,
    )
    return {
        "hip_center": hip_center,
        "shoulder_center": shoulder_center,
        "torso_length_px": math.hypot(
            shoulder_center[0] - hip_center[0],
            shoulder_center[1] - hip_center[1],
        ),
        "hip_center_x": hip_center[0],
        "shoulder_width_px": math.hypot(
            (left_sho[0] - right_sho[0]) * w,
            (left_sho[1] - right_sho[1]) * h,
        ),
    }


def _trunk_lines_from_geometry(geo, w, h):
    """Build normalized MSL / ASIS / hip-level segments from torso geometry."""
    if not geo or geo["torso_length_px"] < 1.0:
        return []

    hip_x, hip_y = geo["hip_center"]
    sho_x, sho_y = geo["shoulder_center"]
    torso = geo["torso_length_px"]
    half_width = TRUNK_LINE_WIDTH_RATIO * geo["shoulder_width_px"] / 2

    def norm(p):
        return (p[0] / w, p[1] / h)

    def segment(p1, p2):
        return norm(p1), norm(p2)

    # Mid-sagittal line: shoulder midpoint -> hip midpoint, extended past both.
    dx, dy = hip_x - sho_x, hip_y - sho_y
    top = (sho_x - dx / torso * MSL_EXTEND_UP_PX,
           sho_y - dy / torso * MSL_EXTEND_UP_PX)
    bottom = (hip_x + dx / torso * MSL_EXTEND_DOWN_PX,
              hip_y + dy / torso * MSL_EXTEND_DOWN_PX)

    def horizontal(k):
        y = hip_y - k * torso
        return (hip_x - half_width, y), (hip_x + half_width, y)

    return [
        TrunkLine("msl", *segment(top, bottom)),
        TrunkLine("asis", *segment(*horizontal(ASIS_K))),
        TrunkLine("hip_level", *segment(*horizontal(HIP_LEVEL_K))),
    ]


# ── Mock engine (for GUI testing without real core) ──────────

class MockPoseEngine:
    """Returns smoothly oscillating fake values so the GUI can be tested
    without a real MediaPipe backend."""

    _BASE_LANDMARKS = [
        # 0  nose
        (0.50, 0.15), (0.48, 0.13), (0.52, 0.13), (0.46, 0.14), (0.54, 0.14),
        # 5  mouth area
        (0.47, 0.16), (0.53, 0.16), (0.45, 0.14), (0.55, 0.14), (0.48, 0.17),
        # 10
        (0.52, 0.17),
        # 11 left shoulder, 12 right shoulder
        (0.38, 0.34), (0.62, 0.34),
        # 13 left elbow, 14 right elbow
        (0.32, 0.48), (0.68, 0.48),
        # 15 left wrist, 16 right wrist
        (0.30, 0.62), (0.70, 0.62),
        # 17-22  hand points
        (0.29, 0.64), (0.30, 0.64), (0.31, 0.63),
        (0.71, 0.64), (0.70, 0.64), (0.69, 0.63),
        # 23 left hip, 24 right hip
        (0.43, 0.62), (0.57, 0.62),
        # 25 left knee, 26 right knee
        (0.42, 0.79), (0.58, 0.79),
        # 27 left ankle, 28 right ankle
        (0.41, 0.95), (0.59, 0.95),
        # 29-32  feet
        (0.40, 0.97), (0.60, 0.97), (0.39, 0.98), (0.61, 0.98),
    ]

    # Face-mesh indices used by the overlay, laid out around the mock nose.
    _FACE_TEMPLATE = {
        468: (0.472, 0.125),   # left iris
        473: (0.528, 0.125),   # right iris
        33:  (0.458, 0.128),   # left eye outer corner
        263: (0.542, 0.128),   # right eye outer corner
        10:  (0.500, 0.090),   # forehead midline
        168: (0.500, 0.115),   # nasion
        4:   (0.500, 0.150),   # nose tip
        164: (0.500, 0.168),   # philtrum
        0:   (0.500, 0.178),   # upper lip
        17:  (0.500, 0.192),   # lower lip
        152: (0.500, 0.215),   # chin
    }

    def __init__(self):
        self._t0 = time.monotonic()

    def process_frame(self, frame_bgr, timestamp_ms: int) -> PoseResult:
        t = time.monotonic() - self._t0
        head = 12.0 * math.sin(t * 0.5)
        tilt = 7.0 * math.sin(t * 0.25 + 0.5)
        trunk = 8.0 * math.sin(t * 0.3 + 1.0)
        level = 5.0 * math.sin(t * 0.2 + 2.0)

        sway = 0.004 * math.sin(t * 1.3)
        landmarks = [
            (x + sway, y + 0.003 * math.sin(t * 2 + y * 10), 0.95)
            for x, y in self._BASE_LANDMARKS
        ]

        head_landmarks = {
            idx: (x + sway, y, 0.95)
            for idx, (x, y) in self._FACE_TEMPLATE.items()
        }

        h, w = frame_bgr.shape[:2]
        geo = _torso_geometry_from_points(
            landmarks[11], landmarks[12], landmarks[23], landmarks[24], w, h
        )
        trunk_lines = _trunk_lines_from_geometry(geo, w, h)

        return PoseResult(
            landmarks=landmarks,
            head_landmarks=head_landmarks,
            trunk_lines=trunk_lines,
            timestamp_ms=timestamp_ms,
            head_rotation_deg=head,
            head_tilt_deg=tilt,
            trunk_rotation_deg=trunk,
            trunk_level_deg=level,
            head_rotation_status=_status(head, "head_rotation"),
            head_tilt_status=_status(tilt, "head_tilt"),
            trunk_rotation_status=_status(trunk, "trunk_rotation"),
            trunk_level_status=_status(level, "trunk_level"),
        )

    def reset(self):
        pass

    def close(self):
        pass


# ── Real core engine ─────────────────────────────────────────
#
# TODO: this is where the real core code is called.  The project's existing
# implementation lives in ``code/pose_detection`` (loaded below).  If you move
# your logic into ``xar/core`` instead, replace ``_load_core_module`` with a
# normal import and adjust the constructor / process_frame calls to match.
#
class RealCoreEngine:
    """Wraps the existing MediaPipe based core without letting it draw.

    The core's classes contain cv2 drawing helpers, but we only use their
    measurement methods.  Every overlay is rendered by QPainter in the GUI.
    """

    def __init__(self):
        core = self._load_core_module()
        self._core = core

        self._head_pose = core.HeadPoseAssessment()
        self._head_lines = core.HeadAnatomicalLine()
        self._head_tracker = core.HeadPossitioningTracker()
        self._trunk_pose = core.TrunkPoseAssessment(model_asset_path=config.CORE_MODEL_PATH)
        self._trunk_lines = core.TrunkAnatomicalLine()
        self._trunk_tracker = core.TrunkPositioningTracker()

        # MediaPipe VIDEO mode requires strictly increasing timestamps.
        self._last_ts = -1

    @staticmethod
    def _load_core_module():
        return preload_core()

    def process_frame(self, frame_bgr, timestamp_ms: int) -> PoseResult:
        import cv2
        import mediapipe as mp

        ts = int(timestamp_ms)
        if ts <= self._last_ts:
            # A new video starts its timestamps at 0 again.  MediaPipe's VIDEO
            # mode rejects timestamps that do not strictly increase, so keep the
            # stream monotonic while preserving a ~30 fps step.
            ts = self._last_ts + 33
        self._last_ts = ts
        h, w = frame_bgr.shape[:2]

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # --- head (FaceMesh) ---
        faces = self._head_pose.get_face_landmarks_list(rgb)
        head_landmarks: Dict[int, Tuple[float, float, float]] = {}
        head_deg = 0.0
        head_status = "not_detected"
        head_tilt_deg = 0.0
        head_tilt_status = "not_detected"

        if faces:
            face = faces[0]
            head_landmarks = {i: (lm.x, lm.y, 1.0) for i, lm in enumerate(face)}
            msl = self._head_lines.compute_head_medial_sagittal_line(face, w, h)

            # --- head tilt (roll) from the same fitted MSL ---
            tilt = self._head_tracker.assess_head_tilt(
                msl, mirrored=config.MIRROR_PREVIEW
            )
            # Normalise the sign so + always means the patient's left, which
            # matches the wording on the status card.
            if tilt["side"] == "left":
                head_tilt_deg = tilt["abs_angle_deg"]
            elif tilt["side"] == "right":
                head_tilt_deg = -tilt["abs_angle_deg"]
            else:
                head_tilt_deg = tilt["angle_deg"]
            head_tilt_status = _status(head_tilt_deg, "head_tilt")

            # --- head rotation (yaw) ---
            rotation = self._head_tracker.assess_head_rotation(
                face, msl, w, h, mirrored=config.MIRROR_PREVIEW
            )
            # Core returns asymmetry in percent; convert to approximate degrees.
            head_deg = rotation["asymmetry_pct"] / config.HEAD_ROTATION_PCT_PER_DEG
            head_status = _status(head_deg, "head_rotation")

        # --- trunk (PoseLandmarker) ---
        pose_landmarks, raw = self._trunk_pose.get_pose_landmarks(mp_image, ts)
        landmarks: List[Tuple[float, float, float]] = []
        trunk_lines: List[TrunkLine] = []
        trunk_rot = 0.0
        trunk_level = 0.0
        trunk_rot_status = "not_detected"
        trunk_level_status = "not_detected"

        if pose_landmarks is not None:
            landmarks = [(lm.x, lm.y, lm.visibility) for lm in pose_landmarks]
            geo = self._trunk_lines.get_torso_geometry(pose_landmarks, (h, w))
            trunk_lines = _trunk_lines_from_geometry(geo, w, h)
            m = self._trunk_tracker.update(raw, w, h)
            if m is not None:
                trunk_rot = float(m["shoulder_yaw"])
                trunk_level = float(m["shoulder_tilt"])
                trunk_rot_status = _status(trunk_rot, "trunk_rotation")
                trunk_level_status = _status(trunk_level, "trunk_level")
        elif raw is not None and getattr(raw, "pose_landmarks", None):
            landmarks = [(lm.x, lm.y, lm.visibility) for lm in raw.pose_landmarks[0]]

        return PoseResult(
            landmarks=landmarks,
            head_landmarks=head_landmarks,
            trunk_lines=trunk_lines,
            timestamp_ms=ts,
            head_rotation_deg=head_deg,
            head_tilt_deg=head_tilt_deg,
            trunk_rotation_deg=trunk_rot,
            trunk_level_deg=trunk_level,
            head_rotation_status=head_status,
            head_tilt_status=head_tilt_status,
            trunk_rotation_status=trunk_rot_status,
            trunk_level_status=trunk_level_status,
        )

    def reset(self):
        # Clear temporal smoothing after a seek / source switch.  The
        # MediaPipe graphs themselves are kept alive; the worker supplies
        # monotonically increasing timestamps so VIDEO mode stays happy.
        # NOTE: _last_ts is intentionally *not* reset: PoseLandmarker only
        # accepts strictly increasing timestamps for the lifetime of the graph.
        self._trunk_tracker = self._core.TrunkPositioningTracker()

    def close(self):
        """Release the native MediaPipe graphs (called on application exit)."""
        for resource in (
            getattr(self._head_pose, "fm", None),
            getattr(self._trunk_pose, "detector", None),
        ):
            try:
                if resource is not None:
                    resource.close()
            except Exception:
                pass


# ── Adapter (the ONLY class the GUI instantiates) ────────────

class PoseAdapter:
    """Single entry point the GUI uses for all pose processing.

    Set *use_mock=False* (or config.USE_MOCK) to use the real core.
    """

    def __init__(self, use_mock: bool = True):
        self.use_mock = bool(use_mock)
        # MediaPipe is not re-entrant; serialise access so a worker that is
        # still shutting down can never run a frame at the same time as its
        # replacement.
        self._lock = threading.RLock()
        if self.use_mock:
            self._engine = MockPoseEngine()
        else:
            self._engine = RealCoreEngine()

    def process_frame(self, frame_bgr, timestamp_ms: int) -> PoseResult:
        """Process one BGR frame and return measurements."""
        with self._lock:
            return self._engine.process_frame(frame_bgr, timestamp_ms)

    def reset(self):
        """Call after a seek or source switch to clear internal state."""
        with self._lock:
            self._engine.reset()

    def close(self):
        with self._lock:
            self._engine.close()


def get_adapter(use_mock: bool = True) -> PoseAdapter:
    """Return the process-wide adapter, creating it on first use.

    Reusing a single adapter is important: every ``RealCoreEngine`` builds two
    MediaPipe graphs (FaceMesh + PoseLandmarker).  Creating a new one for every
    video that is browsed exhausts native resources and eventually crashes the
    process.
    """
    global _ADAPTER
    with _ADAPTER_LOCK:
        if _ADAPTER is None or _ADAPTER.use_mock != bool(use_mock):
            if _ADAPTER is not None:
                _ADAPTER.close()
            _ADAPTER = PoseAdapter(use_mock=use_mock)
        return _ADAPTER


def close_shared_adapter():
    """Close the shared adapter and release its native MediaPipe graphs."""
    global _ADAPTER
    with _ADAPTER_LOCK:
        if _ADAPTER is not None:
            _ADAPTER.close()
            _ADAPTER = None
