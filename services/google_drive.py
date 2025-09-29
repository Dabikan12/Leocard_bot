import gspread
import os
import io
import logging
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from config import GoogleConfig
from utils.parsers import parse_ukrainian_address

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]


class GoogleService:
    """Google Drive and Sheets integration"""

    def __init__(self):
        self.config = GoogleConfig()
        self.creds = self._get_credentials()
        self.drive = build('drive', 'v3', credentials=self.creds) if self.creds else None
        self.sheets = gspread.authorize(self.creds) if self.creds else None

    def _get_credentials(self):
        """Get or refresh OAuth credentials"""
        creds = None

        if os.path.exists(self.config.token_file):
            try:
                creds = Credentials.from_authorized_user_file(self.config.token_file, SCOPES)
            except Exception as e:
                logger.error(f"Failed to load token: {e}")

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    with open(self.config.token_file, 'w') as token:
                        token.write(creds.to_json())
                except Exception as e:
                    logger.error(f"Token refresh failed: {e}")
                    return None
            else:
                if not os.path.exists(self.config.credentials_file):
                    logger.error(f"Credentials file not found: {self.config.credentials_file}")
                    return None

                flow = InstalledAppFlow.from_client_secrets_file(self.config.credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)

                try:
                    with open(self.config.token_file, 'w') as token:
                        token.write(creds.to_json())
                except OSError:
                    logger.warning("Could not save token (read-only filesystem)")

        return creds

    def create_user_folder(self, user_name: str, root_folder_id: str) -> str:
        """Create folder structure and return folder URL"""
        if not self.drive:
            raise ConnectionError("Google Drive not initialized")

        # Get or create Documents folder
        docs_folder_id = self._get_or_create_folder(
            self.config.documents_folder,
            root_folder_id
        )

        # Get or create user folder
        user_folder_id = self._get_or_create_folder(user_name, docs_folder_id)

        # Get folder URL
        folder = self.drive.files().get(fileId=user_folder_id, fields='webViewLink').execute()
        return folder.get('webViewLink')

    def upload_file(self, folder_url: str, filename: str, file_buffer: io.BytesIO, mimetype: str = 'image/jpeg') -> str:
        """Upload file to Drive and return file ID"""
        if not self.drive:
            raise ConnectionError("Google Drive not initialized")

        folder_id = folder_url.split('/')[-1].split('?')[0]

        file_metadata = {'name': filename, 'parents': [folder_id]}
        media = MediaIoBaseUpload(file_buffer, mimetype=mimetype, resumable=True)

        file = self.drive.files().create(body=file_metadata, media_body=media, fields='id').execute()
        logger.info(f"Uploaded {filename} with ID: {file.get('id')}")
        return file.get('id')

    def add_to_sheet(self, user_data: dict, telegram_id: int, folder_url: str, root_folder_id: str):
        """Add user data to Google Sheet"""
        if not self.sheets:
            raise ConnectionError("Google Sheets not initialized")

        worksheet = self._get_or_create_worksheet(root_folder_id)

        passport_data = user_data.get('passport_data', {})
        full_name = passport_data.get('full_name', '').strip()

        # Parse name
        surname, name, patronymic = ('N/A', 'N/A', 'N/A')
        if full_name:
            parts = [p for p in full_name.split() if p]
            if len(parts) == 1:
                surname = parts[0]
            elif len(parts) == 2:
                surname, name = parts
            else:
                surname, name, patronymic = parts[0], parts[1], ' '.join(parts[2:])

        # Parse address
        raw_address = passport_data.get('residency_address', 'N/A')
        if raw_address != 'N/A':
            parsed = parse_ukrainian_address(raw_address)
            city = parsed.get('city', 'N/A')
            street = parsed.get('street', 'N/A')
            building_flat = parsed.get('building_flat', 'N/A')
        else:
            city = street = building_flat = 'N/A'

        row = [
            str(telegram_id),
            surname,
            name,
            patronymic,
            user_data.get('phone_number', 'N/A'),
            passport_data.get('politech_email', 'N/A'),
            passport_data.get('record_no', 'N/A'),
            passport_data.get('date_of_birth', 'N/A'),
            passport_data.get('gender', 'N/A'),
            user_data.get('student_card_valid_until', 'N/A'),
            user_data.get('photo_3x4_link', 'N/A'),
            folder_url,
            raw_address,
            city,
            street,
            building_flat,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]

        worksheet.append_row(row, value_input_option='RAW')
        logger.info(f"Added user {telegram_id} to Google Sheet")

    def _get_or_create_folder(self, folder_name: str, parent_id: str) -> str:
        """Get existing folder or create new one"""
        query = f"name='{folder_name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        response = self.drive.files().list(q=query, fields='files(id)').execute()
        files = response.get('files', [])

        if files:
            return files[0]['id']

        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        folder = self.drive.files().create(body=file_metadata, fields='id').execute()
        logger.info(f"Created folder: {folder_name}")
        return folder.get('id')

    def _get_or_create_worksheet(self, root_folder_id: str):
        """Get or create worksheet with headers"""
        # Find or create spreadsheet
        query = f"name='{self.config.database_sheet}' and '{root_folder_id}' in parents and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        response = self.drive.files().list(q=query, fields='files(id)').execute()
        files = response.get('files', [])

        if files:
            spreadsheet = self.sheets.open_by_key(files[0]['id'])
        else:
            file_metadata = {
                'name': self.config.database_sheet,
                'parents': [root_folder_id],
                'mimeType': 'application/vnd.google-apps.spreadsheet'
            }
            new_file = self.drive.files().create(body=file_metadata, fields='id').execute()
            spreadsheet = self.sheets.open_by_key(new_file.get('id'))

        # Get or create worksheet
        try:
            worksheet = spreadsheet.worksheet(self.config.worksheet_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=self.config.worksheet_name, rows="100", cols="20")

        # Ensure headers
        headers = [
            "Telegram ID", "Прізвище", "Ім'я", "По батькові", "Телефон",
            "Електронна пошта", "Дані з ID-картки", "Дата народження", "Стать",
            "Термін дійсності Студентського квитка", "Фото", "Скани документів",
            "Повна адреса", "Місто", "Вулиця", "Номер будинку, квартира", "дата"
        ]

        first_row = worksheet.row_values(1)
        if not first_row or first_row != headers:
            worksheet.insert_row(headers, index=1)
            logger.info("Added headers to worksheet")

        return worksheet