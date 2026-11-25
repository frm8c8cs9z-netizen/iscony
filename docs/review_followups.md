# コードレビューの残課題

2026-09-08 の全体レビュー(当時の資料は廃棄)で指摘した項目のうち、**2026-11-25 時点で残っているもの**だけをまとめる。
どれも急ぎではない。手を付けるときは、今のコードでまだ当てはまるかを先に確かめること。

## 解消済み(記録のみ)

- 認証がない → `IsconyLoginRequiredMiddleware` で全画面ログイン必須。
- 秘密鍵・DEBUG・ALLOWED_HOSTS の直書き → 本番は `config/local_settings.py`(Git管理外)で上書きする。
- 参加者CSV取込が途中失敗で名簿を欠く → 5e3497f でトランザクション化。
- トーナメント表生成の二重実装 → `services/stage_setup.py` の `generate_tournament_bracket_matches` に一本化。
- 進行表CSV再取込が進行中の試合を消す → トランザクション化し、呼出・開始・終了の状態を引き継ぐ。再取込のロックもある。
- CSV出力の数式インジェクション → `_sanitize_csv_cell`。
- requirements がない → `requirements.txt` / `requirements-dev.txt`。
- シード配置CSV取込 → 機能ごと廃止。

## 残っているもの

### 実害につながりうるもの

| 項目 | 場所 | 内容 | メモ |
|---|---|---|---|
| GETで書き込む | `views/league.py` `generate_group_matches` / `calculate_category_ranking` | リンクを開くだけで総当たり試合の生成・順位の再計算をする | 入口は `category_detail` だけで、メニューから入れない画面。直すならPOSTにする |
| CSVの大きさの上限がない | `views/csv.py` `_read_csv_rows` ほか | アップロードの大きさ・拡張子を確かめていない | ログイン必須になり危険は下がった |
| 棄権の途中失敗に備えがない | `views/league.py` `retire_pair` | トランザクションも `select_for_update` もない | POSTのみ受け付ける |
| 内部の例外文言を画面に出す | `views/csv.py` `import_stage_slots` の `except Exception` | 想定外の例外まで捕まえて、そのまま表示する | 捕まえる例外を絞る |
| 試合キーの重複をDBで防いでいない | `models.py` の `match_key` | 索引のみで、重複を禁止する制約がない | 採番処理で重複しない前提 |
| 本番HTTPS向けの設定 | `config/local_settings.example.py` | `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` がコメントアウトで例示されているだけ。HSTSなどはない | 本番の `local_settings.py` で確かめる |
| 棄権確定のエラー処理の非対称 | `services/operations.py` `save_tournament_retirement` / `cancel_pair_retirement` | 結果保存の兄弟関数と違い、後続Stage反映を `try/except ValidationError` で囲んでいない | 通常の処理では到達しないと確認済み。揃えるだけの話 |

### 構造の整理(大きな変更のついでに)

- `services/operations.py` が大きすぎる(4000行超、複数の領域が混在)。
- `views/csv.py` の `import_stage_slots` に、業務の処理が直接書かれている。
- 採点票・PDF生成の処理に重複がある。`_show_stage_advancement_warning` などにも小さな重複がある。
- 所属の値を保存する処理が2つある。`views/csv.py` `_save_participant_organization_values` と `services/participants.py` `save_participant_organization_values` で、選手ごとに違う値を許すかどうかの仕様差もある。
- `Participant` の旧カラム(`organization` / `player1_name` / `player2_name`)を直接読み書きする箇所が多い。
- `display_order` を持つモデルの一部に `Meta.ordering` がない。
- 「1 Stage = 1競技方式」や `LeagueEntry.category` と `group.category` の一致を、DB制約や `clean()` で守っていない。
- `_round_robin_main_row.html` に `|safe` がある。今は安全な値しか渡していない。
- `LeagueEntry` のテーブル名が `core_pair` のまま。
- selectors 層への読み取り処理の集約が進んでいない(足の番号・採点票などで使い始めた)。
