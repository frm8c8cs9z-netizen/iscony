"""CSVの列名・別名・出力順。output=False は読み込み専用。"""
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Header:
    key: str
    ja: str
    aliases: tuple = ()
    output: bool = True


CSV_HEADERS = {
    "participants": (
        Header('category', 'カテゴリ', ('カテゴリ名',)),
        Header('entry_code', '番号', ('参加者コード', 'エントリー番号')),
        Header('player1_name', '選手1', ()),
        Header('player2_name', '選手2', ()),
        Header('org1', '所属1', ('organization1',)),
        Header('org2', '所属2', ('organization2',)),
        Header('player1_org1', '選手1所属1', ()),
        Header('player1_org2', '選手1所属2', ()),
        Header('player2_org1', '選手2所属1', ()),
        Header('player2_org2', '選手2所属2', ()),
        Header('organization', '所属', (), output=False),
        Header('org3', '所属3', ('organization3',), output=False),
        Header('org4', '所属4', ('organization4',), output=False),
        Header('org5', '所属5', ('organization5',), output=False),
        Header('player1_org3', '選手1所属3', (), output=False),
        Header('player1_org4', '選手1所属4', (), output=False),
        Header('player1_org5', '選手1所属5', (), output=False),
        Header('player2_org3', '選手2所属3', (), output=False),
        Header('player2_org4', '選手2所属4', (), output=False),
        Header('player2_org5', '選手2所属5', (), output=False),
    ),
    "stage_slots": (
        Header('category', 'カテゴリ名', ('カテゴリ',)),
        Header('stage_code', 'ステージコード', ('Stageコード',)),
        Header('stage', 'ステージ名', ('Stage', 'ステージ')),
        Header('stage_type', '形式', ()),
        Header('group', 'リーグ名', ('グループ',)),
        Header('bracket', 'トーナメント名', ('トーナメント表',)),
        Header('slot_label', '枠名', ()),
        Header('display_order', '表示順', ()),
        Header('entry_code', '番号', ('参加者コード', 'エントリー番号')),
        Header('source_type', '進出元の種類', ()),
        Header('source_stage', '進出元ステージ名', ('進出元Stage',)),
        Header('source_stage_code', '進出元ステージコード', ('進出元Stageコード',)),
        Header('source_bracket', '進出元トーナメント名', ('進出元トーナメント表',)),
        Header('source_group', '進出元リーグ名', ('進出元グループ',)),
        Header('source_rank', '進出元順位', ()),
        Header('source_match', '進出元マッチコード', ('進出元試合',)),
        Header('source_result', '進出元マッチの勝敗', ('進出元の勝敗',)),
        Header('match_games', 'ゲーム数', ()),
        Header('start_round', '開始回戦', ()),
    ),
    "schedule": (
        Header('schedule_block', '日程区分', ()),
        Header('category', 'カテゴリ名', ('カテゴリ',)),
        Header('stage_code', 'ステージコード', ('Stageコード',)),
        Header('stage', 'ステージ名', ('Stage', 'ステージ')),
        Header('group', 'リーグ名', ('グループ',)),
        Header('bracket', 'トーナメント名', ('トーナメント表',)),
        Header('court', 'コート', ()),
        Header('order', '順番', ()),
        Header('slot1_code', '枠1', ('pair1',)),
        Header('slot2_code', '枠2', ('pair2',)),
        Header('meeting_number', '何回目', ()),
        Header('match_code', 'マッチコード', ()),
        Header('match_label', '試合名', ()),
        Header('match_games', 'ゲーム数', ()),
        Header('umpire_entry_code', '審判番号', ()),
        Header('stage_type', '形式', (), output=False),
    ),
}


def normalize_header(value):
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def output_headers(kind, language="en"):
    return [h.key if language == "en" else h.ja for h in CSV_HEADERS[kind] if h.output]


def header_label(kind, key):
    return next((h.ja for h in CSV_HEADERS[kind] if h.key == key), key)


def canonicalize_headers(kind, names):
    aliases = {normalize_header(name): h.key
               for h in CSV_HEADERS[kind]
               for name in (h.key, h.ja, *h.aliases)}
    canonical = [aliases.get(normalize_header(name), name) for name in names]
    originals = {}
    for name, key in zip(names, canonical):
        if key in aliases.values():
            originals.setdefault(key, []).append(name)
    duplicates = [name for group in originals.values() if len(group) > 1 for name in group]
    if duplicates:
        raise ValueError("同じ列が2回あります: " + ", ".join(duplicates))
    return canonical, {key: group[0] for key, group in originals.items()}
