import cv2
import pytesseract
import numpy as np
import re
import logging
from datetime import datetime
from typing import IO, Optional, List

logger = logging.getLogger(__name__)


def extract_student_card_valid_until(image_buffer: IO[bytes]) -> Optional[str]:
    """
    Extracts and normalizes the "Valid Until" date from a student card image.

    Returns the latest future date (strictly after today) in DD.MM.YYYY if found among
    all detected dates (word-month and numeric). If no future date is found, returns None
    so that the caller can ask the user to provide it manually.

    :param image_buffer: An image file buffer (e.g., io.BytesIO) containing the student card photo.
    :return: A date string in the format "DD.MM.YYYY" or None if not found.
    """
    # Load image from buffer
    image_buffer.seek(0)
    img_array = np.frombuffer(image_buffer.read(), np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

    if img is None:
        raise ValueError("Failed to load image from buffer.")

    # Preprocess the image (grayscale + denoising)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, h=10)

    # OCR configuration
    custom_config = r'--oem 3 --psm 6'
    text = pytesseract.image_to_string(denoised, lang='ukr', config=custom_config)
    try:
        logger.info(f"Student card OCR text (first 400 chars): {text[:400]!r}")
    except Exception:
        pass

    # Normalize OCR text for easier matching
    norm_text = text.lower()
    norm_text = re.sub(r"[\t\r\f]", " ", norm_text)
    norm_text = re.sub(r"\s+", " ", norm_text)
    # Help Tesseract mistakes: insert spaces between digits and letters when missing (e.g., '30червня')
    norm_text = re.sub(r"(\d)([а-яіїєґ])", r"\1 \2", norm_text)
    norm_text = re.sub(r"([а-яіїєґ])(\d)", r"\1 \2", norm_text)

    # Map Ukrainian month words to month numbers (01-12)
    uk_months = {
        "січня": "01", "січень": "01",
        "лютого": "02", "лютий": "02",
        "березня": "03", "березень": "03",
        "квітня": "04", "квітень": "04",
        "травня": "05", "травень": "05",
        "червня": "06", "червень": "06",
        "липня": "07", "липень": "07",
        "серпня": "08", "серпень": "08",
        "вересня": "09", "вересень": "09",
        "жовтня": "10", "жовтень": "10",
        "листопада": "11", "листопад": "11",
        "грудня": "12", "грудень": "12",
    }

    candidate_dates: List[str] = []

    def map_month_token_to_number(token: str) -> Optional[str]:
        # Keep only Ukrainian letters
        cleaned = re.sub(r"[^а-яіїєґ]", "", token)
        if not cleaned:
            return None
        # Direct match
        direct = uk_months.get(cleaned)
        if direct:
            return direct
        # Substring heuristic: does the token contain a known month word?
        for month_word, month_num in uk_months.items():
            if month_word in cleaned:
                return month_num
        return None

    def normalize_day_token_to_digits(token: str) -> Optional[str]:
        # Convert common OCR confusions to digits for day part only (1-2 digits expected)
        if not token:
            return None
        t = token.lower()
        mapped = []
        for ch in t:
            if ch.isdigit():
                mapped.append(ch)
                continue
            if ch in {'o', 'о'}:  # Latin 'o', Cyrillic 'о' → 0
                mapped.append('0')
                continue
            if ch in {'z', 'з'}:  # Latin 'z', Cyrillic 'з' → 3
                mapped.append('3')
                continue
            if ch in {'i', 'і', 'l'}:  # Latin 'i', Cyrillic 'і', Latin 'l' → 1
                mapped.append('1')
                continue
            # ignore other letters/symbols inside day token
        day_str = ''.join(mapped)
        if not day_str:
            return None
        try:
            day_int = int(day_str)
            if 1 <= day_int <= 31:
                return f"{day_int:02d}"
        except ValueError:
            pass
        return None

    # 1) Collect dates explicitly marked as "Дійсний до" / "Діє до"
    for m in re.finditer(r"(дійсний до|діє до)[^\d]*([0-9a-zа-яіїєґ]{1,3})\s*([а-яіїєґ]+)[^\d]{0,10}(\d{4})", norm_text):
        day_token = m.group(2)
        month_token = m.group(3)
        year = m.group(4)
        day_norm = normalize_day_token_to_digits(day_token)
        month_num = map_month_token_to_number(month_token)
        if day_norm and month_num:
            candidate_dates.append(f"{day_norm}.{month_num}.{year}")

    # 2) Collect all dates with month words (allow missing spaces and noisy tokens)
    for m in re.finditer(r"([0-9a-zа-яіїєґ]{1,3})\s*([а-яіїєґ]+)[^\d]{0,10}(\d{4})", norm_text):
        day_token = m.group(1)
        month_token = m.group(2)
        y = m.group(3)
        day_norm = normalize_day_token_to_digits(day_token)
        month_num = map_month_token_to_number(month_token)
        if day_norm and month_num:
            candidate_dates.append(f"{day_norm}.{month_num}.{y}")

    # 3) Collect all numeric dates DD.MM.YYYY (with optional spaces and OCR 'O/О' as zero)
    numeric_text = norm_text
    # Map common OCR confusions in numeric context: o/о→0, z/з→3, i/і/l→1
    numeric_text = re.sub(r"[oо]", "0", numeric_text)
    numeric_text = re.sub(r"[zз]", "3", numeric_text)
    numeric_text = re.sub(r"[iіл]", "1", numeric_text)
    for d, m, y in re.findall(r"(\d{1,2})\s*[\.\-/]\s*(\d{1,2})\s*[\.\-/]\s*(\d{4})", numeric_text):
        candidate_dates.append(f"{int(d):02d}.{int(m):02d}.{y}")

    try:
        logger.info(f"Student card dates (raw candidates): {candidate_dates}")
    except Exception:
        pass

    # Normalize, dedupe and choose the latest date
    normalized_unique: List[str] = []
    seen = set()
    for dt_str in candidate_dates:
        try:
            dt_norm = datetime.strptime(dt_str, "%d.%m.%Y").strftime("%d.%m.%Y")
        except ValueError:
            continue
        if dt_norm not in seen:
            seen.add(dt_norm)
            normalized_unique.append(dt_norm)

    if not normalized_unique:
        return None

    today = datetime.today().date()
    future_dates = [
        s for s in normalized_unique
        if datetime.strptime(s, "%d.%m.%Y").date() > today
    ]

    try:
        logger.info(f"Student card dates (normalized): {normalized_unique}; future: {future_dates}")
    except Exception:
        pass

    if not future_dates:
        try:
            logger.info("No future date found on student card; will prompt user to input 'valid until' date.")
        except Exception:
            pass
        return None

    latest_future = max(future_dates, key=lambda s: datetime.strptime(s, "%d.%m.%Y"))
    try:
        logger.info(f"Selected student card 'valid until' date: {latest_future}")
    except Exception:
        pass
    return latest_future
