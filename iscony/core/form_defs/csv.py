"""CSV import forms.

CSV import is the fast path for building large tournaments. Keep upload and
reimport confirmation forms here so future import formats can be added without
mixing them with day-of-operation forms.
"""

from django import forms


class CSVUploadForm(forms.Form):
    file = forms.FileField(required=False)
    reimport_confirm_token = forms.CharField(
        required=False,
        widget=forms.HiddenInput,
    )

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("file") and not cleaned_data.get(
            "reimport_confirm_token"
        ):
            self.add_error("file", "CSVファイルを選択してください。")

        return cleaned_data
