from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import LeaveRequest

class LeaveRequestForm(forms.ModelForm):
    start_date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': 'form-control',
                'min': timezone.localdate().isoformat(),  # disallow past
            }
        )
    )
    end_date = forms.DateField(
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': 'form-control',
                'min': timezone.localdate().isoformat(),  # disallow past
            }
        )
    )

    class Meta:
        model = LeaveRequest
        fields = ['start_date', 'end_date', 'reason']
        widgets = {
            'reason': forms.Textarea(attrs={'rows': 4, 'class': 'form-control'}),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start_date')
        end   = cleaned.get('end_date')
        today = timezone.localdate()

        # If either missing, let field-level validators handle
        if start and end:
            if start < today:
                self.add_error('start_date', ValidationError("Start date cannot be in the past."))
            if end < today:
                self.add_error('end_date', ValidationError("End date cannot be in the past."))
            if start > end:
                self.add_error('end_date', ValidationError("End date must be on or after start date."))
        return cleaned
