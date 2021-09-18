from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from pages.login_pages import login, render_login_page


urlpatterns = [
    path('', render_login_page),
    path('validate_login', login),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
