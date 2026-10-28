# X-AR — Radiography Positioning Guidance

Desktop GUI (PyQt5) for checking patient positioning before an X-ray. It shows a
live webcam or video file with a QPainter pose overlay and reports **head
rotation**, **head tilt**, **trunk rotation** and **trunk level**. The look is
modelled on VLC:
dark charcoal surfaces, one teal accent, thin borders and a collapsible panel.

## Install and run

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
python main.py
```

`requirements.txt` adds PyQt5 to the existing MediaPipe/OpenCV stack.

## What you get

- **Media** menu: Open File…, Open Webcam, Close, Quit.
- **View** menu: show/hide the side panel, fullscreen, and a runtime
  *Use Mock Engine* switch.
- **Help**: About.
- Side panel (280 px, collapsible):
  - Source: Webcam / Video File, Browse…, camera selector, current file name.
  - Status: four cards with the measured angle, coloured badge
    (green OK / amber Adjust / grey Not detected) and a short instruction.
  - Guidelines: switches for skeleton, landmarks, head MSL/IPL line,
    shoulder line, hip line, trunk midline (MSL), diaphragm level, ASIS line,
    iliac crest level, hip level and angle labels.
- Video area keeps aspect ratio with black letterboxing; double-click toggles
  fullscreen.
- Transport bar: play/pause, stop, seek slider with elapsed/total time in video
  mode; a red **LIVE** badge in webcam mode.
- Status bar: current source, resolution and processing FPS.

## Where the real core is plugged in

Only `xar/gui/adapter.py` talks to core code.

1. `RealCoreEngine._load_core_module()` loads the existing implementation from
   `code/pose_detection` (path set by `CORE_MODULE_PATH` in
   `xar/gui/config.py`). The model file is `pose_landmarker_full.task`.
2. `RealCoreEngine.process_frame()` is the marked TODO area. It calls the
   core's existing measurement methods:
   - `HeadPoseAssessment` + `HeadAnatomicalLine` + `HeadPossitioningTracker`
     → head rotation and head tilt (roll from the fitted MSL),
   - `TrunkPoseAssessment` + `TrunkPositioningTracker` → trunk rotation and
     level.
   The core's own `cv2` drawing is **not** used; the GUI redraws everything
   with QPainter.
3. If you move the implementation into `xar/core/`, change
   `CORE_MODULE_PATH` (or replace the loader with
   `from xar.core import ...`) and adjust the class names in
   `RealCoreEngine`.

### Mock vs real engine

- `USE_MOCK = False` in `xar/gui/config.py` uses the real core (default, so the
  overlay tracks real people).
- `USE_MOCK = True` uses `MockPoseEngine` with smoothly changing fake values —
  handy for UI work without a camera or model.
- The same switch is available at runtime under **View → Use Mock Engine**.

Both engines implement `process_frame(frame_bgr, timestamp_ms) -> PoseResult`
and `reset()`. The adapter is the single integration point.

### Status thresholds

All angle limits live in one place, `xar/gui/config.py`:

```python
THRESHOLDS = {
    "head_rotation":  {"ok": 3.0, "adjust": 6.0},
    "head_tilt":      {"ok": 3.0, "adjust": 6.0},
    "trunk_rotation": {"ok": 5.0, "adjust": 10.0},
    "trunk_level":    {"ok": 3.0, "adjust": 6.0},
}
```

`|value| <= ok` → OK, `ok < |value| <= adjust` → Adjust, otherwise Adjust as
well (only three statuses are exposed to the GUI).

## Threading model

`xar/gui/worker.py` runs a `QThread` that owns `cv2.VideoCapture` and the pose
adapter. It emits `QImage` + `PoseResult` via `pyqtSignal`; all widget updates
happen in slots on the main thread. Timestamps handed to MediaPipe increase
strictly even after a seek, and `adapter.reset()` is called on every seek and
source switch. The thread is stopped on close, source change and Stop.

## Project layout

```
main.py                     entry point (imports MediaPipe before PyQt5)
xar/
  core/                     untouched
  gui/
    adapter.py              ONLY bridge to core; PoseResult; mock engine
    config.py               thresholds, colours, paths, engine flag
    style.qss               single dark stylesheet
    side_panel.py           source / status / guidelines sections
    toggle_switch.py        iOS-style switch widget
    video_widget.py         letterboxed video + QPainter overlay
    control_bar.py           transport controls
    worker.py               QThread capture + processing
README.md
requirements.txt
```

## Notes

- On Windows, MediaPipe's native bindings fail if PyQt5 is imported first.
  `main.py` loads the core/MediaPipe before PyQt5; keep that order if you
  embed the window in another script.
- If you fork audio/video work, remember `PoseResult.landmarks` are normalized
  `(x, y, visibility)` pose points and `head_landmarks` are FaceMesh points by
  index (468/473 irises, 33/263 eye corners, 10…152 midline).
- `MIRROR_PREVIEW` is `False`; enable it only together with mirroring the
  frames, or left/right advice will be inverted.
