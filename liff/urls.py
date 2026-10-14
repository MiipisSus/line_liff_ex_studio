from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="liff-index"),
    path("auth/line/", views.line_login, name="liff-line-login"),
]
