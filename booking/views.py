from django.db.models import Prefetch
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Service, Staff
from .serializers import AvailabilityQuerySerializer, ServiceSerializer, SlotSerializer


class ServiceListView(ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = ServiceSerializer
    queryset = Service.objects.filter(is_active=True).prefetch_related(
        Prefetch("staff", queryset=Staff.objects.filter(is_active=True))
    )


class AvailabilityView(APIView):
    """GET /api/availability/?service=<id>&date=YYYY-MM-DD[&staff=<id>]"""

    permission_classes = [AllowAny]

    def get(self, request):
        query = AvailabilityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        slots = services.get_available_slots(
            service=query.validated_data["service"],
            day=query.validated_data["date"],
            staff=query.validated_data.get("staff"),
        )
        return Response({
            "date": query.validated_data["date"],
            "slots": SlotSerializer(slots, many=True).data,
        })
