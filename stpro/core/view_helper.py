from django.shortcuts import render, redirect

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

    next_url = request.GET.get("next")

    if next_url:
        return redirect(next_url)

    return redirect(
        default_view_name,
        **kwargs
    )


