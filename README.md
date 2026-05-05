# WebRTC Room Signaling Demo

This project is a local-first WebRTC voice-room demo with:

- FastAPI signaling server
- in-memory room/participant manager
- browser test client for two-tab voice chat
- speaking activity events
- real-time transcript events (browser STT or provider-backed STT)

It is designed to make signaling and media-event flow explicit and easy to inspect.

## Install As Framework

From source:

```bash
pip install .
```

Editable during development:

```bash
pip install -e .
```

After install, users can:

- run CLI server: `rtc-room-server --host 0.0.0.0 --port 8000`
- run as module: `python -m rtc_room_framework --host 0.0.0.0 --port 8000`
- import framework API:

```python
from rtc_room_framework import RTCRoomFramework, create_app

app = create_app()  # FastAPI app
framework = RTCRoomFramework()
manager = framework.manager
stt_service = framework.stt_service
room_control = framework.room_control
```

Admin/control usage:

```python
from rtc_room_framework import get_room_control

room_control = get_room_control()
room_control.create_room("ops-room", max_participants=8)
room_control.update_room_capacity("ops-room", 12)
room_control.close_room("ops-room")
```

### Python SDK (No Browser)

```python
import asyncio

from rtc_room_framework import RTCRoomClient


async def main() -> None:
    creator = RTCRoomClient("ws://localhost:8000/ws", name="Creator")
    joiner = RTCRoomClient("ws://localhost:8000/ws", name="Joiner")

    await creator.connect()
    await joiner.connect()

    await creator.create_room("demo-room")
    await joiner.join_room("demo-room")

    # Raw audio bytes; receiver gets `room_audio` payload.
    await creator.publish_audio(b"\x00\x01\x02\x03", mime_type="audio/pcm", sequence=1)
    message = await joiner.recv()
    print(message["type"], message["from_participant_id"], message["audio_bytes"])

    await creator.close()
    await joiner.close()


asyncio.run(main())
```

### Agentic Participant Layer

You can add a server-side agent into any room as a participant:

```python
import asyncio

from rtc_room_framework import EchoTranscriptResponder, get_manager, get_room_control
from rtc_room_framework.agent import AgentSession


async def main() -> None:
    manager = get_manager()
    room_control = get_room_control()

    agent = AgentSession(
        manager,
        room_control=room_control,
        name="Support Agent",
        responder=EchoTranscriptResponder(prefix="Support Agent: "),
    )

    await agent.start("demo-room", create_if_missing=True)
    # Agent now appears in the room as role "agent" and responds to final transcript events.

    await asyncio.sleep(30)
    await agent.stop()


asyncio.run(main())
```

Run server + echo agent in one process:

```bash
python scripts/run_server_with_agent.py --room-id demo-room --create-room
```

The agent session must run in the same process as the signaling server because room state is in-memory.

## Directory Structure

```text
project-0/
├─ rtc_room_framework/
│  ├─ __init__.py
│  ├─ agent.py
│  ├─ app.py
│  ├─ cli.py
│  └─ web/
│     └─ index.html
├─ participants/
│  ├─ __init__.py
│  ├─ base.py
│  └─ participant.py
├─ room/
│  ├─ __init__.py
│  ├─ manager.py
│  └─ model.py
├─ signaling/
│  └─ server.py
├─ services/
│  ├─ stt_service.py
│  └─ stt_providers.py
├─ web/
│  └─ index.html
├─ tests/
├─ scripts/
│  ├─ run_server_with_agent.py
│  └─ run_https_dev.sh
└─ pyproject.toml
```

## What Runs Where

| Layer | File(s) | Core responsibility |
| --- | --- | --- |
| HTTP + WebSocket server | `signaling/server.py` | Serves UI, handles `/ws`, validates protocol messages, relays SDP/ICE, routes STT payloads |
| Room state + events | `room/manager.py`, `room/model.py` | Tracks participants and rooms, enforces room capacity, emits room events, broadcasts participant events |
| Room control module | `room/control.py` | Centralized room creation, join policy, capacity updates, room close, participant removal |
| Participant model | `participants/*.py` | Participant identity, state (`connecting`/`in_room`/`disconnected`), speaking flag |
| STT normalization | `services/stt_service.py` | Cleans, clamps, de-duplicates transcript updates and optionally calls provider |
| STT providers | `services/stt_providers.py` | Provider-specific transcription adapters (Sarvam, Deepgram, OpenAI) |
| Agentic participant layer | `rtc_room_framework/agent.py` | Register and run server-side agents as room participants with event-driven transcript replies |
| Installable framework API | `rtc_room_framework/app.py`, `rtc_room_framework/cli.py` | Public API + CLI entry point for installed usage |
| Browser client | `web/index.html` | WebSocket client, WebRTC peer logic, mic capture, speaking detection, STT transport |
| HTTPS dev entrypoint | `scripts/run_https_dev.sh` | Self-signed cert generation + `uvicorn` with TLS on `https://localhost:8443` |

## Endpoints

- `GET /`: serves `web/index.html` with no-cache headers
- `GET /health`: returns `{status, stats}` where stats come from `RoomManager.stats()`
- `WS /ws`: signaling and room protocol endpoint

## Core Runtime Logic

### 1) Server boot and STT mode selection

On import, `signaling/server.py` calls `_build_stt_service()`:

- reads `STT_PROVIDER` and provider env vars via `build_stt_provider_from_env()`
- if provider init fails, logs warning and falls back to browser STT mode
- sets `DEFAULT_STT_LANGUAGE_CODE` from `STT_LANGUAGE_CODE` (default `unknown`)

When a socket connects, server immediately sends:

- `type: connected`
- `participant_id`
- `stt` capability object:
  - `provider`
  - `transport`: `browser_text` or `audio_chunk`
  - `default_language_code`

That handshake is how the browser decides whether to use browser speech recognition or provider chunk upload.

### 2) Room lifecycle and participant state

Room lifecycle is managed by `RoomManager`:

- register socket -> create `LocalParticipant` in `CONNECTING` state
- `join_room`:
  - validate `room_id` and `max_participants`
  - create room if needed
  - enforce capacity in `Room.add_participant()`
  - participant state becomes `IN_ROOM`
  - emit `participant_joined`
- `leave_room` or disconnect:
  - remove participant from room
  - emit `participant_left`
  - if room becomes empty, destroy room and emit `room_destroyed` internally
  - participant state goes back to `CONNECTING` on leave, `DISCONNECTED` on socket drop

`RoomManager` is the source of truth for:

- room map
- participant map
- websocket-to-participant index
- bounded event history buffer (default size `200`)

Room creation policy:

- default: `join_room` may auto-create missing room names
- strict mode: set `REQUIRE_EXPLICIT_ROOM_CREATE=true` to require `create_room` before joins

### 3) Room events and broadcasting behavior

Every event goes through `_emit_event()`:

- append to in-memory event history
- fan out to optional event listeners
- optionally broadcast to room peers

Default peer broadcast only applies to:

- `participant_joined`
- `participant_left`
- `participant_speaking_started`
- `participant_speaking_stopped`
- `participant_transcript`

Important nuance:

- `room_created` and `room_destroyed` are emitted in manager history/listeners but are not part of default room peer broadcast logic.
- Room deletion is still surfaced to the leaver through `left_room.room_deleted`.

### 4) WebRTC signaling relay

Client does full P2P media exchange. Server only relays signaling:

- `webrtc_offer`
- `webrtc_answer`
- `ice_candidate`

Before relay, server validates:

- sender is in room
- `target_participant_id` provided
- target exists and is connected
- target is in same room

Relay envelope includes:

- `type`
- `from_participant_id`
- `room_id`
- optional `sdp`/`candidate`

### 5) Speaking-start/stop logic

Speaking detection happens on client using `AudioContext + AnalyserNode`:

- computes RMS from time-domain samples each animation frame
- starts speaking when RMS >= `0.02`
- while speaking, treats RMS >= `0.012` as continued voice
- stops after `900ms` below stop threshold

Client sends `participant_activity` with:

- `speaking_started`
- `speaking_stopped`

Server-side dedupe:

- `RoomManager.update_participant_speaking()` emits only on actual state transitions.

### 6) STT pipeline

#### Browser text mode (`transport = browser_text`)

Client uses `SpeechRecognition`/`webkitSpeechRecognition`:

- sends `stt_transcript` with `text`, `is_final`, `source`
- interim + final both supported

Server uses `STTService.normalize_update()`:

- requires non-empty `participant_id` and string `text`
- trims/normalizes whitespace
- clamps to max chars (default `400`)
- normalizes `source` (fallback `browser_web_speech`)
- de-duplicates identical consecutive `(text, is_final)` per participant
- clears cached interim state when final arrives

If accepted, server emits `participant_transcript` room event.

#### Provider chunk mode (`transport = audio_chunk`)

Client records mic chunks with `MediaRecorder`:

- chunk interval: `2500ms`
- encodes chunk bytes to Base64
- sends `stt_audio_chunk` with `audio_base64`, `mime_type`, `language_code`

Server path:

- validates participant is in room
- validates provider STT is enabled
- validates and Base64-decodes payload
- enforces max chunk size `5MB`
- calls `STTService.transcribe_audio_chunk()`
- emits `participant_transcript` on non-empty provider result

`STTService.transcribe_audio_chunk()`:

- no-op if provider is disabled
- wraps provider errors as `STTServiceError`
- routes provider transcript through same normalization + dedupe path

## STT Provider Core Logic

All providers implement `BaseSTTProvider.transcribe_chunk(...)` and return:

- `ProviderTranscript(text, is_final, source)` or `None`

### Sarvam

- endpoint: `POST https://api.sarvam.ai/speech-to-text`
- auth header: `api-subscription-key`
- multipart fields: `model`, `mode`, `language_code`, `file`
- transcript extraction: top-level `transcript`

### Deepgram

- endpoint: `POST https://api.deepgram.com/v1/listen`
- auth header: `Authorization: Token <key>`
- body: raw audio bytes (`Content-Type` from chunk mime)
- query params:
  - `model`
  - `smart_format=true|false`
  - `language=<code>` when language known, else `detect_language=true`
- transcript extraction: `results.channels[0].alternatives[0].transcript`

### OpenAI

- endpoint: `POST https://api.openai.com/v1/audio/transcriptions`
- auth header: `Authorization: Bearer <key>`
- multipart fields: `model`, optional `language`, `file`
- language normalization: converts BCP-47 input to ISO-639-1 primary tag when possible (`en-IN` -> `en`), omits when unknown
- transcript extraction: top-level `text`

## WebSocket Protocol

### Client -> Server

| Type | Required fields | Notes |
| --- | --- | --- |
| `create_room` | `room_id` | Creates and joins room; if create-room auth enabled also requires `api_key`, `api_token`, `api_ts` |
| `join_room` | `room_id` | optional `name`, `max_participants` |
| `leave_room` | none | leaves current room |
| `participant_activity` | `activity` | `speaking_started` or `speaking_stopped` |
| `publish_audio` | `audio_base64` | optional `mime_type`, `sequence`; broadcasts raw audio chunk to room peers |
| `stt_transcript` | `text` | optional `is_final`, `source`; browser STT mode |
| `stt_audio_chunk` | `audio_base64` | optional `mime_type`, `language_code`; provider mode |
| `webrtc_offer` | `target_participant_id`, `sdp` | relayed by server |
| `webrtc_answer` | `target_participant_id`, `sdp` | relayed by server |
| `ice_candidate` | `target_participant_id`, `candidate` | relayed by server |

### Server -> Client

| Type | Core fields | Notes |
| --- | --- | --- |
| `connected` | `participant_id`, `stt` | initial handshake |
| `joined_room` | `room`, `self`, `participants`, `room_created` | join/create success |
| `left_room` | `room_id`, `room_exists`, `room_deleted` | leave result |
| `audio_published` | `room_id`, `audio_bytes`, `sequence` | sender ack for `publish_audio` |
| `room_audio` | `room_id`, `from_participant_id`, `audio_base64`, `mime_type` | raw audio chunk broadcast to peers |
| `room_event` | `event` | join/leave/speaking/transcript broadcast events |
| `webrtc_offer` | `from_participant_id`, `sdp` | relayed |
| `webrtc_answer` | `from_participant_id`, `sdp` | relayed |
| `ice_candidate` | `from_participant_id`, `candidate` | relayed |
| `error` | `code`, `message` | validation/runtime errors |

## Server Error Codes

| Code | Meaning |
| --- | --- |
| `invalid_payload` | Non-object JSON payload |
| `missing_type` | Message type missing |
| `unsupported_type` | Unknown message type |
| `invalid_room_id` | Missing/invalid room id |
| `room_already_exists` | `create_room` called for existing room |
| `invalid_create_room_auth` | Missing/invalid `create_room` auth fields |
| `unauthorized_create_room` | Invalid `create_room` api key/token |
| `expired_create_room_token` | `create_room` token timestamp outside allowed TTL |
| `create_room_auth_not_configured` | Auth required but server api key/secret not configured |
| `room_not_found` | `join_room` on missing room while explicit-create mode is enabled |
| `invalid_max_participants` | Non-positive/non-int `max_participants` |
| `room_full` | Join rejected because room reached capacity |
| `not_in_room` | Action requires joined room |
| `invalid_activity` | Unknown speaking activity |
| `invalid_audio_payload` | Missing/invalid raw audio payload |
| `audio_chunk_too_large` | Raw room audio chunk > 1MB |
| `invalid_stt_payload` | Invalid browser STT payload |
| `stt_transport_not_available` | Audio-chunk STT sent while provider mode disabled |
| `invalid_stt_audio_chunk` | Missing/invalid Base64 chunk payload |
| `stt_audio_chunk_too_large` | Audio chunk > 5MB |
| `stt_provider_error` | Provider transcription failure |
| `invalid_target` | Missing relay target id |
| `target_not_found` | Target peer not connected |
| `target_not_in_room` | Target peer not in sender room |

## Local Run

### 1) Setup

```bash
source /Users/arshpreet/project-0/venv/bin/activate
pip install -r requirements.txt
```

### 2) Run tests

```bash
python -m unittest discover -s tests -v
```

### 3) Start server (HTTP mode)

```bash
uvicorn signaling.server:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` in two tabs.

### 4) Start server (HTTPS mode, recommended for mic/stt)

```bash
./scripts/run_https_dev.sh
```

Open `https://localhost:8443` in two tabs.
The script auto-generates a localhost self-signed cert in `certs/` if missing.

## Provider Configuration

### Provider selection

- `STT_PROVIDER=sarvam|deepgram|openai`
- unset or `browser|none|off` keeps browser STT mode

### Common

- `STT_LANGUAGE_CODE` (default: `unknown`)
- `ROOM_CREATE_API_KEY` (optional, enables create-room auth when paired with secret)
- `ROOM_CREATE_API_SECRET` (optional, secret used for HMAC token validation)
- `ROOM_CREATE_TOKEN_TTL_SECONDS` (default `300`)
- `REQUIRE_CREATE_ROOM_AUTH` (`true|false`; defaults to `true` when key+secret are both set)
- `REQUIRE_EXPLICIT_ROOM_CREATE` (`true|false`; requires room to be created before joins)

### Create Room Authentication

When enabled, `create_room` must include:

- `api_key`: configured API key
- `api_ts`: unix timestamp (seconds)
- `api_token`: hex HMAC-SHA256 over `"{api_key}:{room_id}:{api_ts}"` using `ROOM_CREATE_API_SECRET`

Using SDK:

```python
from rtc_room_framework import RTCRoomClient

client = RTCRoomClient(
    "ws://localhost:8000/ws",
    create_room_api_key="your-key",
    create_room_api_secret="your-secret",
)
await client.connect()
await client.create_room("secure-room")
```

### Sarvam

- `SARVAM_API_KEY` or `SARVAM_API_SUBSCRIPTION_KEY` (required)
- `SARVAM_STT_MODEL` (default `saaras:v3`)
- `SARVAM_STT_MODE` (default `transcribe`)
- `SARVAM_STT_TIMEOUT_SECONDS` (default `20`)

### Deepgram

- `DEEPGRAM_API_KEY` (required)
- `DEEPGRAM_STT_MODEL` (default `nova-3`)
- `DEEPGRAM_STT_SMART_FORMAT` (default `true`)
- `DEEPGRAM_STT_TIMEOUT_SECONDS` (default `20`)

### OpenAI

- `OPENAI_API_KEY` (required)
- `OPENAI_STT_MODEL` (default `gpt-4o-mini-transcribe`)
- `OPENAI_STT_TIMEOUT_SECONDS` (default `20`)

### Example launch commands

Sarvam:

```bash
STT_PROVIDER=sarvam SARVAM_API_KEY=YOUR_KEY STT_LANGUAGE_CODE=unknown ./scripts/run_https_dev.sh
```

Deepgram:

```bash
STT_PROVIDER=deepgram DEEPGRAM_API_KEY=YOUR_KEY STT_LANGUAGE_CODE=unknown ./scripts/run_https_dev.sh
```

OpenAI:

```bash
STT_PROVIDER=openai OPENAI_API_KEY=YOUR_KEY STT_LANGUAGE_CODE=unknown ./scripts/run_https_dev.sh
```

## Browser Client Runtime Notes

- If host is `0.0.0.0` or `::`, client auto-redirects to `localhost` for secure-origin compatibility.
- Mic acquisition uses modern `navigator.mediaDevices.getUserMedia`, with legacy fallback.
- If not secure context, user gets explicit error hint to open `https://localhost:8443`.
- In provider mode, `MediaRecorder` must support a valid audio mime type (`webm/ogg` variants).

## Testing Scope

Current tests verify:

- room join/leave lifecycle
- speaking event propagation
- transcript event propagation
- STT dedupe behavior
- audio chunk validation and provider routing
- provider factory behavior and provider request/response parsing

Run:

```bash
python -m unittest discover -s tests -v
```

## Known Limitations

- No auth/permissions model on signaling endpoint.
- In-memory state only; no persistence/shared state across processes.
- STUN only (`stun:stun.l.google.com:19302`), no TURN fallback.
- `room_created`/`room_destroyed` are internal manager events; only participant events are broadcast by default.
- Admin operations from `RoomControlModule` are process-local Python calls (no separate authenticated admin HTTP/WS API yet).
