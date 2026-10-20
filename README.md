# RTC Room Framework

WebRTC room signaling server with a browser test client, speaking events, STT pipeline, and optional in-process agent participant.

## Prerequisites

- Python 3.10+
- `pip`
- `openssl` (only needed for HTTPS dev script)

## Setup

Run from project root:

```bash
cd /Users/arshpreet/project-0
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
```

If you only want dependencies (without editable install):

```bash
python3 -m pip install -r requirements.txt
```

## Environment Configuration

Optional `.env` is available at project root. **The server does not auto-load `.env`** — it reads
environment variables from the process environment, so load them into your shell before starting
the server:

```bash
set -a
source .env
set +a
```

Configuration is read at import time and refreshed on server startup, so variables exported after
the module was imported are still picked up. The effective configuration is logged at startup and
exposed on `GET /health` under `config`.

Quick local browser demo (no create-room auth / no explicit room creation):

```bash
unset ROOM_CREATE_API_KEY ROOM_CREATE_API_SECRET REQUIRE_CREATE_ROOM_AUTH
export REQUIRE_EXPLICIT_ROOM_CREATE=false
```

> Note: the checked-in `.env` enables `REQUIRE_CREATE_ROOM_AUTH=true` and
> `REQUIRE_EXPLICIT_ROOM_CREATE=true`, so sourcing it makes `join_room` fail with `room_not_found`
> for rooms that do not exist yet. Use the exports above for a frictionless local demo.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `ROOM_CREATE_API_KEY` | _unset_ | API key accepted for `create_room` |
| `ROOM_CREATE_API_SECRET` | _unset_ | HMAC secret used to sign create-room tokens |
| `REQUIRE_CREATE_ROOM_AUTH` | `true` when key and secret are set | Require signed tokens for `create_room` |
| `ROOM_CREATE_TOKEN_TTL_SECONDS` | `300` | Accepted clock skew for create-room tokens |
| `REQUIRE_EXPLICIT_ROOM_CREATE` | `false` | Require rooms to be created before joining |
| `MAX_ROOM_PARTICIPANTS` | `100` | Upper bound for client-requested room capacity |
| `STT_PROVIDER` | `browser` | `browser`, `openai`, `deepgram`, or `sarvam` |
| `STT_LANGUAGE_CODE` | `unknown` | Default language hint sent to STT providers |
| `OPENAI_API_KEY` / `OPENAI_STT_MODEL` | _unset_ / `gpt-4o-mini-transcribe` | OpenAI STT settings |
| `OPENAI_STT_TIMEOUT_SECONDS` | `20` | OpenAI STT request timeout |
| `DEEPGRAM_API_KEY` / `DEEPGRAM_STT_MODEL` | _unset_ / `nova-3` | Deepgram STT settings |
| `DEEPGRAM_STT_SMART_FORMAT` | `true` | Ask Deepgram for smart-formatted transcripts |
| `DEEPGRAM_STT_TIMEOUT_SECONDS` | `20` | Deepgram STT request timeout |
| `SARVAM_API_KEY` / `SARVAM_API_SUBSCRIPTION_KEY` | _unset_ | Sarvam credentials (either name works) |
| `SARVAM_STT_MODEL` / `SARVAM_STT_MODE` | `saaras:v3` / `transcribe` | Sarvam STT settings |
| `SARVAM_STT_TIMEOUT_SECONDS` | `20` | Sarvam STT request timeout |

### Room capacity

`MAX_ROOM_PARTICIPANTS` caps the `max_participants` value a client may request. The
server-side default (10) is clamped to that cap, so lowering the cap below 10 never
rejects a join that omits the field. The bundled web client reads the effective cap
from `GET /health` and only sends `max_participants` when the field is filled in.

Capacity is fixed when a room is created: a client joining an existing room cannot
change it, and the first client to create a room chooses its size (up to the cap).

### Removing participants

`RoomControlModule.remove_participant()` and `RoomControlModule.close_room()` evict
participants. An evicted client is notified with a `left_room` message (including
`"reason": "evicted"`) and is rejected if it tries to rejoin that room — it receives
`participant_evicted` instead of `joined_room`. The restriction is scoped to the
lifetime of that room: once the room is destroyed and re-created, the participant may
join again.

### Web client asset

The browser client lives at `rtc_room_framework/web/index.html` and is shipped with the package.
`web/index.html` is a symlink to that file so both paths work; edit the packaged file directly.
The server serves `web/index.html` when present and otherwise falls back to the packaged asset.

## Run Commands

### 1) Start server (HTTP, fastest local dev)

```bash
uvicorn signaling.server:app --host 0.0.0.0 --port 8000 --reload
```

Open in browser:

- `http://localhost:8000`

Health check:

```bash
curl http://localhost:8000/health
```

### 2) Start server via installed CLI

```bash
rtc-room-server --host 0.0.0.0 --port 8000 --reload
```

### 3) Start server via module entry point

```bash
python3 -m rtc_room_framework --host 0.0.0.0 --port 8000 --reload
```

### 4) Start HTTPS dev server (auto-generates localhost cert)

```bash
./scripts/run_https_dev.sh
```

Open in browser:

- `https://localhost:8443`

Notes:

- Script expects virtualenv at `venv/` and activates `venv/bin/activate`.
- Cert files are generated in `certs/localhost.crt` and `certs/localhost.key`.
- The private key is git-ignored; only the certificate is tracked.

### 5) Run server with in-process echo agent participant

```bash
python3 scripts/run_server_with_agent.py --room-id demo-room --create-room
```

Common options:

```bash
python3 scripts/run_server_with_agent.py \
  --room-id demo-room \
  --agent-name "Room Agent" \
  --agent-prefix "Agent: " \
  --respond-to-interim \
  --host 0.0.0.0 \
  --port 8000
```

## STT Provider Launch Examples

Default mode is browser STT (`stt_transcript`).

### OpenAI provider

```bash
export STT_PROVIDER=openai
export OPENAI_API_KEY=your_key_here
export OPENAI_STT_MODEL=gpt-4o-mini-transcribe
uvicorn signaling.server:app --host 0.0.0.0 --port 8000 --reload
```

### Deepgram provider

```bash
export STT_PROVIDER=deepgram
export DEEPGRAM_API_KEY=your_key_here
uvicorn signaling.server:app --host 0.0.0.0 --port 8000 --reload
```

### Sarvam provider

```bash
export STT_PROVIDER=sarvam
export SARVAM_API_KEY=your_key_here
uvicorn signaling.server:app --host 0.0.0.0 --port 8000 --reload
```

## Embedding the Framework

```python
from rtc_room_framework import (
    RTCRoomFramework,
    create_app,
    get_manager,
    get_room_control,
    get_stt_service,
    register_shutdown_hook,
    register_startup_hook,
    reload_config,
)

app = create_app()          # FastAPI application
framework = RTCRoomFramework()
framework.manager            # RoomManager
framework.room_control       # RoomControlModule
framework.stt_service        # STTService
```

Lifecycle hooks run inside the app lifespan (no deprecated `on_event` handlers):

```python
async def on_start() -> None:
    ...

async def on_stop() -> None:
    ...

register_startup_hook(on_start)
register_shutdown_hook(on_stop)
```

`reload_config()` re-reads the environment-driven settings on demand.

## Test Commands

Run full test suite:

```bash
python3 -m unittest discover -s tests -v
```

Run specific test files:

```bash
python3 -m unittest tests.test_cli -v
python3 -m unittest tests.test_signaling_server -v
python3 -m unittest tests.test_sdk_client -v
```

## Command Reference (Quick Copy)

```bash
# Setup
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -e .

# HTTP dev server
uvicorn signaling.server:app --host 0.0.0.0 --port 8000 --reload

# CLI server (requires `pip install -e .`)
rtc-room-server --host 0.0.0.0 --port 8000 --reload

# Module server
python3 -m rtc_room_framework --host 0.0.0.0 --port 8000 --reload

# HTTPS server
./scripts/run_https_dev.sh

# Server + agent
python3 scripts/run_server_with_agent.py --room-id demo-room --create-room

# Tests
python3 -m unittest discover -s tests -v
```
