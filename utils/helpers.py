import io
import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)


def bytes_to_image(data: bytes | io.BytesIO) -> np.ndarray:
    """Convert bytes or BytesIO to OpenCV image"""
    if isinstance(data, io.BytesIO):
        data.seek(0)
        data = data.read()
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def image_to_bytes(img: np.ndarray, quality: int = 90) -> io.BytesIO:
    """Convert OpenCV image to JPEG BytesIO"""
    success, encoded = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not success:
        raise ValueError("Failed to encode image")
    buffer = io.BytesIO(encoded.tobytes())
    buffer.seek(0)
    return buffer