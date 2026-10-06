import os
import json
import tempfile
from datetime import datetime
from collector_runtime import init_garmin, get_or_create_drive_folder, upload_to_drive, get_drive_folder_id
from garmin_auth import GarminBusy
from garmin_weight import WeightContext

def sync_health_data(date_str, *, weight_context=None):
    print(f"Fetching health and sleep metrics for {date_str}...")
    try:
        # Parse date for folder structure
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        year_str = dt.strftime("%Y")
        month_str = dt.strftime("%m")

        # Fetch daily stats and sleep data
        with init_garmin() as gclient:
            stats = gclient.get_stats(date_str)
            sleep = gclient.get_sleep_data(date_str)
            # Optional acquisition shares this lease; never a second client.
            context = weight_context if weight_context is not None else WeightContext(date_str)
            weight, weight_acquisition = context.for_day(gclient, date_str)

        print(f"Optional Garmin weight for {date_str}: {weight_acquisition['outcome']}.")

        # Bundle into a single master payload
        health_payload = {
            "date": date_str,
            "daily_stats": stats,
            "sleep_data": sleep,
            "weight_metrics": weight,
            "weight_acquisition": weight_acquisition
        }

        # Route to Daily_Health/Year/Month
        type_folder_id = get_or_create_drive_folder("Daily_Health", get_drive_folder_id())
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

    except GarminBusy:
        print("⏳ Garmin is busy; health synchronization was not completed. Retry later.")
        return False
    except Exception:
        print(f"❌ Error syncing health data for {date_str}: acquisition or upload failed.")
        return False

def main():
    # Standalone today refresh uses the same health-job lease as Telegram.
    from health_jobs import refresh_today
    from health_state import HealthJobBusy, HealthStateError
    try:
        day, success = refresh_today()
    except HealthJobBusy:
        print("Health job busy; retry later.")
        return 75
    except (HealthStateError, OSError, ValueError):
        print("Health runtime configuration is unavailable.")
        return 2
    if success:
        print(f"Provisional/open health snapshot for {day}; finalization is a separate post-day job.")
    return 0 if success else 75


if __name__ == '__main__':
    raise SystemExit(main())
