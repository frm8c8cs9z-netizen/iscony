"""大会全体の足番号表示を、一度の取得で決める。キャッシュはリクエスト限定。"""
from contextlib import contextmanager
from contextvars import ContextVar

from ..models import Tournament, TournamentEntry


_request_decisions = ContextVar("tournament_leg_number_decisions", default=None)


@contextmanager
def leg_number_display_request():
    token = _request_decisions.set({})
    try:
        yield
    finally:
        _request_decisions.reset(token)


def tournament_leg_number_decisions(tournament):
    cache = _request_decisions.get()
    if cache is not None and tournament.pk in cache:
        return cache[tournament.pk]

    entries = TournamentEntry.objects.filter(
        bracket__category__tournament_id=tournament.pk,
    ).values(
        "bracket_id", "bracket__stage__show_leg_numbers", "leg_number", "leg_branch",
        "pair_code", "display_order", "advancement_source__pk", "source_pair_id",
        "participant__entry_code",
    )
    sources, enabled = set(), set()
    has_reason = False
    for entry in entries:
        bracket_id = entry["bracket_id"]
        if entry["advancement_source__pk"] is not None:
            sources.add(bracket_id)
            has_reason = True
        if entry["bracket__stage__show_leg_numbers"] is False:
            continue
        enabled.add(bracket_id)
        code = entry["pair_code"] or str(entry["display_order"])
        if (entry["source_pair_id"] and entry["participant__entry_code"]
                and tournament.default_tournament_reflected_entry_code_mode
                == Tournament.REFLECTED_ENTRY_CODE_ENTRY_CODE):
            code = entry["participant__entry_code"]
        number = str(entry["leg_number"])
        if entry["leg_branch"] is not None:
            number += f"の{entry['leg_branch']}"
        has_reason = has_reason or number != code

    shown = sources | enabled if has_reason else set()
    if cache is not None:
        cache[tournament.pk] = shown
    return shown
