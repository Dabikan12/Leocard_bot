import io
import re
import cv2
import numpy as np
import pytesseract
from datetime import datetime
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)


def preprocess_for_ocr(img: np.ndarray, sharpen: bool = True) -> np.ndarray:
    """Standard OCR preprocessing"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

    if sharpen:
        blur = cv2.GaussianBlur(gray, (0, 0), 1.0)
        gray = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)

    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 10
    )


def normalize_digits(text: str) -> str:
    """Fix common OCR digit mistakes"""
    trans = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "S": "5", "B": "8"})
    return text.translate(trans)


class OCRService:
    """Unified OCR extraction service"""

    @staticmethod
    def extract_id_front(image_buffer: io.BytesIO) -> Dict[str, Optional[str]]:
        """Extract gender, DOB, record number from ID front"""
        from utils.helpers import bytes_to_image

        def try_with_threshold(C: int):
            img = bytes_to_image(image_buffer)
            processed = preprocess_for_ocr(img)

            cfg = r'--oem 1 --psm 6'
            raw_text = pytesseract.image_to_string(processed, config=cfg, lang='eng+ukr')

            # Gender
            gender = None
            txt_norm = normalize_digits(raw_text).upper()
            if re.search(r'\bЧ[ІI/]?M\b', txt_norm):
                gender = "Ч/M"
            elif re.search(r'\bЖ[ІI/]?F\b', txt_norm):
                gender = "Ж/F"

            # DOB
            dob = None
            dob_match = re.search(r'\b(\d{2})[ ./-](\d{2})[ ./-](\d{4})\b', raw_text)
            if dob_match:
                dob = f"{dob_match.group(1)}.{dob_match.group(2)}.{dob_match.group(3)}"

            # Record number
            record = None
            rec_match = re.search(r'\b(\d{8}-\d{5})\b', raw_text)
            if rec_match:
                record = rec_match.group(1)

            return {"gender": gender, "date_of_birth": dob, "record_no": record}

        # Try multiple thresholds
        for C in [8, 10, 12]:
            result = try_with_threshold(C)
            if all(result.values()):
                logger.info(f"ID front extracted successfully with C={C}")
                return result

        return try_with_threshold(10)

    @staticmethod
    def extract_id_back(image_buffer: io.BytesIO) -> Dict[str, Optional[str]]:
        """Extract authority code, issue date, tax ID from ID back"""
        from utils.helpers import bytes_to_image

        img = bytes_to_image(image_buffer)
        processed = preprocess_for_ocr(img)

        cfg = r'--oem 1 --psm 6'
        raw_text = pytesseract.image_to_string(processed, config=cfg, lang='eng+ukr')
        data = pytesseract.image_to_data(processed, output_type=pytesseract.Output.DICT, config=cfg, lang='eng+ukr')

        tokens = [
            {"text": normalize_digits(data["text"][i]), "x": data["left"][i], "y": data["top"][i]}
            for i in range(len(data["text"])) if data["text"][i].strip()
        ]

        # Tax ID
        tax_id = None
        ten_digits = re.findall(r'(?<!\d)(\d{10})(?!\d)', raw_text)
        if ten_digits:
            tax_id = ten_digits[0]

        # Date and authority code
        date_of_issue = None
        authority_code = None

        for line_y in sorted({t["y"] for t in tokens}):
            line_tokens = [t for t in tokens if abs(t["y"] - line_y) < 15]
            line_tokens.sort(key=lambda t: t["x"])

            four_digit = [t for t in line_tokens if re.fullmatch(r"\d{4}", t["text"])]
            if four_digit:
                authority_code = four_digit[-1]["text"]
                idx = line_tokens.index(four_digit[-1])

                if idx >= 3:
                    t1, t2, t3 = line_tokens[idx - 3:idx]
                    if all(re.fullmatch(r"\d{2,4}", t["text"]) for t in [t1, t2, t3]):
                        date_of_issue = f"{t1['text']}.{t2['text']}.{t3['text']}"
                        break

        return {
            "passport_date": date_of_issue,
            "passport_issued_by": authority_code,
            "tax_id": tax_id
        }

    @staticmethod
    def extract_student_valid_until(image_buffer: io.BytesIO) -> Optional[str]:
        """Extract 'Valid Until' date from student card"""
        from utils.helpers import bytes_to_image

        img = bytes_to_image(image_buffer)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        denoised = cv2.fastNlMeansDenoising(gray, h=10)

        cfg = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(denoised, lang='ukr', config=cfg).lower()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"(\d)([а-яіїєґ])", r"\1 \2", text)

        uk_months = {
            "січня": "01", "лютого": "02", "березня": "03", "квітня": "04",
            "травня": "05", "червня": "06", "липня": "07", "серпня": "08",
            "вересня": "09", "жовтня": "10", "листопада": "11", "грудня": "12"
        }

        candidates = []

        # Word months
        for m in re.finditer(r"(\d{1,2})\s*([а-яіїєґ]+)\s*(\d{4})", text):
            day, month_word, year = m.groups()
            month_num = uk_months.get(month_word)
            if month_num:
                try:
                    candidates.append(f"{int(day):02d}.{month_num}.{year}")
                except ValueError:
                    pass

        # Numeric dates
        for d, m, y in re.findall(r"(\d{1,2})[\.\-/](\d{1,2})[\.\-/](\d{4})", text):
            try:
                candidates.append(f"{int(d):02d}.{int(m):02d}.{y}")
            except ValueError:
                pass

        # Return latest future date
        today = datetime.today().date()
        future = [dt for dt in candidates if datetime.strptime(dt, "%d.%m.%Y").date() > today]

        if future:
            result = max(future, key=lambda s: datetime.strptime(s, "%d.%m.%Y"))
            logger.info(f"Extracted student card valid until: {result}")
            return result

        logger.warning("No future date found on student card")
        return None