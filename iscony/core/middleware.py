"""Site-wide authentication with an explicit test opt-out."""

from django.conf import settings
from django.contrib.auth.middleware import LoginRequiredMiddleware

from .selectors.tournament_leg_numbers import leg_number_display_request


class IsconyLoginRequiredMiddleware(LoginRequiredMiddleware):
    def __call__(self, request):
        if self.async_mode:
            return self._leg_number_acall(request)
        with leg_number_display_request():
            return super().__call__(request)

    async def _leg_number_acall(self, request):
        with leg_number_display_request():
            return await super().__acall__(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not settings.ISCONY_LOGIN_REQUIRED:
            return None
        return super().process_view(request, view_func, view_args, view_kwargs)
