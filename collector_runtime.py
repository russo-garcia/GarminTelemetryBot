"""Explicit, lazy operational dependencies; importing helpers performs no I/O."""
import json
import os
from pathlib import Path
import threading
from garmin_auth import GarminCoordinator

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
SYNC_LEDGER = BASE_DIR / "synced_ids.txt"
_config = None
_drive_service = None
_dependency_lock = threading.RLock()


def get_config():
    global _config
    with _dependency_lock:
        if _config is None:
            with open(CONFIG_FILE, "r") as f:
                _config = json.load(f)
        return _config


def get_drive_folder_id():
    return get_config()["DRIVE_FOLDER_ID"]


def get_drive_service():
    global _drive_service
    with _dependency_lock:
        if _drive_service is None:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            scopes = ['https://www.googleapis.com/auth/drive.file']
            creds = Credentials.from_authorized_user_file(str(BASE_DIR / 'token.json'), scopes)
            _drive_service = build('drive', 'v3', credentials=creds)
        return _drive_service


def init_garmin():
    """Return a protected context, never a bare authenticated Garmin client."""
    token = os.environ.get('GARMIN_TOKEN_STORE', str(BASE_DIR / 'garmin_tokens.json'))
    lock = os.environ.get('GARMIN_COORDINATOR_LOCK', str(BASE_DIR / 'garmin_auth.lock'))
    timeout = float(os.environ.get('GARMIN_LOCK_TIMEOUT_SECONDS', '5'))
    coordinator = GarminCoordinator(token, lock, timeout=timeout)
    def factory():
        from garminconnect import Garmin
        config = get_config()
        return Garmin(config['GARMIN_EMAIL'], config['GARMIN_PASSWORD'])
    return coordinator.session(factory)


def get_or_create_drive_folder(folder_name, parent_id):
    """Searches Drive for a folder by name. Creates it if missing."""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
    results = get_drive_service().files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    items = results.get('files', [])

    if items:
        return items[0]['id']
    else:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        file = get_drive_service().files().create(body=file_metadata, fields='id').execute()
        return file.get('id')

def upload_to_drive(file_path, file_name, mime_type, parent_id):
    # Check if a file with this exact name already exists in the target folder
    query = f"name='{file_name}' and '{parent_id}' in parents and trashed=false"
    results = get_drive_service().files().list(q=query, spaces='drive', fields='files(id)').execute()
    items = results.get('files', [])

    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)

    if items:
        # File exists: update the content (overwrite) while keeping the same file ID
        existing_file_id = items[0]['id']
        file = get_drive_service().files().update(fileId=existing_file_id, media_body=media, fields='id').execute()
        return file.get('id')
    else:
        # File does not exist: create a new one
        file_metadata = {'name': file_name, 'parents': [parent_id]}
        file = get_drive_service().files().create(body=file_metadata, media_body=media, fields='id').execute()
        return file.get('id')

def get_synced_ids():
    if not os.path.exists(SYNC_LEDGER):
        return []
    with open(SYNC_LEDGER, "r") as f:
        return [line.strip() for line in f.readlines()]

def mark_as_synced(activity_id):
    with open(SYNC_LEDGER, "a") as f:
        f.write(f"{activity_id}\n")

