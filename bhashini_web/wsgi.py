"""
WSGI config for bhashini_web project.
Not used in production (we use ASGI via Daphne for WebSocket support),
but kept for compatibility.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bhashini_web.settings")

application = get_wsgi_application()
