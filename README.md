# Interview Website (Guest Login + VoiceRSS + SadTalker)

This project implements:

User Interface (Website) -> Backend API (Node.js) -> SadTalker -> Generated Talking Video

## What it does

1. Shows a **Guest Login** entry point.
2. Opens an **Interview section** with a static interviewer image.
3. Accepts user text input.
4. Converts text to speech using **VoiceRSS API**.
5. Sends generated audio + interviewer image to **SadTalker**.
6. Displays generated talking video directly in the same Interview section.

## Tech stack

- Frontend: HTML/CSS/JS in `public/`
- Backend: Node.js + Express (`server.js`)
- TTS: VoiceRSS API
- Talking head: SadTalker (Python model)

## Setup

### 1) Install Node dependencies

```bash
npm install
```

### 2) Configure environment

Copy `.env.example` to `.env` and edit if needed.

Important values:

- `VOICERSS_API_KEY`
- `SADTALKER_DIR`: path to your SadTalker repo clone
- `PYTHON_EXECUTABLE`: python command for the SadTalker environment
- `INTERVIEWER_IMAGE`: source image path (defaults to `./public/interviewer.svg`)

### 3) Prepare SadTalker

Clone and set up SadTalker separately, then point `SADTALKER_DIR` to that directory.

Example requirements:

- `inference.py` exists inside `SADTALKER_DIR`
- all SadTalker model checkpoints are downloaded
- Python environment includes SadTalker dependencies

### 4) Run app

```bash
npm start
```

Open: `http://localhost:3000`

## API

### `POST /api/interview/generate`

Request body:

```json
{
  "text": "Tell me about yourself"
}
```

Response:

```json
{
  "ok": true,
  "videoUrl": "/generated/<id>.mp4"
}
```

## Notes

- If SadTalker path or environment is invalid, backend returns a detailed error.
- Temporary audio/intermediate files are cleaned automatically.
- Generated videos are saved in `public/generated/` for browser playback.
