import base64
import struct
from unittest.mock import patch, MagicMock
from django.test import TestCase
import requests

from transcriber.audio_utils import pcm_to_wav_base64, compute_rms_pcm
from transcriber.bhashini_api import (
    get_pipeline_config,
    transcribe_and_translate,
    translate_text,
    _config_cache,
    _cache_lock,
)

class AudioUtilsTestCase(TestCase):
    def test_compute_rms_pcm_silence(self):
        # Empty input
        self.assertEqual(compute_rms_pcm(b""), 0.0)
        # 1 byte input (less than 2 bytes)
        self.assertEqual(compute_rms_pcm(b"\x00"), 0.0)
        # All zeros
        self.assertEqual(compute_rms_pcm(b"\x00\x00" * 100), 0.0)

    def test_compute_rms_pcm_constant(self):
        # Constant non-zero PCM signal: value = 1000
        # 1000 is 0x03E8 in hex, in little-endian signed 16-bit: b'\xe8\x03'
        pcm_data = b"\xe8\x03" * 100
        # RMS should be exactly 1000.0
        self.assertAlmostEqual(compute_rms_pcm(pcm_data), 1000.0)

    def test_pcm_to_wav_base64_header(self):
        pcm_data = b"\x00\x00" * 8000  # 0.5 seconds of silence at 16kHz
        wav_b64 = pcm_to_wav_base64(pcm_data, sample_rate=16000)
        
        # Decode base64
        wav_bytes = base64.b64decode(wav_b64)
        
        # Check standard WAV header fields
        self.assertEqual(wav_bytes[0:4], b"RIFF")
        # ChunkSize (36 + len(pcm_data))
        expected_chunk_size = 36 + len(pcm_data)
        chunk_size = struct.unpack("<I", wav_bytes[4:8])[0]
        self.assertEqual(chunk_size, expected_chunk_size)
        
        self.assertEqual(wav_bytes[8:12], b"WAVE")
        self.assertEqual(wav_bytes[12:16], b"fmt ")
        
        # Subchunk1Size should be 16
        self.assertEqual(struct.unpack("<I", wav_bytes[16:20])[0], 16)
        # AudioFormat should be 1 (PCM)
        self.assertEqual(struct.unpack("<H", wav_bytes[20:22])[0], 1)
        # NumChannels should be 1
        self.assertEqual(struct.unpack("<H", wav_bytes[22:24])[0], 1)
        # SampleRate should be 16000
        self.assertEqual(struct.unpack("<I", wav_bytes[24:28])[0], 16000)
        # BitsPerSample should be 16
        self.assertEqual(struct.unpack("<H", wav_bytes[34:36])[0], 16)
        
        self.assertEqual(wav_bytes[36:40], b"data")
        # Subchunk2Size should be len(pcm_data)
        self.assertEqual(struct.unpack("<I", wav_bytes[40:44])[0], len(pcm_data))
        # Data bytes should match pcm_data
        self.assertEqual(wav_bytes[44:], pcm_data)


class BhashiniApiTestCase(TestCase):
    def setUp(self):
        # Clear config cache before each test
        _config_cache.clear()

    @patch("transcriber.bhashini_api._session.post")
    def test_get_pipeline_config_success(self, mock_post):
        # Mock successful configuration response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "pipelineResponseConfig": [
                {
                    "taskType": "asr",
                    "config": [{"serviceId": "fake_asr_service_id"}]
                },
                {
                    "taskType": "translation",
                    "config": [{"serviceId": "fake_nmt_service_id"}]
                }
            ],
            "pipelineInferenceAPIEndPoint": {
                "callbackUrl": "https://fake.bhashini.gov.in/inference",
                "inferenceApiKey": {
                    "name": "Authorization",
                    "value": "fake_api_key"
                }
            }
        }
        mock_post.return_value = mock_response

        # Request configuration for Tamil ("ta")
        config = get_pipeline_config("ta")
        
        self.assertEqual(config["callback_url"], "https://fake.bhashini.gov.in/inference")
        self.assertEqual(config["auth_key_name"], "Authorization")
        self.assertEqual(config["auth_key_value"], "fake_api_key")
        self.assertEqual(config["asr_service_id"], "fake_asr_service_id")
        self.assertEqual(config["nmt_service_id"], "fake_nmt_service_id")
        
        # Verify call arguments
        mock_post.assert_called_once()
        
        # Call it again and verify it is served from cache (mock_post not called a second time)
        config2 = get_pipeline_config("ta")
        self.assertEqual(config, config2)
        mock_post.assert_called_once()

    @patch("transcriber.bhashini_api._session.post")
    def test_translate_text_success(self, mock_post):
        # First mock get_pipeline_config mock return
        mock_config_response = MagicMock()
        mock_config_response.status_code = 200
        mock_config_response.json.return_value = {
            "pipelineResponseConfig": [
                {
                    "taskType": "asr",
                    "config": [{"serviceId": "fake_asr_service_id"}]
                },
                {
                    "taskType": "translation",
                    "config": [{"serviceId": "fake_nmt_service_id"}]
                }
            ],
            "pipelineInferenceAPIEndPoint": {
                "callbackUrl": "https://fake.bhashini.gov.in/inference",
                "inferenceApiKey": {
                    "name": "Authorization",
                    "value": "fake_api_key"
                }
            }
        }
        
        # Mock translate text response
        mock_translate_response = MagicMock()
        mock_translate_response.status_code = 200
        mock_translate_response.json.return_value = {
            "pipelineResponse": [
                {
                    "taskType": "translation",
                    "output": [{"target": "Hello World"}]
                }
            ]
        }
        
        mock_post.side_effect = [mock_config_response, mock_translate_response]

        translation = translate_text("வணக்கம் உலகம்", "ta")
        self.assertEqual(translation, "Hello World")
        self.assertEqual(mock_post.call_count, 2)

    @patch("transcriber.bhashini_api._session.post")
    def test_transcribe_and_translate_success(self, mock_post):
        # 1. Pipeline config mock
        mock_config_response = MagicMock()
        mock_config_response.status_code = 200
        mock_config_response.json.return_value = {
            "pipelineResponseConfig": [
                {
                    "taskType": "asr",
                    "config": [{"serviceId": "fake_asr_service_id"}]
                },
                {
                    "taskType": "translation",
                    "config": [{"serviceId": "fake_nmt_service_id"}]
                }
            ],
            "pipelineInferenceAPIEndPoint": {
                "callbackUrl": "https://fake.bhashini.gov.in/inference",
                "inferenceApiKey": {
                    "name": "Authorization",
                    "value": "fake_api_key"
                }
            }
        }

        # 2. Chained compute response mock
        mock_compute_response = MagicMock()
        mock_compute_response.status_code = 200
        mock_compute_response.json.return_value = {
            "pipelineResponse": [
                {
                    "taskType": "asr",
                    "output": [{"source": "வணக்கம்"}]
                },
                {
                    "taskType": "translation",
                    "output": [{"target": "Hello"}]
                }
            ]
        }

        mock_post.side_effect = [mock_config_response, mock_compute_response]

        transcript, translation = transcribe_and_translate("fake_audio_base64", "ta")
        self.assertEqual(transcript, "வணக்கம்")
        self.assertEqual(translation, "Hello")
        self.assertEqual(mock_post.call_count, 2)

    @patch("transcriber.bhashini_api._session.post")
    def test_transcribe_and_translate_retry_on_500(self, mock_post):
        # 1. Config call (cached afterwards)
        mock_config_response = MagicMock()
        mock_config_response.status_code = 200
        mock_config_response.json.return_value = {
            "pipelineResponseConfig": [
                {
                    "taskType": "asr",
                    "config": [{"serviceId": "fake_asr_service_id"}]
                },
                {
                    "taskType": "translation",
                    "config": [{"serviceId": "fake_nmt_service_id"}]
                }
            ],
            "pipelineInferenceAPIEndPoint": {
                "callbackUrl": "https://fake.bhashini.gov.in/inference",
                "inferenceApiKey": {
                    "name": "Authorization",
                    "value": "fake_api_key"
                }
            }
        }

        # 2. Compute call fails first with 500, then succeeds
        mock_500_response = MagicMock()
        mock_500_response.status_code = 500
        mock_500_response.ok = False

        mock_success_response = MagicMock()
        mock_success_response.status_code = 200
        mock_success_response.ok = True
        mock_success_response.json.return_value = {
            "pipelineResponse": [
                {
                    "taskType": "asr",
                    "output": [{"source": "வணக்கம்"}]
                },
                {
                    "taskType": "translation",
                    "output": [{"target": "Hello"}]
                }
            ]
        }

        mock_post.side_effect = [mock_config_response, mock_500_response, mock_success_response]

        with patch("transcriber.bhashini_api.time.sleep") as mock_sleep:
            transcript, translation = transcribe_and_translate("fake_audio_base64", "ta")
            self.assertEqual(transcript, "வணக்கம்")
            self.assertEqual(translation, "Hello")
            self.assertEqual(mock_post.call_count, 3)
            mock_sleep.assert_called_once()

    @patch("transcriber.bhashini_api._session.post")
    def test_get_pipeline_config_ttl_and_force_refresh(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "pipelineResponseConfig": [
                {"taskType": "asr", "config": [{"serviceId": "fake_asr_service_id"}]},
                {"taskType": "translation", "config": [{"serviceId": "fake_nmt_service_id"}]}
            ],
            "pipelineInferenceAPIEndPoint": {
                "callbackUrl": "https://fake.bhashini.gov.in/inference",
                "inferenceApiKey": {"name": "Authorization", "value": "fake_api_key"}
            }
        }
        mock_post.return_value = mock_response

        # Request configuration for Tamil ("ta")
        config1 = get_pipeline_config("ta")
        self.assertEqual(mock_post.call_count, 1)

        # Retrieve again - should be cached
        config2 = get_pipeline_config("ta")
        self.assertEqual(mock_post.call_count, 1)

        # Force refresh - should bypass cache
        config3 = get_pipeline_config("ta", force_refresh=True)
        self.assertEqual(mock_post.call_count, 2)

        # Mock TTL expiry by modifying timestamp
        with _cache_lock:
            _config_cache[("ta", "en")]["timestamp"] -= 4000  # > 3600 seconds ago

        # Retrieve again - cache should be expired and fetched again
        config4 = get_pipeline_config("ta")
        self.assertEqual(mock_post.call_count, 3)

    @patch("transcriber.bhashini_api._session.post")
    def test_transcribe_and_translate_retry_on_401(self, mock_post):
        # 1. Config call
        mock_config_response = MagicMock()
        mock_config_response.status_code = 200
        mock_config_response.json.return_value = {
            "pipelineResponseConfig": [
                {"taskType": "asr", "config": [{"serviceId": "old_asr_service_id"}]},
                {"taskType": "translation", "config": [{"serviceId": "old_nmt_service_id"}]}
            ],
            "pipelineInferenceAPIEndPoint": {
                "callbackUrl": "https://fake.bhashini.gov.in/inference",
                "inferenceApiKey": {"name": "Authorization", "value": "old_api_key"}
            }
        }

        # 2. Compute call fails with 401 Unauthorized
        mock_401_response = MagicMock()
        mock_401_response.status_code = 401
        mock_401_response.ok = False

        # 3. Config call (during force refresh)
        mock_refresh_response = MagicMock()
        mock_refresh_response.status_code = 200
        mock_refresh_response.json.return_value = {
            "pipelineResponseConfig": [
                {"taskType": "asr", "config": [{"serviceId": "new_asr_service_id"}]},
                {"taskType": "translation", "config": [{"serviceId": "new_nmt_service_id"}]}
            ],
            "pipelineInferenceAPIEndPoint": {
                "callbackUrl": "https://fake.bhashini.gov.in/inference",
                "inferenceApiKey": {"name": "Authorization", "value": "new_api_key"}
            }
        }

        # 4. Successful Compute call with new key
        mock_success_response = MagicMock()
        mock_success_response.status_code = 200
        mock_success_response.ok = True
        mock_success_response.json.return_value = {
            "pipelineResponse": [
                {"taskType": "asr", "output": [{"source": "வணக்கம்"}]},
                {"taskType": "translation", "output": [{"target": "Hello"}]}
            ]
        }

        # Flow:
        # - get_pipeline_config (config call #1)
        # - transcribe_and_translate POST (compute call #1) -> returns 401
        # - force refresh config (config call #2)
        # - retry transcribe_and_translate POST (compute call #2) -> returns 200
        mock_post.side_effect = [
            mock_config_response,
            mock_401_response,
            mock_refresh_response,
            mock_success_response
        ]

        with patch("transcriber.bhashini_api.time.sleep") as mock_sleep:
            transcript, translation = transcribe_and_translate("fake_audio_base64", "ta")
            self.assertEqual(transcript, "வணக்கம்")
            self.assertEqual(translation, "Hello")
            self.assertEqual(mock_post.call_count, 4)
            mock_sleep.assert_called_once()

