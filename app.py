import os
import io
import time
import base64
import struct
import math
import requests
import numpy as np
import gradio as gr
from dotenv import load_dotenv

# Load local environment variables (if any)
load_dotenv()

# ─── Configuration ──────────────────────────────────────────────────────────
BHASHINI_USER_ID = os.getenv("BHASHINI_USER_ID")
BHASHINI_ULCA_KEY = os.getenv("BHASHINI_ULCA_KEY")
BHASHINI_INFERENCE_KEY = os.getenv("BHASHINI_INFERENCE_KEY")
CONFIG_URL = "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline"
PIPELINE_ID = "64392f96daac500b55c543cd"

SAMPLE_RATE = 16000
SILENCE_RMS_THRESHOLD = 300

LANGUAGES = {
    "ta": "Tamil (தமிழ்)",
    "te": "Telugu (తెలుగు)",
    "kn": "Kannada (ಕನ್ನಡ)",
    "ml": "Malayalam (മലയാളം)"
}

# Thread-safe config cache
_config_cache = {}

# ─── Bhashini API Layer ──────────────────────────────────────────────────────

def get_pipeline_config(source_lang: str) -> dict:
    """Fetch pipeline configurations for ASR and NMT."""
    if source_lang in _config_cache:
        return _config_cache[source_lang]

    if not all([BHASHINI_USER_ID, BHASHINI_ULCA_KEY, BHASHINI_INFERENCE_KEY]):
        raise ValueError("Missing Bhashini API credentials in environment variables.")

    headers = {
        "userID": BHASHINI_USER_ID,
        "ulcaApiKey": BHASHINI_ULCA_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "pipelineTasks": [
            {
                "taskType": "asr",
                "config": {
                    "language": {"sourceLanguage": source_lang}
                }
            },
            {
                "taskType": "translation",
                "config": {
                    "language": {
                        "sourceLanguage": source_lang,
                        "targetLanguage": "en"
                    }
                }
            }
        ],
        "pipelineRequestConfig": {
            "pipelineId": PIPELINE_ID
        }
    }

    resp = requests.post(CONFIG_URL, json=payload, headers=headers, timeout=15)
    if not resp.ok:
        raise RuntimeError(f"Failed to fetch pipeline config: {resp.status_code}")

    data = resp.json()
    
    # Parse services
    asr_cfg = next((t for t in data["languages"][0]["pipelineResponseBody"] if t["taskType"] == "asr"), None)
    nmt_cfg = next((t for t in data["languages"][0]["pipelineResponseBody"] if t["taskType"] == "translation"), None)

    if not asr_cfg or not nmt_cfg:
        raise RuntimeError("ASR or Translation config missing from API response.")

    endpoint = data["pipelineInferenceAPIEndPoint"]
    
    config = {
        "callback_url": endpoint["callbackUrl"],
        "auth_key_name": endpoint["inferenceApiKey"]["name"],
        "auth_key_value": endpoint["inferenceApiKey"]["value"],
        "asr_service_id": asr_cfg["config"][0]["serviceId"],
        "nmt_service_id": nmt_cfg["config"][0]["serviceId"],
    }

    _config_cache[source_lang] = config
    return config


def transcribe_and_translate(audio_b64_wav: str, source_lang: str) -> tuple[str, str]:
    """Transcribe Dravidian audio and translate to English using Bhashini API."""
    config = get_pipeline_config(source_lang)

    payload = {
        "pipelineTasks": [
            {
                "taskType": "asr",
                "config": {
                    "language": {"sourceLanguage": source_lang},
                    "serviceId": config["asr_service_id"],
                    "audioFormat": "wav",
                    "samplingRate": SAMPLE_RATE,
                }
            },
            {
                "taskType": "translation",
                "config": {
                    "language": {
                        "sourceLanguage": source_lang,
                        "targetLanguage": "en"
                    },
                    "serviceId": config["nmt_service_id"],
                }
            }
        ],
        "inputData": {
            "audio": [{"audioContent": audio_b64_wav}],
        }
    }

    headers = {
        config["auth_key_name"]: config["auth_key_value"],
        "Content-Type": "application/json"
    }

    max_retries = 3
    retry_delay = 0.3
    last_error = ""

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(config["callback_url"], json=payload, headers=headers, timeout=10)
            if resp.ok:
                data = resp.json()
                break
            else:
                last_error = f"API {resp.status_code}: {resp.reason}"
                time.sleep(retry_delay * attempt)
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            time.sleep(retry_delay * attempt)
    else:
        raise RuntimeError(f"Bhashini API call failed: {last_error}")

    # Parse response
    responses = data["pipelineResponse"]
    asr_out = next((r for r in responses if r["taskType"] == "asr"), None)
    nmt_out = next((r for r in responses if r["taskType"] == "translation"), None)

    transcript = asr_out["output"][0]["source"] if asr_out else ""
    translation = nmt_out["output"][0]["target"] if nmt_out else ""

    return transcript, translation


# ─── Audio Processing Helpers ────────────────────────────────────────────────

def resample_audio(audio_data: np.ndarray, from_rate: int, to_rate: int = 16000) -> np.ndarray:
    """Linearly resample numpy audio data using linear interpolation."""
    if from_rate == to_rate:
        return audio_data
    duration = len(audio_data) / from_rate
    num_samples = int(duration * to_rate)
    return np.interp(
        np.linspace(0, duration, num_samples, endpoint=False),
        np.linspace(0, duration, len(audio_data), endpoint=False),
        audio_data
    )


def float_to_int16_pcm(audio_data: np.ndarray) -> bytes:
    """Convert float32/float64 array to signed 16-bit PCM bytes."""
    audio_data = np.clip(audio_data, -1.0, 1.0)
    audio_data = (audio_data * 32767).astype(np.int16)
    return audio_data.tobytes()


def pcm_to_wav_base64(pcm_bytes: bytes, sample_rate: int = 16000) -> str:
    """Prefix PCM bytes with a WAV header and encode to base64."""
    num_channels = 1
    sample_width = 2
    
    header = bytearray()
    header.extend(b'RIFF')
    header.extend(struct.pack('<I', 36 + len(pcm_bytes)))
    header.extend(b'WAVE')
    header.extend(b'fmt ')
    header.extend(struct.pack('<I', 16))
    header.extend(struct.pack('<H', 1))
    header.extend(struct.pack('<H', num_channels))
    header.extend(struct.pack('<I', sample_rate))
    header.extend(struct.pack('<I', sample_rate * num_channels * sample_width))
    header.extend(struct.pack('<H', num_channels * sample_width))
    header.extend(struct.pack('<H', sample_width * 8))
    header.extend(b'data')
    header.extend(struct.pack('<I', len(pcm_bytes)))
    
    return base64.b64encode(bytes(header) + pcm_bytes).decode("utf-8")


# ─── Gradio Handler ──────────────────────────────────────────────────────────

def transcribe_stream(audio, state, lang):
    """Processes streamed mic chunks and appends translations to state."""
    if state is None:
        state = {
            "buffer": np.array([], dtype=np.float32),
            "transcript": "",
            "translation": ""
        }

    if audio is None:
        return state["transcript"], state["translation"], state

    rate, y = audio
    
    # Convert stereo to mono
    if len(y.shape) > 1:
        y = y.mean(axis=1)

    # Normalize input to float32
    if y.dtype == np.int16:
        y = y.astype(np.float32) / 32768.0
    elif y.dtype == np.int32:
        y = y.astype(np.float32) / 2147483648.0

    # Resample to 16000Hz
    y_resampled = resample_audio(y, rate, 16000)
    
    # Accumulate samples in session state
    state["buffer"] = np.concatenate([state["buffer"], y_resampled])
    
    # Process when buffer reaches 3 seconds (48000 samples)
    chunk_samples = 3 * 16000
    while len(state["buffer"]) >= chunk_samples:
        pcm_chunk = state["buffer"][:chunk_samples]
        state["buffer"] = state["buffer"][chunk_samples:]

        # Calculate RMS for silence gating
        rms = np.sqrt(np.mean(pcm_chunk ** 2)) * 32768.0
        
        if rms >= SILENCE_RMS_THRESHOLD:
            pcm_bytes = float_to_int16_pcm(pcm_chunk)
            wav_b64 = pcm_to_wav_base64(pcm_bytes)
            
            try:
                transcript, translation = transcribe_and_translate(wav_b64, lang)
                if transcript.strip():
                    state["transcript"] += " " + transcript.strip()
                    state["translation"] += " " + translation.strip()
            except Exception as e:
                print(f"[ASR Error] {e}")

    return state["transcript"].strip(), state["translation"].strip(), state


def reset_state():
    """Clear session data."""
    return "", "", {
        "buffer": np.array([], dtype=np.float32),
        "transcript": "",
        "translation": ""
    }


# ─── UI Layout ───────────────────────────────────────────────────────────────

with gr.Blocks(theme=gr.themes.Soft()) as demo:
    gr.Markdown(
        """
        # 🎙️ Bhashini Live Transcriber (Dravidian → English)
        *Powered by ULCA / Dhruva API · Government of India*
        
        Stream voice live from your browser. Silence and background noise are automatically skipped.
        """
    )
    
    # Session State
    state = gr.State()

    with gr.Row():
        lang_dropdown = gr.Dropdown(
            choices=[(name, code) for code, name in LANGUAGES.items()],
            value="ta",
            label="Select Source Language"
        )
        audio_input = gr.Audio(
            sources=["microphone"],
            streaming=True,
            label="Speak Here"
        )

    with gr.Row():
        with gr.Column():
            transcript_box = gr.Textbox(
                label="Source Transcription", 
                placeholder="Transcribed text will appear here...", 
                interactive=False
            )
        with gr.Column():
            translation_box = gr.Textbox(
                label="English Translation", 
                placeholder="English translation will appear here...", 
                interactive=False
            )

    clear_btn = gr.Button("Clear Output", variant="secondary")

    # Gradio streaming event
    audio_input.stream(
        fn=transcribe_stream,
        inputs=[audio_input, state, lang_dropdown],
        outputs=[transcript_box, translation_box, state],
        show_progress="hidden"
    )
    
    # Reset event
    clear_btn.click(
        fn=reset_state,
        inputs=[],
        outputs=[transcript_box, translation_box, state]
    )

# Run the app (HF Spaces automatically binds on 7860)
if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
