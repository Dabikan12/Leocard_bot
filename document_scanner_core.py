import cv2
import numpy as np


def _resize_for_processing(image: np.ndarray, max_dim: int = 1800) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    if max(height, width) <= max_dim:
        return image, 1.0
    scale = max_dim / float(max(height, width))
    new_size = (int(width * scale), int(height * scale))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA), scale


def _preprocess_edges(bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150, L2gradient=True)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.dilate(edges, kernel, iterations=1)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    return edges


def _quad_score(contour: np.ndarray, image_area: float) -> tuple[float, np.ndarray | None]:
    area = cv2.contourArea(contour)
    if area < 0.10 * image_area:
        return 0.0, None

    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    if len(approx) != 4 or not cv2.isContourConvex(approx):
        return 0.0, None

    quad = approx.reshape(4, 2).astype(np.float32)
    rect = cv2.minAreaRect(approx)
    (w, h) = rect[1]
    rect_area = max(w, 1) * max(h, 1)
    if rect_area <= 1e-6:
        return 0.0, None
    fill_ratio = float(area) / float(rect_area)
    area_norm = float(area) / float(image_area)

    aspect = max(w, h) / max(min(w, h), 1e-6)
    if aspect < 0.4 or aspect > 2.8:
        return 0.0, None

    score = area_norm * (0.7 + 0.3 * fill_ratio)
    return score, quad


def _order_points_clockwise(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _expand_quad(quad: np.ndarray, percent: float = 0.035) -> np.ndarray:
    center = quad.mean(axis=0, keepdims=True)
    return (center + (quad - center) * (1.0 + percent)).astype(np.float32)


def detect_document_quad(image_bgr: np.ndarray) -> np.ndarray | None:
    """Return ordered 4x2 float32 document corners in original image coords.

    Returns None if no suitable contours are found.
    """
    resized, scale = _resize_for_processing(image_bgr)
    edges = _preprocess_edges(resized)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    img_area = float(resized.shape[0] * resized.shape[1])
    best_score = 0.0
    best_quad: np.ndarray | None = None

    for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:20]:
        score, quad = _quad_score(cnt, img_area)
        if score > best_score and quad is not None:
            best_score = score
            best_quad = quad

    if best_quad is None:
        # Fallback to minAreaRect if any contour exists
        try:
            largest = max(contours, key=cv2.contourArea)
            rect = cv2.minAreaRect(largest)
            best_quad = cv2.boxPoints(rect).astype(np.float32)
        except Exception:
            return None

    quad_orig = (best_quad / scale).astype(np.float32)
    return _order_points_clockwise(quad_orig)


def warp_document(image_bgr: np.ndarray, quad: np.ndarray, pad_percent: float = 0.035) -> np.ndarray:
    """Warp the document defined by `quad` (ordered TL,TR,BR,BL) with padding."""
    quad = _expand_quad(quad, percent=pad_percent)
    tl, tr, br, bl = quad

    width_top = np.linalg.norm(tr - tl)
    width_bottom = np.linalg.norm(br - bl)
    max_w = int(round(max(width_top, width_bottom)))

    height_left = np.linalg.norm(bl - tl)
    height_right = np.linalg.norm(br - tr)
    max_h = int(round(max(height_left, height_right)))

    dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    return cv2.warpPerspective(image_bgr, M, (max_w, max_h), flags=cv2.INTER_CUBIC)


def _auto_orient(image: np.ndarray) -> tuple[np.ndarray, str]:
    h, w = image.shape[:2]
    if h >= w:
        return image, "student_id"
    return image, "passport"


def scan_document_auto(
    image_bgr: np.ndarray,
    pad_percent: float = 0.035,
) -> tuple[np.ndarray, str]:
    """Detect, deskew and return the scanned document image.

    - Preserves original color
    - Returns (result_image, detected_type)
    - If detection fails or cropped area < 60% of original, returns original
    """
    original_h, original_w = image_bgr.shape[:2]
    original_area = float(original_h * original_w)

    quad = detect_document_quad(image_bgr)
    if quad is None:
        # No contours detected -> return original
        return image_bgr, _auto_orient(image_bgr)[1]

    warped = warp_document(image_bgr, quad, pad_percent=pad_percent)
    oriented, doc_type = _auto_orient(warped)

    # Ensure the final image area is at least 60% of original
    final_area = float(oriented.shape[0] * oriented.shape[1])
    if final_area < 0.60 * original_area:
        return image_bgr, _auto_orient(image_bgr)[1]

    return oriented, doc_type


def scan_document_auto_from_path(
    image_path: str,
    pad_percent: float = 0.035,
) -> tuple[np.ndarray, str]:
    """Convenience wrapper that reads the image from disk."""
    image_bgr = cv2.imread(image_path)
    if image_bgr is None:
        raise FileNotFoundError(f"Unable to read image: {image_path}")
    return scan_document_auto(image_bgr, pad_percent=pad_percent)


