import time
import os
import json
import tempfile
from datetime import datetime
from GarminTelemetry import init_garmin, get_synced_ids, mark_as_synced, get_or_create_drive_folder, upload_to_drive, DRIVE_FOLDER_ID

def run_full_backfill():
    print("Starting full backfill since June 26...")
    gclient = init_garmin()
    synced_ids = get_synced_ids()
    
    new_sync_count = 0
    start_index = 0
    chunk_size = 50
    
    while True:
        print(f"Fetching activities {start_index} to {start_index + chunk_size}...")
        activities = gclient.get_activities(start_index, chunk_size)
        
        if not activities:
            print("No more activities found in Garmin history.")
            break
            
        for activity in activities:
            act_id = str(activity['activityId'])
            act_type = activity['activityType']['typeKey']
            start_time_str = activity.get('startTimeLocal', '')
            
            # Stop the script entirely if we hit a date before June 26, 2026
            if start_time_str:
                dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                if dt < datetime(2026, 6, 26):
                    print(f"\nReached date boundary ({start_time_str}). Stopping fetch.")
                    print(f"Backfill complete. Synced {new_sync_count} new historical activities.")
                    return
            
            if act_id not in synced_ids:
                print(f"Processing {act_type} ({act_id}) from {start_time_str}...")
                
                year_str = dt.strftime("%Y") if start_time_str else "Unknown_Year"
                month_str = dt.strftime("%m") if start_time_str else "Unknown_Month"

                # Route to appropriate Drive folders
                type_folder_id = get_or_create_drive_folder(act_type.capitalize(), DRIVE_FOLDER_ID)
                year_folder_id = get_or_create_drive_folder(year_str, type_folder_id)
                final_folder_id = get_or_create_drive_folder(month_str, year_folder_id)
                
                try:
                    # Download raw FIT file
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
                    new_sync_count += 1
                    print(f"Successfully synced {act_id}. Waiting 3 seconds to avoid rate limits...")
                    time.sleep(3) # Crucial pause to prevent 429 IP ban
                    
                except Exception as e:
                    print(f"Error syncing {act_id}: {e}")
                    time.sleep(10) # Back off if an error occurs
        
        start_index += chunk_size

    print(f"\nBackfill complete. Synced {new_sync_count} historical activities.")

if __name__ == '__main__':
    run_full_backfill()
