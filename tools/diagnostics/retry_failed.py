import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
CONFIG_FILE = PROJECT_DIR / "config.json"
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    config = json.load(f)

configured_status_file = Path(os.path.expandvars(config["paths"]["status_file"]))
STATUS_FILE = (
    configured_status_file
    if configured_status_file.is_absolute()
    else PROJECT_DIR / configured_status_file
)

def reset_failed():
    if not os.path.exists(STATUS_FILE):
        print("No status file found.")
        return
        
    with open(STATUS_FILE, "r", encoding="utf-8") as f:
        status_data = json.load(f)
        
    reset_count = 0
    for sku, info in status_data.items():
        # Check for errors, timeouts or processing hangs
        if info["status"] == "error" or info["status"] == "processing":
            print(f"Resetting SKU {sku} (was {info['status']})")
            info["status"] = "pending"
            info["retries"] = 0
            info["error_type"] = None
            reset_count += 1
            
    if reset_count > 0:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=4)
        print(f"Reset {reset_count} SKUs. You can now run run_batch.py again.")
    else:
        print("No failed SKUs to reset.")

if __name__ == "__main__":
    reset_failed()
