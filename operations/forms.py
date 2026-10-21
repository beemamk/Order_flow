from django import forms
from .models import Employee, ExceptionTask


INPUT_CLASS = "block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"


class UploadForm(forms.Form):
    file = forms.FileField(
        widget=forms.FileInput(
            attrs={"accept": ".csv,.xlsx", "class": "sr-only", "id": "data-file"}
        )
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        suffix = upload.name.lower().rsplit(".", 1)[-1] if "." in upload.name else ""
        if suffix not in {"csv", "xlsx"}:
            raise forms.ValidationError("Upload a CSV or XLSX file.")
        if upload.size > 10 * 1024 * 1024:
            raise forms.ValidationError("File size must be under 10 MB.")
        return upload


class TaskUpdateForm(forms.ModelForm):
    class Meta:
        model = ExceptionTask
        fields = ["assigned_employee", "status"]
        widgets = {
            "assigned_employee": forms.Select(attrs={"class": INPUT_CLASS}),
            "status": forms.Select(attrs={"class": INPUT_CLASS}),
        }


class DashboardFilterForm(forms.Form):
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CLASS}))
    category = forms.ChoiceField(required=False, choices=[("", "All exception types")] + ExceptionTask.CATEGORY_CHOICES, widget=forms.Select(attrs={"class": INPUT_CLASS}))
    employee = forms.ModelChoiceField(required=False, queryset=Employee.objects.none(), empty_label="All employees", widget=forms.Select(attrs={"class": INPUT_CLASS}))
    priority = forms.ChoiceField(required=False, choices=[("", "All priorities")] + ExceptionTask.PRIORITY_CHOICES, widget=forms.Select(attrs={"class": INPUT_CLASS}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True)
