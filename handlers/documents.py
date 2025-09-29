from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from config import Messages, Buttons, FileNames
from services.ocr import OCRService
from services.scanner import DocumentScanner
from services.pdf import PDFService
import io
import logging

logger = logging.getLogger(__name__)

# States
AWAITING_PASSPORT_FRONT = 3
AWAITING_PASSPORT_BACK = 4
AWAITING_RESIDENCY_EXTRACT = 5
AWAITING_TAX_ID_PHOTO = 6

msg = Messages()
btn = Buttons()
fn = FileNames()


async def handle_passport_front(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle ID front photo"""
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)

    # Scan and deskew
    file_buffer = DocumentScanner.scan_and_deskew(file_buffer)
    context.user_data[fn.passport_front] = file_buffer

    # OCR extraction
    file_buffer.seek(0)
    front_data = OCRService.extract_id_front(file_buffer)

    if front_data.get("record_no"):
        context.user_data["passport_data"] = front_data
        await update.message.reply_text("Тепер надішліть фото зворотної сторони.")

        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open("examples/id_back_example.jpg", "rb"),
                caption="Приклад зворотної сторони ID-картки"
            )
        except Exception as e:
            logger.warning(f"Could not send example: {e}")

        return AWAITING_PASSPORT_BACK
    else:
        await update.message.reply_text("Не вдалося розпізнати номер. Спробуйте якісніше фото.")
        return AWAITING_PASSPORT_FRONT


async def handle_passport_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle ID back photo"""
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)

    # Scan and deskew
    file_buffer = DocumentScanner.scan_and_deskew(file_buffer)
    context.user_data[fn.passport_back] = file_buffer

    await update.message.reply_text(msg.ask_residency)
    return AWAITING_RESIDENCY_EXTRACT


async def handle_residency_extract(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle residency extract PDF"""
    if not update.message.document or update.message.document.mime_type != "application/pdf":
        await update.message.reply_text("Будь ласка, надішліть саме PDF файл витягу.")
        return AWAITING_RESIDENCY_EXTRACT

    file = await update.message.document.get_file()
    file_buffer = io.BytesIO()
    await file.download_to_memory(file_buffer)
    context.user_data[fn.residency_extract] = file_buffer

    # Extract text and parse
    file_buffer.seek(0)
    text = PDFService.extract_text(file_buffer)
    extracted = parse_residency_extract(text)

    # Merge with existing data
    passport_data = context.user_data.setdefault("passport_data", {})
    for field in ("date_of_birth", "record_no", "tax_id", "residency_address", "full_name"):
        if extracted.get(field):
            passport_data[field] = extracted[field]

    # Check if tax ID was found
    if "tax_id" not in passport_data:
        await update.message.reply_text(
            "Не вдалося знайти Ідентифікаційний код (РНОКПП). "
            "Надішліть, будь ласка, фото документа з кодом."
        )
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open("examples/tin_example.jpg", "rb"),
                caption="Приклад: документ з РНОКПП"
            )
        except Exception as e:
            logger.warning(f"Could not send example: {e}")
        return AWAITING_TAX_ID_PHOTO

    # Ask to confirm name
    full_name = passport_data.get("full_name")
    if full_name:
        reply_keyboard = [[btn.yes, btn.no]]
        await update.message.reply_text(
            f"Ми розпізнали ваше ПІБ: {full_name}. Все правильно?",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        from handlers.validation import AWAITING_FULL_NAME_CONFIRMATION
        return AWAITING_FULL_NAME_CONFIRMATION
    else:
        await update.message.reply_text("Не вдалося розпізнати ПІБ. Введіть його вручну.")
        from handlers.validation import AWAITING_FULL_NAME
        return AWAITING_FULL_NAME


async def handle_tax_id_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle tax ID document photo"""
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)

    file_buffer = DocumentScanner.scan_and_deskew(file_buffer)
    context.user_data[fn.tax_id] = file_buffer

    # Continue to name confirmation
    passport_data = context.user_data.get("passport_data", {})
    full_name = passport_data.get("full_name")

    if full_name:
        reply_keyboard = [[btn.yes, btn.no]]
        await update.message.reply_text(
            f"Ми розпізнали ваше ПІБ: {full_name}. Все правильно?",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        from handlers.validation import AWAITING_FULL_NAME_CONFIRMATION
        return AWAITING_FULL_NAME_CONFIRMATION
    else:
        await update.message.reply_text("Введіть, будь ласка, ваше ПІБ.")
        from handlers.validation import AWAITING_FULL_NAME
        return AWAITING_FULL_NAME


def parse_residency_extract(text: str) -> dict:
    """Parse residency extract text into fields"""
    import re
    result = {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # Date of birth
    dob_match = re.search(r"(Дата народження|date of birth)[^\d]*(\d{2}\.\d{2}\.\d{4})", text, re.IGNORECASE)
    if dob_match:
        result["date_of_birth"] = dob_match.group(2)

    # УНЗР (record number)
    for i, line in enumerate(lines):
        if "УНЗР" in line and i + 1 < len(lines):
            unzr_match = re.search(r"\d{4,}-\d{4,}", lines[i + 1])
            if unzr_match:
                result["record_no"] = unzr_match.group()
                break

    # Tax ID
    for i, line in enumerate(lines):
        if "РНОКПП" in line and i + 1 < len(lines):
            ipn_match = re.search(r"\d{8,10}", lines[i + 1])
            if ipn_match:
                result["tax_id"] = ipn_match.group()
                break

    # Address
    start_index = None
    for i, line in enumerate(lines):
        if "Адреса місця проживання" in line:
            start_index = i
            break

    if start_index is not None:
        address_candidates = []
        for line in lines[start_index + 1:]:
            if any(kw in line.lower() for kw in ["область", "район", "місто", "вул", "буд", "кв"]):
                address_candidates.append(line)
            elif address_candidates:
                break
        if address_candidates:
            result["residency_address"] = ", ".join(address_candidates)

    # Full name
    last_name = first_name = patronymic = ""
    for i, line in enumerate(lines):
        if "Прізвище" in line and i + 1 < len(lines):
            last_name = lines[i + 1]
        if "Власне ім'я" in line and i + 1 < len(lines):
            first_name = lines[i + 1]
        if "По батькові" in line and i + 1 < len(lines):
            patronymic = lines[i + 1]

    if first_name and last_name:
        result["full_name"] = f"{last_name} {first_name} {patronymic}".strip()

    return result


async def wrong_input_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle wrong input type"""
    await update.message.reply_text(msg.wrong_input)