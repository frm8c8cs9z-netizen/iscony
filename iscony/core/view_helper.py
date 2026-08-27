from urllib.parse import urlencode

from django.shortcuts import render, redirect


def safe_next_url(request):
    """GET next はアプリ内の相対パスだけ戻り先として扱う。"""

    next_url = request.GET.get("next")

    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url

    return None

def render_score_input(
    request,
    match,
    winning_games,
    mode,
    back_url,
    tournament=None,
    error=None
):

    context = {
        "match": match,
        "winning_games": winning_games,
        "mode": mode,
        "back_url": back_url,
        "can_input_score": not (
            mode == "tournament"
            and (not match.pair1_id or not match.pair2_id)
        ),
    }

    if tournament:
        context["tournament"] = tournament

    if error:
        context["error"] = error

    return render(
        request,
        "core/input_score.html",
        context
    )


def redirect_next_or_default(
    request,
    default_view_name,
    **kwargs
):

    next_url = safe_next_url(request)

    if next_url:
        return redirect(next_url)

    return redirect(
        default_view_name,
        **kwargs
    )


def redirect_next_or_url(request, default_url):
    """next があればそこへ、なければ組み立て済みURLへ戻す。"""

    next_url = safe_next_url(request)

    if next_url:
        return redirect(next_url)

    return redirect(default_url)


def url_with_next(url, next_url):
    """クエリなしの結果入力URLへ戻り先を付ける。"""

    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode({'next': next_url})}"


