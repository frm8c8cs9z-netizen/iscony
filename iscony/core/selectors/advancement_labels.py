"""表示用に先読み済みの関連オブジェクトへ、進出元ラベルの集計を渡す。"""

from ..models import AdvancementSource, TournamentMatch


def prepare_related_result_labels(objects):
    """関連のキャッシュだけを巡回し、未取得の関連を個別に問い合わせない。"""
    objects = list(objects)
    pending = list(objects)
    seen = set()
    matches = []
    while pending:
        obj = pending.pop()
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        if isinstance(obj, TournamentMatch):
            matches.append(obj)
        pending.extend(obj._state.fields_cache.values())
        for related in getattr(obj, "_prefetched_objects_cache", {}).values():
            pending.extend(related)
    AdvancementSource.prepare_tournament_result_labels(matches)
    return objects
