import cv2
import numpy as np
import io
from typing import Optional


class DocumentScanner:
    """Document scanning and deskewing service"""

    @staticmethod
    def scan_and_deskew(image_buffer: io.BytesIO, pad_percent: float = 0.035) -> io.BytesIO:
        """Detect, deskew and return scanned document"""
        from utils.helpers import bytes_to_image, image_to_bytes

        try:
            img = bytes_to_image(image_buffer)
            if img is None:
                image_buffer.seek(0)
                return image_buffer

            # Detect document quad
            quad = DocumentScanner._detect_quad(img)
            if quad is None:
                return image_to_bytes(img)

            # Warp perspective
            warped = DocumentScanner._warp_document(img, quad, pad_percent)

            # Check if result is reasonable (at least 60% of original area)
            original_area = img.shape[0] * img.shape[1]
            warped_area = warped.shape[0] * warped.shape[1]

            if warped_area < 0.60 * original_area:
                return image_to_bytes(img)

            return image_to_bytes(warped)

        except Exception:
            image_buffer.seek(0)
            return image_buffer

    @staticmethod
    def _detect_quad(image: np.ndarray) -> Optional[np.ndarray]:
        """Detect document quadrilateral"""
        # Resize for processing
        height, width = image.shape[:2]
        max_dim = 1800
        scale = 1.0
        if max(height, width) > max_dim:
            scale = max_dim / max(height, width)
            resized = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        else:
            resized = image

        # Edge detection
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 50, 150, L2gradient=True)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        edges = cv2.dilate(edges, kernel, iterations=1)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)

        # Find contours
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        img_area = resized.shape[0] * resized.shape[1]
        best_quad = None
        best_score = 0.0

        for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:20]:
            area = cv2.contourArea(cnt)
            if area < 0.10 * img_area:
                continue

            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)

            if len(approx) == 4 and cv2.isContourConvex(approx):
                quad = approx.reshape(4, 2).astype(np.float32)
                score = area / img_area
                if score > best_score:
                    best_score = score
                    best_quad = quad

        if best_quad is None:
            return None

        # Scale back to original coordinates
        quad_orig = (best_quad / scale).astype(np.float32)
        return DocumentScanner._order_points(quad_orig)

    @staticmethod
    def _order_points(pts: np.ndarray) -> np.ndarray:
        """Order points: TL, TR, BR, BL"""
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]  # TL
        rect[2] = pts[np.argmax(s)]  # BR
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]  # TR
        rect[3] = pts[np.argmax(diff)]  # BL
        return rect

    @staticmethod
    def _warp_document(image: np.ndarray, quad: np.ndarray, pad_percent: float) -> np.ndarray:
        """Warp perspective to rectangle"""
        # Add padding
        center = quad.mean(axis=0, keepdims=True)
        quad = (center + (quad - center) * (1.0 + pad_percent)).astype(np.float32)

        tl, tr, br, bl = quad

        width_top = np.linalg.norm(tr - tl)
        width_bottom = np.linalg.norm(br - bl)
        max_w = int(max(width_top, width_bottom))

        height_left = np.linalg.norm(bl - tl)
        height_right = np.linalg.norm(br - tr)
        max_h = int(max(height_left, height_right))

        dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(quad, dst)
        return cv2.warpPerspective(image, M, (max_w, max_h), flags=cv2.INTER_CUBIC)