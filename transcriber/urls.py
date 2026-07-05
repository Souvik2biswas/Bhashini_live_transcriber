"""
URL configuration for the transcriber app.
"""

from django.urls import path

from . import views

app_name = "transcriber"

urlpatterns = [
    path("", views.index, name="index"),
]
