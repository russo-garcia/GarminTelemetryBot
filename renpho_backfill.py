import time
from datetime import datetime, timedelta
from health_sync import sync_health_data

def backfill_history():
    # Start date of your Renpho tracking
    start_date = datetime(2026, 4, 23)
    end_date = datetime.now()
    
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        print(f"\n--- Backfilling {date_str} ---")
        
        # Calls your working extraction module
        success = sync_health_data(date_str)
        
        if success:
            print(f"✅ {date_str} backfilled successfully.")
        else:
            print(f"⚠️ {date_str} backfill failed.")
            
        # 3-second pause to prevent rate-limiting from Garmin or Google Drive
        time.sleep(3)
        
        current_date += timedelta(days=1)

if __name__ == '__main__':
    print("Starting historical backfill from April 23, 2026...")
    backfill_history()
    print("\n🎉 Backfill complete! All history is synced.")
