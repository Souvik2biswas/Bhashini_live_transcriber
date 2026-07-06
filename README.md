---
title: Bhashini Live Transcriber
emoji: 🎙️
colorFrom: indigo
colorTo: green
sdk: gradio
sdk_version: 4.21.0
python_version: "3.10"
app_file: app.py
pinned: false
---

# Bhashini Live Transcriber & Translator

A premium, real-time speech transcription and translation web application built using **Django Channels (WebSockets)**, powered by the **Bhashini (ULCA / Dhruva) API** (MeitY, Govt. of India) to translate Dravidian languages (Tamil, Telugu, Kannada, and Malayalam) into English.

## 🚀 Key Features
* **Real-time Gapless Streaming**: Uses browser-side Web Audio API (`ScriptProcessorNode`) to stream continuous, raw PCM audio directly over WebSockets, avoiding standard `MediaRecorder` starting/stopping gaps.
* **Low Latency (<1.5s)**: Optimised server-side WAV packaging. Packaged in-memory in pure Python using `struct` (no external subprocesses or `ffmpeg` required on the hot path).
* **Calibrated Silence Gate**: Automatic background noise filtering (calibrated to ignore ambient laptop mic noise under 800 RMS) to prevent blank inputs from crashing the ASR engine.
* **Robust Transient Error Handling**: Transparent API retry logic (up to 3 attempts with backoff) to recover from temporary Bhashini service hiccups.
* **Premium Glassmorphic UI**: Real-time audio level meter, pulsing recording indicators, responsive slide-in cards, and support for dark mode.
* **Dual Translation Modes**: High-fidelity live audio transcription/translation alongside instant text-input translation.

---

## 🛠️ Architecture

```
🎤 Browser Mic (Web Audio API)
     │
     ▼ (Continuous 16kHz PCM streaming)
WebSocket Frame
     │
     ▼
Django Channels (TranscriptionConsumer)
     │
     ▼ (In-Memory 44-Byte WAV Header Attachment)
Bhashini Chained API (ASR + NMT)
     │
     ▼ (Dynamic callback response)
WebSocket Push
     │
     ▼
Browser UI (Animated Cards)
```

---

## 💻 Local Setup & Installation

### 1. Prerequisites
* **Python 3.10+**
* **ffmpeg** (Optional: only needed if testing legacy WebM/Opus fallbacks)

### 2. Clone and Install
```bash
git clone https://github.com/Souvik2biswas/Bhashini_live_transcriber.git
cd Bhashini_live_transcriber
pip install -r requirements.txt
```

### 3. Configure API Credentials
Create a `.env` file in the root directory:
```env
# Bhashini Credentials (Get yours from https://bhashini.gov.in/ulca)
BHASHINI_USER_ID=your_user_id
BHASHINI_ULCA_KEY=your_ulca_key
BHASHINI_INFERENCE_KEY=your_inference_key

# Django Settings
DJANGO_SECRET_KEY=your-production-secret-key
DJANGO_DEBUG=True
```

### 4. Run the Development Server
```bash
python manage.py runserver 8000
```
Open **`http://127.0.0.1:8000`** in your browser, select a language, and start speaking!

---

## 🌐 Production Deployment (Render)

This project requires an **ASGI server (Daphne)** to support long-running WebSocket connections. **Render** is the recommended zero-config PaaS platform.

1. Connect your repository to **Render** and create a new **Web Service**.
2. Configure settings:
   * **Runtime**: `Python`
   * **Build Command**: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
   * **Start Command**: `daphne -b 0.0.0.0 -p $PORT bhashini_web.asgi:application`
3. Add Environment Variables:
   * `BHASHINI_USER_ID`
   * `BHASHINI_ULCA_KEY`
   * `BHASHINI_INFERENCE_KEY`
   * `DJANGO_DEBUG` = `False`

---

## 📂 Project Structure
```
bhashini_web/
├── bhashini_web/          # Settings, routing, and ASGI config
│   ├── asgi.py            # Protocol routers (HTTP & WebSockets)
│   └── settings.py
├── transcriber/           # Application Package
│   ├── bhashini_api.py    # Thread-safe API caching & retries
│   ├── audio_utils.py     # Pure-Python WAV header construction & RMS
│   ├── consumers.py       # WebSocket connection & binary frame accumulator
│   ├── static/            # Stylesheets and frontend application JS
│   └── templates/         # Main HTML layout
├── requirements.txt
└── manage.py
```