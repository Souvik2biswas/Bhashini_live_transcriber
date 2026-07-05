"""
ASGI config for bhashini_web project.
Routes HTTP and WebSocket protocols to the appropriate handlers.
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bhashini_web.settings")
django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

from transcriber.routing import websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        "http": get_asgi_application(),
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
