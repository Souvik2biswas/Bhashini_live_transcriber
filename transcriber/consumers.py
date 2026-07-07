"""
WebSocket Consumer for Live Transcription
──────────────────────────────────────────
Handles bidirectional communication between the browser and Bhashini API.

Protocol:
  Client → Server:
    - JSON: {"type": "config", "lang": "ta"}          → set source language
    - JSON: {"type": "translate_text", "text": "...", "lang": "ta"} → text translation
    - Binary: raw WebM/Opus audio chunk                → transcribe + translate

  Server → Client:
    - JSON: {"type": "config_ready", "lang": "Tamil", ...}
    - JSON: {"type": "transcription", "transcript": "...", "translation": "...", ...}
    - JSON: {"type": "translation", "source": "...", "translation": "...", ...}
    - JSON: {"type": "error", "message": "..."}
    - JSON: {"type": "silence"}
"""

import json
import time
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async

from .bhashini_api import (
    get_pipeline_config,
    transcribe_and_translate,
    translate_text,
    LANGUAGES,
    SUPPORTED_CODES,
)
from .audio_utils import pcm_to_wav_base64, compute_rms_pcm

logger = logging.getLogger(__name__)

# Silence threshold for raw 16-bit signed PCM (adjusted from 300 to 800 to filter out ambient mic noise)
SILENCE_RMS_THRESHOLD = 800



class TranscriptionConsumer(AsyncWebsocketConsumer):
    """
    Async WebSocket consumer that:
    1. Accepts a language configuration message
    2. Receives binary audio chunks from the browser's MediaRecorder
    3. Converts WebM → WAV, sends to Bhashini ASR+NMT
    4. Pushes transcription + translation results back to the client
    """

    async def connect(self):
        """Accept the WebSocket connection and initialise session state."""
        self.source_lang = "ta"  # Default to Tamil
        self.chunk_count = 0
        self.skip_count = 0
        self.error_count = 0
        self.pcm_buffer = bytearray()  # Raw PCM stream buffer
        await self.accept()

        # Send initial supported languages list
        await self.send(text_data=json.dumps({
            "type": "languages",
            "languages": {
                code: {
                    "name": info["name"],
                    "native": info["native"],
                    "asr_supported": info.get("asr_supported", True),
                    "translation_supported": info.get("translation_supported", True),
                    "family": info.get("family", "Other"),
                }
                for code, info in LANGUAGES.items()
                if info["supported"]
            }
        }))

    async def disconnect(self, close_code):
        """Log session summary on disconnect."""
        logger.info(
            f"WebSocket disconnected (code={close_code}). "
            f"Chunks: {self.chunk_count}, Skipped: {self.skip_count}, "
            f"Errors: {self.error_count}"
        )

    async def receive(self, text_data=None, bytes_data=None):
        """
        Handle incoming messages:
        - text_data (JSON): configuration, text translation, or stop requests
        - bytes_data: raw 16kHz mono 16-bit PCM chunks (streamed in real-time)
        """
        # ── JSON Messages ─────────────────────────────────────────────────────
        if text_data:
            try:
                msg = json.loads(text_data)
            except json.JSONDecodeError:
                await self._send_error("Invalid JSON message.")
                return

            msg_type = msg.get("type", "")

            if msg_type == "config":
                await self._handle_config(msg)
            elif msg_type == "translate_text":
                await self._handle_text_translation(msg)
            elif msg_type == "stop":
                await self._handle_stop()
            else:
                await self._send_error(f"Unknown message type: {msg_type}")
            return

        # ── Binary PCM Stream ─────────────────────────────────────────────────
        if bytes_data:
            self.pcm_buffer.extend(bytes_data)

            # Process in 3-second slices (3 * 16000 samples * 2 bytes = 96000 bytes)
            chunk_size = 3 * 16000 * 2

            while len(self.pcm_buffer) >= chunk_size:
                pcm_chunk = bytes(self.pcm_buffer[:chunk_size])
                self.pcm_buffer = self.pcm_buffer[chunk_size:]
                await self._process_pcm_chunk(pcm_chunk)

    # ── Config Handler ────────────────────────────────────────────────────────

    async def _handle_config(self, msg: dict):
        """Set the source language and pre-fetch the pipeline config."""
        lang = msg.get("lang", "ta")

        if lang not in SUPPORTED_CODES:
            await self._send_error(
                f"Unsupported language: {lang}. "
                f"Supported: {', '.join(SUPPORTED_CODES)}"
            )
            return

        self.source_lang = lang
        self.chunk_count = 0
        self.skip_count = 0
        self.error_count = 0
        self.pcm_buffer.clear()

        # Pre-fetch pipeline config (warm up)
        try:
            config = await sync_to_async(get_pipeline_config)(lang)
            lang_info = LANGUAGES[lang]
            await self.send(text_data=json.dumps({
                "type": "config_ready",
                "lang": lang_info["name"],
                "native": lang_info["native"],
                "code": lang,
                "asr_service": config["asr_service_id"],
                "nmt_service": config["nmt_service_id"],
            }))
        except RuntimeError as e:
            await self._send_error(f"Pipeline config failed: {e}")

    # ── Stop Handler ──────────────────────────────────────────────────────────

    async def _handle_stop(self):
        """Process any remaining audio in the PCM buffer when recording stops."""
        # Process if there is at least 1.5 seconds of audio (to prevent cutoffs)
        if len(self.pcm_buffer) >= 1.5 * 16000 * 2:
            pcm_chunk = bytes(self.pcm_buffer)
            self.pcm_buffer.clear()
            await self._process_pcm_chunk(pcm_chunk)
        else:
            self.pcm_buffer.clear()

    # ── Audio Chunk Handler ───────────────────────────────────────────────────

    async def _process_pcm_chunk(self, pcm_bytes: bytes):
        """
        Process a raw PCM chunk:
        1. Calculate RMS directly (silence gate)
        2. Create WAV header in memory (no ffmpeg or subprocess)
        3. Send to Bhashini API
        4. Send results back to client
        """
        self.chunk_count += 1
        chunk_num = self.chunk_count

        try:
            # ── Step 1: Check silence ─────────────────────────────────────────
            rms = await sync_to_async(compute_rms_pcm)(pcm_bytes)

            if rms < SILENCE_RMS_THRESHOLD:
                self.skip_count += 1
                await self.send(text_data=json.dumps({
                    "type": "silence",
                    "chunk_num": chunk_num,
                    "rms": round(rms, 1),
                }))
                return

            # Notify client that processing has started
            await self.send(text_data=json.dumps({
                "type": "processing",
                "chunk_num": chunk_num,
                "rms": round(rms, 1),
            }))

            # ── Step 2: Convert to WAV and transcribe ─────────────────────────
            wav_b64 = await sync_to_async(pcm_to_wav_base64)(pcm_bytes)



            t0 = time.perf_counter()
            transcript, translation = await sync_to_async(
                transcribe_and_translate
            )(wav_b64, self.source_lang)
            latency = time.perf_counter() - t0

            # ── Step 3: Send result ───────────────────────────────────────────
            if not transcript:
                await self.send(text_data=json.dumps({
                    "type": "no_speech",
                    "chunk_num": chunk_num,
                }))
                return

            lang_info = LANGUAGES[self.source_lang]
            await self.send(text_data=json.dumps({
                "type": "transcription",
                "chunk_num": chunk_num,
                "transcript": transcript,
                "translation": translation,
                "lang": lang_info["name"],
                "native": lang_info["native"],
                "latency": round(latency, 2),
                "rms": round(rms, 1),
            }))

        except RuntimeError as e:
            self.error_count += 1
            logger.error(f"PCM Chunk #{chunk_num} error: {e}")
            await self._send_error(str(e), chunk_num=chunk_num)

        except Exception as e:
            self.error_count += 1
            logger.exception(f"Unexpected error on PCM chunk #{chunk_num}")
            await self._send_error(
                f"Unexpected error: {type(e).__name__}: {e}",
                chunk_num=chunk_num
            )

    # ── Text Translation Handler ──────────────────────────────────────────────

    async def _handle_text_translation(self, msg: dict):
        """Handle a text-only translation request."""
        text = msg.get("text", "").strip()
        lang = msg.get("lang", self.source_lang)

        if not text:
            await self._send_error("Text cannot be empty.")
            return

        if lang not in SUPPORTED_CODES:
            await self._send_error(f"Unsupported language: {lang}")
            return

        try:
            t0 = time.perf_counter()
            translation = await sync_to_async(translate_text)(text, lang)
            latency = time.perf_counter() - t0

            await self.send(text_data=json.dumps({
                "type": "translation",
                "source": text,
                "translation": translation,
                "lang": LANGUAGES[lang]["name"],
                "latency": round(latency, 2),
            }))
        except (RuntimeError, ValueError) as e:
            await self._send_error(f"Translation failed: {e}")

    # ── Error Helper ──────────────────────────────────────────────────────────

    async def _send_error(self, message: str, chunk_num: int = None):
        """Send a structured error message to the client."""
        payload = {
            "type": "error",
            "message": message,
        }
        if chunk_num is not None:
            payload["chunk_num"] = chunk_num
        await self.send(text_data=json.dumps(payload))
