"""CSV見出しの互換性と往復を、テスト用DBのみで検証する。"""
import csv
import io
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .csv_headers import CSV_HEADERS, canonicalize_headers, header_label, output_headers
from .models import (
    AdvancementSource, Category, LeagueEntry, Participant,
    ParticipantOrganizationValue, RoundRobinMatch, Schedule, Stage, Tournament,
    TournamentEntry, TournamentMatch,
)
from .views.csv import _read_csv_rows


class JapaneseCsvHeaderTests(TestCase):
    def setUp(self):
        self.tournament = Tournament.objects.create(code="JA_CSV", name="見出しテスト")

    def upload(self, kind, headers, rows, encoding="utf-8-sig"):
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(headers)
        writer.writerows(rows)
        return self.post_bytes(kind, out.getvalue().encode(encoding))

    def post_bytes(self, kind, data):
        return self.client.post(reverse("import_" + kind, args=[self.tournament.code]), {
            "file": SimpleUploadedFile("data.csv", data, content_type="text/csv"),
        })

    def headers(self, kind, keys, mode):
        return [header_label(kind, k) if mode == "ja" or (mode == "mixed" and i % 2 == 0)
                else k for i, k in enumerate(keys)]

    def participants(self, mode="en", encoding="utf-8-sig"):
        keys = ["category", "entry_code", "player1_name", "player2_name", "org1", "player2_org1"]
        return self.upload("participants", self.headers("participants", keys, mode), [
            ["男子", "1", "山田", "鈴木", "東京", "大阪"],
            ["男子", "2", "佐藤", "田中", "京都", "京都"],
            ["男子", "3", "井上", "中村", "神戸", "神戸"],
        ], encoding)

    def slots(self, mode="en", encoding="utf-8-sig"):
        keys = output_headers("stage_slots")
        values = [
            dict(category="男子", stage_code="L1", stage="予選", stage_type="L", group="A",
                 slot_label="1", display_order=1, entry_code="1", match_games=5),
            dict(category="男子", stage_code="L1", stage="予選", stage_type="L", group="A",
                 slot_label="2", display_order=2, entry_code="2", match_games=5),
            dict(category="男子", stage_code="T1", stage="決勝", stage_type="T", bracket="本戦",
                 slot_label="1", display_order=1, source_type="LR", source_stage="予選",
                 source_stage_code="L1", source_group="A", source_rank=1, match_games=7),
            dict(category="男子", stage_code="T1", stage="決勝", stage_type="T", bracket="本戦",
                 slot_label="2", display_order=2, entry_code="3", match_games=7),
        ]
        return self.upload("stage_slots", self.headers("stage_slots", keys, mode),
                           [[v.get(k, "") for k in keys] for v in values], encoding)

    def schedule(self, mode="en", encoding="utf-8-sig"):
        keys = output_headers("schedule")
        values = [
            dict(schedule_block="初日", category="男子", stage_code="L1", stage="予選", group="A",
                 court="中央", order=1, slot1_code="1", slot2_code="2", meeting_number=1,
                 match_games=5, umpire_entry_code="3"),
            dict(schedule_block="初日", category="男子", stage_code="T1", stage="決勝", bracket="本戦",
                 court="中央", order=2, match_code="M1", match_label="決勝戦", match_games=7),
        ]
        return self.upload("schedule", self.headers("schedule", keys, mode),
                           [[v.get(k, "") for k in keys] for v in values], encoding)

    def participant_state(self):
        return (list(Participant.objects.order_by("entry_code").values_list(
            "entry_code", "player1_name", "player2_name", "organization")),
            list(ParticipantOrganizationValue.objects.order_by("participant__entry_code", "player_no", "field__code")
                 .values_list("participant__entry_code", "player_no", "field__code", "original_value")))

    def slot_state(self):
        return (
            list(Stage.objects.order_by("code").values_list("code", "name", "stage_type", "display_order")),
            list(LeagueEntry.objects.order_by("pair_code").values_list(
                "group__name", "pair_code", "display_order", "participant__entry_code")),
            list(TournamentEntry.objects.order_by("pair_code").values_list(
                "bracket__name", "pair_code", "display_order", "participant__entry_code", "start_round")),
            list(AdvancementSource.objects.values_list("source_type", "source_stage__code", "source_group__name", "source_rank")),
            list(TournamentMatch.objects.values_list("match_code", "match_games")),
            list(RoundRobinMatch.objects.values_list("match_games", "meeting_number")),
        )

    def schedule_state(self):
        return list(Schedule.objects.order_by("order").values_list(
            "schedule_block__name", "court__name", "order", "referee_participant__entry_code",
            "round_robin_match__group__stage__code", "round_robin_match__pair1__pair_code",
            "round_robin_match__pair2__pair_code", "round_robin_match__meeting_number",
            "round_robin_match__match_games", "tournament_match__match_code",
            "tournament_match__match_label", "tournament_match__match_games"))

    def test_participant_languages_and_cp932(self):
        expected = None
        for mode, encoding in [("en", "utf-8-sig"), ("ja", "utf-8-sig"), ("mixed", "utf-8-sig"), ("ja", "cp932")]:
            with self.subTest(mode=mode, encoding=encoding):
                self.assertEqual(self.participants(mode, encoding).status_code, 302)
                state = self.participant_state()
                if expected is None:
                    expected = state
                self.assertEqual(state, expected)
        self.assertEqual(expected[0][0][1:3], ("山田", "鈴木"))

    def test_slot_languages_cp932_and_roundtrip(self):
        self.participants()
        expected = None
        for mode, encoding in [("en", "utf-8-sig"), ("ja", "utf-8-sig"), ("mixed", "utf-8-sig"), ("ja", "cp932")]:
            with self.subTest(mode=mode, encoding=encoding):
                self.assertEqual(self.slots(mode, encoding).status_code, 302)
                state = self.slot_state()
                if expected is None:
                    expected = state
                self.assertEqual(state, expected)
        for language in ("ja", "en"):
            response = self.client.get(reverse("export_stage_slots", args=[self.tournament.code]),
                                       {} if language == "ja" else {"headers": "en"})
            self.assert_export_headers(response, "stage_slots", language)
            self.assertEqual(self.post_bytes("stage_slots", response.content).status_code, 302)
            self.assertEqual(self.slot_state(), expected)

    def test_schedule_languages_cp932_and_roundtrip(self):
        self.participants()
        self.slots()
        expected = None
        for mode, encoding in [("en", "utf-8-sig"), ("ja", "utf-8-sig"), ("mixed", "utf-8-sig"), ("ja", "cp932")]:
            with self.subTest(mode=mode, encoding=encoding):
                self.assertEqual(self.schedule(mode, encoding).status_code, 302)
                state = self.schedule_state()
                if expected is None:
                    expected = state
                self.assertEqual(state, expected)
        for language in ("ja", "en"):
            response = self.client.get(reverse("export_schedule", args=[self.tournament.code]),
                                       {} if language == "ja" else {"headers": "en"})
            self.assert_export_headers(response, "schedule", language)
            self.assertEqual(self.post_bytes("schedule", response.content).status_code, 302)
            self.assertEqual(self.schedule_state(), expected)

    def assert_export_headers(self, response, kind, language):
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        headers = next(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(headers, output_headers(kind, language))

    def test_templates_languages(self):
        expected_ja = {
            "participants": "カテゴリ,番号,選手1,選手2,所属1,所属2,選手1所属1,選手1所属2,選手2所属1,選手2所属2",
            "stage_slots": "カテゴリ名,ステージコード,ステージ名,形式,リーグ名,トーナメント名,枠名,表示順,番号,進出元の種類,進出元ステージ名,進出元ステージコード,進出元トーナメント名,進出元リーグ名,進出元順位,進出元マッチコード,進出元マッチの勝敗,ゲーム数,開始回戦",
            "schedule": "日程区分,カテゴリ名,ステージコード,ステージ名,リーグ名,トーナメント名,コート,順番,枠1,枠2,何回目,マッチコード,試合名,ゲーム数,審判番号",
        }
        for kind in CSV_HEADERS:
            for language in ("ja", "en"):
                with self.subTest(kind=kind, language=language):
                    response = self.client.get(reverse("download_csv_format", args=[self.tournament.code, kind]),
                                               {} if language == "ja" else {"headers": "en"})
                    self.assert_export_headers(response, kind, language)
                    if language == "ja":
                        self.assertEqual(response.content.decode("utf-8-sig").strip(), expected_ja[kind])

    def test_duplicates_rejected_without_writes(self):
        self.participants()
        self.slots()
        self.schedule()
        before = self.participant_state(), self.slot_state(), self.schedule_state()
        for kind in CSV_HEADERS:
            for names in (("番号", "entry_code") if kind != "schedule" else ("枠1", "pair1"),
                          ("カテゴリ", "CATEGORY"), (" stage ", "ＳＴＡＧＥ") if kind != "participants"
                          else ("所属1", "organization1")):
                with self.subTest(kind=kind, names=names):
                    response = self.upload(kind, list(names), [["x", "y"]])
                    self.assertEqual(response.context["errors"], ["同じ列が2回あります: " + ", ".join(names)])
                    self.assertEqual((self.participant_state(), self.slot_state(), self.schedule_state()), before)

    def test_missing_columns_use_japanese_names(self):
        for kind, missing in (("participants", "選手1"), ("stage_slots", "表示順"), ("schedule", "コート")):
            response = self.upload(kind, ["カテゴリ"], [["男子"]])
            self.assertIn(missing, response.context["errors"][0])
            self.assertNotIn("category", response.context["errors"][0])
        self.assertFalse(Category.objects.exists())

    def test_all_defined_aliases_normalize_and_unknowns_stay_unknown(self):
        for kind, definitions in CSV_HEADERS.items():
            for h in definitions:
                for name in (h.key, h.ja, *h.aliases):
                    variant = "　" + " ".join(name.upper()) + "　"
                    fullwidth = "".join(chr(ord(c) + 0xFEE0) if '!' <= c <= '~' else c for c in variant)
                    with self.subTest(kind=kind, name=name):
                        reader, rows = _read_csv_rows(io.BytesIO((fullwidth + "\nx\n").encode()), kind)
                        self.assertEqual(reader.fieldnames, [h.key])
                        self.assertEqual(rows, [{h.key: "x"}])
            self.assertEqual(canonicalize_headers(kind, ["番号(entry_code)"])[0], ["番号(entry_code)"])

    def test_legacy_aliases_and_normalization_import_together(self):
        response = self.upload("participants", [" カテゴリ名　", "ＥＮＴＲＹ＿ＣＯＤＥ", "選 手１", "選手２", "所属", "ＯＲＧＡＮＩＺＡＴＩＯＮ２", "organization5"],
                               [["男子", "1", "山田", "鈴木", "東京", "大阪", "京都"],
                                ["男子", "2", "佐藤", "田中", "東京", "大阪", "京都"]])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ParticipantOrganizationValue.objects.filter(field__code="org5", original_value="京都").count(), 4)
        response = self.upload("stage_slots", ["カテゴリ", "Stageコード", "ＳＴＡＧＥ", "形式", "グループ", "枠名", "表示順", "参加者コード"],
                               [["男子", "L1", "予選", "L", "A", "1", 1, "1"], ["男子", "L1", "予選", "L", "A", "2", 2, "2"]])
        self.assertEqual(response.status_code, 302)
        response = self.upload("schedule", ["カテゴリ", "ステージ", "形式", "グループ", "コート", "順番", "ＰＡＩＲ１", "枠２"],
                               [["男子", "予選", "L", "A", "中央", 1, "1", "2"]])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Schedule.objects.count(), 1)

    def test_row_errors_preserve_uploaded_spelling_and_default_names(self):
        response = self.upload("participants", [" カテゴリ名 ", "番号", "選手1", "選手2"], [["", 1, "a", "b"]])
        self.assertEqual(response.context["errors"], ["2行目:  カテゴリ名 が空です。"])
        response = self.upload("stage_slots", ["カテゴリ", "Stage", "形式", "グループ", "枠名", " 表示 順 "],
                               [["男子", "予選", "L", "A", "1", "x"]])
        self.assertIn("2行目:  表示 順 は整数で入力してください。", response.context["errors"])
        response = self.upload("schedule", ["カテゴリ", "コート", " ＯＲＤＥＲ "], [["男子", "中央", "x"]])
        self.assertIn("2行目:  ＯＲＤＥＲ は整数で入力してください。", response.context["errors"])
        self.assertFalse(Category.objects.exists())

    def test_output_reordering_keeps_values_attached_to_columns(self):
        self.participants()
        self.slots()
        self.schedule()
        for kind in ("stage_slots", "schedule"):
            url = reverse("export_" + kind, args=[self.tournament.code])
            before = list(csv.DictReader(io.StringIO(self.client.get(url).content.decode("utf-8-sig"))))
            reordered = {**CSV_HEADERS, kind: tuple(reversed(CSV_HEADERS[kind]))}
            # Simulate a definition change, including the derived compatibility constant.
            with patch.dict(CSV_HEADERS, reordered), patch.dict("core.views.csv.CSV_FORMAT_HEADERS", {
                kind: output_headers(kind),
            }):
                response = self.client.get(url)
                self.assert_export_headers(response, kind, "ja")
                after = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
                self.assertEqual(after, before)

    def test_download_links_and_column_explanations(self):
        for kind in CSV_HEADERS:
            response = self.client.get(reverse("import_" + kind, args=[self.tournament.code]))
            self.assertContains(response, "英語の見出しで出力")
            self.assertContains(response, header_label(kind, "category") + "(category)")
            self.assertContains(response, "?headers=en")
        response = self.client.get(reverse("maintenance_menu", args=[self.tournament.code]))
        self.assertContains(response, "英語の見出しで出力")
