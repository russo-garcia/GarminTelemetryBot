import json
from datetime import datetime

def get_renpho_metrics(date_str):
    try:
        from renpho import RenphoClient
        config_path = "/home/russogarcia/Garmin_Telemetry_Bot/config.json"
        with open(config_path, "r") as f:
            config = json.load(f)
            
        email = config.get("GARMIN_EMAIL")
        password = config.get("RENPHO_PASSWORD")
        
        if not email or not password:
            print("❌ Credentials missing in config.json")
            return None
            
        client = RenphoClient(email, password)
        client.login()
        measurements = client.get_all_measurements()
        
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        
        # Parse all measurements into a list of (date_object, raw_data)
        parsed_measurements = []
        for m in measurements:
            meas_date = datetime.fromtimestamp(m["timeStamp"]).date()
            parsed_measurements.append((meas_date, m))
            
        # Sort descending (newest to oldest)
        parsed_measurements.sort(key=lambda x: x[0], reverse=True)
        
        # Find the first measurement that happened on OR before the target date
        for meas_date, m in parsed_measurements:
            if meas_date <= target_date:
                is_carried_forward = (meas_date < target_date)
                
                return {
                    "weight": m.get("weight"),
                    "bmi": m.get("bmi"),
                    "is_carried_forward": is_carried_forward,
                    "last_measured_date": meas_date.strftime("%Y-%m-%d")
                }
                
        # If the loop finishes without returning, the target date is before your first weigh-in
        return None 
        
    except Exception:
        print("❌ Renpho API Error: acquisition failed.")
        return None

if __name__ == '__main__':
    # Test for today
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"Testing extraction for: {today_str}")
    
    result = get_renpho_metrics(today_str)
    print(json.dumps(result, indent=2))
