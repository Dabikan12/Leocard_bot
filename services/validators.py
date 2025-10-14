import io
import re
import logging
from services.pdf import PDFService
from services.ocr import OCRService

logger = logging.getLogger(__name__)


class Validators:
    """Document and data validation"""

    @staticmethod
    def validate_payment_receipt(receipt_buffer: io.BytesIO, user_data: dict, mimetype: str) -> bool:
        """Validate payment receipt contains user surname"""
        try:
            # Extract text
            if "pdf" in mimetype:
                text = PDFService.extract_text(receipt_buffer)
            else:
                # For images, use OCR (simplified - just check presence)
                receipt_buffer.seek(0)
                import pytesseract
                from utils.helpers import bytes_to_image
                img = bytes_to_image(receipt_buffer)
                text = pytesseract.image_to_string(img, lang='ukr+eng')

            # Get surname
            full_name = user_data.get("passport_data", {}).get("full_name", "")
            if not full_name:
                logger.error("No full name in user data for receipt validation")
                return False

            surname = full_name.split()[0].lower()

            # Check if surname is in payment purpose
            payment_match = re.search(r"Призначення\s*платежу\s*:(.*)", text, re.IGNORECASE | re.DOTALL)
            if payment_match and surname in payment_match.group(1).lower():
                logger.info(f"Payment receipt validated: surname '{surname}' found")
                return True

            logger.warning(f"Surname '{surname}' not found in receipt")
            return False

        except Exception as e:
            logger.error(f"Receipt validation error: {e}")
            return False