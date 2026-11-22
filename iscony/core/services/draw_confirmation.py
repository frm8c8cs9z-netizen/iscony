"""組み合わせ確定の判定。結果入力によるロックとは独立して扱う。"""

DRAW_CONFIRMED_REASON = "このStageは組み合わせを確定しているため、番号や組み合わせの形が変わる操作はできません。必要なら確定を解除してください。"


def stage_draw_confirmed(stage):
    return bool(stage and stage.draw_confirmed_at is not None)


def participant_draw_confirmed(participant):
    if not participant.pk:
        return False
    return any(stage_draw_confirmed(entry.group.stage) for entry in participant.leagueentry_set.select_related("group__stage")) or any(
        stage_draw_confirmed(entry.bracket.stage) for entry in participant.tournamententry_set.select_related("bracket__stage")
    )


def protect_form_fields(form, fields):
    """表示は無効化し、細工したPOSTは無視せずエラーにする。"""
    for name in fields:
        field = form.fields[name]
        field.widget.attrs["disabled"] = True
        field.help_text = DRAW_CONFIRMED_REASON
        if form.is_bound and name not in form.data:
            data = form.data.copy()
            data[name] = form.initial.get(name, "")
            form.data = data
            form._bound_fields_cache.clear()


def validate_protected_form_fields(form, cleaned_data, fields):
    for name in fields:
        if name in cleaned_data and cleaned_data[name] != getattr(form.instance, name):
            form.add_error(name, DRAW_CONFIRMED_REASON)
