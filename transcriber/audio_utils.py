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
import numpy as np

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit = 2 bytes


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
    Computes RMS energy directly from raw signed 16-bit PCM bytes using numpy.
    """
    if len(pcm_bytes) < 2:
        return 0.0
    # Ensure length is even for int16 (2 bytes per sample)
    even_len = (len(pcm_bytes) // 2) * 2
    samples = np.frombuffer(pcm_bytes[:even_len], dtype=np.int16).astype(np.float64)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples ** 2)))

