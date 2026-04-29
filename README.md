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
    - `room_destroyed`
  - relayed `webrtc_offer` / `webrtc_answer` / `ice_candidate`
  - `error`
