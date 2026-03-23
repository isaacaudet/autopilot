"""Auto-detect facecam PiP overlay in Twitch stream clips.

Two-stage detection:
  1. Haar cascades find face candidates in corner regions (good at small faces)
  2. MediaPipe BlazeFace confirms each candidate is a real human face
     (rejects game character portraits, HUD icons, etc.)

Corner-focused: webcam overlays are always in a corner of the frame.
A face that appears consistently in the same corner across multiple
sampled frames is the facecam.

Returns normalized 0..1 bounding rect of the webcam window.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Top 12% excluded — game scoreboards (Deadlock, CS, Valorant, etc.)
# have character portraits there that fool both Haar and MediaPipe.
_HUD_TOP = 0.12

# Overlapping regions — webcams often sit near the vertical midpoint.
_CORNERS = {
    "top_left":     (0.0, _HUD_TOP, 0.35, 0.55),
    "top_right":    (0.65, _HUD_TOP, 1.0, 0.55),
    "bottom_left":  (0.0, 0.45, 0.35, 1.0),
    "bottom_right": (0.65, 0.45, 1.0, 1.0),
}


def _find_tightest_cluster(
    detections: list[tuple[float, float, float, float]],
    min_size: int,
    max_spread: float = 0.06,
) -> list[tuple[float, float, float, float]] | None:
    """Find the largest group of detections where x and y spread < max_spread."""
    if len(detections) < min_size:
        return None

    xs = [d[0] for d in detections]
    ys = [d[1] for d in detections]
    med_x = sorted(xs)[len(xs) // 2]
    med_y = sorted(ys)[len(ys) // 2]

    scored = sorted(
        ((abs(d[0] - med_x) + abs(d[1] - med_y), i) for i, d in enumerate(detections))
    )

    cluster_indices: list[int] = []
    cluster_xs: list[float] = []
    cluster_ys: list[float] = []
    for _, idx in scored:
        x, y = detections[idx][0], detections[idx][1]
        test_xs = cluster_xs + [x]
        test_ys = cluster_ys + [y]
        if max(test_xs) - min(test_xs) <= max_spread and max(test_ys) - min(test_ys) <= max_spread:
            cluster_indices.append(idx)
            cluster_xs.append(x)
            cluster_ys.append(y)

    if len(cluster_indices) >= min_size:
        return [detections[i] for i in cluster_indices]
    return None


def _filter_size_outliers(
    detections: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Remove detections with face size >1.5x or <0.6x the median width.

    Real webcam faces are very consistent in size (same camera, same distance).
    Game character faces vary wildly. Tight tolerance separates the two.
    """
    if len(detections) < 3:
        return detections
    ws = sorted(d[2] for d in detections)
    med_w = ws[len(ws) // 2]
    return [d for d in detections if 0.6 * med_w <= d[2] <= 1.5 * med_w]


def _get_mediapipe_detector():
    """Create a MediaPipe face detector for confirmation. Returns None if unavailable."""
    try:
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        import urllib.request
        import os

        model_path = os.path.expanduser("~/.cache/mediapipe/blaze_face_short_range.tflite")
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        if not os.path.exists(model_path):
            urllib.request.urlretrieve(
                "https://storage.googleapis.com/mediapipe-models/face_detector/"
                "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite",
                model_path,
            )
        return vision.FaceDetector.create_from_options(
            vision.FaceDetectorOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=model_path),
                min_detection_confidence=0.55,
            )
        )
    except Exception as e:
        logger.info("MediaPipe unavailable (%s), skipping confirmation step", e)
        return None


def _confirm_real_face(frame, fx: int, fy: int, fw: int, fh: int, mp_detector) -> bool:
    """Use MediaPipe to confirm a Haar detection is a real human face, not game art."""
    import cv2
    import mediapipe as mp

    h, w = frame.shape[:2]
    # Expand the crop area around the detected face for better context
    pad = max(fw, fh)
    x0 = max(0, fx - pad)
    y0 = max(0, fy - pad)
    x1 = min(w, fx + fw + pad)
    y1 = min(h, fy + fh + pad)
    crop = frame[y0:y1, x0:x1]

    # Upscale small crops so MediaPipe can see the face
    ch, cw = crop.shape[:2]
    min_dim = min(cw, ch)
    if min_dim < 200:
        scale = max(2, 256 // min_dim)
        crop = cv2.resize(crop, (cw * scale, ch * scale))

    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    result = mp_detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if result.detections:
        conf = result.detections[0].categories[0].score
        logger.debug("MediaPipe confirm: conf=%.3f at (%d,%d,%d,%d)", conf, fx, fy, fw, fh)
        return True
    return False


def detect_facecam_rect(
    video_path: str | Path,
    *,
    num_frames: int = 12,
    max_region_ratio: float = 0.45,
    min_detections: int = 3,
) -> dict | None:
    """Detect a fixed-position facecam overlay in a video.

    Stage 1: Haar cascades scan corner regions for face candidates.
    Stage 2: MediaPipe confirms candidates are real human faces.

    Returns {"rect": {...}, "tight": {...}} where both contain normalized 0..1 coords.
    "rect" is the padded webcam window; "tight" is just the head/face region.
    Returns None if no consistent facecam region found.
    """
    try:
        import cv2
    except ImportError:
        logger.warning("opencv not installed — skipping facecam detection")
        return None

    video_path = Path(video_path)
    if not video_path.exists():
        return None

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None

    mp_detector = _get_mediapipe_detector()

    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if total_frames < num_frames or frame_w <= 0 or frame_h <= 0:
            return None

        start = int(total_frames * 0.1)
        end = int(total_frames * 0.9)
        step = max(1, (end - start) // num_frames)
        sample_indices = [start + i * step for i in range(num_frames)]

        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        alt_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml"
        )
        profile_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_profileface.xml"
        )

        corner_detections: dict[str, list[tuple[float, float, float, float]]] = {
            k: [] for k in _CORNERS
        }

        for frame_idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            for corner_name, (rx0, ry0, rx1, ry1) in _CORNERS.items():
                x0 = int(rx0 * frame_w)
                y0 = int(ry0 * frame_h)
                x1 = int(rx1 * frame_w)
                y1 = int(ry1 * frame_h)
                sub = gray[y0:y1, x0:x1]

                clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
                sub = clahe.apply(sub)

                min_face_px = max(20, int(frame_w * 0.03))
                min_face = (min_face_px, min_face_px)

                best_face = None
                best_area = 0
                for casc in (cascade, alt_cascade, profile_cascade):
                    faces = casc.detectMultiScale(sub, scaleFactor=1.05, minNeighbors=2, minSize=min_face)
                    if len(faces) > 0:
                        largest = max(faces, key=lambda f: f[2] * f[3])
                        area = largest[2] * largest[3]
                        if area > best_area:
                            best_face = largest
                            best_area = area

                if best_face is not None:
                    fx, fy_loc, fw, fh = best_face

                    # Stage 2: confirm with MediaPipe (if available)
                    if mp_detector is not None:
                        abs_x = x0 + fx
                        abs_y = y0 + fy_loc
                        if not _confirm_real_face(frame, abs_x, abs_y, fw, fh, mp_detector):
                            continue  # game art, not a real face

                    corner_detections[corner_name].append((
                        (x0 + fx) / frame_w,
                        (y0 + fy_loc) / frame_h,
                        fw / frame_w,
                        fh / frame_h,
                    ))

        best_corner = None
        best_cluster: list[tuple[float, float, float, float]] = []

        for corner_name, dets in corner_detections.items():
            if len(dets) < min_detections:
                continue
            # Filter outlier face sizes (>2x or <0.5x median width)
            dets = _filter_size_outliers(dets)
            if len(dets) < min_detections:
                continue
            cluster = _find_tightest_cluster(dets, min_detections)
            if cluster and len(cluster) > len(best_cluster):
                best_cluster = cluster
                best_corner = corner_name

        if best_corner is None or not best_cluster:
            det_counts = {k: len(v) for k, v in corner_detections.items() if v}
            logger.debug(
                "Facecam detection: no corner had %d+ confirmed faces. Counts: %s",
                min_detections, det_counts,
            )
            return None

        logger.debug(
            "Facecam detection: best corner=%s with %d confirmed faces",
            best_corner, len(best_cluster),
        )
        return _cluster_to_rect(best_cluster, best_corner, max_region_ratio)

    finally:
        cap.release()
        if mp_detector is not None:
            mp_detector.close()


def _cluster_to_rect(
    detections: list[tuple[float, float, float, float]],
    corner: str,
    max_region_ratio: float,
) -> dict | None:
    """Convert a cluster of face detections into a webcam window rect."""
    xs = [d[0] for d in detections]
    ys = [d[1] for d in detections]
    ws = [d[2] for d in detections]
    hs = [d[3] for d in detections]

    x_spread = max(xs) - min(xs)
    y_spread = max(ys) - min(ys)
    avg_w = sum(ws) / len(ws)
    avg_h = sum(hs) / len(hs)
    pad_w = avg_w * 0.8
    pad_h_above = avg_h * 0.4
    pad_h_below = avg_h * 0.9

    region_x = max(0.0, min(xs) - pad_w)
    region_y = max(0.0, min(ys) - pad_h_above)
    region_w = min(1.0 - region_x, max(ws) + 2 * pad_w + x_spread)
    region_h = min(1.0 - region_y, max(hs) + pad_h_above + pad_h_below + y_spread)

    # Snap to corner edges
    corner_rx0, corner_ry0, corner_rx1, corner_ry1 = _CORNERS[corner]
    if corner_rx0 == 0.0 and region_x < 0.05:
        region_w += region_x
        region_x = 0.0
    if corner_ry0 == 0.0 and region_y < 0.05:
        region_h += region_y
        region_y = 0.0
    if corner_rx1 == 1.0 and (region_x + region_w) > 0.95:
        region_w = 1.0 - region_x
    if corner_ry1 == 1.0 and (region_y + region_h) > 0.95:
        region_h = 1.0 - region_y

    if region_w > max_region_ratio or region_h > max_region_ratio:
        logger.debug("Facecam region too large (%.2f x %.2f)", region_w, region_h)
        return None

    rect = {
        "x": round(float(region_x), 4),
        "y": round(float(region_y), 4),
        "w": round(float(region_w), 4),
        "h": round(float(region_h), 4),
    }

    # Tight face rect — median of cluster with minimal padding (for fill-width head crop).
    # Small padding: 10% sides, 20% top, 40% bottom → shows head+forehead+chin, no body.
    med_x = sorted(xs)[len(xs) // 2]
    med_y = sorted(ys)[len(ys) // 2]
    med_w = sorted(ws)[len(ws) // 2]
    med_h = sorted(hs)[len(hs) // 2]
    tight_rect = {
        "x": round(max(0.0, med_x - med_w * 0.05), 4),
        "y": round(max(0.0, med_y - med_h * 0.25), 4),
        "w": round(min(1.0, med_w * 1.1), 4),
        "h": round(min(1.0, med_h * 1.2), 4),
    }

    logger.info("Auto-detected facecam region in %s: window=%s tight=%s", corner, rect, tight_rect)
    return {"rect": rect, "tight": tight_rect}
