#!/usr/bin/env python3
"""Read Fanqie's short-lived status for the DankBar widget."""

import json
import os
from pathlib import Path

path = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "fanqie-status.json"
try:
    status = json.loads(path.read_text(encoding="utf-8"))
    os.kill(status["pid"], 0)
except (FileNotFoundError, KeyError, ValueError, PermissionError, ProcessLookupError):
    status = {"active": False}
print(json.dumps(status, ensure_ascii=False))
