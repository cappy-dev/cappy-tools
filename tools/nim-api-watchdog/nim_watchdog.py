#!/usr/bin/env python3
"""
NIM API Watchdog — silent when down, speaks only on recovery.

A lightweight health check for the NVIDIA NIM API (build.nvidia.com).
Designed to run as a cron job script for Hermes Agent's auto-resume feature.

Behavior:
  - API was DOWN, now UP  → prints signal to stdout (triggers agent resume)
  - API was UP, still UP  → silent (no output, no agent spawned)
  - API is DOWN            → silent (no output)
  - No API key found       → exits silently with code 1

Output signals (on recovery only):
  NIM_API_RECOVERED|NO_TASK           — API is back, no task was in progress
  NIM_API_RECOVERED|HAS_TASK|<desc>   — API is back, resume task <desc>

State file: /tmp/nim_watchdog_state.json
Task file:  /tmp/nim_active_task.txt   — written by the agent before timeout

Environment:
  NVIDIA_API_KEY  — API key for NIM (required)
                     Loaded from env var, or from ~/.hermes/.env as fallback

Usage:
  python nim_watchdog.py

Token cost: 1 token per check (negligible on free tier).

Originally built for Hermes Agent (https://hermes-agent.nousresearch.com)
by Cappy & Mario 🎩✨
"""

import json
import os
import sys
import urllib.request
import urllib.error

# --- Configurable constants ---
STATE_FILE = "/tmp/nim_watchdog_state.json"
TASK_FILE = "/tmp/nim_active_task.txt"
BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL = ""  # e.g. "z-ai/glm-5.1" — set via NIM_MODEL env var or leave empty for default
TIMEOUT = 15
ENV_KEY_NAME = "NVIDIA_API_KEY"
ENV_FILE_PATH = os.path.expanduser("~/.hermes/.env")

# --- Load API key from environment, with .env fallback ---
API_KEY = os.environ.get(ENV_KEY_NAME, "")
if not API_KEY and os.path.exists(ENV_FILE_PATH):
    with open(ENV_FILE_PATH) as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{ENV_KEY_NAME}="):
                API_KEY = line.split("=", 1)[1].strip()
                break

if not API_KEY:
    # Silent exit — no key means nothing to check
    sys.exit(1)

# Allow model override via env var
model = os.environ.get("NIM_MODEL", MODEL) or "z-ai/glm-5.1"

def read_state():
    """Read the last known API status from state file."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_status": "unknown"}

def write_state(status):
    """Persist current API status to state file."""
    with open(STATE_FILE, "w") as f:
        json.dump({"last_status": status}, f)

def read_task():
    """Read the active task description (if any) from task file."""
    try:
        with open(TASK_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

# --- Main health check ---
state = read_state()
was_up = state.get("last_status") == "up"

payload = json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 1
}).encode()

req = urllib.request.Request(
    f"{BASE_URL}/chat/completions",
    data=payload,
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
)

try:
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        write_state("up")
        if not was_up:
            active_task = read_task()
            if active_task:
                print(f"NIM_API_RECOVERED|HAS_TASK|{active_task}")
            else:
                print("NIM_API_RECOVERED|NO_TASK")
            sys.exit(0)
        else:
            # Already up — stay silent to avoid spam
            sys.exit(0)
except urllib.error.HTTPError as e:
    write_state("down")
    # Silent — no stdout means no message delivered
    sys.exit(1)
except Exception:
    write_state("down")
    sys.exit(1)
