import os
import json
import tempfile
from datetime import datetime
from GarminTelemetry import init_garmin, get_or_create_drive_folder, upload_to_drive, DRIVE_FOLDER_ID
from renpho_sync import get_renpho_metrics

def sync_health_data(date_str):
    print(f"Fetching health and sleep metrics for {date_str}...")
    try:
        gclient = init_garmin()

        # Parse date for folder structure
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        year_str = dt.strftime("%Y")
        month_str = dt.strftime("%m")

        # Fetch daily stats and sleep data
        stats = gclient.get_stats(date_str)
        sleep = gclient.get_sleep_data(date_str)

        # Fetch Renpho data
        print(f"Fetching Renpho data for {date_str}...")
        renpho_data = get_renpho_metrics(date_str)

        # Bundle into a single master payload
        health_payload = {
            "date": date_str,
            "daily_stats": stats,
            "sleep_data": sleep,
            "weight_metrics": renpho_data
        }

        # Route to Daily_Health/Year/Month
        type_folder_id = get_or_create_drive_folder("Daily_Health", DRIVE_FOLDER_ID)
        year_folder_id = get_or_create_drive_folder(year_str, type_folder_id)
        final_folder_id = get_or_create_drive_folder(month_str, year_folder_id)

        # Upload to Drive
        with tempfile.TemporaryDirectory() as tmpdirname:
            json_path = os.path.join(tmpdirname, f"health_{date_str}.json")
            with open(json_path, "w") as f:
                json.dump(health_payload, f, indent=4)

            print(f"Uploading health_{date_str}.json to Drive...")
            upload_to_drive(json_path, f"health_{date_str}.json", "application/json", final_folder_id)

        print(f"✅ Successfully synced health data for {date_str}.")
        return True

    except Exception as e:
        print(f"❌ Error syncing health data for {date_str}: {e}")
        return False

if __name__ == '__main__':
    # When run directly, it defaults to syncing today's data
    today_str = datetime.now().strftime("%Y-%m-%d")
    sync_health_data(today_str)
