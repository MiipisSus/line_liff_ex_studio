from django import forms

from booking.models import Service, Staff


class _ServiceStaffForm(forms.Form):
    service = forms.ModelChoiceField(queryset=Service.objects.filter(is_active=True))
    # 留空代表「不指定人員」
    staff = forms.ModelChoiceField(queryset=Staff.objects.filter(is_active=True), required=False)


class SlotQueryForm(_ServiceStaffForm):
    date = forms.DateField(required=False)


class BookingForm(_ServiceStaffForm):
    # ISO 8601（含 +08:00），由時段按鈕帶入
    start_at = forms.DateTimeField()
