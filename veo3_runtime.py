"""Runtime path helpers shared by source runs and the standalone executable."""

import os
from pathlib import Path


PROJECT_DIR_ENV = "VEO3_PROJECT_DIR"
EMBEDDED_WORKER_ENV = "VEO3_EMBEDDED_WORKER"


def get_project_dir(module_file):
    """Return the writable data directory selected by the desktop app."""
    configured = str(os.environ.get(PROJECT_DIR_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(module_file).resolve().parent


def is_embedded_worker():
    return os.environ.get(EMBEDDED_WORKER_ENV) == "1"
