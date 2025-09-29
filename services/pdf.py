import io
import fitz
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class PDFService:
    """PDF creation and manipulation"""

    @staticmethod
    def extract_text(pdf_buffer: io.BytesIO) -> str:
        """Extract text from PDF"""
        try:
            pdf_buffer.seek(0)
            doc = fitz.open(stream=pdf_buffer.read(), filetype="pdf")
            return "\n".join(page.get_text() for page in doc).strip()
        except Exception as e:
            logger.error(f"Failed to extract PDF text: {e}")
            return ""

    @staticmethod
    def create_combined_pdf(files: Dict[str, io.BytesIO], receipt_mime: str = "image/jpeg") -> io.BytesIO:
        """Combine all documents into single PDF"""
        from config import FileNames
        fn = FileNames()

        def add_image_as_page(doc: fitz.Document, img_buffer: io.BytesIO):
            """Add image as PDF page"""
            img_buffer.seek(0)
            img_data = img_buffer.read()
            img_pdf = fitz.open(stream=img_data, filetype="jpg")
            rect = img_pdf[0].rect
            page = doc.new_page(width=rect.width, height=rect.height)
            page.insert_image(rect, stream=img_data)

        combined = fitz.open()

        # Order: Form pages → Passport → Residency → Student ID → Tax ID → Receipt
        order = [
            fn.form_page_1,
            fn.form_page_2,
            fn.passport_front,
            fn.passport_back,
            fn.residency_extract,
            fn.student_id,
            fn.tax_id,
            fn.payment_receipt
        ]

        for key in order:
            buffer = files.get(key)
            if not buffer:
                continue

            try:
                if key == fn.residency_extract:
                    buffer.seek(0)
                    pdf_doc = fitz.open(stream=buffer.read(), filetype="pdf")
                    combined.insert_pdf(pdf_doc)
                elif key == fn.payment_receipt and "pdf" in receipt_mime:
                    buffer.seek(0)
                    pdf_doc = fitz.open(stream=buffer.read(), filetype="pdf")
                    combined.insert_pdf(pdf_doc)
                else:
                    add_image_as_page(combined, buffer)
            except Exception as e:
                logger.warning(f"Failed to add {key} to PDF: {e}")

        result = io.BytesIO(combined.tobytes())
        result.seek(0)
        return result
