from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InputFile
from telegram.ext import ContextTypes, ConversationHandler
from config import Messages, Buttons, FileNames, BotConfig
from services.validators import Validators
from services.scanner import DocumentScanner
from services.pdf import PDFService
from services.google_drive import GoogleService
import io
import os
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# States
AWAITING_FILLED_FORMS = 14
AWAITING_PAYMENT_CHOICE = 15
AWAITING_PAYMENT_RECEIPT = 16

msg = Messages()
btn = Buttons()
fn = FileNames()


async def handle_filled_forms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle application form pages"""
    photo_file = await update.message.photo[-1].get_file()
    file_buffer = io.BytesIO()
    await photo_file.download_to_memory(file_buffer)

    # Scan and deskew
    file_buffer = DocumentScanner.scan_and_deskew(file_buffer)

    forms = context.user_data.setdefault("filled_forms", [])

    if not forms:
        forms.append(file_buffer)
        context.user_data[fn.form_page_1] = file_buffer
        await update.message.reply_text("Першу сторінку отримано. Тепер надішліть фото другої.")
        return AWAITING_FILLED_FORMS
    else:
        forms.append(file_buffer)
        context.user_data[fn.form_page_2] = file_buffer
        await update.message.reply_text("Другу сторінку отримано. Залишився останній крок - оплата.")

        reply_keyboard = [[btn.yes], [btn.no]]
        await update.message.reply_text(
            "Ви вже оплатили вартість виготовлення?",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return AWAITING_PAYMENT_CHOICE


async def handle_payment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle payment status choice"""
    config = BotConfig.from_env()

    if update.message.text == btn.yes:
        await update.message.reply_text(
            "Добре. Надішліть квитанцію про оплату (скрін або PDF).",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text(
            f"Оплату можна здійснити тут: {config.payment_url}\n\n"
            "Щойно отримаєте квитанцію, надішліть її мені.",
            reply_markup=ReplyKeyboardRemove()
        )

    return AWAITING_PAYMENT_RECEIPT


async def handle_payment_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle payment receipt and finalize"""
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

    # Validate receipt
    file_buffer.seek(0)
    is_valid = Validators.validate_payment_receipt(file_buffer, context.user_data, mimetype)

    if not is_valid:
        await update.message.reply_text(
            "Не вдалося знайти ваше прізвище у квитанції. Перевірте її та надішліть ще раз."
        )
        return AWAITING_PAYMENT_RECEIPT

    await update.message.reply_text(
        "Квитанцію перевірено! Всі документи зібрано. Надсилаю дані в обробку..."
    )

    # Prepare files for upload
    files_to_upload = {
        fn.passport_front: context.user_data.get(fn.passport_front),
        fn.passport_back: context.user_data.get(fn.passport_back),
        fn.student_id: context.user_data.get(fn.student_id),
        fn.tax_id: context.user_data.get(fn.tax_id),
        fn.residency_extract: context.user_data.get(fn.residency_extract),
        fn.photo_3x4: context.user_data.get(fn.photo_3x4),
        fn.form_page_1: context.user_data.get(fn.form_page_1),
        fn.form_page_2: context.user_data.get(fn.form_page_2),
        fn.payment_receipt: file_buffer
    }

    # Create combined PDF
    combined_pdf = PDFService.create_combined_pdf(files_to_upload, mimetype)

    # Process photo 3x4 for upload
    photo_3x4_buffer = files_to_upload.get(fn.photo_3x4)
    if photo_3x4_buffer:
        photo_3x4_buffer = DocumentScanner.scan_and_deskew(photo_3x4_buffer)

    # Try Google upload
    config = BotConfig.from_env()
    try:
        google_service = GoogleService()

        user_pib = context.user_data.get("passport_data", {}).get(
            "full_name", f"user_{update.effective_user.id}"
        )

        # Create user folder in appropriate batch and get batch number
        folder_url, batch_number = google_service.create_user_folder(user_pib, config.google_root_folder_id)
        logger.info(f"Created folder for {user_pib} in Batch {batch_number}")

        # Upload combined PDF
        combined_pdf.seek(0)
        combined_file_id = google_service.upload_file(
            folder_url, fn.combined_pdf, combined_pdf, mimetype="application/pdf"
        )

        # Upload photo 3x4
        photo_3x4_id = None
        if photo_3x4_buffer:
            photo_3x4_buffer.seek(0)
            photo_3x4_id = google_service.upload_file(
                folder_url, fn.photo_3x4, photo_3x4_buffer, mimetype="image/jpeg"
            )

        # Save links
        if combined_file_id:
            context.user_data["scans_link"] = f"https://drive.google.com/file/d/{combined_file_id}/view?usp=drive_link"
        if photo_3x4_id:
            context.user_data["photo_3x4_link"] = f"https://drive.google.com/file/d/{photo_3x4_id}/view?usp=drive_link"

        # Add to both main database and batch-specific sheet
        google_service.add_to_sheet(
            context.user_data,
            update.effective_user.id,
            context.user_data.get("scans_link", folder_url),
            config.google_root_folder_id,
            batch_number  # Pass batch number
        )

        logger.info(f"✅ Successfully uploaded data for user {update.effective_user.id} to Batch {batch_number}")
        await update.message.reply_text(
            f"Готово! Всі ваші документи успішно оброблено та надіслано.\n"
            f"📦 Ваша партія: {batch_number}"
        )

    except Exception as e:
        error_message = f"GOOGLE ERROR for user {update.effective_user.id}:\n\n{type(e).__name__}: {e}"
        logger.error(error_message, exc_info=True)

        # Notify admin
        if config.admin_chat_id:
            await context.bot.send_message(chat_id=config.admin_chat_id, text=error_message)

            # Send user data as text
            await send_user_data_to_admin(context, update)

            # Send files
            try:
                combined_pdf.seek(0)
                await context.bot.send_document(
                    chat_id=config.admin_chat_id,
                    document=InputFile(combined_pdf, filename=fn.combined_pdf),
                    caption=f"Files for user {update.effective_user.id}"
                )

                if photo_3x4_buffer:
                    photo_3x4_buffer.seek(0)
                    await context.bot.send_document(
                        chat_id=config.admin_chat_id,
                        document=InputFile(photo_3x4_buffer, filename=fn.photo_3x4),
                        caption="Photo 3x4"
                    )
            except Exception as send_err:
                logger.error(f"Failed to send files to admin: {send_err}")

        # Local backup
        save_local_backup(update.effective_user.id, files_to_upload, context.user_data)

    await update.message.reply_text("Готово! Всі ваші документи успішно оброблено та надіслано.")
    context.user_data.clear()
    return ConversationHandler.END


async def send_user_data_to_admin(context, update):
    """Send user data to admin as formatted text"""
    from utils.parsers import parse_ukrainian_address
    config = BotConfig.from_env()

    try:
        passport_data = context.user_data.get("passport_data", {})
        full_name = passport_data.get("full_name", "").strip()

        # Parse name
        surname, name, patronymic = ("N/A", "N/A", "N/A")
        if full_name:
            parts = [p for p in full_name.split() if p]
            if len(parts) == 1:
                surname = parts[0]
            elif len(parts) == 2:
                surname, name = parts
            else:
                surname, name, patronymic = parts[0], parts[1], ' '.join(parts[2:])

        # Parse address
        raw_address = passport_data.get("residency_address", "N/A")
        if raw_address != "N/A":
            parsed = parse_ukrainian_address(raw_address)
            city = parsed.get("city", "N/A")
            street = parsed.get("street", "N/A")
            building_flat = parsed.get("building_flat", "N/A")
        else:
            city = street = building_flat = "N/A"

        admin_text = (
            "Дані користувача:\n"
            f"- Telegram ID: {update.effective_user.id}\n"
            f"- Прізвище: {surname}\n"
            f"- Ім'я: {name}\n"
            f"- По батькові: {patronymic}\n"
            f"- Телефон: {context.user_data.get('phone_number', 'N/A')}\n"
            f"- Email: {passport_data.get('politech_email', 'N/A')}\n"
            f"- Запис №: {passport_data.get('record_no', 'N/A')}\n"
            f"- ІПН: {passport_data.get('tax_id', 'N/A')}\n"
            f"- Дата народження: {passport_data.get('date_of_birth', 'N/A')}\n"
            f"- Стать: {passport_data.get('gender', 'N/A')}\n"
            f"- Місто: {city}\n"
            f"- Вулиця: {street}\n"
            f"- Буд./кв.: {building_flat}\n"
            f"- Студентський до: {context.user_data.get('student_card_valid_until', 'N/A')}\n"
        )

        await context.bot.send_message(chat_id=config.admin_chat_id, text=admin_text)

    except Exception as e:
        logger.error(f"Failed to send user data to admin: {e}")


def save_local_backup(user_id: int, files: dict, user_data: dict):
    """Save files locally as backup"""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_dir = os.path.join("local_backups", f"user_{user_id}_{timestamp}")
    os.makedirs(backup_dir, exist_ok=True)

    # Save files
    for name, buff in files.items():
        try:
            if not buff:
                continue
            buff.seek(0)
            with open(os.path.join(backup_dir, name), "wb") as f:
                f.write(buff.read())
        except Exception as e:
            logger.error(f"Failed to save local file '{name}': {e}")

    # Save JSON data
    try:
        with open(os.path.join(backup_dir, "data.json"), "w", encoding="utf-8") as f:
            json.dump(user_data.get("passport_data", {}), f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Failed to save JSON data: {e}")

    logger.info(f"Local backup saved to: {backup_dir}")