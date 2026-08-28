"""Cache helpers for core views.

公開画面など、読み取り専用ビューへ短時間キャッシュを付けるための補助関数を置く。
キャッシュ対象の判定やURL設計は呼び出し側で行い、ここではDjangoのcache_pageを
設定値に沿って適用する薄いラッパーに留める。
"""

from functools import wraps

from django.conf import settings
from django.views.decorators.cache import cache_page


def public_view_cache(view_func):
    """Apply a short, settings-controlled cache to public read-only views."""

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        timeout = int(getattr(settings, "PUBLIC_VIEW_CACHE_SECONDS", 0) or 0)

        if timeout <= 0:
            return view_func(request, *args, **kwargs)

        cached_view = cache_page(
            timeout,
            key_prefix="public-view",
        )(view_func)
        return cached_view(request, *args, **kwargs)

    return wrapped
