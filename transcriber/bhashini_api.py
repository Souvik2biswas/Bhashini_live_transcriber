"""
Bhashini API Layer
──────────────────
Refactored from the standalone CLI scripts for use in Django.
Provides pipeline configuration, ASR + NMT chained inference,
and text-only translation.

All functions are synchronous (called via sync_to_async in the consumer).
Pipeline config is cached per-language for the lifetime of the process.
"""

import json
import threading
import time
from typing import Optional

import requests
from django.conf import settings


# ─── Constants ──────────────────────────────────────────────────────────────────
CONFIG_URL  = settings.BHASHINI_CONFIG_URL
PIPELINE_ID = settings.BHASHINI_PIPELINE_ID
USER_ID     = settings.BHASHINI_USER_ID
ULCA_API_KEY = settings.BHASHINI_ULCA_KEY

SAMPLE_RATE = 16000

# ─── Language Registry ─────────────────────────────────────────────────────────
LANGUAGES = {
    "en": {
        "name": "English",
        "native": "English",
        "supported": True,
        "asr_supported": True,
        "translation_supported": False,
        "family": "Other",
    },
    "as": {
        "name": "Assamese",
        "native": "অসমীয়া",
        "supported": True,
        "asr_supported": False,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "bn": {
        "name": "Bengali",
        "native": "বাংলা",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "brx": {
        "name": "Bodo",
        "native": "बर'",
        "supported": True,
        "asr_supported": False,
        "translation_supported": True,
        "family": "Sino-Tibetan",
    },
    "doi": {
        "name": "Dogri",
        "native": "डोगरी",
        "supported": True,
        "asr_supported": False,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "gu": {
        "name": "Gujarati",
        "native": "ગુજરાતી",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "hi": {
        "name": "Hindi",
        "native": "हिन्दी",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "kn": {
        "name": "Kannada",
        "native": "ಕನ್ನಡ",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Dravidian",
    },
    "ks": {
        "name": "Kashmiri",
        "native": "کٲشُر",
        "supported": True,
        "asr_supported": False,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "gom": {
        "name": "Konkani (Goan)",
        "native": "कोंकणी",
        "supported": True,
        "asr_supported": False,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "mai": {
        "name": "Maithili",
        "native": "मैथिली",
        "supported": True,
        "asr_supported": False,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "ml": {
        "name": "Malayalam",
        "native": "മലയാളം",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Dravidian",
    },
    "mni": {
        "name": "Manipuri",
        "native": "মৈতৈলোন্",
        "supported": True,
        "asr_supported": False,
        "translation_supported": True,
        "family": "Sino-Tibetan",
    },
    "mr": {
        "name": "Marathi",
        "native": "मराठी",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "ne": {
        "name": "Nepali",
        "native": "नेपाली",
        "supported": True,
        "asr_supported": False,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "or": {
        "name": "Odia (Oriya)",
        "native": "ଓଡ଼ିଆ",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "pa": {
        "name": "Punjabi",
        "native": "ਪੰਜਾਬੀ",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "sa": {
        "name": "Sanskrit",
        "native": "संस्कृतम्",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "sat": {
        "name": "Santali",
        "native": "ᱥᱟᱱᱛᱟᱲᱤ",
        "supported": True,
        "asr_supported": False,
        "translation_supported": True,
        "family": "Austroasiatic",
    },
    "sd": {
        "name": "Sindhi",
        "native": "سنڌي",
        "supported": True,
        "asr_supported": False,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
    "ta": {
        "name": "Tamil",
        "native": "தமிழ்",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Dravidian",
    },
    "te": {
        "name": "Telugu",
        "native": "తెలుగు",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Dravidian",
    },
    "ur": {
        "name": "Urdu",
        "native": "اُردُو",
        "supported": True,
        "asr_supported": True,
        "translation_supported": True,
        "family": "Indo-Aryan",
    },
}

SUPPORTED_CODES = [c for c, v in LANGUAGES.items() if v["supported"]]


# ─── Thread-Safe Pipeline Config Cache ──────────────────────────────────────────
_config_cache: dict = {}
_cache_lock = threading.Lock()


def get_pipeline_config(source_lang: str, target_lang: str = "en", force_refresh: bool = False) -> dict:
    """
    Pipeline Config Call — fetches ASR + NMT service IDs and the dynamic
    inference endpoint. Result is cached per (source, target) pair with a
    1-hour Time-To-Live (TTL) to handle dynamic key expiration/rotation.

    Returns dict with:
        callback_url, auth_key_name, auth_key_value,
        asr_service_id, nmt_service_id
    """
    cache_key = (source_lang, target_lang)
    if not force_refresh:
        with _cache_lock:
            if cache_key in _config_cache:
                entry = _config_cache[cache_key]
                if time.time() - entry["timestamp"] < 3600:
                    return entry["data"]

    tasks = []
    lang_info = LANGUAGES.get(source_lang, {})
    if lang_info.get("asr_supported", True):
        tasks.append({
            "taskType": "asr",
            "config": {
                "language": {"sourceLanguage": source_lang}
            }
        })
    if lang_info.get("translation_supported", True):
        tasks.append({
            "taskType": "translation",
            "config": {
                "language": {
                    "sourceLanguage": source_lang,
                    "targetLanguage": target_lang
                }
            }
        })

    payload = {
        "pipelineTasks": tasks,
        "pipelineRequestConfig": {
            "pipelineId": PIPELINE_ID
        }
    }

    headers = {
        "userID":       USER_ID,
        "ulcaApiKey":   ULCA_API_KEY,
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(CONFIG_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        raise RuntimeError("Pipeline config call timed out (15s). Check your internet.")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Pipeline config HTTP {resp.status_code}: {e}")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Pipeline config request failed: {e}")

    # ── Parse response ────────────────────────────────────────────────────────
    try:
        task_configs = data["pipelineResponseConfig"]

        asr_cfg = next(
            (t for t in task_configs if t["taskType"] == "asr"), None
        )
        nmt_cfg = next(
            (t for t in task_configs if t["taskType"] == "translation"), None
        )

        asr_service_id = None
        if lang_info.get("asr_supported", True):
            if asr_cfg is None:
                raise RuntimeError(
                    f"No ASR config returned for language '{source_lang}'."
                )
            asr_service_id = asr_cfg["config"][0]["serviceId"]

        nmt_service_id = None
        if lang_info.get("translation_supported", True):
            if nmt_cfg is None:
                raise RuntimeError(
                    f"No NMT config returned for language '{source_lang}'."
                )
            nmt_service_id = nmt_cfg["config"][0]["serviceId"]

        endpoint       = data["pipelineInferenceAPIEndPoint"]
        callback_url   = endpoint["callbackUrl"]
        auth_key_name  = endpoint["inferenceApiKey"]["name"]
        auth_key_value = endpoint["inferenceApiKey"]["value"]

    except (KeyError, IndexError) as e:
        raise RuntimeError(
            f"Unexpected config response structure (key: {e}).\n"
            f"Raw:\n{json.dumps(data, indent=2, ensure_ascii=False)}"
        )

    result = {
        "callback_url":   callback_url,
        "auth_key_name":  auth_key_name,
        "auth_key_value": auth_key_value,
        "asr_service_id": asr_service_id,
        "nmt_service_id": nmt_service_id,
    }

    with _cache_lock:
        _config_cache[cache_key] = {
            "data": result,
            "timestamp": time.time()
        }

    return result


def transcribe_and_translate(
    audio_b64_wav: str,
    source_lang: str,
    target_lang: str = "en",
) -> tuple[str, str]:
    """
    Pipeline Compute Call — sends base64-encoded WAV audio through chained
    ASR → NMT in a single API round-trip.

    Args:
        audio_b64_wav : base64-encoded WAV string (16kHz, mono, PCM16)
        source_lang   : ISO 639 code ('ta', 'te', 'kn', 'ml')
        target_lang   : Target language ISO 639 code (default: 'en')

    Returns:
        (transcript_in_source_lang, english_translation)
    """
    config = get_pipeline_config(source_lang, target_lang)

    if not config.get("asr_service_id"):
        raise RuntimeError(f"Speech recognition is not supported for {LANGUAGES.get(source_lang, {}).get('name', source_lang)}.")

    tasks = [
        {
            "taskType": "asr",
            "config": {
                "language":     {"sourceLanguage": source_lang},
                "serviceId":    config["asr_service_id"],
                "audioFormat":  "wav",
                "samplingRate": SAMPLE_RATE,
            }
        }
    ]

    if config.get("nmt_service_id"):
        tasks.append({
            "taskType": "translation",
            "config": {
                "language": {
                    "sourceLanguage": source_lang,
                    "targetLanguage": target_lang
                },
                "serviceId": config["nmt_service_id"],
            }
        })

    payload = {
        "pipelineTasks": tasks,
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
    last_error_msg = ""
    
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                config["callback_url"],
                json=payload,
                headers=headers,
                timeout=10  # 10s per attempt
            )
            if resp.ok:
                data = resp.json()
                break
            else:
                last_error_msg = f"Dhruva API {resp.status_code}: {resp.reason} (Attempt {attempt})"
                if resp.status_code in (401, 403):
                    try:
                        config = get_pipeline_config(source_lang, target_lang, force_refresh=True)
                        headers = {
                            config["auth_key_name"]: config["auth_key_value"],
                            "Content-Type": "application/json"
                        }
                        for task in payload["pipelineTasks"]:
                            if task["taskType"] == "asr":
                                task["config"]["serviceId"] = config["asr_service_id"]
                            elif task["taskType"] == "translation":
                                task["config"]["serviceId"] = config["nmt_service_id"]
                    except Exception as refresh_err:
                        raise RuntimeError(f"Config refresh failed on HTTP {resp.status_code}: {refresh_err}")
                    time.sleep(retry_delay * attempt)
                    continue
                elif resp.status_code == 500 or resp.status_code == 429:
                    time.sleep(retry_delay * attempt)
                    continue
                else:
                    raise RuntimeError(last_error_msg + f"\nBody: {resp.text[:500]}")
        except requests.exceptions.Timeout:
            last_error_msg = f"Compute call timed out (Attempt {attempt})"
            time.sleep(retry_delay * attempt)
            continue
        except requests.exceptions.RequestException as e:
            last_error_msg = f"Compute call failed: {e} (Attempt {attempt})"
            time.sleep(retry_delay * attempt)
            continue
    else:
        raise RuntimeError(f"Compute call failed after {max_retries} attempts. Last error: {last_error_msg}")

    # ── Parse chained response ────────────────────────────────────────────────
    try:
        responses = data["pipelineResponse"]

        asr_out = next(
            (r for r in responses if r["taskType"] == "asr"), None
        )
        nmt_out = next(
            (r for r in responses if r["taskType"] == "translation"), None
        )

        if asr_out is None:
            raise RuntimeError(
                f"Missing ASR block in response.\n"
                f"Raw:\n{json.dumps(data, indent=2, ensure_ascii=False)}"
            )

        transcript = asr_out["output"][0]["source"]
        
        if config.get("nmt_service_id"):
            if nmt_out is None:
                raise RuntimeError(
                    f"Missing NMT block in response.\n"
                    f"Raw:\n{json.dumps(data, indent=2, ensure_ascii=False)}"
                )
            translation = nmt_out["output"][0]["target"]
        else:
            translation = transcript

        return transcript.strip(), translation.strip()

    except (KeyError, IndexError) as e:
        raise RuntimeError(
            f"Response parse error (key: {e}).\n"
            f"Raw:\n{json.dumps(data, indent=2, ensure_ascii=False)}"
        )


def translate_text(text: str, source_lang: str, target_lang: str = "en") -> str:
    """
    Text-only translation via Bhashini NMT.
    Uses only the NMT service (no ASR).

    Args:
        text        : Input text in source language
        source_lang : ISO 639 code
        target_lang : Target language code (default: 'en')

    Returns:
        Translated string
    """
    if not text.strip():
        raise ValueError("Input text cannot be empty.")

    if source_lang == target_lang:
        return text

    config = get_pipeline_config(source_lang)
    if not config.get("nmt_service_id"):
        return text

    payload = {
        "pipelineTasks": [
            {
                "taskType": "translation",
                "config": {
                    "language": {
                        "sourceLanguage": source_lang,
                        "targetLanguage": target_lang
                    },
                    "serviceId": config["nmt_service_id"]
                }
            }
        ],
        "inputData": {
            "input": [{"source": text}]
        }
    }

    headers = {
        config["auth_key_name"]: config["auth_key_value"],
        "Content-Type": "application/json"
    }

    max_retries = 3
    retry_delay = 0.3
    last_error_msg = ""

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                config["callback_url"],
                json=payload,
                headers=headers,
                timeout=25
            )
            if resp.ok:
                data = resp.json()
                break
            else:
                last_error_msg = f"Dhruva API {resp.status_code}: {resp.reason} (Attempt {attempt})"
                if resp.status_code in (401, 403):
                    try:
                        config = get_pipeline_config(source_lang, force_refresh=True)
                        headers = {
                            config["auth_key_name"]: config["auth_key_value"],
                            "Content-Type": "application/json"
                        }
                        payload["pipelineTasks"][0]["config"]["serviceId"] = config["nmt_service_id"]
                    except Exception as refresh_err:
                        raise RuntimeError(f"Config refresh failed on HTTP {resp.status_code}: {refresh_err}")
                    time.sleep(retry_delay * attempt)
                    continue
                elif resp.status_code == 500 or resp.status_code == 429:
                    time.sleep(retry_delay * attempt)
                    continue
                else:
                    raise RuntimeError(last_error_msg + f"\nBody: {resp.text[:500]}")
        except requests.exceptions.Timeout:
            last_error_msg = f"Translation call timed out (Attempt {attempt})"
            time.sleep(retry_delay * attempt)
            continue
        except requests.exceptions.RequestException as e:
            last_error_msg = f"Translation call failed: {e} (Attempt {attempt})"
            time.sleep(retry_delay * attempt)
            continue
    else:
        raise RuntimeError(f"Translation failed after {max_retries} attempts. Last error: {last_error_msg}")

    try:
        return data["pipelineResponse"][0]["output"][0]["target"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Translation response parse error: {e}")
