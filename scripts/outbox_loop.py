"""Development-only polling process. Production uses the Render cron job."""
import subprocess
import sys
import time

while True:
    subprocess.run([sys.executable, "manage.py", "process_outbox"], check=True)
    time.sleep(10)
