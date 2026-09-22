import os
import json
import tempfile
from datetime import datetime
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from garminconnect import Garmin
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CONFIG_FILE = "config.json"
SYNC_LEDGER = "synced_ids.txt"

with open(CONFIG_FILE, "r") as f:
    config = json.load(f)

TELEGRAM_TOKEN = config["TELEGRAM_TOKEN"]
GARMIN_EMAIL = config["GARMIN_EMAIL"]
GARMIN_PASSWORD = config["GARMIN_PASSWORD"]
DRIVE_FOLDER_ID = config["DRIVE_FOLDER_ID"]

bot = telebot.TeleBot(TELEGRAM_TOKEN)

scopes = ['https://www.googleapis.com/auth/drive.file']
creds = Credentials.from_authorized_user_file('token.json', scopes)
drive_service = build('drive', 'v3', credentials=creds)

def init_garmin():
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login("garmin_tokens.json")
    return client

def get_or_create_drive_folder(folder_name, parent_id):
    """Searches Drive for a folder by name. Creates it if missing."""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
    results = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
    items = results.get('files', [])
    
    if items:
        return items[0]['id']
    else:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        file = drive_service.files().create(body=file_metadata, fields='id').execute()
        return file.get('id')

def upload_to_drive(file_path, file_name, mime_type, parent_id):
    # Check if a file with this exact name already exists in the target folder
    query = f"name='{file_name}' and '{parent_id}' in parents and trashed=false"
    results = drive_service.files().list(q=query, spaces='drive', fields='files(id)').execute()
    items = results.get('files', [])
    
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
    
    if items:
        # File exists: update the content (overwrite) while keeping the same file ID
        existing_file_id = items[0]['id']
        file = drive_service.files().update(fileId=existing_file_id, media_body=media, fields='id').execute()
        return file.get('id')
    else:
        # File does not exist: create a new one
        file_metadata = {'name': file_name, 'parents': [parent_id]}
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return file.get('id')

def get_synced_ids():
    if not os.path.exists(SYNC_LEDGER):
        return []
    with open(SYNC_LEDGER, "r") as f:
        return [line.strip() for line in f.readlines()]

def mark_as_synced(activity_id):
    with open(SYNC_LEDGER, "a") as f:
        f.write(f"{activity_id}\n")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    # Row 1: 2 buttons
    markup.row(
        KeyboardButton("🏃 Get Latest Activities"),
        KeyboardButton("❤️ Get Health Data")
    )
    # Row 2: 2 buttons
    markup.row(
        KeyboardButton("✨ New Button"),
        KeyboardButton("📊 Sync Status")
    )
    bot.send_message(message.chat.id, "🛰️ Garmin Telemetry Router Online.\nReady to extract and sync.", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "🏃 Get Latest Activities")
def trigger_sync(message):
    bot.send_message(message.chat.id, "🔄 Connecting to Garmin API... fetching recent activities.")
    
    try:
        gclient = init_garmin()
        activities = gclient.get_activities(0, 5)
        synced_ids = get_synced_ids()
        
        new_sync_count = 0
        
        for activity in activities:
            act_id = str(activity['activityId'])
            act_type = activity['activityType']['typeKey']
            
            if act_id not in synced_ids:
                new_sync_count += 1
                bot.send_message(message.chat.id, f"📥 Routing new activity: {act_type.capitalize()} ({act_id})...")
                
                # 1. Parse Date for Folder Structure
                start_time_str = activity.get('startTimeLocal', '') # e.g., "2026-09-18 10:30:00"
                if start_time_str:
                    dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                    year_str = dt.strftime("%Y")
                    month_str = dt.strftime("%m")
                else:
                    year_str = "Unknown_Year"
                    month_str = "Unknown_Month"

                # 2. Build the Drive Path (Activity Type -> Year -> Month)
                type_folder_id = get_or_create_drive_folder(act_type.capitalize(), DRIVE_FOLDER_ID)
                year_folder_id = get_or_create_drive_folder(year_str, type_folder_id)
                final_folder_id = get_or_create_drive_folder(month_str, year_folder_id)
                
                # 3. Download and Upload
                fit_data = gclient.download_activity(act_id, dl_fmt=gclient.ActivityDownloadFormat.ORIGINAL)
                
                with tempfile.TemporaryDirectory() as tmpdirname:
                    fit_path = os.path.join(tmpdirname, f"{act_id}.fit")
                    with open(fit_path, "wb") as f:
                        f.write(fit_data)
                    upload_to_drive(fit_path, f"{act_type}_{act_id}.fit", "application/octet-stream", final_folder_id)
                    
                    json_path = os.path.join(tmpdirname, f"{act_id}.json")
                    with open(json_path, "w") as f:
                        json.dump(activity, f, indent=4)
                    upload_to_drive(json_path, f"{act_type}_{act_id}.json", "application/json", final_folder_id)
                
                mark_as_synced(act_id)
        
        if new_sync_count == 0:
            bot.send_message(message.chat.id, "✅ No new activities to sync. Drive is up to date.")
        else:
            bot.send_message(message.chat.id, f"✅ Successfully pushed {new_sync_count} structured workout(s) to Google Drive.")
            
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Sync Error: {str(e)}")

@bot.message_handler(func=lambda message: message.text == "📊 Sync Status")
def sync_status(message):
    synced_count = len(get_synced_ids())
    bot.send_message(message.chat.id, f"📊 **System Status**\nTotal unique activities synced to Drive: {synced_count}\nGoogle Drive API: Connected\nGarmin API: Ready")

@bot.message_handler(func=lambda message: message.text == "❤️ Get Health Data")
def trigger_health_sync_bot(message):
    from health_sync import sync_health_data

    bot.send_message(message.chat.id, "❤️ Fetching today's health and sleep metrics...")
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # Calls the standalone script logic
    success = sync_health_data(today_str)
    
    if success:
        bot.send_message(message.chat.id, f"✅ Successfully synced comprehensive health data for {today_str}.")
    else:
        bot.send_message(message.chat.id, f"❌ Failed to sync health data for {today_str}. Check Pi logs.")

@bot.message_handler(func=lambda message: message.text == "✨ New Button")
def placeholder_handler(message):
    bot.send_message(message.chat.id, "🔧 Feature under construction.")

if __name__ == '__main__':
    bot.infinity_polling()
