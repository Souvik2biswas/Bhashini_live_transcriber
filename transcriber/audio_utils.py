"""
Audio Utilities
───────────────
Handles conversion of browser-captured raw PCM audio to the
WAV format expected by Bhashini ASR (16kHz, mono, PCM 16-bit).
Runs in pure Python (no external subprocesses or ffmpeg required).
"""

import io
import base64
import struct
import math

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit = 2 bytes


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

