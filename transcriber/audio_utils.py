"""
Audio Utilities
───────────────
Handles conversion of browser-captured WebM/Opus audio to the
WAV format expected by Bhashini ASR (16kHz, mono, PCM 16-bit).
Uses pydub (backed by ffmpeg) for reliable format conversion.
"""

import io
import base64
import struct
import math

# Ensure ffmpeg binaries are in the system path for pydub to find
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except ImportError:
    pass

from pydub import AudioSegment

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit = 2 bytes


def webm_to_wav_base64(webm_bytes: bytes) -> str:
    """
    Converts WebM/Opus audio bytes (from browser MediaRecorder)
    to a base64-encoded WAV string suitable for Bhashini ASR.

    Pipeline: WebM/Opus → pydub (ffmpeg) → WAV (16kHz mono PCM16) → base64

    Args:
        webm_bytes: Raw bytes of the WebM audio blob from the browser.

    Returns:
        Base64-encoded WAV string.
    """
    # Load WebM/Opus into pydub via ffmpeg
    audio = AudioSegment.from_file(io.BytesIO(webm_bytes), format="webm")

    # Normalise to Bhashini's expected format
    audio = (
        audio
        .set_frame_rate(SAMPLE_RATE)
        .set_channels(CHANNELS)
        .set_sample_width(SAMPLE_WIDTH)
    )

    # Export as WAV to memory
    wav_buffer = io.BytesIO()
    audio.export(wav_buffer, format="wav")
    wav_bytes = wav_buffer.getvalue()

    return base64.b64encode(wav_bytes).decode("utf-8")


def compute_rms_from_wav_b64(wav_b64: str) -> float:
    """
    Computes RMS energy from a base64-encoded WAV string.
    Used for silence detection — chunks below a threshold are skipped.

    Args:
        wav_b64: Base64-encoded WAV string (16-bit PCM).

    Returns:
        RMS energy as a float.
    """
    wav_bytes = base64.b64decode(wav_b64)

    # Skip WAV header (44 bytes for standard PCM WAV)
    pcm_data = wav_bytes[44:]

    if len(pcm_data) < 2:
        return 0.0

    # Unpack 16-bit signed integers
    n_samples = len(pcm_data) // 2
    samples = struct.unpack(f"<{n_samples}h", pcm_data[:n_samples * 2])

    if not samples:
        return 0.0

    # RMS calculation
    sum_sq = sum(s * s for s in samples)
    return math.sqrt(sum_sq / n_samples)


def compute_rms_from_webm(webm_bytes: bytes) -> float:
    """
    Computes RMS energy directly from WebM bytes.
    Converts to raw PCM via pydub first.

    Args:
        webm_bytes: Raw WebM audio bytes.

    Returns:
        RMS energy as a float.
    """
    try:
        audio = AudioSegment.from_file(io.BytesIO(webm_bytes), format="webm")
        audio = audio.set_frame_rate(SAMPLE_RATE).set_channels(1).set_sample_width(2)
        return audio.rms
    except Exception:
        return 0.0


def pcm_to_wav_base64(pcm_bytes: bytes, sample_rate: int = 16000) -> str:
    """
    Constructs a WAV header for raw 16-bit PCM bytes and returns base64 string.
    This runs in microseconds without invoking any external subprocesses!
    """
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
    
    wav_bytes = bytes(header) + pcm_bytes
    return base64.b64encode(wav_bytes).decode("utf-8")


def compute_rms_pcm(pcm_bytes: bytes) -> float:
    """
    Computes RMS energy directly from raw signed 16-bit PCM bytes.
    Does not require pydub or ffmpeg.
    """
    if len(pcm_bytes) < 2:
        return 0.0
    n_samples = len(pcm_bytes) // 2
    # Unpack PCM little-endian signed 16-bit short integers
    samples = struct.unpack(f"<{n_samples}h", pcm_bytes[:n_samples * 2])
    sum_sq = sum(s * s for s in samples)
    return math.sqrt(sum_sq / n_samples)

