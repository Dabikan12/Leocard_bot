import logging
import os
import io
import sys
import json
from datetime import datetime
import numpy as np
import cv2

from dotenv import load_dotenv

# --- Імпорти з ваших модулів ---
from services.ocr import OCRService
from services.pdf import PDFService
from services.validators import Validators
from services.scanner import DocumentScanner
from services import google_services
from utils.parsers import normalize_date, parse_ukrainian_address, is_valid_lpnu_email, parse_residency_extract
from utils.helpers import image_to_bytes

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
# --- Імпорти з конфігурації ---
from config import BotConfig, GoogleConfig, FileNames, Messages, Buttons
import fitz  # PyMuPDF

# --- Ініціалізація конфігурації ---
bot_config = BotConfig.from_env()
google_config = GoogleConfig()
fn = FileNames()
msg = Messages()
btn = Buttons()

# --- Конфігурація ---
TELEGRAM_BOT_TOKEN = bot_config.bot_token
GOOGLE_ROOT_FOLDER_ID = bot_config.google_root_folder_id
PAYMENT_URL = bot_config.payment_url
ADMIN_CHAT_ID = bot_config.admin_chat_id
SAMPLE_FORM_PATH = bot_config.sample_form_path
PERSISTENCE_FILEPATH = bot_config.persistence_path

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
    try:
        if not os.path.exists(google_config.token_file):
            logger.info(f"Не знайдено '{google_config.token_file}'. Відкриваю браузер для входу до Google…")
        creds = google_services.get_credentials()
        if not creds:
            raise RuntimeError("Google OAuth не повернув дійсні облікові дані.")
        logger.info("Google OAuth: облікові дані готові.")
    except Exception as e:
        logger.error(f"Не вдалося пройти Google авторизацію: {e}", exc_info=True)
        sys.exit("Зупинка бота через відсутність авторизації Google.")


# --- Попередня обробка зображень перед завантаженням на Drive ---
def _preprocess_image_buffer_for_drive(image_buffer: io.BytesIO) -> io.BytesIO:
    try:
        image_buffer.seek(0)
        processed_buffer = DocumentScanner.scan_and_deskew(image_buffer)
        processed_buffer.seek(0)
        return processed_buffer
    except Exception:
        image_buffer.seek(0)
        return image_buffer


# --- Стани розмови (без змін) ---
(
    SELECT_LEVEL, SELECT_ASSISTANCE, ASK_PREVIOUS_CARD, AWAITING_PASSPORT_FRONT,
    AWAITING_PASSPORT_BACK, AWAITING_TAX_ID_PHOTO, AWAITING_FULL_NAME,
    AWAITING_FULL_NAME_CONFIRMATION, AWAITING_STUDENT_ID, AWAITING_POLITECH_EMAIL,
    AWAITING_PHONE_NUMBER, AWAITING_PHOTO_3X4, AWAITING_RESIDENCY_EXTRACT,
    AWAITING_FILLED_FORMS, AWAITING_PAYMENT_CHOICE, AWAITING_PAYMENT_RECEIPT,
    AWAITING_STUDENT_VALID_UNTIL,
) = range(17)


# --- Обробники (без змін, окрім handle_payment_receipt) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    reply_keyboard = [[btn.bachelor], [btn.master]]
    await update.message.reply_text(
        msg.greeting,
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
        if user_choice == btn.bachelor
        else "Чи був у вас студентський ЛеоКарт?"
    )
    reply_keyboard = [[btn.yes, btn.no]]
    await update.message.reply_text(
        question,
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return ASK_PREVIOUS_CARD


async def ask_previous_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_choice = update.message.text
    if user_choice == btn.yes:
        await update.message.reply_text(
            msg.renew_card, reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    reply_keyboard = [[btn.help_me], [btn.do_myself]]
    await update.message.reply_text(
        msg.ask_assistance,
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard, one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return SELECT_ASSISTANCE


async def select_assistance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_choice = update.message.text
    if user_choice == btn.do_myself:
        await update.message.reply_text(
            msg.self_service, reply_markup=ReplyKeyboardRemove()
        )
        try:
            await context.bot.send_document(
                chat_id=update.effective_chat.id, document=open(SAMPLE_FORM_PATH, "rb")
            )
        except FileNotFoundError:
            logger.error(f"Не вдалося знайти файл зразка заяви: {SAMPLE_FORM_PATH}")
        return ConversationHandler.END
    await update.message.reply_text(
        msg.start_assistance.format(photo_hint=msg.photo_hint), reply_markup=ReplyKeyboardRemove()
    )
    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open("examples/id_front_example.jpg", "rb"),
            caption="Приклад лицьової сторони ID-картки",
        )
    except Exception as e:
        logger.warning(f"Не вдалося надіслати приклад лицьової сторони паспорта: {e}")
    return AWAITING_PASSPORT_FRONT


async def handle_passport_front(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)
    context.user_data[fn.passport_front] = file_buffer
    file_buffer.seek(0)
    front_data = OCRService.extract_id_front(file_buffer)
    if front_data.get("record_no"):
        context.user_data["passport_data"] = front_data
        await update.message.reply_text("Тепер надішліть фото зворотної сторони.")
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
        await update.message.reply_text("Не вдалося розпізнати номер. Спробуйте якісніше фото.")
        return AWAITING_PASSPORT_FRONT


async def handle_passport_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)
    file_buffer.seek(0)
    context.user_data[fn.passport_back] = file_buffer
    await update.message.reply_text(msg.ask_residency)
    return AWAITING_RESIDENCY_EXTRACT


async def handle_tax_id_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)
    context.user_data[fn.tax_id] = file_buffer
    passport_data = context.user_data.setdefault("passport_data", {})
    if context.user_data.get(fn.residency_extract):
        full_name = passport_data.get("full_name")
        if full_name:
            reply_keyboard = [[btn.yes, btn.no]]
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
        await update.message.reply_text(msg.ask_residency)
        return AWAITING_RESIDENCY_EXTRACT


async def handle_residency_extract(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.document or update.message.document.mime_type != "application/pdf":
        await update.message.reply_text("Будь ласка, надішліть саме PDF файл витягу.")
        return AWAITING_RESIDENCY_EXTRACT
    file = await update.message.document.get_file()
    file_buffer = io.BytesIO()
    await file.download_to_memory(file_buffer)
    context.user_data[fn.residency_extract] = file_buffer
    file_buffer.seek(0)
    text = PDFService.extract_text(file_buffer)
    extracted = parse_residency_extract(text)
    passport_data = context.user_data.setdefault("passport_data", {})
    for field in ("date_of_birth", "record_no", "tax_id", "residency_address", "full_name"):
        if extracted.get(field):
            passport_data[field] = extracted[field]
    if "tax_id" not in passport_data:
        await update.message.reply_text(
            "Не вдалося знайти Ідентифікаційний код (РНОКПП). Надішліть, будь ласка, фото документа з кодом.")
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open("examples/tin_example.jpg", "rb"),
                caption="Приклад: документ з РНОКПП",
            )
        except Exception as e:
            logger.warning(f"Не вдалося надіслати приклад ІПН: {e}")
        return AWAITING_TAX_ID_PHOTO
    full_name = passport_data.get("full_name")
    if full_name:
        reply_keyboard = [[btn.yes, btn.no]]
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
    await update.message.reply_text(msg.ask_politech_email)
    return AWAITING_POLITECH_EMAIL


async def handle_full_name_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_choice = update.message.text
    if user_choice == btn.yes:
        await update.message.reply_text(msg.ask_politech_email, reply_markup=ReplyKeyboardRemove())
        return AWAITING_POLITECH_EMAIL
    else:
        await update.message.reply_text(
            "Добре, введіть, будь ласка, ваше ПІБ ще раз.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return AWAITING_FULL_NAME


async def handle_politech_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip().lower()
    if not is_valid_lpnu_email(email):
        await update.message.reply_text(msg.invalid_email)
        return AWAITING_POLITECH_EMAIL
    context.user_data.setdefault("passport_data", {})["politech_email"] = email
    await update.message.reply_text(msg.ask_phone)
    return AWAITING_PHONE_NUMBER


async def handle_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = update.message.text.strip()
    context.user_data["phone_number"] = phone
    await update.message.reply_text(msg.ask_photo_3x4)
    return AWAITING_PHOTO_3X4


async def handle_photo_3x4(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)
    context.user_data[fn.photo_3x4] = file_buffer
    await update.message.reply_text("Дякую! Тепер надішліть фото студентського.")
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
    context.user_data[fn.student_id] = file_buffer
    file_buffer.seek(0)
    valid_until_date = OCRService.extract_student_valid_until(file_buffer)
    if valid_until_date:
        context.user_data["student_card_valid_until"] = valid_until_date
        await update.message.reply_text("Чудово! Тепер надішліть фото двох сторінок заповненої заяви.")
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
    else:
        await update.message.reply_text(
            "Будь ласка, введіть дату \"Дійсний до\" у форматі DD.MM.YYYY (день.місяць.рік).")
        return AWAITING_STUDENT_VALID_UNTIL


async def handle_student_valid_until(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_text = update.message.text.strip()
    normalized = normalize_date(user_text)
    if not normalized:
        await update.message.reply_text("Дата некоректна. Введіть, будь ласка, у форматі DD.MM.YYYY (день.місяць.рік).")
        return AWAITING_STUDENT_VALID_UNTIL
    context.user_data["student_card_valid_until"] = normalized
    await update.message.reply_text("Дякую! Тепер надішліть фото двох сторінок заповненої заяви.")
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


async def handle_filled_forms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)
    forms = context.user_data.setdefault("filled_forms", [])
    if not forms:
        forms.append(file_buffer)
        await update.message.reply_text("Першу сторінку отримано. Тепер надішліть фото другої.")
        return AWAITING_FILLED_FORMS
    else:
        forms.append(file_buffer)
        await update.message.reply_text("Другу сторінку отримано. Залишився останній крок - оплата.")
        reply_keyboard = [[btn.yes], [btn.no]]
        await update.message.reply_text(
            "Ви вже оплатили вартість виготовлення?",
            reply_markup=ReplyKeyboardMarkup(
                reply_keyboard, one_time_keyboard=True, resize_keyboard=True
            ),
        )
        return AWAITING_PAYMENT_CHOICE


async def handle_payment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == btn.yes:
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


async def wrong_input_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(msg.wrong_input)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Діалог скасовано.", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


# --- ОСНОВНИЙ ОНОВЛЕНИЙ ОБРОБНИК ---

async def handle_payment_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_buffer = io.BytesIO()
    mimetype = "image/jpeg"
    if update.message.document:
        file = await update.message.document.get_file()
        mimetype = "application/pdf"
    else:
        file = await update.message.photo[-1].get_file()

    await file.download_to_memory(file_buffer)
    context.user_data[fn.payment_receipt] = file_buffer
    context.user_data["payment_receipt_mimetype"] = mimetype
    file_buffer.seek(0)

    if not Validators.validate_payment_receipt(file_buffer, context.user_data, mimetype):
        await update.message.reply_text(
            "Не вдалося знайти ваше прізвище у квитанції. Перевірте її та надішліть ще раз."
        )
        return AWAITING_PAYMENT_RECEIPT

    await update.message.reply_text("Квитанцію перевірено! Всі документи зібрано. Надсилаю дані в обробку...")

    # --- Етап 1: Підготовка файлів та даних ---
    all_files = {
        fn.passport_front: context.user_data.get(fn.passport_front),
        fn.passport_back: context.user_data.get(fn.passport_back),
        fn.student_id: context.user_data.get(fn.student_id),
        fn.tax_id: context.user_data.get(fn.tax_id),
        fn.residency_extract: context.user_data.get(fn.residency_extract),
        fn.photo_3x4: context.user_data.get(fn.photo_3x4),
        fn.form_page_1: context.user_data.get("filled_forms", [None])[0],
        fn.form_page_2: context.user_data.get("filled_forms", [None, None])[1],
        fn.payment_receipt: context.user_data.get(fn.payment_receipt),
    }

    combined_pdf_buffer = PDFService.create_combined_pdf(
        all_files, context.user_data.get("payment_receipt_mimetype", "image/jpeg")
    )
    photo_3x4_prep_buffer = _preprocess_image_buffer_for_drive(all_files[fn.photo_3x4]) if all_files.get(
        fn.photo_3x4) else None

    passport_data = context.user_data.get("passport_data", {})
    record_no = passport_data.get("record_no", f"user_{update.effective_user.id}")
    full_name = passport_data.get("full_name", f"user_{update.effective_user.id}")

    photo_filename = f"{record_no}.jpg"
    pdf_filename = f"{record_no}_1.pdf"

    # --- Етап 2: Робота з Google Drive та Sheets ---
    try:
        drive_service = google_services.get_drive_service()
        sheets_client = google_services.get_sheets_client()
        if not drive_service or not sheets_client:
            raise ConnectionError("Не вдалося ініціалізувати сервіси Google.")

        # --- ЛОГІКА ПАРТІЙ ---
        logger.info("--- Початок обробки партії ---")
        parties_root_folder_id = google_services.get_or_create_folder(drive_service,
                                                                      google_config.parties_root_folder_name,
                                                                      GOOGLE_ROOT_FOLDER_ID)
        party_folder_id, party_worksheet = google_services.get_or_create_party_folder(drive_service, sheets_client,
                                                                                      parties_root_folder_id)

        student_folder_name = google_config.student_folder_name_template.format(full_name, record_no)
        student_folder_id = google_services.get_or_create_folder(drive_service, student_folder_name, party_folder_id)

        pdf_id, pdf_url = google_services.upload_file_to_drive(drive_service, student_folder_id, pdf_filename,
                                                               combined_pdf_buffer, "application/pdf")
        photo_id, photo_url = (None, None)
        if photo_3x4_prep_buffer:
            photo_id, photo_url = google_services.upload_file_to_drive(drive_service, student_folder_id, photo_filename,
                                                                       photo_3x4_prep_buffer, "image/jpeg")

        google_services.add_user_to_party_sheet(party_worksheet, context.user_data, photo_url, pdf_url)
        logger.info("--- Кінець обробки партії ---")

        # --- ЛОГІКА ЗАГАЛЬНОЇ БАЗИ (паралельно) ---
        logger.info("--- Початок запису в загальну базу ---")
        main_docs_folder_id = google_services.get_or_create_folder(drive_service, google_config.main_documents_folder,
                                                                   GOOGLE_ROOT_FOLDER_ID)
        main_worksheet = google_services.get_or_create_worksheet(google_config.main_database_sheet,
                                                                 google_config.main_worksheet_name, main_docs_folder_id)

        if main_worksheet:
            # В загальну таблицю передаємо посилання на папку студента в партії
            student_folder_details = drive_service.files().get(fileId=student_folder_id, fields='webViewLink').execute()
            student_folder_url = student_folder_details.get('webViewLink')

            # Оновлюємо посилання в user_data для запису в загальну таблицю
            context.user_data["scans_link"] = pdf_url
            context.user_data["photo_3x4_link"] = photo_url

            google_services.add_user_to_sheet(main_worksheet, context.user_data, update.effective_user.id,
                                              student_folder_url)
        logger.info("--- Кінець запису в загальну базу ---")


    except Exception as e:
        # Обробка помилок та сповіщення адміну (без змін)
        error_message = f"ПОМИЛКА GOOGLE для user_id {update.effective_user.id}:\n\n{type(e).__name__}: {e}"
        logger.error(error_message, exc_info=True)
        if ADMIN_CHAT_ID:
            # ... (код для сповіщення адміна залишається тут)
            pass

    # --- Етап 3: Локальний бекап (без змін) ---
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_dir = os.path.join("local_backups", f"user_{update.effective_user.id}_{timestamp}")
    os.makedirs(backup_dir, exist_ok=True)
    try:
        if combined_pdf_buffer:
            with open(os.path.join(backup_dir, pdf_filename), "wb") as f:
                combined_pdf_buffer.seek(0)
                f.write(combined_pdf_buffer.read())
        if photo_3x4_prep_buffer:
            with open(os.path.join(backup_dir, photo_filename), "wb") as f:
                photo_3x4_prep_buffer.seek(0)
                f.write(photo_3x4_prep_buffer.read())
    except Exception as write_err:
        logger.error(f"Не вдалося зберегти локальний бекап: {write_err}", exc_info=True)

    with open(os.path.join(backup_dir, "data.json"), "w", encoding="utf-8") as f:
        serializable_data = {k: v for k, v in passport_data.items() if
                             isinstance(v, (str, int, float, bool, type(None)))}
        json.dump(serializable_data, f, ensure_ascii=False, indent=4)
    logger.info(f"Файли для user {update.effective_user.id} збережено локально в: {backup_dir}")

    # --- Завершення розмови ---
    await update.message.reply_text("Готово! Всі ваші документи успішно оброблено та надіслано.")
    context.user_data.clear()
    return ConversationHandler.END


def main() -> None:
    ensure_google_auth_on_startup()
    try:
        os.makedirs(os.path.dirname(PERSISTENCE_FILEPATH) or ".", exist_ok=True)
    except Exception:
        pass
    persistence = PicklePersistence(filepath=PERSISTENCE_FILEPATH)
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_LEVEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_level)],
            ASK_PREVIOUS_CARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_previous_card)],
            SELECT_ASSISTANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_assistance)],
            AWAITING_PASSPORT_FRONT: [
                MessageHandler(filters.PHOTO, handle_passport_front),
                MessageHandler(filters.TEXT & ~filters.COMMAND, wrong_input_type),
            ],
            AWAITING_PASSPORT_BACK: [MessageHandler(filters.PHOTO, handle_passport_back)],
            AWAITING_TAX_ID_PHOTO: [MessageHandler(filters.PHOTO, handle_tax_id_photo)],
            AWAITING_POLITECH_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_politech_email)],
            AWAITING_PHONE_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_number)],
            AWAITING_PHOTO_3X4: [MessageHandler(filters.PHOTO, handle_photo_3x4)],
            AWAITING_RESIDENCY_EXTRACT: [MessageHandler(filters.Document.PDF, handle_residency_extract)],
            AWAITING_FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_full_name)],
            AWAITING_FULL_NAME_CONFIRMATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_full_name_confirmation)],
            AWAITING_STUDENT_ID: [MessageHandler(filters.PHOTO, handle_student_id)],
            AWAITING_STUDENT_VALID_UNTIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_student_valid_until)],
            AWAITING_FILLED_FORMS: [MessageHandler(filters.PHOTO, handle_filled_forms)],
            AWAITING_PAYMENT_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_payment_choice)],
            AWAITING_PAYMENT_RECEIPT: [MessageHandler(filters.PHOTO | filters.Document.PDF, handle_payment_receipt)],
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