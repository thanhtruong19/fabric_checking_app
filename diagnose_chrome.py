"""Safely test the isolated Chrome remote-debugging connection."""
import urllib.request

from run_batch import CDP_PORT, CDP_URL, CHROME_USER_DATA_DIR, cdp_is_ready, launch_automation_chrome

print("=" * 60)
print("VEO3 CHROME DEBUG PORT TEST")
print("=" * 60)
print(f"Port: {CDP_PORT}")
print(f"Profile: {CHROME_USER_DATA_DIR}")

if not cdp_is_ready():
    print("Automation Chrome is not running; starting it now...")
    launch_automation_chrome()

with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=5) as response:
    print(f"CDP HTTP status: {response.status}")

print("PASS: Chrome remote debugging is ready.")
print("This test did not close or modify your normal Chrome profile.")
