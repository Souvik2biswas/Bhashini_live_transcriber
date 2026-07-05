"""
Root URL configuration for bhashini_web project.
"""

from django.urls import path, include

urlpatterns = [
    path("", include("transcriber.urls")),
]
