# Telephony Adapter (Asterisk AudioSocket)

This module is a self-contained telephony source adapter that connects VoiceShield AI to a live PBX (Asterisk or FreeSWITCH). 
It bridges live call audio into the existing Audio Ingestion Service without requiring modifications to the core fraud-detection pipelines.

## Architecture
It uses a **named pipe (FIFO)** strategy:
1. The adapter listens on a TCP port for Asterisk AudioSocket connections.
2. When a call connects, it creates a local FIFO inside `demo_audio/`.
3. It calls the existing Gateway `POST /api/v1/stream/start` endpoint, passing the FIFO name as the "filename".
4. The adapter writes raw PCM AudioSocket packets into the FIFO.
5. The Gateway and Sidecar poll the FIFO using standard stream processing.

This completely bypasses the need to persist raw audio to disk, satisfying privacy invariants.

## Testing with Local Asterisk
You can point a self-hosted Asterisk PBX to this adapter.
By default, the adapter listens on `0.0.0.0:8080`.

### Asterisk `extensions.conf` Example
To fork an incoming call to the adapter using AudioSocket, add this to your dialplan:
```ini
exten => 1000,1,Answer()
exten => 1000,n,Dial(AudioSocket/127.0.0.1:8080/${UNIQUEID})
exten => 1000,n,Hangup()
```

### Free SIP Trunk for Dev Testing
You do not need a paid production trunk to test this. You can use free developer sandbox trunks like:
- **Twilio Elastic SIP Trunking Sandbox**
- **Exotel Sandbox**

Register Asterisk with the sandbox SIP credentials and route incoming calls to the `1000` extension above.

## Running the Adapter
From the `backend` directory, simply run:
```bash
python -m telephony_adapter.server
```
