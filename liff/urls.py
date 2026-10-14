from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="liff-index"),
    path("auth/line/", views.line_login, name="liff-line-login"),
    path("book/", views.book_services, name="liff-book-services"),
    path("book/services/<int:service_id>/staff/", views.book_staff, name="liff-book-staff"),
    path("book/slots/", views.book_slots, name="liff-book-slots"),
    path("book/confirm/", views.book_confirm, name="liff-book-confirm"),
    path("book/create/", views.book_create, name="liff-book-create"),
]
