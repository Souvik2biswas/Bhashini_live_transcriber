"""
Views for the transcriber app.
"""

from django.shortcuts import render


def index(request):
    """Render the main live transcription page."""
    return render(request, "transcriber/index.html")
