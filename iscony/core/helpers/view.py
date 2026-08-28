"""View helpers shared by multiple core screens.

結果入力後の戻り先処理、next URLの安全確認、共通テンプレートへの描画など、
複数のビューで使う小さな画面補助を集める。

このファイルの関心はHTTPリクエスト/レスポンスに近い部分。
たとえば、GETパラメタの next がアプリ内の相対URLか確認する、保存後に next へ戻す、
リーグ/トーナメント共通の結果入力テンプレートを描画する、といった処理を担当する。

ここでは、勝敗の保存、順位再計算、後続Stage反映、スナップショット作成などの
業務判断は行わない。それらは core.services に置く。

結果入力導線は現場運用でかなり重要なので、戻り先の仕様を変える場合は
横断表示、QR/キー検索、個別リーグ表、個別トーナメント表の流れを一緒に確認する。
"""

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


