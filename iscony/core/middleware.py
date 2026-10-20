"""Site-wide authentication with an explicit test opt-out."""

from django.conf import settings
from django.contrib.auth.middleware import LoginRequiredMiddleware


class IsconyLoginRequiredMiddleware(LoginRequiredMiddleware):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if not settings.ISCONY_LOGIN_REQUIRED:
            return None
        return super().process_view(request, view_func, view_args, view_kwargs)
