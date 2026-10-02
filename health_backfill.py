import time
from datetime import datetime, timedelta
from health_sync import sync_health_data

def run_health_backfill():
    start_date = datetime(2026, 9, 23)
    end_date = datetime.now()
    
    print(f"Starting health data backfill from {start_date.strftime('%Y-%m-%d')} to today...")
    
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        
        success = sync_health_data(date_str)
        
        if success:
            time.sleep(3) # Crucial pause to prevent rate limits
        else:
            time.sleep(10) # Longer pause if an error occurs
            
        current_date += timedelta(days=1)
        
    print("\n✅ Historical health backfill complete.")

if __name__ == '__main__':
    run_health_backfill()
