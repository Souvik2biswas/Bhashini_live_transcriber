# Bhashini Live Transcriber & Translator

A high-performance, real-time speech transcription and machine translation web application built using **Django Channels (WebSockets)** and browser **Web Audio API (`AudioWorkletProcessor`)**, powered by the **Bhashini (ULCA / Dhruva) API** (MeitY, Govt. of India) to transcribe and translate **22+ Scheduled Indian Languages** into English.

---

## 🚀 Key Features

* **🌐 Pan-Indian Multilingual Support (22+ Languages)**: Supports ASR and NMT across 22+ Scheduled Indian languages (Hindi, Tamil, Telugu, Bengali, Marathi, Gujarati, Kannada, Malayalam, Odia, Punjabi, Sanskrit, Urdu, Assamese, Manipuri, Bodo, Dogri, Kashmiri, Konkani, Maithili, Nepali, Santali, Sindhi) covering 4 major language families (*Indo-Aryan, Dravidian, Sino-Tibetan, Austroasiatic*).
* **🎙️ Off-Thread Real-Time Audio Resampling**: Uses a custom browser-side `AudioWorkletProcessor` running on a dedicated audio render thread to capture, downsample (to 16kHz mono), and convert Float32 audio to 16-bit PCM in real time without blocking the UI main thread.
* **🔊 Dual Audio Capture Modes**: Seamlessly switch between **Microphone** input and **System Audio** (Screen/Tab/Window audio sharing).
* **⚡ Ultra-Low Latency (<1.5s)**: In-memory pure Python 44-byte WAV header attachment using `struct` and `numpy`. Operates entirely on the hot path with zero disk I/O and zero external `ffmpeg` subprocess overhead.
* **🔇 Calibrated RMS Silence Gate**: Calculates RMS energy directly from raw signed 16-bit PCM. Automatically filters out background ambient noise (<800 RMS) to eliminate empty ASR API triggers.
* **🛡️ Thread-Safe Caching & Resilience**: Features a thread-safe (`threading.Lock`) 1-hour TTL pipeline configuration cache, HTTP connection pooling (`requests.Session`), automatic 401/403 auth key expiration refresh, and exponential backoff retry mechanisms.
* **📥 Multi-Format Export Options**: Easily export live transcriptions as **Plain Text (TXT)**, **Subtitles (SRT with timestamps)**, or copy directly to clipboard.
* **🌳 Interactive Language Family Visualizations**: Explore language relationships through interactive SVG language trees/graphs categorized by linguistic branches, alongside tabular grid search and family filter pills.
* **✨ Glassmorphic UI & Dark Mode**: Real-time audio level meters, dynamic status indicators, dark/light theme toggle, auto-scroll controls, and an interactive onboarding guide.

---

## 🛠️ Architecture

```text
🎤 Browser Audio (Mic / System)
     │
     ▼ (Dedicated Render Thread: AudioWorkletProcessor - Float32 → 16kHz Int16 PCM)
ArrayBuffer (Zero-copy transfer to main thread)
     │
     ▼ (Continuous WebSocket binary PCM streaming)
Django Channels (TranscriptionConsumer / Daphne ASGI)
     │
     ├──► NumPy RMS Silence Gate (<800 RMS dropped)
     │
     ▼ (Pure-Python In-Memory WAV Packaging via `struct`)
Bhashini Chained API (ASR + NMT)
     │
     ▼ (JSON callback push)
WebSocket Frame
     │
     ▼
Browser UI (Real-time Cards, Level Meter, SRT Exporter)
```

---

## 🔤 Supported Languages (22+)

| Language Family | Languages Supported |
| :--- | :--- |
| **Indo-Aryan** | Hindi (`hi`), Bengali (`bn`), Marathi (`mr`), Gujarati (`gu`), Punjabi (`pa`), Odia (`or`), Sanskrit (`sa`), Urdu (`ur`), Assamese (`as`), Dogri (`doi`), Kashmiri (`ks`), Konkani (`gom`), Maithili (`mai`), Nepali (`ne`), Sindhi (`sd`) |
| **Dravidian** | Tamil (`ta`), Telugu (`te`), Kannada (`kn`), Malayalam (`ml`) |
| **Sino-Tibetan** | Bodo (`brx`), Manipuri (`mni`) |
| **Austroasiatic** | Santali (`sat`) |
| **Global Target** | English (`en`) |

---

## 💻 Local Setup & Installation

### 1. Prerequisites
* **Python 3.10+**

### 2. Clone and Install
```bash
git clone https://github.com/Souvik2biswas/Bhashini_live_transcriber.git
cd Bhashini_live_transcriber/bhashini_web
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file in `bhashini_web/`:
```env
# Bhashini Credentials (Get yours from https://bhashini.gov.in/ulca)
BHASHINI_USER_ID=your_user_id
BHASHINI_ULCA_KEY=your_ulca_key
BHASHINI_INFERENCE_KEY=your_inference_key

# Django Settings
DJANGO_SECRET_KEY=your-production-secret-key
DJANGO_DEBUG=True
```

### 4. Run Development Server
```bash
python manage.py runserver 8000
```
Open **`http://127.0.0.1:8000`** in your browser, pick your language, and start transcribing live!

---

## 🌐 Production Deployment (Render)

This project uses **Daphne (ASGI)** to handle full-duplex WebSocket connections.

1. Connect your repository to **Render** and create a new **Web Service**.
2. Settings:
   * **Runtime**: `Python`
   * **Build Command**: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
   * **Start Command**: `daphne -b 0.0.0.0 -p $PORT bhashini_web.asgi:application`
3. Environment Variables:
   * Add `BHASHINI_USER_ID`, `BHASHINI_ULCA_KEY`, `BHASHINI_INFERENCE_KEY`, `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY`.

---

## 📂 Project Structure

```text
bhashini_web/
├── bhashini_web/          # Core Django project settings & ASGI configuration
│   ├── asgi.py            # Protocol routers (HTTP & WebSockets)
│   └── settings.py
├── transcriber/           # Application Package
│   ├── bhashini_api.py    # Thread-safe API client, TTL caching & retries
│   ├── audio_utils.py     # Pure-Python WAV header construction & NumPy RMS
│   ├── consumers.py       # WebSocket consumer & binary PCM chunk processor
│   ├── static/transcriber/
│   │   ├── js/
│   │   │   ├── audio_processor.js  # AudioWorkletProcessor (off-thread resampling)
│   │   │   └── app_v3.js           # Main client UI & WebSocket controller
│   │   └── css/style.css           # Glassmorphic responsive styling
│   └── templates/transcriber/
│       └── index.html              # Modern dashboard template
├── requirements.txt
└── manage.py
```