import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BotConfig:
    """Main bot configuration"""
    bot_token: str
    admin_chat_id: str
    google_root_folder_id: str
    persistence_path: str = "bot_persistence/state.pkl"
    payment_url: str = "https://easypay.ua/ua/catalog/bustickets/leocart-sub/leocart-student"
    sample_form_path: str = "Зразок_заяви_загальна_категорія_для_студентів_для_друку_2025.pdf"

    @classmethod
    def from_env(cls):
        return cls(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            admin_chat_id=os.getenv("ADMIN_CHAT_ID"),
            google_root_folder_id=os.getenv("GOOGLE_ROOT_FOLDER_ID"),
            persistence_path=os.getenv("BOT_PERSISTENCE_FILE", "bot_persistence/state.pkl"),
            payment_url=os.getenv("PAYMENT_URL", cls.payment_url)
        )


@dataclass
class GoogleConfig:
    """Google Drive and Sheets configuration"""
    documents_folder: str = "Documents"
    database_sheet: str = "database"
    worksheet_name: str = "Аркуш1"
    credentials_file: str = "credentials.json"
    token_file: str = "token.json"


@dataclass
class FileNames:
    """Standard filenames for uploaded documents"""
    passport_front: str = "passport_front.jpg"
    passport_back: str = "passport_back.jpg"
    student_id: str = "student_id.jpg"
    tax_id: str = "tax_id.jpg"
    form_page_1: str = "form_page_1.jpg"
    form_page_2: str = "form_page_2.jpg"
    payment_receipt: str = "payment_receipt"  # Extension added dynamically
    residency_extract: str = "residency_extract.pdf"
    photo_3x4: str = "photo_3x4.jpg"
    combined_pdf: str = "scan_bundle.pdf"


@dataclass
class Messages:
    """All bot messages in one place"""
    greeting: str = (
        "Привіт! Я твій помічник у виготовленні студентської ЛеоКарт.\n\n"
        "Для початку, вкажи, будь ласка, на якому ти рівні навчання?"
    )

    ask_assistance: str = "Бажаєте оформити ЛеоКарт самостійно чи з моєю допомогою?"

    self_service: str = (
        "Добре! Ось інструкція для самостійного оформлення:\n\n"
        "1. Роздрукуйте та заповніть **Заяву на виготовлення ЛеоКарт** та **Згоду на обробку персональних даних**.\n"
        "2. Ознайомтесь з детальною [Інструкцією від ЛКП 'Львівавтодор'](https://drive.google.com/file/d/1kneBFVC22NYBBqfWce2ieKhqx6V4QSmm/view?usp=sharing).\n"
        "3. Заповніть [офіційну Гугл-Форму](https://docs.google.com/forms/d/e/1FAIpQLSfIacV_XxxbWwdL5xR-1BavcnP7RG1qmS3g9jTGmx50BZx_Gg/viewform).\n\n"
        "Якщо передумаєте, просто напишіть /start."
    )

    renew_card: str = (
        "Супер! У вашому випадку потрібно просто поновити існуючу картку.\n\n"
        "Зробити це можна в **Центрі Обслуговування Пасажирів** за адресою:\n"
        "📍 м. Львів, вул. Горбачевського, 10.\n\n"
        "Гарного дня!"
    )

    photo_hint: str = "Фотографуйте документи на білому фоні, наприклад на аркуші паперу!"

    start_assistance: str = (
        "Гаразд, я допоможу тобі крок за кроком! "
        "Давай розпочнемо зі збору документів.\n\n"
        "Будь ласка, сфотографуй та надішли **лицьову сторону** своєї ID-картки.\n\n"
        "{photo_hint}"
    )

    wrong_input: str = "Ой, здається, це текст. Будь ласка, надішліть фотографію."

    ask_politech_email: str = "Вкажіть вашу корпоративну пошту (@lpnu.ua)"
    invalid_email: str = "Будь ласка, введіть коректну політехівську пошту у форматі name@lpnu.ua"

    ask_phone: str = "Вкажіть ваш номер телефону"
    ask_photo_3x4: str = "Надайте, будь ласка, фотографію 3×4 — сфотографовану рівно та обрізану по контуру або цифровий варіант."

    ask_residency: str = (
        "Будь ласка, надішліть PDF документ — 'Витяг з реєстру територіальної громади'.\n"
        "Інструкцію можна знайти за посиланням: https://diia.gov.ua/services/vityag-z-reyestru-teritorialnoyi-gromadi"
    )


@dataclass
class Buttons:
    """Button texts"""
    bachelor: str = "Я Бакалавр"
    master: str = "Я Магістр"
    help_me: str = "Допоможи мені"
    do_myself: str = "Я зроблю все сам"
    yes: str = "Так"
    no: str = "Ні"
