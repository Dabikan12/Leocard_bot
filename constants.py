# constants.py

# --- Налаштування для Google ---
DOCUMENTS_FOLDER_NAME = "Documents"
DATABASE_SHEET_NAME = "database"
WORKSHEET_NAME = "Аркуш1"

# --- Назви файлів для збереження ---
PASSPORT_FRONT_FILENAME = "passport_front.jpg"
PASSPORT_BACK_FILENAME = "passport_back.jpg"
STUDENT_ID_FILENAME = "student_id.jpg"
TAX_ID_FILENAME = "tax_id.jpg"
FORM_PAGE_1_FILENAME = "form_page_1.jpg"
FORM_PAGE_2_FILENAME = "form_page_2.jpg"
PAYMENT_RECEIPT_FILENAME = "payment_receipt"  # розширення додається динамічно
RESIDENCY_EXTRACT_FILENAME = "residency_extract.pdf"
PHOTO_3X4_FILENAME = "photo_3x4.jpg"
COMBINED_PDF_FILENAME = "scan_bundle.pdf"

# --- Ключові слова для валідації ---
STUDENT_ID_KEYWORDS = [
    "національний",
    "університет",
    "львівська",
    "політехніка",
    "інститут",
]
FORM_PAGE_1_KEYWORDS = ["заява", "львівавтодор", "леокарт", "студентом", "студенткою"]

# --- Тексти повідомлень ---
MSG_GREETING = (
    "Привіт! Я твій помічник у виготовленні студентської ЛеоКарт.\n\n"
    "Для початку, вкажи, будь ласка, на якому ти рівні навчання?"
)

MSG_ASK_ASSISTANCE = "Бажаєте оформити ЛеоКарт самостійно чи з моєю допомогою?"

MSG_SELF_SERVICE_INSTRUCTIONS = (
    "Добре! Ось інструкція для самостійного оформлення:\n\n"
    "1. Роздрукуйте та заповніть **Заяву на виготовлення ЛеоКарт** та **Згоду на обробку персональних даних**.\n"
    "   (файл зразка я надішлю наступним повідомленням)\n\n"
    "2. Ознайомтесь з детальною [Інструкцією від ЛКП 'Львівавтодор'](https://drive.google.com/file/d/1kneBFVC22NYBBqfWce2ieKhqx6V4QSmm/view?usp=sharing).\n\n"
    "3. Заповніть [офіційну Гугл-Форму](https://docs.google.com/forms/d/e/1FAIpQLSfIacV_XxxbWwdL5xR-1BavcnP7RG1qmS3g9jTGmx50BZx_Gg/viewform), куди слід прикріпити фото 3х4 см та об'єднані документи у форматі PDF.\n\n"
    "Якщо передумаєте, просто напишіть /start."
)

MSG_RENEW_INSTRUCTIONS = (
    "Супер! У вашому випадку потрібно просто поновити існуючу картку.\n\n"
    "Зробити це можна в **Центрі Обслуговування Пасажирів** за адресою:\n"
    "📍 м. Львів, вул. Горбачевського, 10.\n\n"
    "Гарного дня!"
)

MSG_PHOTO_BG_HINT = "Фотографуйте документи на білому фоні, наприклад на аркуші паперу!"

MSG_START_ASSISTANCE = (
    "Гаразд, я допоможу тобі крок за кроком! "
    "Давай розпочнемо зі збору документів.\n\n"
    "Будь ласка, сфотографуй та надішли **лицьову сторону** своєї ID-картки.\n\n"
    f"{MSG_PHOTO_BG_HINT}"
)

MSG_WRONG_INPUT_PHOTO = "Ой, здається, це текст. Будь ласка, надішліть фотографію."

# --- Електронна пошта ---
MSG_ASK_POLITECH_EMAIL = "Вкажіть вашу корпоративну пошту (@lpnu.ua)"
MSG_INVALID_POLITECH_EMAIL = (
    "Будь ласка, введіть коректну політехівську пошту у форматі name@lpnu.ua"
)

# --- Телефон та фото 3x4 ---
MSG_ASK_PHONE_NUMBER = "Вкажіть ваш номер телефону"
MSG_INVALID_PHONE_NUMBER = "Будь ласка, введіть номер телефону"
MSG_ASK_PHOTO_3X4 = (
    "Надайте, будь ласка, фотографію 3×4 — сфотографовану рівно та обрізану по контуру або цифровий варіант."
)

# --- Витяг з реєстру ---
MSG_REQUEST_RESIDENCY_EXTRACT = (
    "Будь ласка, надішліть PDF документ — ‘Витяг з реєстру територіальної громади’.\n"
    "Інструкцію можна знайти за посиланням: https://diia.gov.ua/services/vityag-z-reyestru-teritorialnoyi-gromadi"
)

# --- Кнопки ---
BTN_BACHELOR = "Я Бакалавр"
BTN_MASTER = "Я Магістр"
BTN_HELP_ME = "Допоможи мені"
BTN_DO_IT_MYSELF = "Я зроблю все сам"
BTN_YES = "Так"
BTN_NO = "Ні"
