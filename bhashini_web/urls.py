"""
Root URL configuration for bhashini_web project.
"""

from django.urls import path, include
from django.http import HttpResponse

def google_verification(request):
    return HttpResponse("google-site-verification: googlef6547862a22eb603.html", content_type="text/html")

urlpatterns = [
    path("googlef6547862a22eb603.html", google_verification),
    path("", include("transcriber.urls")),
]
