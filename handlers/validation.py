from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from config import Messages, Buttons, FileNames
from utils.parsers import is_valid_lpnu_email, normalize_date
import logging

logger = logging.getLogger(__name__)

# States
AWAITING_FULL_NAME = 7
AWAITING_FULL_NAME_CONFIRMATION = 8
AWAITING_POLITECH_EMAIL = 9
AWAITING_PHONE_NUMBER = 10
AWAITING_PHOTO_3X4 = 11
AWAITING_STUDENT_ID = 12
AWAITING_STUDENT_VALID_UNTIL = 13

msg = Messages()
btn = Buttons()
fn = FileNames()


async def handle_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle manually entered full name"""
    full_name = update.message.text.strip()
    if not full_name:
        await update.message.reply_text("Будь ласка, введіть ПІБ.")
        return AWAITING_FULL_NAME

    context.user_data.setdefault("passport_data", {})["full_name"] = full_name
    await update.message.reply_text(msg.ask_politech_email)
    return AWAITING_POLITECH_EMAIL


async def handle_full_name_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirm extracted full name"""
    if update.message.text == btn.yes:
        await update.message.reply_text(msg.ask_politech_email, reply_markup=ReplyKeyboardRemove())
        return AWAITING_POLITECH_EMAIL
    else:
        await update.message.reply_text(
            "Добре, введіть, будь ласка, ваше ПІБ ще раз.",
            reply_markup=ReplyKeyboardRemove()
        )
        return AWAITING_FULL_NAME


async def handle_politech_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle and validate LPNU email"""
    email = update.message.text.strip().lower()
    if not is_valid_lpnu_email(email):
        await update.message.reply_text(msg.invalid_email)
        return AWAITING_POLITECH_EMAIL

    context.user_data.setdefault("passport_data", {})["politech_email"] = email
    await update.message.reply_text(msg.ask_phone)
    return AWAITING_PHONE_NUMBER


async def handle_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle phone number"""
    phone = update.message.text.strip()
    context.user_data["phone_number"] = phone
    await update.message.reply_text(msg.ask_photo_3x4)
    return AWAITING_PHOTO_3X4


async def handle_photo_3x4(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 3x4 photo"""
    from services.scanner import DocumentScanner

    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)

    # Scan and deskew
    file_buffer = DocumentScanner.scan_and_deskew(file_buffer)
    context.user_data[fn.photo_3x4] = file_buffer

    await update.message.reply_text("Дякую! Тепер надішліть фото студентського.")

    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open("examples/student_id_example.png", "rb"),
            caption="Приклад студентського квитка"
        )
    except Exception as e:
        logger.warning(f"Could not send example: {e}")

    return AWAITING_STUDENT_ID


async def handle_student_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle student ID card"""
    from services.scanner import DocumentScanner
    from services.ocr import OCRService

    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)

    # Scan and deskew
    file_buffer = DocumentScanner.scan_and_deskew(file_buffer)
    context.user_data[fn.student_id] = file_buffer

    # Try to extract valid until date
    file_buffer.seek(0)
    valid_until = OCRService.extract_student_valid_until(file_buffer)

    if valid_until:
        context.user_data["student_card_valid_until"] = valid_until
        logger.info(f"Student card valid until: {valid_until}")

        await update.message.reply_text("Чудово! Тепер надішліть фото двох сторінок заповненої заяви.")

        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open("examples/document_page_1_example.png", "rb"),
                caption="Приклад: Заява, сторінка 1"
            )
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open("examples/document_page_2_example.png", "rb"),
                caption="Приклад: Заява, сторінка 2"
            )
        except Exception as e:
            logger.warning(f"Could not send examples: {e}")

        context.user_data["filled_forms"] = []
        from handlers.finalization import AWAITING_FILLED_FORMS
        return AWAITING_FILLED_FORMS
    else:
        await update.message.reply_text(
            "Будь ласка, введіть дату \"Дійсний до\" у форматі DD.MM.YYYY (день.місяць.рік)."
        )
        return AWAITING_STUDENT_VALID_UNTIL


async def handle_student_valid_until(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle manually entered valid until date"""
    user_text = update.message.text.strip()
    normalized = normalize_date(user_text)

    if not normalized:
        await update.message.reply_text(
            "Дата некоректна. Введіть, будь ласка, у форматі DD.MM.YYYY (день.місяць.рік)."
        )
        return AWAITING_STUDENT_VALID_UNTIL

    context.user_data["student_card_valid_until"] = normalized
    await update.message.reply_text("Дякую! Тепер надішліть фото двох сторінок заповненої заяви.")

    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open("examples/document_page_1_example.png", "rb"),
            caption="Приклад: Заява, сторінка 1"
        )
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open("examples/document_page_2_example.png", "rb"),
            caption="Приклад: Заява, сторінка 2"
        )
    except Exception as e:
        logger.warning(f"Could not send examples: {e}")

    context.user_data["filled_forms"] = []
    from handlers.finalization import AWAITING_FILLED_FORMS
    return AWAITING_FILLED_FORMS