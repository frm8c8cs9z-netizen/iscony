# View / Helper 構造整理 詳細設計メモ

このメモは、`iscony` の view / helper / service 周りを整理するための詳細設計です。

目的は、今後予定している「手入力での大会作成」「参加者・所属DB整理」「指定審判」「変更履歴」「表示ブロック設定画面化」を進める前に、既存コードの責務を見通しやすくすることです。

## 背景

現在の実装は、機能追加を優先して育ってきたため、次のような状態になっています。

- `views.py` に大会トップ、進行表、試合選択、設定、複製、進行メンテナンスなどが混在している。
- `tournament_views.py` に、トーナメント画面、SVG描画、分割トーナメント、結果入力、編集、CSV、進行表、採点票導線が同居している。
- `league_views.py` に、リーグ表表示、リーグ結果入力、順位計算、同率補助表、リタイア、枠メンテナンスが同居している。
- `forms.py` に、CSV、検索、リーグ、トーナメント、進行、設定、複製のフォームがまとめて入っている。
- `services.py` に、後続Stage反映、リーグ結果保存、トーナメント結果保存、試合順変更など複数の業務ロジックが入っている。
- `view_helper.py` は結果入力の戻り先や共通レンダリングを担当しているが、今後増えると名前と責務が曖昧になりやすい。

このまま大会作成画面や変更履歴を足すと、どの画面が何を変更しているのか追いにくくなります。
そのため、まずは挙動を変えずに、責務の境界を整理します。

## 整理の原則

- 最初はDB変更をしない。
- 最初は画面挙動を変えない。
- URL name は維持する。
- テンプレート名は原則維持する。
- 大きなファイルを一気に動かさず、機能単位で小さく移す。
- 1回の整理ごとにテストを通す。
- 表示調整と構造整理を同じコミットに混ぜない。
- 既存の公開画面と管理画面の差分は維持する。
- 共通化は「同じ責務」だけに限定し、片山/両山のように自然な差分があるものは無理に潰さない。

## 最終的なディレクトリ方針

最終的には次のような構成を目指します。

```text
iscony/core/
  urls.py
  models.py

  views/
    __init__.py
    tournament_pages.py
    category_pages.py
    schedule_pages.py
    result_input_pages.py
    csv_import_pages.py
    public_pages.py
    snapshot_pages.py
    settings_pages.py
    maintenance_pages.py
    advancement_pages.py

  forms/
    __init__.py
    csv_forms.py
    league_forms.py
    tournament_forms.py
    schedule_forms.py
    settings_forms.py
    search_forms.py

  services/
    __init__.py
    advancement.py
    league_results.py
    league_ranking.py
    tournament_results.py
    tournament_generation.py
    schedule_operations.py
    clone.py
    snapshots.py
    score_sheets.py
    csv_imports.py

  rendering/
    __init__.py
    league_table.py
    tournament_svg.py
    tournament_split.py
    svg_text_blocks.py
    entry_display.py

  selectors/
    __init__.py
    tournament_queries.py
    league_queries.py
    schedule_queries.py
    stage_queries.py
```

ただし、いきなり `core/views/` や `core/forms/` ディレクトリを作ると、既存の `core/views.py`、`core/forms.py` と名前が衝突します。
そのため、移行は段階的に行います。

## 移行フェーズ

### Phase 1: rendering / services / selectors を先に切り出す

最初に view ファイルの中から、画面ではない処理を外に出します。

この段階では、既存の `views.py`、`league_views.py`、`tournament_views.py` は残します。
URLも変えません。

切り出し候補:

- SVG描画
- トーナメント分割データ作成
- 表示テキストブロック
- リーグ表用データ作成
- 試合検索
- Stage状態/Notice計算
- 後続Stage反映
- 採点票PDF用データ取得

狙い:

- view は request / form / redirect / template context に集中させる。
- 描画と業務ロジックを view から外す。
- 以後の画面追加で、既存の巨大viewを直接触る回数を減らす。

### Phase 2: forms を用途別に分ける

`forms.py` が肥大化しているため、用途別に分けます。

ただし `core/forms.py` と `core/forms/` は共存できないため、次のどちらかで進めます。

案A:

```text
core/form_defs/
```

を先に作り、既存 `forms.py` から import する。
安定後に `forms.py` を `forms/` パッケージへ移す。

案B:

一度で `forms.py` を削除し、`forms/` パッケージへ移行する。
既存 import の差し替え範囲が広くなるため、テストが十分あるタイミングで行う。

第一候補は案Aです。
理由は、今の機能を壊さずに少しずつ移せるためです。

### Phase 3: views を用途別に分ける

`core/views.py` が存在する間は `core/views/` パッケージにできないため、まずは現行の分割済み view をさらに整理します。

短期方針:

- `views.py` から、設定、進行、検索、複製を別モジュールへ出す。
- `tournament_views.py` から、SVG描画とCSV系を出し、画面関数だけに寄せる。
- `league_views.py` から、ランキング計算、表データ作成、結果保存を外へ出す。

安定後:

```text
core/page_views/
```

または

```text
core/views/
```

へ移行します。

`core/views/` にする場合は、既存 `core/views.py` をなくす必要があります。
そのタイミングでは import 差し替えが大きくなるため、ひとまとまりのリファクタコミットとして扱います。

## 現状ファイル別の整理案

## `views.py`

現状の主な責務:

- 大会一覧
- 大会詳細
- 公開大会トップ
- 進行表
- 公開進行表
- 管理メニュー
- 試合選択
- QR/キー検索
- 大会設定
- 大会複製
- 進行メンテナンス
- 試合順変更
- カテゴリ追加
- コート表示順

代表的な関数:

- `tournament_list`
- `tournament_detail`
- `public_tournament_detail`
- `court_status`
- `schedule_view`
- `maintenance_menu`
- `result_input_select`
- `reception_match_search`
- `tournament_settings`
- `clone_tournament_view`
- `schedule_maintenance`
- `edit_schedule`
- `order_maintenance`
- `update_schedule_status`
- `move_schedule_view`
- `add_category`
- `court_order_maintenance`

移動先の方針:

| 現在の処理 | 移動先候補 | 理由 |
| --- | --- | --- |
| 大会一覧/大会詳細 | `views/tournament_pages.py` | 大会トップ系に限定する |
| 公開大会トップ | `views/public_pages.py` | 公開URLと管理URLを分ける |
| 進行表/公開進行表 | `views/schedule_pages.py` | 当日運営の中心機能として独立させる |
| 試合選択/QR検索 | `views/result_input_pages.py` | 結果入力導線として独立させる |
| 大会設定 | `views/settings_pages.py` | 設定画面として独立させる |
| 大会複製 | `services/clone.py` + `views/maintenance_pages.py` | 複製処理と画面を分ける |
| 進行メンテナンス/移動 | `services/schedule_operations.py` + `views/schedule_pages.py` | 変更履歴やundoと接続しやすくする |
| カテゴリ追加/順序 | `views/category_pages.py` | カテゴリ操作として独立させる |

優先して切り出すもの:

1. 試合選択/QR検索
2. 進行表/進行メンテナンス
3. 大会設定
4. 大会複製

理由:

今後、結果入力のモーダル化、QR、指定審判、進行変更履歴がここに集中するためです。

## `tournament_views.py`

現状の主な責務:

- トーナメントSVG描画
- SVGテキストブロック
- 分割トーナメント表示
- 上位トーナメント表示
- トーナメント詳細画面
- トーナメント結果入力
- トーナメント枠/試合編集
- トーナメント試合生成
- トーナメントCSV取込
- トーナメント採点票
- トーナメント進行表
- トーナメント一覧/個別設定

代表的な関数:

- `_svg_text_block_dimensions`
- `_svg_text_block_spec`
- `_append_svg_text_block_label`
- `_append_svg_entry_block_label`
- `_append_svg_entry_code_label`
- `_append_svg_match_code_label`
- `_append_svg_score_label`
- `_build_svg_bracket_data`
- `build_tournament_bracket_display_data`
- `tournament_bracket_detail`
- `input_tournament_match_score`
- `tournament_match_maintenance`
- `edit_tournament_match`
- `edit_tournament_entry`
- `generate_tournament_matches`
- `import_bracket_seeds`
- `import_bracket_positions`
- `tournament_match_score_sheet`
- `tournament_schedule_view`
- `bracket_list`
- `add_tournament_bracket`
- `edit_tournament_bracket`
- `import_tournament_schedule`
- `add_tournament_match_schedule`

移動先の方針:

| 現在の処理 | 移動先候補 | 理由 |
| --- | --- | --- |
| SVGテキストブロック | `rendering/svg_text_blocks.py` | 参加者、優勝者、match label、score labelで共通化する |
| SVG描画本体 | `rendering/tournament_svg.py` | 表示ロジックをviewから分離する |
| 分割/上位トーナメント整形 | `rendering/tournament_split.py` | SVGに渡すデータ整形だけを担当する |
| トーナメント表示データ作成 | `selectors/tournament_queries.py` または `rendering/tournament_svg.py` | DB取得と描画用整形を分ける |
| 結果保存/勝ち上がり | `services/tournament_results.py` | 結果入力、Bye、勝ち上がり、自動反映と接続する |
| 試合生成 | `services/tournament_generation.py` | CSV/手入力作成の両方から使えるようにする |
| CSV取込 | `views/csv_import_pages.py` + `services/csv_imports.py` | 古いCSV整理と手入力作成に備える |
| トーナメント設定画面 | `views/settings_pages.py` | 大会設定/ブラケット設定と責務を揃える |

最優先で切り出すもの:

1. `rendering/svg_text_blocks.py`
2. `rendering/tournament_svg.py`
3. `rendering/tournament_split.py`

理由:

トーナメントSVGは最近の修正が集中しており、今後も表示ブロック、線、スコア、分割、上位トーナメントの調整が続くためです。
描画部品を独立させると、画面導線や編集画面の変更からSVGを守りやすくなります。

## `league_views.py`

現状の主な責務:

- カテゴリ詳細
- リーグ表データ作成
- 同率補助表
- リーグ試合生成
- 追加対戦
- リーグ結果入力
- 順位計算
- 順位手入力
- リタイア/取消
- リーグ枠メンテナンス

代表的な関数:

- `build_category_group_data`
- `_build_round_robin_tie_tables`
- `category_detail`
- `generate_group_matches`
- `add_extra_round_robin_match`
- `input_match_score`
- `calculate_category_ranking`
- `edit_group_ranking`
- `retire_pair`
- `league_entry_action`
- `cancel_retire_pair`
- `pair_maintenance`
- `edit_pair`

移動先の方針:

| 現在の処理 | 移動先候補 | 理由 |
| --- | --- | --- |
| リーグ表データ作成 | `rendering/league_table.py` | 管理/公開/横断表示で条件を揃えやすくする |
| 同率補助表 | `services/league_ranking.py` + `rendering/league_table.py` | 計算と表示を分ける |
| 結果保存 | `services/league_results.py` | 結果変更時の順位再計算、自動反映と接続する |
| 追加対戦 | `services/schedule_operations.py` または `services/league_results.py` | 進行表変更履歴と接続する |
| リタイア | `services/league_results.py` | ランク再計算、後続反映消し戻しと連動する |
| 枠編集 | `views/maintenance_pages.py` | 通常運用と高度操作を分ける |

優先して切り出すもの:

1. リーグ表データ作成
2. 同率計算/順位確定判定
3. 結果保存後の再計算/通知

理由:

リーグのNotice、自動反映、手入力順位クリア、同率未確定通知がつながっているためです。

## `stage_views.py`

現状の主な責務:

- カテゴリ横断表示
- 公開カテゴリ結果
- Stage状態計算
- 後続反映状況
- Notice/未確定通知
- リーグ/トーナメント表示データの集約

代表的な関数:

- `_league_stage_data`
- `_tournament_stage_data`
- `_pending_advancement_source_count`
- `_public_stage_status`
- `_advancement_data`
- `_stage_notices`
- `_stage_blockers`
- `_build_category_stage_rows`
- `category_stage_overview`
- `tournament_stage_overview_index`
- `public_category_results`

移動先の方針:

| 現在の処理 | 移動先候補 | 理由 |
| --- | --- | --- |
| Stage状態判定 | `services/stage_status.py` | 管理/公開/Noticeで共通利用する |
| Notice生成 | `services/stage_notices.py` | 表示文言と判定を分ける |
| 横断表示データ | `selectors/stage_queries.py` | DB取得と表示組み立てを分ける |
| 公開カテゴリ結果 | `views/public_pages.py` | 公開表示として切り出す |
| 管理横断表示 | `views/category_pages.py` | 管理のカテゴリ作業画面に寄せる |

このファイルは比較的整理されているため、`tournament_views.py` のSVG切り出し後に進めます。

## `services.py`

現状の主な責務:

- 大会複製
- 後続Stage反映
- AdvancementSource入れ替え
- 追加リーグ試合
- 進行表挿入/移動/undo
- リーグ順位更新
- リーグ結果保存/削除
- リタイア取消
- トーナメント結果保存/削除
- Bye勝ち上がり
- トーナメント結果変更バリデーション

代表的な関数:

- `clone_tournament_without_results`
- `inspect_stage_advancement_readiness`
- `auto_apply_stage_advancements_if_ready`
- `apply_ready_stage_advancements`
- `apply_stage_advancements`
- `swap_advancement_sources`
- `create_extra_round_robin_match`
- `insert_round_robin_schedule`
- `undo_schedule_replacement`
- `move_schedule`
- `update_group_ranking`
- `save_round_robin_score`
- `advance_tournament_single_entry_winners`
- `advance_tournament_bye_winners`
- `save_tournament_score`
- `save_tournament_retirement`
- `validate_tournament_score_change`

移動先の方針:

| 現在の処理 | 移動先候補 |
| --- | --- |
| 大会複製 | `services/clone.py` |
| 後続Stage反映 | `services/advancement.py` |
| リーグ結果/順位 | `services/league_results.py`, `services/league_ranking.py` |
| トーナメント結果/勝ち上がり | `services/tournament_results.py` |
| トーナメント生成/Bye | `services/tournament_generation.py` |
| 進行表移動/undo | `services/schedule_operations.py` |

この分割は、変更履歴モデルを導入するときに特に効きます。
どの操作が履歴対象かを service 単位で判断しやすくなるためです。

## `forms.py`

現状の主なフォーム:

- `CSVUploadForm`
- `ExtraRoundRobinMatchForm`
- `ReceptionMatchSearchForm`
- `LeagueEntryEditForm`
- `TournamentEntryEditForm`
- `ScheduleEditForm`
- `TournamentMatchEditForm`
- `BracketGenerateForm`
- `ScheduleCreateForm`
- `ScheduleMoveForm`
- `CategoryForm`
- `TournamentSettingsForm`
- `ScheduleBlockSettingsForm`
- `TournamentCloneForm`
- `TournamentBracketForm`

移動先の方針:

| 現在のフォーム | 移動先候補 |
| --- | --- |
| CSVUploadForm | `form_defs/csv_forms.py` |
| ReceptionMatchSearchForm | `form_defs/search_forms.py` |
| LeagueEntryEditForm, ExtraRoundRobinMatchForm | `form_defs/league_forms.py` |
| TournamentEntryEditForm, TournamentMatchEditForm, TournamentBracketForm, BracketGenerateForm | `form_defs/tournament_forms.py` |
| ScheduleEditForm, ScheduleCreateForm, ScheduleMoveForm, ScheduleBlockSettingsForm | `form_defs/schedule_forms.py` |
| CategoryForm, TournamentSettingsForm, TournamentCloneForm | `form_defs/settings_forms.py` |

短期的には `forms.py` を互換レイヤとして残し、各分割フォームを再exportします。

```python
from .form_defs.csv_forms import CSVUploadForm
from .form_defs.search_forms import ReceptionMatchSearchForm
```

この形なら既存 import を大きく壊さずに段階移行できます。

## URL整理方針

現状は `core/urls.py` に全URLがまとまっています。
まずは URL name を変えず、import 先だけ少しずつ変更します。

将来的には次のように include 分割します。

```text
core/urls.py
  -> core/urls_tournament.py
  -> core/urls_league.py
  -> core/urls_schedule.py
  -> core/urls_result_input.py
  -> core/urls_public.py
  -> core/urls_import.py
  -> core/urls_snapshot.py
  -> core/urls_maintenance.py
```

ただし、URL分割は view 分割の後でよいです。
先にURLだけ分けても、巨大viewの責務は減らないためです。

## SVG描画の整理詳細

SVGは直近の修正が多く、今後も大会ごとの表示設定に発展するため、特に丁寧に分けます。

### 目標

- 参加者表示、優勝者表示、進出元仮表示、entry code、match label、score label をすべて「テキストブロック」として扱う。
- テキストブロックは、行数、縦書き/横書き、フォントサイズ、行間、余白からサイズを計算する。
- 配置は、TopLeft、TopCenter、TopRight、MiddleLeft、MiddleRight、BottomLeft、BottomCenter、BottomRight のアンカーを使う。
- 描画側は「基準点 + offset_x / offset_y + anchor」で配置できるようにする。
- 片山/両山の差分は「どの基準点にどのアンカーで置くか」に閉じ込める。

### 切り出し候補

```text
rendering/svg_text_blocks.py
  - SvgTextBlockLayout
  - TextBlockSpec
  - block dimensions
  - block bounds
  - anchor origin
  - horizontal / vertical text append

rendering/tournament_svg.py
  - SVG全体描画
  - 線レイヤ
  - 結果ラインレイヤ
  - ラベルレイヤ
  - スコアレイヤ

rendering/tournament_split.py
  - 分割ブラケット用 round_data 作成
  - 上位トーナメント用 round_data 作成
  - split_count / Bye 含めた足数調整
```

### 注意点

- 両山表示の中央付近には、片山表示で必要な「準決勝から決勝へ立ち上がる横線」と同じ見た目の短い線が出やすい。
- 両山表示では、その短い横線が不要な場合がある。
- 以前からこの線がはみ出しや切り欠きの原因になりやすいため、描画関数内にコメントを残す。
- 黒の通常線と赤の結果線はレイヤーを分け、通常線を先、結果線を後に描画する。
- ただし、線を消す制御は片山/両山を混ぜず、layout type ごとの条件に閉じ込める。
- 分割トーナメントも上位トーナメントも、できるだけ通常トーナメントと同じ round_data として渡す。
- 上位トーナメントだけ特別座標で描くのではなく、進出元未確定の参加者ラベルだけを通常参加者の代替データとして扱う。

## 表示ブロック設定画面化への接続

今回のリファクタは、将来の表示設定画面化の土台です。

将来的に大会設定またはトーナメント設定で扱う項目:

- 参加者表示項目
  - entry code
  - 氏名
  - 短縮名
  - 所属
  - 複数所属
- 優勝者表示項目
  - 氏名
  - 所属
  - entry code
- 進出元仮表示
  - slot_label
  - match label + 勝者/敗者
  - ブラケット名 + 分割番号
- 配置
  - 横書き/縦書き
  - 1行/2行/3行
  - anchor
  - offset_x
  - offset_y
  - 線からの距離
- 見た目
  - フォントサイズ
  - フォント色
  - 太字
  - 行間
  - ブロック内余白
- 余白
  - entry code と参加者表示の距離
  - 参加者表示とトーナメントラインの距離
  - 優勝者表示と勝ち上がり線の距離
  - match label と線の距離
  - score label と線の距離

`offset_x` / `offset_y` の意味:

- `offset_x` は、基準点から見た水平方向の補正値。
- 正の値は右、負の値は左へ移動する。
- `offset_y` は、基準点から見た垂直方向の補正値。
- SVGでは上が原点なので、正の値は下、負の値は上へ移動する。
- offset は、テキストブロックの左上座標ではなく、指定した anchor の基準点に対して適用する。

## 手入力大会作成への接続

構造整理後に、手入力大会作成へ進みます。

その前に必要なDB整理:

- 選手ごとの所属
- フルネームと短縮表示名
- 当初エントリーと当日出場者の分離
- 変更履歴
- 指定審判
- 採点票テンプレート
- 表示ブロック設定
- `slot_label` / `entry_code` / `pair_code` の責務整理
- Byeの勝ち上がり情報を結果ではなく構造情報として扱う

これらは `docs/manual_tournament_setup_flow.md` と連動します。

## テスト方針

リファクタ後も次を守るテストを維持します。

### SVG

- 通常トーナメントが表示される。
- 片山表示で、最終勝者の横線が残る。
- 両山表示で、不要な中央の短い横線が出ない。
- 黒線を描いた後に赤線が前面に出る。
- シード/Bye勝ち上がりが赤線で出る。
- シード選手が初戦で負けた場合、不要なBye勝ち上がり赤線は出ない。
- 分割トーナメントが表示される。
- 上位トーナメントが未確定でも足数分表示される。
- 上位トーナメントの片側だけ確定しても表示が崩れない。
- 優勝者ラベルが縦書き/横書きで見切れない。

### リーグ

- 管理側リーグ表で、試合セル全体から結果入力へ進める。
- 公開側リーグ表で、試合セル全体から進行情報へ進める。
- 自分対自分セル以外の未入力セルに不要な `-` が出ない。
- 結果修正時に順位が再計算される。
- 手入力順位は結果修正時にクリアされる。
- 同率で自動確定できない場合のみNotice対象になる。

### 後続Stage反映

- リーグのグループ単位で順位が確定したら後続へ反映できる。
- トーナメントの勝敗が確定したら後続へ反映できる。
- 元結果を修正したとき、自動反映済みの後続枠を消し戻す。
- 後続側の該当試合に結果がある場合は消さずに警告する。
- 一部がロックされた場合、同じ参加者が複数枠に反映されない。
- 自動反映時にスナップショットが作成される。

### 導線

- 横断表示からリーグ結果入力後、横断表示へ戻る。
- 横断表示からトーナメント結果入力後、横断表示へ戻る。
- QR/キー検索から結果入力後、検索画面へ戻る。
- 対戦相手未確定の結果入力画面から戻れる。
- 管理メニュー、カテゴリ、試合選択、進行表の主導線が重複しすぎない。

## 実行順

### Step 1: SVGテキストブロック切り出し

対象:

- `SvgTextBlockLayout`
- `_svg_text_block_dimensions`
- `_svg_vertical_text_block_dimensions`
- `_svg_horizontal_text_block_dimensions`
- `_svg_block_anchor_origin`
- `_svg_text_block_spec`
- `_append_svg_text_block_label`
- `_append_svg_horizontal_block_label`
- `_append_svg_vertical_block_label`

移動先:

```text
iscony/core/rendering/svg_text_blocks.py
```

確認:

- SVG既存テスト
- 県総体_6 の分割トーナメント片山/両山
- 県総体_7 の上位トーナメント未確定表示

### Step 2: トーナメントSVG描画切り出し

対象:

- `_build_svg_bracket_data`
- `_add_svg_match`
- `_add_svg_champion_label`
- `_append_svg_entry_block_label`
- `_append_svg_entry_code_label`
- `_append_svg_match_code_label`
- `_append_svg_score_label`
- 線描画関連

移動先:

```text
iscony/core/rendering/tournament_svg.py
```

確認:

- 通常トーナメント
- 分割トーナメント
- 上位トーナメント
- 片山/両山
- score display mode
- champion display mode

### Step 3: トーナメント分割データ切り出し

対象:

- `_is_power_of_two`
- `_split_svg_round_data`
- `_normalize_svg_round_data`
- `_build_split_winner_round_data`

移動先:

```text
iscony/core/rendering/tournament_split.py
```

確認:

- 2分割
- 4分割
- 8分割
- 結果未入力の上位トーナメント
- 一部のみ勝者確定した上位トーナメント

### Step 4: リーグ表データ切り出し

対象:

- `build_category_group_data`
- `_build_round_robin_tie_tables`
- `_build_round_robin_tie_row_scores`

移動先:

```text
iscony/core/rendering/league_table.py
```

確認:

- 管理側リーグ表
- 公開側リーグ表
- 横断表示リーグ表
- 同率補助表
- ランク自動表示

### Step 5: 結果保存 service 分割

対象:

- リーグ結果保存/削除
- トーナメント結果保存/削除
- Bye勝ち上がり
- リタイア
- 順位再計算

移動先:

```text
iscony/core/services/league_results.py
iscony/core/services/tournament_results.py
```

確認:

- 結果入力
- 結果修正
- リタイア
- 後続Stage反映
- スナップショット

### Step 6: 進行表 service 分割

対象:

- 試合順変更
- コート変更
- 追加試合
- undo
- 指定審判追加予定の受け皿

移動先:

```text
iscony/core/services/schedule_operations.py
```

確認:

- 進行表表示
- 進行メンテナンス
- 試合順変更
- 追加対戦

### Step 7: forms 分割

対象:

- CSV
- 検索
- リーグ
- トーナメント
- 進行
- 設定

移動先:

```text
iscony/core/form_defs/
```

確認:

- 各画面のGET/POST
- CSV取込
- 設定保存
- 編集フォームの選択肢制限

### Step 8: view 分割

対象:

- `views.py`
- `league_views.py`
- `tournament_views.py`

移動先:

```text
iscony/core/page_views/
```

または、最終的に `core/views.py` をなくして:

```text
iscony/core/views/
```

確認:

- URL name 維持
- 既存画面遷移
- テンプレート解決
- 公開URL
- 管理メニュー

## 当面やらないこと

このリファクタでは、次はまだ実装しません。

- ユーザ所有化
- 権限管理
- 参加者DBの本格変更
- 指定審判DB追加
- 採点票テンプレートDB化
- 表示ブロック設定画面
- 手入力大会作成画面
- 旧CSVの完全削除

ただし、これらを後で足しやすくするために、責務の境界だけ先に整えます。

## 完了条件

この整理が一段落したと言える状態:

- `tournament_views.py` からSVG描画の大半が外れている。
- `league_views.py` からリーグ表データ作成の大半が外れている。
- `views.py` から試合選択、進行表、設定、複製が別責務へ分かれている。
- `forms.py` は互換レイヤになり、実体は用途別ファイルへ移っている。
- `services.py` は巨大な単一ファイルではなく、業務単位に分かれている。
- URL name と画面表示は既存のまま維持されている。
- 既存テストが通る。
- 県総体系の実データで、トーナメント分割、上位トーナメント、Bye勝ち上がり、片山/両山表示が大きく崩れない。

