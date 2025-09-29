import gspread
import os
import io
import logging
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
import constants as C
from utils import parse_ukrainian_address

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]


def _is_running_in_docker() -> bool:
    try:
        return os.path.exists('/.dockerenv') or os.getenv('RUNNING_IN_DOCKER') == '1'
    except Exception:
        return False


CREDENTIALS_FILE = os.getenv('GOOGLE_CREDENTIALS_FILE', 'credentials.json')
TOKEN_FILE = os.getenv('GOOGLE_TOKEN_FILE', 'token.json')


def get_credentials():
    """
    Авторизується від імені користувача через OAuth 2.0.
    При першому запуску вимагатиме входу через браузер.
    """
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
            # Намагаймося зберегти оновлений токен, але не перериваємо роботу, якщо файл лише для читання
            try:
                with open(TOKEN_FILE, 'w') as token:
                    token.write(creds.to_json())
                logger.info(f"Токен доступу збережено у файл {TOKEN_FILE}")
            except OSError:
                logger.warning(
                    f"Не вдалось записати оновлений токен у '{TOKEN_FILE}' (можливо, змонтовано лише для читання). Продовжую використовувати токен у пам'яті."
                )
        else:
            # У контейнері забороняємо інтерактивну авторизацію: токен має бути попередньо змонтовано
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

            # Після локальної авторизації намагаємось записати токен на диск
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


def get_or_create_worksheet(spreadsheet_name: str, worksheet_name: str, parent_folder_id: str):
    drive_service = get_drive_service()
    sheets_client = get_sheets_client()
    if not drive_service or not sheets_client:
        return None

    spreadsheet_id = find_file_by_name(drive_service, spreadsheet_name, parent_id=parent_folder_id,
                                       mime_type='application/vnd.google-apps.spreadsheet')

    if spreadsheet_id:
        spreadsheet = sheets_client.open_by_key(spreadsheet_id)
    else:
        logger.warning(f"Google Sheet '{spreadsheet_name}' не знайдено. Створюємо нову...")
        file_metadata = {'name': spreadsheet_name, 'parents': [parent_folder_id],
                         'mimeType': 'application/vnd.google-apps.spreadsheet'}
        new_spreadsheet_file = drive_service.files().create(body=file_metadata, fields='id').execute()
        spreadsheet_id = new_spreadsheet_file.get('id')
        spreadsheet = sheets_client.open_by_key(spreadsheet_id)

    # Внутрішня утиліта для гарантії наявності заголовків в ПЕРШОМУ рядку
    def ensure_worksheet_headers(target_worksheet):
        header = [
            "Telegram ID",
            "Прізвище",
            "Ім'я",
            "По батькові",
            "Телефон",
            "Електронна пошта",
            "Дані з ID-картки",
            "Дата народження",
            "Стать",
            "Термін дійсності Студентського квитка",
            "Фото",
            "Скани документів",
            "Повна адреса",
            "Місто",
            "Вулиця",
            "Номер будинку, квартира",
            "дата"
        ]
        try:
            first_row = target_worksheet.row_values(1)
            if not first_row:
                target_worksheet.insert_row(header, index=1)
                logger.info("Додано заголовки в порожній аркуш.")
            else:
                # Якщо перший рядок не збігається із заголовками — вставляємо заголовки на перший рядок
                if first_row != header:
                    target_worksheet.insert_row(header, index=1)
                    logger.info("Додано заголовки як перший рядок, існуючі дані зсунуті вниз.")
        except Exception as e:
            logger.error(f"Не вдалося гарантувати заголовки аркуша: {e}", exc_info=True)

    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
        ensure_worksheet_headers(worksheet)
        return worksheet
    except gspread.WorksheetNotFound:
        logger.warning(f"Аркуш '{worksheet_name}' не знайдено. Створюємо новий...")
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows="100", cols="20")
        ensure_worksheet_headers(worksheet)
        logger.info(f"Створено аркуш '{worksheet_name}' та додано заголовки.")
        return worksheet


def create_user_folder_structure(service, user_pib: str, root_folder_id: str):
    documents_folder_id = get_or_create_folder(service, C.DOCUMENTS_FOLDER_NAME, parent_id=root_folder_id)
    user_folder_id = get_or_create_folder(service, user_pib, parent_id=documents_folder_id)
    folder_details = service.files().get(fileId=user_folder_id, fields='webViewLink').execute()
    return folder_details.get('webViewLink')


def upload_file_to_drive(service, folder_url: str, filename: str, file_buffer: io.BytesIO, mimetype='image/jpeg'):
    try:
        folder_id = folder_url.split('/')[-1].split('?')[0]
    except (IndexError, AttributeError):
        logger.error(f"Не вдалося витягти ID папки з URL: {folder_url}")
        return None

    file_metadata = {'name': filename, 'parents': [folder_id]}
    media = MediaIoBaseUpload(file_buffer, mimetype=mimetype, resumable=True)
    try:
        file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        logger.info(f"Файл '{filename}' завантажено з ID: {file.get('id')}")
        return file.get('id')
    except HttpError as error:
        logger.error(f"Помилка під час завантаження файлу '{filename}': {error}", exc_info=True)
        raise error


def add_user_to_sheet(worksheet, user_data: dict, telegram_id: int, folder_url: str):
    try:
        passport_data = user_data.get('passport_data', {})
        full_name = passport_data.get('full_name', '').strip()
        surname, name, patronymic = ('N/A', 'N/A', 'N/A')
        if full_name:
            parts = [p for p in full_name.split() if p]
            if len(parts) == 1:
                surname = parts[0]
            elif len(parts) == 2:
                surname, name = parts
            else:
                surname, name, patronymic = parts[0], parts[1], ' '.join(parts[2:])

        phone_number = user_data.get('phone_number', 'N/A')
        photo_link = user_data.get('photo_3x4_link', 'N/A')
        student_card_valid_until = user_data.get("student_card_valid_until", "N/A")
        politech_email = user_data.get('politech_email') or passport_data.get('politech_email', 'N/A')

        # Get raw address string
        raw_address = passport_data.get('residency_address', 'N/A')

        # Try to parse it into components
        if raw_address != 'N/A':
            parsed_address = parse_ukrainian_address(raw_address)
            city = parsed_address.get('city', 'N/A')
            street = parsed_address.get('street', 'N/A')
            building_flat = parsed_address.get('building_flat', 'N/A')
        else:
            city = street = building_flat = 'N/A'

        row = [
            str(telegram_id),  # Telegram ID
            surname,  # Прізвище
            name,  # Імʼя
            patronymic,  # По батькові
            phone_number,  # Телефон
            politech_email,  # Електронна пошта
            passport_data.get('record_no', 'N/A'),  # Дані з ID-картки
            passport_data.get('date_of_birth', 'N/A'),  # Дата народження
            passport_data.get('gender', 'N/A'),  # Стать
            student_card_valid_until,  # Термін дійсності Студентського квитка
            photo_link,  # Фото
            folder_url,  # Скани документів
            raw_address,  # Повна адреса
            city,  # Місто
            street,  # Вулиця
            building_flat,  # Номер будинку, квартира
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # дата
        ]
        # Використовуємо RAW, щоб зберегти значення як текст (зокрема, з нулями на початку)
        worksheet.append_row(row, value_input_option='RAW')
        logger.info(f"Дані для користувача {telegram_id} успішно додано в Google Sheet.")
    except Exception as e:
        logger.error(f"Помилка під час запису в Google Sheet: {e}", exc_info=True)
