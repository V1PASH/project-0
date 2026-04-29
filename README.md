# WebRTC Room Signaling Demo

This repo now includes:
- room/participant manager with join/leave events
- FastAPI websocket signaling server
- browser test page for local two-tab voice chat

## 1) Setup

```bash
source /Users/arshpreet/project-0/venv/bin/activate
pip install -r requirements.txt
```

## 2) Run tests

```bash
python -m unittest discover -s tests -v
```

## 3) Start signaling server

```bash
uvicorn signaling.server:app --host 0.0.0.0 --port 8000 --reload
```

## 4) Browser test

1. Open `http://localhost:8000` in two tabs (not `0.0.0.0` / LAN IP).
2. Click `Connect` in both tabs.
3. Enter different names but same room id.
4. Click `Join Room` in both tabs.
5. Allow microphone permission.
6. Speak in one tab and listen in the other.

## If mic still says "not in secure context"

Use HTTPS/WSS local mode:

```bash
./scripts/run_https_dev.sh
```

Then open `https://localhost:8443` in two tabs and use default websocket URL (`wss://localhost:8443/ws`).
Your browser will show a certificate warning once for the self-signed cert; proceed for local testing.

## Websocket message types

- Client -> Server:
  - `join_room`
  - `leave_room`
  - `participant_activity` (`speaking_started` / `speaking_stopped`)
  - `stt_transcript` (`text`, `is_final`, `source`)
  - `stt_audio_chunk` (`audio_base64`, `mime_type`, `language_code`) for provider-backed STT
  - `webrtc_offer`
  - `webrtc_answer`
  - `ice_candidate`
- Server -> Client:
  - `connected`
  - `joined_room`
  - `left_room`
  - `room_event`
    - `participant_joined`
    - `participant_left`
    - `participant_speaking_started`
    - `participant_speaking_stopped`
    - `participant_transcript`
    - `room_destroyed`
  - relayed `webrtc_offer` / `webrtc_answer` / `ice_candidate`
  - `error`

## STT notes

- Real-time STT in this demo uses browser speech recognition (`SpeechRecognition` / `webkitSpeechRecognition`) on the client.
- Transcript updates are sent to the signaling server and broadcast to the room as `participant_transcript` events.
- Browser STT support is limited and may not work on all browsers.

## STT provider setup (Sarvam, Deepgram, OpenAI)

The server now supports provider-based STT with a pluggable provider architecture.

1. Choose provider with `STT_PROVIDER`:
   - `sarvam`
   - `deepgram`
   - `openai`
2. Set provider credentials:
   - Sarvam:
     - `SARVAM_API_KEY=<your_api_key>` (or `SARVAM_API_SUBSCRIPTION_KEY`)
   - Deepgram:
     - `DEEPGRAM_API_KEY=<your_api_key>`
   - OpenAI:
     - `OPENAI_API_KEY=<your_api_key>`
3. Optional provider settings:
   - Common:
     - `STT_LANGUAGE_CODE=unknown`
   - Sarvam:
     - `SARVAM_STT_MODEL=saaras:v3`
     - `SARVAM_STT_MODE=transcribe`
     - `SARVAM_STT_TIMEOUT_SECONDS=20`
   - Deepgram:
     - `DEEPGRAM_STT_MODEL=nova-3`
     - `DEEPGRAM_STT_SMART_FORMAT=true`
     - `DEEPGRAM_STT_TIMEOUT_SECONDS=20`
   - OpenAI:
     - `OPENAI_STT_MODEL=gpt-4o-mini-transcribe`
     - `OPENAI_STT_TIMEOUT_SECONDS=20`
4. Start server as usual.
5. Client auto-switches to provider audio-chunk transport on connect when server reports provider mode.
