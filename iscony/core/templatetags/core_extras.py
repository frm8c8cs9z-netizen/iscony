from django import template

from core.models import (
    Category,
    Court,
    Group,
    LeagueEntry,
    RoundRobinMatch,
    Schedule,
    ScheduleBlock,
    Stage,
    Tournament,
    TournamentBracket,
    TournamentMatch,
)


register = template.Library()


@register.filter
def dict_get(value, key):
    if not isinstance(value, dict):
        return ""

    return value.get(key, "")


def _tournament_code_from_value(value):
    if value is None:
        return ""

    if isinstance(value, Tournament):
        return value.code

    if isinstance(value, Category):
        return value.tournament.code

    if isinstance(value, Group):
        return value.category.tournament.code

    if isinstance(value, LeagueEntry):
        return value.category.tournament.code

    if isinstance(value, RoundRobinMatch):
        return value.group.category.tournament.code

    if isinstance(value, ScheduleBlock):
        return value.tournament.code

    if isinstance(value, TournamentBracket):
        return value.category.tournament.code

    if isinstance(value, TournamentMatch):
        return value.bracket.category.tournament.code

    if isinstance(value, Court):
        return value.tournament.code

    if isinstance(value, Stage):
        return value.category.tournament.code

    if isinstance(value, Schedule):
        return value.court.tournament.code

    return ""


def _tournament_code_from_request(request):
    resolver_match = getattr(request, "resolver_match", None)
    if not resolver_match:
        return ""

    kwargs = resolver_match.kwargs
    view_name = resolver_match.view_name or ""

    code = kwargs.get("code") or kwargs.get("tournament_code")
    if code:
        return code

    category_id = kwargs.get("category_id")
    if category_id:
        return (
            Category.objects.filter(
                id=category_id
            ).values_list(
                "tournament__code",
                flat=True
            ).first()
            or ""
        )

    group_id = kwargs.get("group_id")
    if group_id:
        return (
            Group.objects.filter(
                id=group_id
            ).values_list(
                "category__tournament__code",
                flat=True
            ).first()
            or ""
        )

    pair_id = kwargs.get("pair_id")
    if pair_id:
        return (
            LeagueEntry.objects.filter(
                id=pair_id
            ).values_list(
                "category__tournament__code",
                flat=True
            ).first()
            or ""
        )

    bracket_id = kwargs.get("bracket_id")
    if bracket_id:
        return (
            TournamentBracket.objects.filter(
                id=bracket_id
            ).values_list(
                "category__tournament__code",
                flat=True
            ).first()
            or ""
        )

    match_id = kwargs.get("match_id")
    if match_id:
        if view_name == "input_match_score":
            return (
                RoundRobinMatch.objects.filter(
                    id=match_id
                ).values_list(
                    "group__category__tournament__code",
                    flat=True
                ).first()
                or ""
            )

        if view_name in {
            "input_tournament_match_score",
            "edit_tournament_match",
            "tournament_match_maintenance",
            "tournament_first_round_score_sheets_pdf",
            "tournament_match_score_sheet",
            "add_tournament_match_schedule",
        }:
            return (
                TournamentMatch.objects.filter(
                    id=match_id
                ).values_list(
                    "bracket__category__tournament__code",
                    flat=True
                ).first()
                or ""
            )

    schedule_id = kwargs.get("schedule_id")
    if schedule_id:
        return (
            Schedule.objects.filter(
                id=schedule_id
            ).values_list(
                "court__tournament__code",
                flat=True
            ).first()
            or ""
        )

    schedule_block_id = kwargs.get("schedule_block_id")
    if schedule_block_id:
        return (
            ScheduleBlock.objects.filter(
                id=schedule_block_id
            ).values_list(
                "tournament__code",
                flat=True
            ).first()
            or ""
        )

    court_id = kwargs.get("court_id")
    if court_id:
        return (
            Court.objects.filter(
                id=court_id
            ).values_list(
                "tournament__code",
                flat=True
            ).first()
            or ""
        )

    return ""


def _public_token_from_value(value):
    if value is None:
        return ""

    if isinstance(value, Tournament):
        return value.public_token if value.is_public else ""

    if isinstance(value, Category):
        tournament = value.tournament
        return tournament.public_token if tournament.is_public else ""

    if isinstance(value, Group):
        tournament = value.category.tournament
        return tournament.public_token if tournament.is_public else ""

    if isinstance(value, LeagueEntry):
        tournament = value.category.tournament
        return tournament.public_token if tournament.is_public else ""

    if isinstance(value, RoundRobinMatch):
        tournament = value.group.category.tournament
        return tournament.public_token if tournament.is_public else ""

    if isinstance(value, ScheduleBlock):
        tournament = value.tournament
        return tournament.public_token if tournament.is_public else ""

    if isinstance(value, TournamentBracket):
        tournament = value.category.tournament
        return tournament.public_token if tournament.is_public else ""

    if isinstance(value, TournamentMatch):
        tournament = value.bracket.category.tournament
        return tournament.public_token if tournament.is_public else ""

    if isinstance(value, Court):
        tournament = value.tournament
        return tournament.public_token if tournament.is_public else ""

    if isinstance(value, Stage):
        tournament = value.category.tournament
        return tournament.public_token if tournament.is_public else ""

    if isinstance(value, Schedule):
        tournament = value.court.tournament
        return tournament.public_token if tournament.is_public else ""

    return ""


def _public_token_from_request(request):
    resolver_match = getattr(request, "resolver_match", None)
    if not resolver_match:
        return ""

    kwargs = resolver_match.kwargs
    code = kwargs.get("code") or kwargs.get("tournament_code")
    if code:
        return (
            Tournament.objects.filter(
                code=code,
                is_public=True,
            ).values_list(
                "public_token",
                flat=True
            ).first()
            or ""
        )

    category_id = kwargs.get("category_id")
    if category_id:
        return (
            Category.objects.filter(
                id=category_id,
                tournament__is_public=True,
            ).values_list(
                "tournament__public_token",
                flat=True
            ).first()
            or ""
        )

    group_id = kwargs.get("group_id")
    if group_id:
        return (
            Group.objects.filter(
                id=group_id,
                category__tournament__is_public=True,
            ).values_list(
                "category__tournament__public_token",
                flat=True
            ).first()
            or ""
        )

    bracket_id = kwargs.get("bracket_id")
    if bracket_id:
        return (
            TournamentBracket.objects.filter(
                id=bracket_id,
                category__tournament__is_public=True,
            ).values_list(
                "category__tournament__public_token",
                flat=True
            ).first()
            or ""
        )

    match_id = kwargs.get("match_id")
    if match_id:
        if resolver_match.view_name == "input_match_score":
            return (
                RoundRobinMatch.objects.filter(
                    id=match_id,
                    group__category__tournament__is_public=True,
                ).values_list(
                    "group__category__tournament__public_token",
                    flat=True
                ).first()
                or ""
            )

        if resolver_match.view_name in {
            "input_tournament_match_score",
            "edit_tournament_match",
            "tournament_match_maintenance",
            "tournament_first_round_score_sheets_pdf",
            "tournament_match_score_sheet",
            "add_tournament_match_schedule",
        }:
            return (
                TournamentMatch.objects.filter(
                    id=match_id,
                    bracket__category__tournament__is_public=True,
                ).values_list(
                    "bracket__category__tournament__public_token",
                    flat=True
                ).first()
                or ""
            )

    schedule_id = kwargs.get("schedule_id")
    if schedule_id:
        return (
            Schedule.objects.filter(
                id=schedule_id,
                court__tournament__is_public=True,
            ).values_list(
                "court__tournament__public_token",
                flat=True
            ).first()
            or ""
        )

    schedule_block_id = kwargs.get("schedule_block_id")
    if schedule_block_id:
        return (
            ScheduleBlock.objects.filter(
                id=schedule_block_id,
                tournament__is_public=True,
            ).values_list(
                "tournament__public_token",
                flat=True
            ).first()
            or ""
        )

    court_id = kwargs.get("court_id")
    if court_id:
        return (
            Court.objects.filter(
                id=court_id,
                tournament__is_public=True,
            ).values_list(
                "tournament__public_token",
                flat=True
            ).first()
            or ""
        )

    return ""


@register.simple_tag(takes_context=True)
def admin_menu_code(context):
    request = context.get("request")
    if request:
        resolver_match = getattr(request, "resolver_match", None)
        view_name = getattr(resolver_match, "view_name", "") or ""
        if view_name.startswith("public_"):
            return ""

    for key in (
        "tournament",
        "category",
        "group",
        "pair",
        "match",
        "bracket",
        "schedule_block",
        "court",
        "stage",
    ):
        code = _tournament_code_from_value(context.get(key))
        if code:
            return code

    if request and getattr(request, "resolver_match", None):
        code = _tournament_code_from_request(request)
        if code:
            return code

    return ""


@register.simple_tag(takes_context=True)
def admin_menu_public_token(context):
    request = context.get("request")
    if request:
        resolver_match = getattr(request, "resolver_match", None)
        view_name = getattr(resolver_match, "view_name", "") or ""
        if view_name.startswith("public_"):
            return ""

    for key in (
        "tournament",
        "category",
        "group",
        "pair",
        "match",
        "bracket",
        "schedule_block",
        "court",
        "stage",
    ):
        token = _public_token_from_value(context.get(key))
        if token:
            return token

    if request and getattr(request, "resolver_match", None):
        token = _public_token_from_request(request)
        if token:
            return token

    return ""
