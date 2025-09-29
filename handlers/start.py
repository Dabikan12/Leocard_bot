from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler
from config import Messages, Buttons
import logging

logger = logging.getLogger(__name__)

# States
SELECT_LEVEL = 0
ASK_PREVIOUS_CARD = 1
SELECT_ASSISTANCE = 2

msg = Messages()
btn = Buttons()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start conversation"""
    context.user_data.clear()

    reply_keyboard = [[btn.bachelor], [btn.master]]
    await update.message.reply_text(
        msg.greeting,
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SELECT_LEVEL


async def select_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle education level selection"""
    user_choice = update.message.text
    context.user_data["level"] = user_choice

    question = (
        "Чи був у вас учнівський ЛеоКарт?" if user_choice == btn.bachelor
        else "Чи був у вас студентський ЛеоКарт?"
    )

    reply_keyboard = [[btn.yes, btn.no]]
    await update.message.reply_text(
        question,
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return ASK_PREVIOUS_CARD


async def ask_previous_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Check if user had previous card"""
    if update.message.text == btn.yes:
        await update.message.reply_text(msg.renew_card, reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    reply_keyboard = [[btn.help_me], [btn.do_myself]]
    await update.message.reply_text(
        msg.ask_assistance,
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SELECT_ASSISTANCE


async def select_assistance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Choose assisted or self-service mode"""
    from config import BotConfig

    if update.message.text == btn.do_myself:
        await update.message.reply_text(msg.self_service, reply_markup=ReplyKeyboardRemove())

        # Send sample form
        try:
            config = BotConfig.from_env()
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=open(config.sample_form_path, "rb")
            )
        except FileNotFoundError:
            logger.error("Sample form file not found")

        return ConversationHandler.END

    # Start assisted mode
    await update.message.reply_text(
        msg.start_assistance.format(photo_hint=msg.photo_hint),
        reply_markup=ReplyKeyboardRemove()
    )

    # Send example
    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open("examples/id_front_example.jpg", "rb"),
            caption="Приклад лицьової сторони ID-картки"
        )
    except Exception as e:
        logger.warning(f"Could not send example: {e}")

    from handlers.documents import AWAITING_PASSPORT_FRONT
    return AWAITING_PASSPORT_FRONT


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel conversation"""
    context.user_data.clear()
    await update.message.reply_text("Діалог скасовано.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END