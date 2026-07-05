"""
Django settings for bhashini_web project.
Live Transcription & Translation WebApp powered by Bhashini API.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ─── Load .env ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ─── Core Settings ──────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-bhashini-dev-key-change-in-production"
)
DEBUG = os.getenv("DJANGO_DEBUG", "True").lower() in ("true", "1", "yes")
ALLOWED_HOSTS = ["*"]

# ─── Bhashini API Credentials ──────────────────────────────────────────────────
BHASHINI_USER_ID = os.getenv(
    "BHASHINI_USER_ID", "ec359508556040529d5383c73ede5568"
)
BHASHINI_ULCA_KEY = os.getenv(
    "BHASHINI_ULCA_KEY", "157ebde0c5-7907-481b-9420-5e148dc255c2"
)
BHASHINI_INFERENCE_KEY = os.getenv(
    "BHASHINI_INFERENCE_KEY",
    "aFT_C1IEbGW4cHwGl5JDZ2NDY3UnhxeP7i7YagvKhkW980pK3CwCBAUlcOniHUHW"
)
BHASHINI_CONFIG_URL = "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline"
BHASHINI_PIPELINE_ID = "64392f96daac500b55c543cd"

# ─── Application Definition ────────────────────────────────────────────────────
INSTALLED_APPS = [
    "daphne",
    "django.contrib.staticfiles",
    "channels",
    "transcriber",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "bhashini_web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.static",
            ],
        },
    },
]

# ─── ASGI (Channels) ───────────────────────────────────────────────────────────
ASGI_APPLICATION = "bhashini_web.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# ─── Database (not needed for this app, but Django requires it) ─────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# ─── Static Files ──────────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ─── Internationalization ──────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
