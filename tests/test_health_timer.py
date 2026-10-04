import configparser
from datetime import datetime, timezone
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
class TimerReviewTests(unittest.TestCase):
    def test_unit_contract_without_activation(self):
        timer=configparser.ConfigParser(interpolation=None);timer.read(ROOT/'systemd/garmin-health-daily.timer')
        self.assertEqual(timer['Timer']['OnCalendar'],'*-*-* 10:00:00 Europe/Berlin')
        self.assertEqual(timer['Timer']['Persistent'],'true')
        self.assertEqual(timer['Timer']['Unit'],'garmin-health-daily.service')
        text=(ROOT/'systemd/garmin-health-daily.service.in').read_text()
        self.assertIn('Type=oneshot',text);self.assertIn('Restart=no',text)
        self.assertIn('/venv/bin/python -B @COLLECTOR_DIR@/health_daily.py',text)
        self.assertIn('GARMIN_TOKEN_STORE=@COLLECTOR_DIR@/garmin_tokens.json',text)
        self.assertIn('GARMIN_COORDINATOR_LOCK=@COLLECTOR_DIR@/garmin_auth.lock',text)
        self.assertNotIn('RemainAfterExit=yes',text)
        self.assertNotIn('backfill.py',text)
    def test_berlin_ten_oclock_dst_offsets(self):
        berlin=ZoneInfo('Europe/Berlin')
        for month,day,utc_hour in ((3,28,9),(3,29,8),(10,24,8),(10,25,9)):
            instant=datetime(2026,month,day,10,tzinfo=berlin)
            self.assertEqual(instant.astimezone(timezone.utc).hour,utc_hour)

if __name__=='__main__':unittest.main()
