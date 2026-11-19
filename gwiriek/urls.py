"""URL configuration: the Django admin is the only web interface of the core.

The language switcher (``i18n/setlang/``) is declared before the admin, which is mounted
at the root and would otherwise catch the URL.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
    path("", admin.site.urls),
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]
