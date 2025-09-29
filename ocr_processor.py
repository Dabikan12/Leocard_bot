# ocr_processor.py
from typing import Dict, Optional

import cv2
import pytesseract
import re
import logging
import io
import numpy as np

# Імпорт констант
import constants as C

logger = logging.getLogger(__name__)


def preprocess_image(image_buffer: io.BytesIO):
    image_buffer.seek(0)
    image_array = np.frombuffer(image_buffer.read(), dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.medianBlur(gray, 3)
    thresh = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
    )
    return thresh


def extract_text_from_image(processed_image) -> str:
    if processed_image is None:
        return ""
    try:
        config = "--oem 3 --psm 6"
        text = pytesseract.image_to_string(
            processed_image, lang="ukr+eng", config=config
        )
        logger.info(
            f"Розпізнано текст: \n--- \n{text[:300]}\n---"
        )  # Логуємо лише частину
        return text
    except Exception as e:
        logger.error(f"Помилка під час розпізнавання тексту: {e}")
        return ""


def extract_front_passport_data(image_buffer: io.BytesIO) -> dict:
    data = {}
    processed_image = preprocess_image(image_buffer)
    text = extract_text_from_image(processed_image)
    doc_number_match = re.search(r"\b\d{9}\b", text)
    if doc_number_match:
        data["passport_number"] = doc_number_match.group(0)
    return data


def extract_back_passport_data(image_buffer: io.BytesIO) -> dict:
    data = {}
    processed_image = preprocess_image(image_buffer)
    text = extract_text_from_image(processed_image)
    authority_match = re.search(r"\b\d{4}\b", text)
    if authority_match:
        data["passport_issued_by"] = authority_match.group(0)
    date_match = re.search(r"\b(\d{2})\s?(\d{2})\s?(\d{4})\b", text)
    if date_match and (
            not authority_match or date_match.group(3) != authority_match.group(0)
    ):
        data["passport_date"] = (
            f"{date_match.group(1)}.{date_match.group(2)}.{date_match.group(3)}"
        )
    tax_id_match = re.search(r"\b\d{10}\b", text)
    if tax_id_match:
        data["tax_id"] = tax_id_match.group(0)
    return data


def extract_tax_id(image_buffer: io.BytesIO) -> dict:
    data = {}
    processed_image = preprocess_image(image_buffer)
    text = extract_text_from_image(processed_image)
    tax_id_match = re.search(r"\b\d{10}\b", text)
    if tax_id_match:
        data["tax_id"] = tax_id_match.group(0)
    return data


def is_valid_student_id(image_buffer: io.BytesIO) -> bool:
    processed_image = preprocess_image(image_buffer)
    text = extract_text_from_image(processed_image).lower()

    for keyword in C.STUDENT_ID_KEYWORDS:
        if keyword in text:
            logger.info(f"Знайдено ключове слово '{keyword}' на студентському.")
            return True

    logger.warning("На фото студентського не знайдено ключових слів.")
    return False


from id_back_ocr import extract_passport_data_from_bytes


def extract_back_passport_data(buf) -> Dict[str, Optional[str]]:
    """
    Accepts BytesIO or bytes, returns keys expected by the bot:
    - passport_date (dd.mm.yyyy)
    - passport_issued_by (4-digit code)
    - tax_id (10 digits)  # optional
    """
    raw = extract_passport_data_from_bytes(buf)  # {"authority_code","date_of_issue","tax_id"}
    return {
        "passport_date": raw.get("date_of_issue"),
        "passport_issued_by": raw.get("authority_code"),
        "tax_id": raw.get("tax_id"),
    }
