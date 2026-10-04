import os
import json
import tempfile
from datetime import datetime
from garmin_auth import GarminBusy
from collector_runtime import (init_garmin, get_config, get_drive_folder_id,
    get_or_create_drive_folder, upload_to_drive, get_synced_ids, mark_as_synced)

# Initialized explicitly by run_bot; helper imports do not create a Telegram bot.
bot = None

def send_welcome(message):
    from telebot.types import ReplyKeyboardMarkup, KeyboardButton
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

def trigger_sync(message):
    bot.send_message(message.chat.id, "🔄 Connecting to Garmin API... fetching recent activities.")
    
    try:
        with init_garmin() as gclient:
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
                type_folder_id = get_or_create_drive_folder(act_type.capitalize(), get_drive_folder_id())
                year_folder_id = get_or_create_drive_folder(year_str, type_folder_id)
                final_folder_id = get_or_create_drive_folder(month_str, year_folder_id)
                
                # 3. Download and Upload
                with init_garmin() as gclient:
                    fit_data = gclient.download_activity(act_id)
                
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
            
    except GarminBusy:
        bot.send_message(message.chat.id, "⏳ Garmin is busy. Please retry shortly.")
    except Exception:
        bot.send_message(message.chat.id, "❌ Sync Error: acquisition or upload failed.")

def sync_status(message):
    synced_count = len(get_synced_ids())
    bot.send_message(message.chat.id, f"📊 **System Status**\nTotal unique activities synced to Drive: {synced_count}\nGoogle Drive API: Connected\nGarmin API: Ready")

def trigger_health_sync_bot(message):
    from health_jobs import refresh_today
    from health_state import HealthJobBusy, HealthStateError

    bot.send_message(message.chat.id, "❤️ Refreshing today's provisional health snapshot (Europe/Berlin)...")
    try:
        today_str, success = refresh_today()
    except HealthJobBusy:
        bot.send_message(message.chat.id, "⏳ A health job is active. Please retry later.")
        return
    except (HealthStateError, OSError, ValueError):
        bot.send_message(message.chat.id, "❌ Health runtime configuration is unavailable.")
        return
    if success:
        bot.send_message(message.chat.id, f"✅ Successfully synced provisional/open health data for {today_str}. It is expected to be finalized automatically after the day ends.")
    else:
        bot.send_message(message.chat.id, f"❌ Failed to refresh provisional health data for {today_str}. Please retry later.")


def placeholder_handler(message):
    bot.send_message(message.chat.id, "🔧 Feature under construction.")

def run_bot():
    global bot
    import telebot
    bot = telebot.TeleBot(get_config()["TELEGRAM_TOKEN"])
    bot.register_message_handler(send_welcome, commands=['start'])
    for text, handler in (
        ("🏃 Get Latest Activities", trigger_sync),
        ("📊 Sync Status", sync_status),
        ("❤️ Get Health Data", trigger_health_sync_bot),
        ("✨ New Button", placeholder_handler),
    ):
        bot.register_message_handler(handler, func=lambda message, text=text: message.text == text)
    bot.infinity_polling()


if __name__ == '__main__':
    run_bot()
