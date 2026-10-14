from django.urls import path

from . import views

urlpatterns = [
    path("services/", views.ServiceListView.as_view(), name="api-service-list"),
    path("availability/", views.AvailabilityView.as_view(), name="api-availability"),
]
