# form_validator.py
import re
import logging
import io
import fitz  # PyMuPDF

from ocr_processor import preprocess_image, extract_text_from_image
import constants as C

logger = logging.getLogger(__name__)


def is_valid_application_form_page1(form_image_buffer: io.BytesIO) -> bool:
    try:
        processed_image = preprocess_image(form_image_buffer)
        text = extract_text_from_image(processed_image).lower()

        found_keywords = sum(1 for keyword in C.FORM_PAGE_1_KEYWORDS if keyword in text)

        if found_keywords >= 3:
            logger.info(
                f"Валідація сторінки 1: Успішно, знайдено {found_keywords} ключових слів."
            )
            return True
        else:
            logger.warning(
                f"Валідація сторінки 1: Невдало, знайдено лише {found_keywords} ключових слів."
            )
            return False

    except Exception as e:
        logger.error(f"Помилка під час валідації сторінки 1: {e}")
        return False


def validate_payment_receipt(
    receipt_buffer: io.BytesIO, user_data: dict, mimetype: str
) -> (bool, str):
    text = ""
    try:
        image_buffer_for_ocr = io.BytesIO()

        if "pdf" in mimetype:
            receipt_buffer.seek(0)
            pdf_document = fitz.open(stream=receipt_buffer, filetype="pdf")
            page = pdf_document.load_page(0)
            pix = page.get_pixmap(dpi=300)
            img_bytes = pix.tobytes("png")
            image_buffer_for_ocr.write(img_bytes)
        else:
            receipt_buffer.seek(0)
            image_buffer_for_ocr.write(receipt_buffer.read())

        image_buffer_for_ocr.seek(0)
        processed_image = preprocess_image(image_buffer_for_ocr)
        text = extract_text_from_image(processed_image)

        full_name = user_data.get("passport_data", {}).get("full_name", "")
        if not full_name:
            logger.error("Не вдалося отримати ПІБ користувача для валідації квитанції.")
            return False, text

        surname = full_name.split()[0].lower()

        payment_purpose_match = re.search(
            r"Призначення\s*платежу\s*:(.*)", text, re.IGNORECASE | re.DOTALL
        )

        if payment_purpose_match:
            payment_purpose_text = payment_purpose_match.group(1).lower()
            if surname in payment_purpose_text:
                logger.info(
                    f"Прізвище '{surname}' знайдено в призначенні платежу. Валідація успішна."
                )
                return True, text
            else:
                logger.warning(
                    f"Прізвище '{surname}' НЕ знайдено в призначенні платежу."
                )
                return False, text
        else:
            logger.warning("Не вдалося знайти 'Призначення платежу' в квитанції.")
            return False, text

    except Exception as e:
        logger.error(f"Помилка під час валідації квитанції: {e}")
        return False, text


def is_valid_politech_email(email: str) -> bool:
    try:
        if not email:
            return False
        email = email.strip().lower()
        pattern = r"^[A-Za-z0-9._%+-]+@lpnu\.ua$"
        return re.match(pattern, email) is not None
    except Exception:
        return False
