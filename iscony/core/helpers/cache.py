"""Cache helpers for core views.

公開画面など、読み取り専用ビューへ短時間キャッシュを付けるための補助関数を置く。

このファイルはDjangoの cache_page を、プロジェクト設定
PUBLIC_VIEW_CACHE_SECONDS に沿って適用する薄いラッパーを担当する。
キャッシュを使うかどうかの業務判断、公開URLの構造、公開トークンの検証は呼び出し側の責務。

管理画面や結果入力画面には、基本的にここを適用しない。
試合結果・進行状況・後続Stage反映のように即時性が必要な画面へ広げる場合は、
古い表示が運営事故につながらないかを先に確認する。
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
