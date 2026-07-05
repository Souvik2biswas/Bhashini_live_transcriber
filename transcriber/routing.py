"""
WebSocket URL routing for the transcriber app.
"""

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"ws/transcribe/$", consumers.TranscriptionConsumer.as_asgi()),
]
