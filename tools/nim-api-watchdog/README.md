# 🎩 NIM API Watchdog

A lightweight Python health check for the [NVIDIA NIM API](https://build.nvidia.com). Designed to run as a cron script for [Hermes Agent](https://hermes-agent.nousresearch.com) — silent when the API is down, speaks only when it recovers.

## Why?

NIM's free tier has rate limits. When you hit them mid-task, your AI agent stops dead. Instead of manually checking "is it back yet?", this watchdog:

- Pings the API every hour (1 token per check — basically free)
- Stays **silent** when the API is still down (no spam!)
- Stays **silent** when the API was already up (no spam!)
- **Only speaks** when the API recovers from a downtime
- If you had an active task, it signals your agent to **auto-resume**

## How It Works

The script tracks API status in a state file (`/tmp/nim_watchdog_state.json`):

| Last Status | Current Status | Output |
|---|---|---|
| down | up | `NIM_API_RECOVERED\|NO_TASK` or `NIM_API_RECOVERED\|HAS_TASK\|<description>` |
| up | up | _(silent)_ |
| up | down | _(silent)_ |
| down | down | _(silent)_ |
| unknown | up | `NIM_API_RECOVERED\|...` |

## Setup

### 1. Set your API key

```bash
export NVIDIA_API_KEY="nvapi-..."
```

Or let the script read it from `~/.hermes/.env` automatically.

### 2. Run it

```bash
python nim_watchdog.py
```

Exit code 0 = API is up. Exit code 1 = API is down or key missing.

### 3. Set up a cron job (Hermes Agent)

In Hermes, create a cron job that uses this script:

```bash
hermes cron add \
  --name "NIM Resume Watchdog" \
  --schedule "every 1h" \
  --script nim_watchdog.py \
  --prompt "The NIM API just recovered. If the signal says HAS_TASK, auto-resume the task without asking. If NO_TASK, notify the user the API is back." \
  --deliver origin
```

### 4. Task auto-resume (optional)

Before starting a long task, write a description to the task file:

```bash
echo "Setting up nginx reverse proxy" > /tmp/nim_active_task.txt
```

When the API recovers, the watchdog reads this file and signals your agent to resume.

Clear it when done:

```bash
rm /tmp/nim_active_task.txt
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `NVIDIA_API_KEY` | _(required)_ | Your NIM API key |
| `NIM_MODEL` | `z-ai/glm-5.1` | Model to ping (override via env var) |
| `STATE_FILE` | `/tmp/nim_watchdog_state.json` | Where to track up/down state |
| `TASK_FILE` | `/tmp/nim_active_task.txt` | Active task description |
| `TIMEOUT` | `15` | Request timeout in seconds |

## Token Cost

Each check uses 1 completion token. On NIM's free tier (1,000 credits/month, 1 credit = 1,000 tokens), running this every hour costs ~0.72 credits/month. Negligible.

## License

MIT

---

Built with ❤️ by [cappy-dev](https://github.com/cappy-dev) — a Bonneter who got tired of checking if the API was back up 🎩✨
