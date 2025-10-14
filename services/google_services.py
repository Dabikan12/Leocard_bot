import gspread
import os
import io
import logging
import re
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
import constants as C
from utils.parsers import parse_ukrainian_address
from config import GoogleConfig  # Імпортуємо конфігурацію

logger = logging.getLogger(__name__)
google_config = GoogleConfig()  # Ініціалізуємо конфіг

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]


# --- Існуючі функції для авторизації та отримання сервісів ---
# (Тут без змін)
def _is_running_in_docker() -> bool:
    try:
        return os.path.exists('/.dockerenv') or os.getenv('RUNNING_IN_DOCKER') == '1'
    except Exception:
        return False


CREDENTIALS_FILE = os.getenv('GOOGLE_CREDENTIALS_FILE', google_config.credentials_file)
TOKEN_FILE = os.getenv('GOOGLE_TOKEN_FILE', google_config.token_file)


def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            logger.error(f"Не вдалося прочитати файл токену '{TOKEN_FILE}': {e}", exc_info=True)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Оновлення простроченого токену...")
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.error(f"Не вдалося оновити токен доступу: {e}", exc_info=True)
                return None
            try:
                with open(TOKEN_FILE, 'w') as token:
                    token.write(creds.to_json())
                logger.info(f"Токен доступу збережено у файл {TOKEN_FILE}")
            except OSError:
                logger.warning(
                    f"Не вдалось записати оновлений токен у '{TOKEN_FILE}' (можливо, змонтовано лише для читання). Продовжую використовувати токен у пам'яті."
                )
        else:
            if _is_running_in_docker() and not os.getenv('GOOGLE_OAUTH_ALLOW_BROWSER'):
                logger.error(
                    "В контейнері відсутній дійсний токен OAuth. Згенеруйте 'token.json' локально та змонтуйте його у контейнер."
                )
                return None

            logger.info("Необхідна авторизація через браузер...")
            if not os.path.exists(CREDENTIALS_FILE):
                logger.error(f"Критична помилка: файл {CREDENTIALS_FILE} не знайдено!")
                return None

            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

            try:
                with open(TOKEN_FILE, 'w') as token:
                    token.write(creds.to_json())
                logger.info(f"Токен доступу збережено у файл {TOKEN_FILE}")
            except OSError:
                logger.warning(
                    f"Авторизацію виконано, але запис до '{TOKEN_FILE}' неможливий (read-only). Токен буде доступний лише у пам'яті під час цього запуску."
                )

    return creds


def get_drive_service():
    creds = get_credentials()
    if creds:
        return build('drive', 'v3', credentials=creds)
    return None


def get_sheets_client():
    creds = get_credentials()
    if creds:
        return gspread.authorize(creds)
    return None


def find_file_by_name(service, name: str, parent_id: str, mime_type: str = None):
    query = f"name='{name}' and '{parent_id}' in parents and trashed=false"
    if mime_type:
        query += f" and mimeType='{mime_type}'"
    try:
        response = service.files().list(q=query, fields='files(id, name)').execute()
        files = response.get('files', [])
        return files[0].get('id') if files else None
    except HttpError as error:
        logger.error(f"Помилка при пошуку файлу '{name}': {error}")
        return None


def get_or_create_folder(service, folder_name: str, parent_id: str):
    folder_id = find_file_by_name(service, folder_name, parent_id=parent_id,
                                  mime_type='application/vnd.google-apps.folder')
    if folder_id:
        return folder_id

    logger.info(f"Папка '{folder_name}' не знайдена. Створюємо нову...")
    file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_id]}
    try:
        folder = service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')
    except HttpError as error:
        logger.error(f"Критична помилка при створенні папки '{folder_name}': {error}", exc_info=True)
        raise error


# --- НОВА ЛОГІКА ДЛЯ КЕРУВАННЯ ПАРТІЯМИ ---

def get_or_create_party_folder(drive_service, sheets_client, root_folder_id: str):
    """
    Знаходить останню активну папку партії або створює нову, якщо остання заповнена.
    Повертає ID папки партії та об'єкт worksheet для запису.
    """
    logger.info("Пошук активної папки для партії...")

    # 1. Шукаємо всі папки, що відповідають шаблону "Партія N"
    query = f"name contains 'Партія' and '{root_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    response = drive_service.files().list(q=query, fields='files(id, name)').execute()
    party_folders = response.get('files', [])

    latest_party_number = 0
    latest_party_folder_id = None

    if party_folders:
        # 2. Визначаємо останню партію за номером у назві
        for folder in party_folders:
            match = re.search(r'Партія (\d+)', folder['name'])
            if match:
                party_number = int(match.group(1))
                if party_number > latest_party_number:
                    latest_party_number = party_number
                    latest_party_folder_id = folder['id']

    # 3. Перевіряємо, чи остання партія не заповнена
    if latest_party_folder_id:
        spreadsheet_name = f"Партія {latest_party_number}"
        spreadsheet_id = find_file_by_name(drive_service, spreadsheet_name, parent_id=latest_party_folder_id,
                                           mime_type='application/vnd.google-apps.spreadsheet')

        if spreadsheet_id:
            try:
                spreadsheet = sheets_client.open_by_key(spreadsheet_id)
                worksheet = spreadsheet.sheet1
                # Перевіряємо кількість записів (не враховуючи заголовок)
                if len(worksheet.get_all_records()) < 50:
                    logger.info(f"Знайдено активну партію: 'Партія {latest_party_number}'. В ній є місце.")
                    return latest_party_folder_id, worksheet
            except Exception as e:
                logger.error(f"Помилка при перевірці таблиці в партії {latest_party_number}: {e}")
                # Якщо помилка, створюємо нову партію для надійності

    # 4. Створюємо нову партію, якщо не знайдено активної або остання заповнена
    new_party_number = latest_party_number + 1
    new_party_folder_name = f"Партія {new_party_number}"
    logger.info(f"Створення нової папки для партії: '{new_party_folder_name}'")

    new_party_folder_id = get_or_create_folder(drive_service, new_party_folder_name, root_folder_id)

    # 5. Копіюємо шаблон таблиці в нову папку партії
    try:
        template_id = google_config.template_spreadsheet_id
        new_spreadsheet_name = f"Партія {new_party_number}"
        copied_file = drive_service.files().copy(
            fileId=template_id,
            body={'name': new_spreadsheet_name, 'parents': [new_party_folder_id]}
        ).execute()

        spreadsheet = sheets_client.open_by_key(copied_file['id'])
        worksheet = spreadsheet.sheet1
        logger.info(f"Шаблон таблиці скопійовано в нову папку партії.")

        return new_party_folder_id, worksheet
    except HttpError as error:
        logger.error(f"Не вдалося скопіювати шаблон таблиці: {error}", exc_info=True)
        raise error


def add_user_to_party_sheet(worksheet, user_data: dict, photo_url: str, pdf_url: str):
    """
    Додає дані користувача в таблицю партії.
    Структура колонок має відповідати вашому шаблону.
    """
    try:
        passport_data = user_data.get('passport_data', {})
        full_name = passport_data.get('full_name', '').strip()
        surname, name, patronymic = ('', '', '')
        if full_name:
            parts = [p for p in full_name.split() if p]
            if len(parts) == 1:
                surname = parts[0]
            elif len(parts) == 2:
                surname, name = parts
            else:
                surname, name, patronymic = parts[0], parts[1], ' '.join(parts[2:])

        # Створюємо рядок з даними відповідно до вашого шаблону
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # Реєстраційний номер та дата
            "Студент",  # Категорія заявника
            surname,
            name,
            patronymic,
            user_data.get('phone_number', ''),
            passport_data.get('politech_email', ''),
            passport_data.get('residency_address', ''),
            "Україна",  # Країна
            "",  # Індекс
            "Львів",  # Місто
            "",  # Вулиця
            "",  # Будинок, квартира
            "Отримання пільгової транспортної картки ЛеоКард",  # Зміст
            "Особисто",  # Форма отримання
            "ЛКП 'Львівавтодор'",  # Адресовано до
            "",  # Коментар
            "",  # Результат
            passport_data.get('record_no', ''),  # ID
            pdf_url,  # Посилання на документ
            passport_data.get('date_of_birth', ''),
            passport_data.get('gender', ''),
            "Студентський",  # Профіль отримувача
            user_data.get("student_card_valid_until", ""),  # Закінчення дії профіля
        ]

        worksheet.append_row(row, value_input_option='USER_ENTERED')
        logger.info(f"Дані для користувача {full_name} додано в таблицю партії.")

    except Exception as e:
        logger.error(f"Помилка під час запису в таблицю партії: {e}", exc_info=True)


# --- Існуючі функції ---
# (Тут без змін, лише додано нові вище)
def get_or_create_worksheet(spreadsheet_name: str, worksheet_name: str, parent_folder_id: str):
    # ... (код цієї функції залишається без змін)
    pass


def create_user_folder_structure(service, user_pib: str, root_folder_id: str):
    documents_folder_id = get_or_create_folder(service, C.DOCUMENTS_FOLDER_NAME, parent_id=root_folder_id)
    user_folder_id = get_or_create_folder(service, user_pib, parent_id=documents_folder_id)
    folder_details = service.files().get(fileId=user_folder_id, fields='webViewLink').execute()
    return folder_details.get('webViewLink')


def upload_file_to_drive(service, folder_url: str, filename: str, file_buffer: io.BytesIO, mimetype='image/jpeg'):
    try:
        folder_id = folder_url.split('/')[-1].split('?')[0]
    except (IndexError, AttributeError):
        # Якщо URL не вдається розпарсити, спробуємо припустити, що це ID
        folder_id = folder_url
        logger.warning(f"Не вдалося розпарсити URL папки, використовуємо '{folder_url}' як ID.")

    file_metadata = {'name': filename, 'parents': [folder_id]}
    media = MediaIoBaseUpload(file_buffer, mimetype=mimetype, resumable=True)
    try:
        file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
        logger.info(f"Файл '{filename}' завантажено з ID: {file.get('id')}")
        return file.get('id'), file.get('webViewLink')
    except HttpError as error:
        logger.error(f"Помилка під час завантаження файлу '{filename}': {error}", exc_info=True)
        raise error


def add_user_to_sheet(worksheet, user_data: dict, telegram_id: int, folder_url: str):
    # ... (код цієї функції залишається без змін)
    pass