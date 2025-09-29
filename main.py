import logging
import os
import io
import sys
import json
from datetime import datetime
import numpy as np
import cv2

from dotenv import load_dotenv

from id_front_ocr import extract_passport_front_data_auto_c
from residency_extract import extract_text_from_pdf_buffer, parse_residency_extract_text
from student_card_extract import extract_student_card_valid_until
from utils import normalize_dd_mm_yyyy_date, parse_ukrainian_address

load_dotenv()

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
    PicklePersistence,
)
import constants as C
from services import document_scanner_core as docscan, google_services
import form_validator
import fitz  # PyMuPDF

# --- Конфігурація ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_ROOT_FOLDER_ID = os.getenv("GOOGLE_ROOT_FOLDER_ID")
PAYMENT_URL = os.getenv(
    "PAYMENT_URL",
    "https://easypay.ua/ua/catalog/bustickets/leocart-sub/leocart-student",
)
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
SAMPLE_FORM_PATH = "Зразок_заяви_загальна_категорія_для_студентів_для_друку_2025.pdf"
PERSISTENCE_FILEPATH = os.getenv("BOT_PERSISTENCE_FILE", os.path.join("bot_persistence", "state.pkl"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

if not all([TELEGRAM_BOT_TOKEN, GOOGLE_ROOT_FOLDER_ID]):
    logger.error(
        "КРИТИЧНА ПОМИЛКА: Не всі змінні середовища налаштовано! (TELEGRAM_BOT_TOKEN, GOOGLE_ROOT_FOLDER_ID)"
    )
    sys.exit("Зупинка бота.")


# --- Перевірка Google авторизації на старті ---
def ensure_google_auth_on_startup() -> None:
    """Переконуємось, що на старті є дійсний Google OAuth токен.
    Якщо токена немає або його неможливо оновити, відкриється браузер для входу.
    """
    try:
        if not os.path.exists("token.json"):
            logger.info("Не знайдено 'token.json'. Відкриваю браузер для входу до Google…")
        creds = google_services.get_credentials()
        if not creds:
            raise RuntimeError("Google OAuth не повернув дійсні облікові дані.")
        logger.info("Google OAuth: облікові дані готові.")
    except Exception as e:
        logger.error(f"Не вдалося пройти Google авторизацію: {e}", exc_info=True)
        sys.exit("Зупинка бота через відсутність авторизації Google.")


# --- Попередня обробка зображень перед завантаженням на Drive ---
def _preprocess_image_buffer_for_drive(image_buffer: io.BytesIO) -> io.BytesIO:
    """Deskew/crop a document photo using document scanner and return JPEG buffer.

    If decoding or processing fails, returns the original buffer positioned at 0.
    """
    try:
        image_buffer.seek(0)
        image_bytes = image_buffer.read()
        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        image_bgr = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        if image_bgr is None:
            image_buffer.seek(0)
            return image_buffer

        processed_bgr, _ = docscan.scan_document_auto(image_bgr, pad_percent=0.035)
        success, encoded = cv2.imencode(
            ".jpg", processed_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90]
        )
        if not success:
            image_buffer.seek(0)
            return image_buffer

        out = io.BytesIO(encoded.tobytes())
        out.seek(0)
        return out
    except Exception:
        # У разі будь-якого збою повертаємо оригінальний буфер
        image_buffer.seek(0)
        return image_buffer


# --- Стани розмови ---
(
    SELECT_LEVEL,
    SELECT_ASSISTANCE,
    ASK_PREVIOUS_CARD,
    AWAITING_PASSPORT_FRONT,
    AWAITING_PASSPORT_BACK,
    AWAITING_TAX_ID_PHOTO,
    AWAITING_FULL_NAME,
    AWAITING_FULL_NAME_CONFIRMATION,
    AWAITING_STUDENT_ID,
    AWAITING_POLITECH_EMAIL,
    AWAITING_PHONE_NUMBER,
    AWAITING_PHOTO_3X4,
    AWAITING_RESIDENCY_EXTRACT,
    AWAITING_FILLED_FORMS,
    AWAITING_PAYMENT_CHOICE,
    AWAITING_PAYMENT_RECEIPT,
    AWAITING_STUDENT_VALID_UNTIL,
) = range(17)


# --- Обробники ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    reply_keyboard = [[C.BTN_BACHELOR], [C.BTN_MASTER]]
    await update.message.reply_text(
        C.MSG_GREETING,
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return SELECT_LEVEL


async def select_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_choice = update.message.text
    context.user_data["level"] = user_choice

    question = (
        "Чи був у вас учнівський ЛеоКарт?"
        if user_choice == C.BTN_BACHELOR
        else "Чи був у вас студентський ЛеоКарт?"
    )
    reply_keyboard = [[C.BTN_YES, C.BTN_NO]]

    await update.message.reply_text(
        question,
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return ASK_PREVIOUS_CARD


async def ask_previous_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_choice = update.message.text
    if user_choice == C.BTN_YES:
        await update.message.reply_text(
            C.MSG_RENEW_INSTRUCTIONS, reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    reply_keyboard = [[C.BTN_HELP_ME], [C.BTN_DO_IT_MYSELF]]
    await update.message.reply_text(
        C.MSG_ASK_ASSISTANCE,
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return SELECT_ASSISTANCE


async def select_assistance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_choice = update.message.text
    if user_choice == C.BTN_DO_IT_MYSELF:
        await update.message.reply_text(
            C.MSG_SELF_SERVICE_INSTRUCTIONS, reply_markup=ReplyKeyboardRemove()
        )
        try:
            await context.bot.send_document(
                chat_id=update.effective_chat.id, document=open(SAMPLE_FORM_PATH, "rb")
            )
        except FileNotFoundError:
            logger.error(f"Не вдалося знайти файл зразка заяви: {SAMPLE_FORM_PATH}")
        return ConversationHandler.END

    await update.message.reply_text(
        C.MSG_START_ASSISTANCE, reply_markup=ReplyKeyboardRemove()
    )
    # Надсилаємо приклад лицьової сторони паспорта
    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open("examples/id_front_example.jpg", "rb"),
            caption="Приклад лицьової сторони ID-картки",
        )
    except Exception as e:
        logger.warning(f"Не вдалося надіслати приклад лицьової сторони паспорта: {e}")
    return AWAITING_PASSPORT_FRONT


async def handle_passport_front(
        update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)
    context.user_data[C.PASSPORT_FRONT_FILENAME] = file_buffer

    file_buffer.seek(0)
    front_data = extract_passport_front_data_auto_c(file_buffer)

    # Rename key to match expected bot logic
    if "record_no" in front_data:
        front_data["record_no"] = front_data.pop("record_no")

    if front_data.get("record_no"):
        context.user_data["passport_data"] = front_data
        await update.message.reply_text(
            "Тепер надішліть фото зворотної сторони."
        )

        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open("examples/id_back_example.jpg", "rb"),
                caption="Приклад зворотної сторони ID-картки",
            )
        except Exception as e:
            logger.warning(f"Не вдалося надіслати приклад зворотної сторони паспорта: {e}")
        return AWAITING_PASSPORT_BACK
    else:
        await update.message.reply_text(
            "Не вдалося розпізнати номер. Спробуйте якісніше фото."
        )
        return AWAITING_PASSPORT_FRONT


async def handle_passport_back(
        update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    # Download the photo into memory
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)
    file_buffer.seek(0)

    # Save in user data
    context.user_data[C.PASSPORT_BACK_FILENAME] = file_buffer

    # Proceed directly to the next step (request residency extract)
    await update.message.reply_text(C.MSG_REQUEST_RESIDENCY_EXTRACT)
    return AWAITING_RESIDENCY_EXTRACT


async def handle_tax_id_photo(
        update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)
    context.user_data[C.TAX_ID_FILENAME] = file_buffer
    
    # Після отримання фото з ідентифікаційним кодом, переходимо далі без розпізнавання
    passport_data = context.user_data.setdefault("passport_data", {})

    if context.user_data.get(C.RESIDENCY_EXTRACT_FILENAME):
        full_name = passport_data.get("full_name")
        if full_name:
            reply_keyboard = [[C.BTN_YES, C.BTN_NO]]
            await update.message.reply_text(
                f"Ми розпізнали ваше ПІБ: {full_name}. Все правильно?",
                reply_markup=ReplyKeyboardMarkup(
                    reply_keyboard, one_time_keyboard=True, resize_keyboard=True,
                ),
            )
            return AWAITING_FULL_NAME_CONFIRMATION
        else:
            await update.message.reply_text("Введіть, будь ласка, ваше ПІБ.")
            return AWAITING_FULL_NAME
    else:
        await update.message.reply_text(C.MSG_REQUEST_RESIDENCY_EXTRACT)
        return AWAITING_RESIDENCY_EXTRACT


async def handle_residency_extract(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.document or update.message.document.mime_type != "application/pdf":
        await update.message.reply_text("Будь ласка, надішліть саме PDF файл витягу.")
        return AWAITING_RESIDENCY_EXTRACT

    # Load PDF
    file = await update.message.document.get_file()
    file_buffer = io.BytesIO()
    await file.download_to_memory(file_buffer)
    context.user_data[C.RESIDENCY_EXTRACT_FILENAME] = file_buffer

    # Extract text
    file_buffer.seek(0)
    text = extract_text_from_pdf_buffer(file_buffer)

    # Parse text into fields
    extracted = parse_residency_extract_text(text)

    # Get existing passport data or initialize
    passport_data = context.user_data.setdefault("passport_data", {})

    # Update extracted fields
    for field in ("date_of_birth", "record_no", "tax_id", "residency_address", "full_name"):
        if extracted.get(field):
            passport_data[field] = extracted[field]

    # Якщо у витязі немає РНОКПП — просимо окремий документ
    if "tax_id" not in passport_data:
        await update.message.reply_text("Не вдалося знайти Ідентифікаційний код (РНОКПП). Надішліть, будь ласка, фото документа з кодом.")
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open("examples/tin_example.jpg", "rb"),
                caption="Приклад: документ з РНОКПП",
            )
        except Exception as e:
            logger.warning(f"Не вдалося надіслати приклад ІПН: {e}")
        return AWAITING_TAX_ID_PHOTO

    # Ask to confirm extracted name
    full_name = passport_data.get("full_name")
    if full_name:
        reply_keyboard = [[C.BTN_YES, C.BTN_NO]]
        await update.message.reply_text(
            f"Ми розпізнали ваше ПІБ: {full_name}. Все правильно?",
            reply_markup=ReplyKeyboardMarkup(
                reply_keyboard, one_time_keyboard=True, resize_keyboard=True,
            ),
        )
        return AWAITING_FULL_NAME_CONFIRMATION
    else:
        await update.message.reply_text("Не вдалося розпізнати ПІБ. Введіть його вручну.")
        return AWAITING_FULL_NAME


async def handle_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    full_name = update.message.text.strip()
    if not full_name:
        await update.message.reply_text("Будь ласка, введіть ПІБ.")
        return AWAITING_FULL_NAME

    context.user_data.setdefault("passport_data", {})["full_name"] = full_name
    await update.message.reply_text(C.MSG_ASK_POLITECH_EMAIL)
    return AWAITING_POLITECH_EMAIL


async def handle_full_name_confirmation(
        update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    user_choice = update.message.text

    if user_choice == C.BTN_YES:
        await update.message.reply_text(C.MSG_ASK_POLITECH_EMAIL, reply_markup=ReplyKeyboardRemove())
        return AWAITING_POLITECH_EMAIL

    else:
        await update.message.reply_text(
            "Добре, введіть, будь ласка, ваше ПІБ ще раз.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return AWAITING_FULL_NAME


async def handle_politech_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip().lower()
    if not form_validator.is_valid_politech_email(email):
        await update.message.reply_text(C.MSG_INVALID_POLITECH_EMAIL)
        return AWAITING_POLITECH_EMAIL
    context.user_data.setdefault("passport_data", {})["politech_email"] = email
    await update.message.reply_text(C.MSG_ASK_PHONE_NUMBER)
    return AWAITING_PHONE_NUMBER


async def handle_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = update.message.text.strip()
    context.user_data["phone_number"] = phone
    await update.message.reply_text(C.MSG_ASK_PHOTO_3X4)
    return AWAITING_PHOTO_3X4


async def handle_photo_3x4(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)
    context.user_data[C.PHOTO_3X4_FILENAME] = file_buffer
    await update.message.reply_text(
        "Дякую! Тепер надішліть фото студентського."
    )
    # Приклад студентського
    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open("examples/student_id_example.png", "rb"),
            caption="Приклад студентського квитка",
        )
    except Exception as e:
        logger.warning(f"Не вдалося надіслати приклад студентського: {e}")
    return AWAITING_STUDENT_ID


async def handle_student_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)
    context.user_data[C.STUDENT_ID_FILENAME] = file_buffer

    # Extract "Valid Until" date from the student card
    file_buffer.seek(0)
    valid_until_date = extract_student_card_valid_until(file_buffer)

    if valid_until_date:
        context.user_data["student_card_valid_until"] = valid_until_date  # Save for later
        print("📅 Student card valid until:", valid_until_date)

        await update.message.reply_text(
            "Чудово! Тепер надішліть фото двох сторінок заповненої заяви."
        )
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open("./examples/document_page_1_example.png", "rb"),
                caption="Приклад: Заява, сторінка 1",
            )
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open("./examples/document_page_2_example.png", "rb"),
                caption="Приклад: Заява, сторінка 2",
            )
        except Exception as e:
            logger.warning(f"Не вдалося надіслати приклади заяви: {e}")

        context.user_data["filled_forms"] = []
        await update.message.reply_text(
            "Чекаю на фото першої сторінки заяви."
        )
        return AWAITING_FILLED_FORMS
    else:
        await update.message.reply_text(
            "Будь ласка, введіть дату \"Дійсний до\" у форматі DD.MM.YYYY (день.місяць.рік)."
        )
        return AWAITING_STUDENT_VALID_UNTIL


async def handle_student_valid_until(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_text = update.message.text.strip()
    normalized = normalize_dd_mm_yyyy_date(user_text)
    if not normalized:
        await update.message.reply_text(
            "Дата некоректна. Введіть, будь ласка, у форматі DD.MM.YYYY (день.місяць.рік)."
        )
        return AWAITING_STUDENT_VALID_UNTIL

    context.user_data["student_card_valid_until"] = normalized
    await update.message.reply_text(
        "Дякую! Тепер надішліть фото двох сторінок заповненої заяви."
    )
    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open("./examples/document_page_1_example.png", "rb"),
            caption="Приклад: Заява, сторінка 1",
        )
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open("./examples/document_page_2_example.png", "rb"),
            caption="Приклад: Заява, сторінка 2",
        )
    except Exception as e:
        logger.warning(f"Не вдалося надіслати приклади заяви: {e}")

    context.user_data["filled_forms"] = []
    await update.message.reply_text("Чекаю на фото першої сторінки заяви.")
    return AWAITING_FILLED_FORMS


async def handle_filled_forms(
        update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)

    forms = context.user_data.setdefault("filled_forms", [])
    if not forms:
        forms.append(file_buffer)
        await update.message.reply_text(
            "Першу сторінку отримано. Тепер надішліть фото другої."
        )
        return AWAITING_FILLED_FORMS
    else:
        forms.append(file_buffer)
        await update.message.reply_text(
            "Другу сторінку отримано. Залишився останній крок - оплата."
        )
        reply_keyboard = [[C.BTN_YES], [C.BTN_NO]]
        await update.message.reply_text(
            "Ви вже оплатили вартість виготовлення?",
            reply_markup=ReplyKeyboardMarkup(
                reply_keyboard, one_time_keyboard=True, resize_keyboard=True
            ),
        )
        return AWAITING_PAYMENT_CHOICE


async def handle_payment_choice(
        update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    if update.message.text == C.BTN_YES:
        await update.message.reply_text(
            "Добре. Надішліть квитанцію про оплату (скрін або PDF).",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await update.message.reply_text(
            f"Оплату можна здійснити тут: {PAYMENT_URL}\n\nЩойно отримаєте квитанцію, надішліть її мені.",
            reply_markup=ReplyKeyboardRemove(),
        )
    return AWAITING_PAYMENT_RECEIPT


async def handle_payment_receipt(
        update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    file_buffer = io.BytesIO()
    mimetype = "image/jpeg"
    if update.message.document:
        file = await update.message.document.get_file()
        mimetype = "application/pdf"
    else:
        file = await update.message.photo[-1].get_file()

    await file.download_to_memory(file_buffer)
    context.user_data[C.PAYMENT_RECEIPT_FILENAME] = file_buffer
    context.user_data["payment_receipt_mimetype"] = mimetype

    file_buffer.seek(0)
    is_valid, _ = form_validator.validate_payment_receipt(
        file_buffer, context.user_data, mimetype
    )

    if not is_valid:
        await update.message.reply_text(
            "Не вдалося знайти ваше прізвище у квитанції. Перевірте її та надішліть ще раз."
        )
        return AWAITING_PAYMENT_RECEIPT

    await update.message.reply_text(
        "Квитанцію перевірено! Всі документи зібрано. Надсилаю дані в обробку..."
    )

    files_to_upload = {
        C.PASSPORT_FRONT_FILENAME: context.user_data.get(C.PASSPORT_FRONT_FILENAME),
        C.PASSPORT_BACK_FILENAME: context.user_data.get(C.PASSPORT_BACK_FILENAME),
        C.STUDENT_ID_FILENAME: context.user_data.get(C.STUDENT_ID_FILENAME),
        C.TAX_ID_FILENAME: context.user_data.get(C.TAX_ID_FILENAME),
        C.RESIDENCY_EXTRACT_FILENAME: context.user_data.get(C.RESIDENCY_EXTRACT_FILENAME),
        C.PHOTO_3X4_FILENAME: context.user_data.get(C.PHOTO_3X4_FILENAME),
        C.FORM_PAGE_1_FILENAME: context.user_data.get("filled_forms", [None])[0],
        C.FORM_PAGE_2_FILENAME: context.user_data.get("filled_forms", [None, None])[1],
    }
    receipt_ext = "pdf" if "pdf" in mimetype else "jpg"
    files_to_upload[f"{C.PAYMENT_RECEIPT_FILENAME}.{receipt_ext}"] = (
        context.user_data.get(C.PAYMENT_RECEIPT_FILENAME)
    )

    # 1) Підготуємо фінальні файли (об'єднаний PDF та фото 3x4) ДО спроби Google Drive
    def _image_bytes_to_pdf_pages(doc: fitz.Document, image_buffer: io.BytesIO):
        image_buffer.seek(0)
        img_data = image_buffer.read()
        img_pdf = fitz.open(stream=img_data, filetype="jpg")
        rect = img_pdf[0].rect
        page = doc.new_page(width=rect.width, height=rect.height)
        page.insert_image(rect, stream=img_data)

    combined = fitz.open()

    if files_to_upload.get(C.FORM_PAGE_1_FILENAME):
        _image_bytes_to_pdf_pages(combined, files_to_upload[C.FORM_PAGE_1_FILENAME])
    if files_to_upload.get(C.FORM_PAGE_2_FILENAME):
        _image_bytes_to_pdf_pages(combined, files_to_upload[C.FORM_PAGE_2_FILENAME])

    if files_to_upload.get(C.PASSPORT_FRONT_FILENAME):
        _image_bytes_to_pdf_pages(combined, files_to_upload[C.PASSPORT_FRONT_FILENAME])
    if files_to_upload.get(C.PASSPORT_BACK_FILENAME):
        _image_bytes_to_pdf_pages(combined, files_to_upload[C.PASSPORT_BACK_FILENAME])

    if files_to_upload.get(C.RESIDENCY_EXTRACT_FILENAME):
        try:
            buf = files_to_upload[C.RESIDENCY_EXTRACT_FILENAME]
            buf.seek(0)
            extract_pdf = fitz.open(stream=buf.read(), filetype="pdf")
            combined.insert_pdf(extract_pdf)
        except Exception as e:
            logger.warning(f"Не вдалося додати витяг до PDF: {e}")

    if files_to_upload.get(C.STUDENT_ID_FILENAME):
        _image_bytes_to_pdf_pages(combined, files_to_upload[C.STUDENT_ID_FILENAME])

    # Додаємо фото з РНОКПП завжди, якщо користувач його надав, і розміщуємо одразу після студентського квитка
    if files_to_upload.get(C.TAX_ID_FILENAME):
        _image_bytes_to_pdf_pages(combined, files_to_upload[C.TAX_ID_FILENAME])

    receipt_buf = context.user_data.get(C.PAYMENT_RECEIPT_FILENAME)
    if receipt_buf:
        receipt_mime = context.user_data.get("payment_receipt_mimetype", "image/jpeg")
        try:
            if "pdf" in receipt_mime:
                receipt_buf.seek(0)
                receipt_pdf = fitz.open(stream=receipt_buf.read(), filetype="pdf")
                combined.insert_pdf(receipt_pdf)
            else:
                _image_bytes_to_pdf_pages(combined, receipt_buf)
        except Exception as e:
            logger.warning(f"Не вдалося додати квитанцію до PDF: {e}")

    combined_pdf_bytes = combined.tobytes()
    combined_pdf_buffer = io.BytesIO(combined_pdf_bytes)
    combined_pdf_buffer.seek(0)

    photo_3x4_prep_buffer = None
    if files_to_upload.get(C.PHOTO_3X4_FILENAME):
        try:
            photo_3x4_prep_buffer = _preprocess_image_buffer_for_drive(files_to_upload[C.PHOTO_3X4_FILENAME])
            photo_3x4_prep_buffer.seek(0)
        except Exception:
            photo_3x4_prep_buffer = None

    # 2) Спроба завантажити у Google Drive і записати у Google Sheet
    try:
        drive_service = google_services.get_drive_service()
        if not drive_service:
            raise ConnectionError("Не вдалося ініціалізувати сервіс Google Drive.")

        user_pib = context.user_data.get("passport_data", {}).get(
            "full_name", f"user_{update.effective_user.id}"
        )
        folder_url = google_services.create_user_folder_structure(
            drive_service, user_pib, GOOGLE_ROOT_FOLDER_ID
        )

        combined_file_id = google_services.upload_file_to_drive(
            drive_service,
            folder_url,
            C.COMBINED_PDF_FILENAME,
            combined_pdf_buffer,
            mimetype="application/pdf",
        )
        photo_3x4_id = None
        if photo_3x4_prep_buffer:
            photo_3x4_id = google_services.upload_file_to_drive(
                drive_service,
                folder_url,
                C.PHOTO_3X4_FILENAME,
                photo_3x4_prep_buffer,
                mimetype="image/jpeg",
            )

        if combined_file_id:
            context.user_data["scans_link"] = f"https://drive.google.com/file/d/{combined_file_id}/view?usp=drive_link"
        if photo_3x4_id:
            context.user_data["photo_3x4_link"] = f"https://drive.google.com/file/d/{photo_3x4_id}/view?usp=drive_link"

        try:
            worksheet = google_services.get_or_create_worksheet(
                C.DATABASE_SHEET_NAME, C.WORKSHEET_NAME, GOOGLE_ROOT_FOLDER_ID
            )
            if worksheet:
                google_services.add_user_to_sheet(
                    worksheet,
                    context.user_data,
                    update.effective_user.id,
                    context.user_data.get("scans_link", folder_url)
                )
        except Exception as e:
            logger.warning(f"Не вдалося записати у Google Sheet: {e}", exc_info=True)

        logger.info(
            f"Об'єднаний PDF та фото 3x4 для user {update.effective_user.id} завантажено на Google Drive."
        )

    except Exception as e:
        error_message = f"ПОМИЛКА GOOGLE для user_id {update.effective_user.id}:\n\n{type(e).__name__}: {e}"
        logger.error(error_message, exc_info=True)
        if ADMIN_CHAT_ID:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=error_message)

            # Надсилаємо до адміністратора дані, які зазвичай пишемо у Google Sheet
            try:
                passport_data = context.user_data.get("passport_data", {})
                full_name = passport_data.get("full_name", "N/A").strip()
                surname, name, patronymic = ("N/A", "N/A", "N/A")
                if full_name:
                    parts = [p for p in full_name.split() if p]
                    if len(parts) == 1:
                        surname = parts[0]
                    elif len(parts) == 2:
                        surname, name = parts
                    else:
                        surname, name, patronymic = parts[0], parts[1], " ".join(parts[2:])

                raw_address = passport_data.get("residency_address", "N/A")
                if raw_address != "N/A":
                    parsed_address = parse_ukrainian_address(raw_address)
                    city = parsed_address.get("city", "N/A")
                    street = parsed_address.get("street", "N/A")
                    building_flat = parsed_address.get("building_flat", "N/A")
                else:
                    city = street = building_flat = "N/A"

                student_card_valid_until = context.user_data.get("student_card_valid_until", "N/A")
                phone_number = context.user_data.get("phone_number", "N/A")

                admin_data_text = (
                    "Дані користувача (для таблиці):\n"
                    f"- Telegram ID: {update.effective_user.id}\n"
                    f"- Прізвище: {surname}\n"
                    f"- Імʼя: {name}\n"
                    f"- По батькові: {patronymic}\n"
                    f"- Телефон: {phone_number}\n"
                    f"- Електронна адреса: {passport_data.get('politech_email', 'N/A')}\n"
                    f"- № запису в реєстрі: {passport_data.get('record_no', 'N/A')}\n"
                    f"- Дата видачі: {passport_data.get('passport_date', 'N/A')}\n"
                    f"- Орган, що видав: {passport_data.get('passport_issued_by', 'N/A')}\n"
                    f"- ІПН: {passport_data.get('tax_id', 'N/A')}\n"
                    f"- Дата народження: {passport_data.get('date_of_birth', 'N/A')}\n"
                    f"- Стать: {passport_data.get('gender', 'N/A')}\n"
                    f"- Місто: {city}\n"
                    f"- Вулиця: {street}\n"
                    f"- Номер будинку, квартира: {building_flat}\n"
                    f"- Термін дії студентського: {student_card_valid_until}\n"
                )
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_data_text)
            except Exception as info_err:
                logger.error(f"Не вдалося надіслати дані адміну: {info_err}", exc_info=True)

            # Надсилаємо фінальні файли адміністратору
            try:
                combined_pdf_buffer.seek(0)
                await context.bot.send_document(
                    chat_id=ADMIN_CHAT_ID,
                    document=InputFile(combined_pdf_buffer, filename=C.COMBINED_PDF_FILENAME),
                    caption=(
                        f"Файли користувача {update.effective_user.id} ("
                        f"{context.user_data.get('passport_data', {}).get('full_name', 'N/A')})."
                    ),
                )
                if photo_3x4_prep_buffer:
                    photo_3x4_prep_buffer.seek(0)
                    await context.bot.send_document(
                        chat_id=ADMIN_CHAT_ID,
                        document=InputFile(photo_3x4_prep_buffer, filename=C.PHOTO_3X4_FILENAME),
                        caption="Фото 3x4",
                    )
            except Exception as send_err:
                logger.error(f"Не вдалося надіслати файли адміністратору: {send_err}", exc_info=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_dir = os.path.join(
            "local_backups", f"user_{update.effective_user.id}_{timestamp}"
        )
        os.makedirs(backup_dir, exist_ok=True)

        for name, buff in files_to_upload.items():
            try:
                if not buff:
                    continue
                buff.seek(0)
                with open(os.path.join(backup_dir, name), "wb") as f:
                    f.write(buff.read())
            except Exception as write_err:
                logger.error(
                    f"Не вдалося зберегти локальний файл '{name}': {write_err}",
                    exc_info=True,
                )

        with open(os.path.join(backup_dir, "data.json"), "w", encoding="utf-8") as f:
            json.dump(
                context.user_data.get("passport_data", {}),
                f,
                ensure_ascii=False,
                indent=4,
            )

        logger.info(
            f"Файли для user {update.effective_user.id} збережено локально в: {backup_dir}"
        )

    await update.message.reply_text(
        "Готово! Всі ваші документи успішно оброблено та надіслано."
    )
    context.user_data.clear()
    return ConversationHandler.END


async def wrong_input_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(C.MSG_WRONG_INPUT_PHOTO)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Діалог скасовано.", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


def main() -> None:
    # Перевіряємо авторизацію Google одразу при запуску бота
    ensure_google_auth_on_startup()

    # Гарантуємо, що директорія для persistence існує, а шлях вказує на файл
    try:
        os.makedirs(os.path.dirname(PERSISTENCE_FILEPATH) or ".", exist_ok=True)
    except Exception:
        pass
    persistence = PicklePersistence(filepath=PERSISTENCE_FILEPATH)
    application = (
        Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    )

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_LEVEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_level)
            ],
            ASK_PREVIOUS_CARD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_previous_card)
            ],
            SELECT_ASSISTANCE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_assistance)
            ],
            AWAITING_PASSPORT_FRONT: [
                MessageHandler(filters.PHOTO, handle_passport_front),
                MessageHandler(filters.TEXT & ~filters.COMMAND, wrong_input_type),
            ],
            AWAITING_PASSPORT_BACK: [
                MessageHandler(filters.PHOTO, handle_passport_back)
            ],
            AWAITING_TAX_ID_PHOTO: [
                MessageHandler(filters.PHOTO, handle_tax_id_photo)
            ],
            AWAITING_POLITECH_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_politech_email)
            ],
            AWAITING_PHONE_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_number)
            ],
            AWAITING_PHOTO_3X4: [
                MessageHandler(filters.PHOTO, handle_photo_3x4)
            ],
            AWAITING_RESIDENCY_EXTRACT: [
                MessageHandler(filters.Document.PDF, handle_residency_extract)
            ],
            AWAITING_FULL_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_full_name)
            ],
            AWAITING_FULL_NAME_CONFIRMATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_full_name_confirmation)
            ],
            AWAITING_STUDENT_ID: [
                MessageHandler(filters.PHOTO, handle_student_id)
            ],
            AWAITING_STUDENT_VALID_UNTIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_student_valid_until)
            ],
            AWAITING_FILLED_FORMS: [
                MessageHandler(filters.PHOTO, handle_filled_forms)
            ],
            AWAITING_PAYMENT_CHOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_payment_choice)
            ],
            AWAITING_PAYMENT_RECEIPT: [
                MessageHandler(filters.PHOTO | filters.Document.PDF, handle_payment_receipt)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
        persistent=True,
        name="leocard_main_conversation",
        conversation_timeout=3600,
    )

    application.add_handler(conv_handler)
    logger.info("Бот запускається...")
    application.run_polling()


if __name__ == "__main__":
    main()
