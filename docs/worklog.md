# Worklog

このファイルは、Codex がこのリポジトリでコード変更・DB操作・運用確認を行ったときに、作業内容を短く残すためのログです。

運用ルール:
- Codex が実装や実データ操作をしたら、作業の最後にこのファイルへ追記する。
- チャット全文ではなく、あとから再開するために必要な判断・変更点・確認結果を残す。
- テスト未実行や失敗があれば、その理由も書く。

## 2026-07-02

### やったこと
- カテゴリ単体スナップショット作成・直接復元を停止し、大会全体スナップショットから復元範囲を指定する運用へ整理。
- カテゴリ別スナップショット画面のテンプレートを削除し、古いURLは大会スナップショット画面へ誘導する互換動作に変更。
- 実データ `ケンコーカップ2026` を、結果未入力状態の大会 `ケンコーカップ2026_1` / `KENKOCUP2026_1` として複製。
- トーナメント結果入力に `両者リタイア` を追加。
- 両者リタイア時は `0-0`、勝者なし、リタイア結果として保存し、次試合へ勝者を送らないようにした。
- 両者リタイアで次試合が片側シードだけになった場合、そのシードを不戦通過/優勝として `winner` にする処理を追加。
- 両者リタイアやシード優勝を、トーナメント表・進行表・コート表示・メンテ画面で正しく表示するよう調整。
- 実例 `ケンコーカップ2026 / 女子A / 5-6位L-3位` で、シード勝ち上がり線が赤くならない原因を確認し、SVG赤線判定を修正。

### 主な変更ファイル
- `stpro/core/models.py`
- `stpro/core/services.py`
- `stpro/core/tournament_views.py`
- `stpro/core/snapshot_views.py`
- `stpro/core/snapshot_services.py`
- `stpro/core/templates/core/input_score.html`
- `stpro/core/templates/core/tournament_bracket_detail.html`
- `stpro/core/templates/core/_tournament_match_box.html`
- `stpro/core/templates/core/tournament_match_maintenance.html`
- `stpro/core/templates/core/tournament_schedule.html`
- `stpro/core/templates/core/court_status.html`
- `stpro/core/templates/core/schedule.html`
- `stpro/core/templates/core/maintenance_menu.html`
- `stpro/core/templates/core/category_stage_overview.html`
- `stpro/core/tests.py`

### 実データ操作
- `ケンコーカップ2026` から `ケンコーカップ2026_1` を作成。
- 複製先の確認結果:
  - カテゴリ 4
  - 参加者 136
  - Stage 35
  - グループ 64
  - リーグ枠 272
  - リーグ試合 471
  - トーナメント 46
  - トーナメント枠 112
  - トーナメント試合 80
  - コート 16
  - 日程区分 2
  - 進行枠 537
  - 進出元設定 248
- 複製先では、リーグ結果・トーナメント結果・順位表・進行状態・差し替え履歴・リタイア状態が 0 件であることを確認。
- `ケンコーカップ2026 / 女子A / 5-6位L-3位` で、`S1 1-3位 -> M2 winner 1-3位` の赤線判定が `True` になることを確認。

### 確認
- `./venv/bin/python stpro/manage.py test core.tests.CategorySnapshotTests core.tests.MaintenanceMenuTests --keepdb`
- `./venv/bin/python stpro/manage.py test core.tests.TournamentAdvancementTests --keepdb`
- `./venv/bin/python stpro/manage.py test core --keepdb`
- 最終確認時点で `core` は 156 tests OK。

### 次候補
- 大会複製を管理画面の正式機能にする。
- 両者リタイア時のPDF/帳票表示を確認する。
- 大会全体スナップショットからのカテゴリ復元・日程復元を実データで運用確認する。

### 追記: 大会複製UI化
- 大会構成を結果未入力状態で複製する `clone_tournament_without_results` を追加。
- 管理メニューから `大会複製` 画面へ移動できるようにした。
- 複製画面では、新しい大会名と大会コードを入力して複製できる。
- 複製対象:
  - カテゴリ、Stage、参加者、リーグ枠、トーナメント枠、リーグ試合、トーナメント試合、コート、日程区分、進行表、進出元設定。
- 複製しない/初期化するもの:
  - リーグ結果、トーナメント結果、順位表、呼出・試合中・完了状態、リタイア状態、差し替え履歴、スナップショット。
- 反映済みの後続Stage枠は、複製先では AdvancementSource の未反映枠として空に戻す。
- 変更ファイル:
  - `stpro/core/services.py`
  - `stpro/core/forms.py`
  - `stpro/core/views.py`
  - `stpro/core/urls.py`
  - `stpro/core/templates/core/clone_tournament.html`
  - `stpro/core/templates/core/maintenance_menu.html`
  - `stpro/core/tests.py`
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentCloneTests core.tests.MaintenanceMenuTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 158 tests OK。

### 追記: リーグ表からStage一覧への戻り導線
- リーグ表画面 `category_detail.html` の上部に `Stage一覧へ戻る` を追加。
- 試合進行表・カテゴリ一覧へのリンクも同じヘッダー内にまとめた。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.RoundRobinMeetingTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 159 tests OK。

### 追記: 当日受付UI案とコミット運用
- `TODO.md` に、リーグ試合とトーナメント試合を同じ画面で検索・入力できる当日受付用UI案を追加。
- 今後は、修正ごとに作業内容を区切ってコミットする運用に戻す。
- この追記自体はTODO/運用ログ更新のみのため、追加テストは不要。

## 2026-07-03

### トーナメントSVGのマッチラベル位置調整
- トーナメントSVGのマッチラベルを、横線と同じY座標に置いたうえで `dominant-baseline="middle"` を付けるようにした。
- これにより、SVGテキストのベースライン基準でラベルが横線より上に見える問題を補正。
- 通常マッチと左右分割レイアウトの決勝マッチラベルに適用。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_tournament_bracket_detail_shows_svg_bracket --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 159 tests OK。

### メンテナンスメニュー再構築 第1段階
- `maintenance_menu.html` を、実装単位ではなく作業目的別の構成へ再配置。
- 新しい区分:
  - 当日運営
  - Stage・結果反映
  - 大会準備
  - 表示・設定
  - 高度なメンテナンス
- 受付・結果入力、採点票出力、進行状況確認を上部に寄せた。現時点では既存の試合進行表やコート状況へリンクし、後で受付用横断検索画面を追加したら差し替えられる形にした。
- カテゴリ別のStage一覧、リーグ表、リーグ枠メンテナンスへの導線は維持。
- 通常運用では使いにくい旧リーグ枠CSV取込とトーナメント追加は「高度なメンテナンス」へ移動。
- `TODO.md` に、第1段階完了と次段階の受付・結果入力横断検索画面を追記。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.MaintenanceMenuTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 159 tests OK。

### トーナメントSVGマッチラベルのリンク復旧
- トーナメントSVGのマッチラベル/マッチコードから、結果入力画面へ移動できる導線を復旧。
- これまでは両ペアが確定している試合だけSVGラベルにリンクを付けていたが、対戦未確定の後続試合でもマッチコードから結果入力画面へ移動できるようにした。
- 通常マッチと左右分割レイアウトの決勝ラベルに適用。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_tournament_bracket_detail_draws_later_round_skeleton_lines --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 159 tests OK。

### 対戦未確定トーナメント試合の結果入力ブロック
- トーナメントSVGのマッチラベルから結果入力画面へ移動する導線は維持しつつ、対戦相手が未確定の試合では結果を保存できないようにした。
- 入力画面では未確定枠を `未確定` と表示し、スコア入力欄と保存ボタンを無効化。
- POST側でも、対戦相手未確定の新規結果保存・リタイア入力を拒否する防御を追加。既存結果の削除は復旧用途として従来どおり先に処理する。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_unresolved_tournament_match_score_input_is_disabled core.tests.TournamentScheduleBehaviorTests.test_unresolved_tournament_match_rejects_score_post --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 161 tests OK。

### TODO追記: スコアシートQR導線
- `TODO.md` に、スコアシートへQRコードを印字して結果入力画面へ移動する将来案を追記。
- QRは `コート番号 + 第何試合` ではなく、対戦カードまたは試合そのものを特定できる情報を使う方針にした。
- 理由: 印刷後にコートや試合順が変わっても、印刷済みスコアシートを使う場合があるため。
- あわせて、受付画面の主な探し方は `カテゴリ + 番号` と `コート + 第何試合` とし、選手名検索は補助導線として整理。
- ドキュメント更新のみのためテストは未実行。

### TODO追記: スコアシート電子化の将来像
- `TODO.md` に、スコアシートを電子化してリアルタイムで結果入力する将来案を追記。
- ただし、現行コンセプトでは紙のスコアシート運用を安定させることを優先し、電子化は最後の最後に検討する方針。
- QR導線、PDF採点票、誤入力防止、復元運用が固まってから着手する前提にした。
- 電子化する場合も、通信不良・端末不足・現場混雑時に紙運用へ戻れる設計を条件として記載。
- ドキュメント更新のみのためテストは未実行。

### TODO追記: スコアシート欄外検索キー
- `TODO.md` に、QRを読めない場合の補助として、スコアシート欄外へ短い検索キーを印字する将来案を追記。
- 例として、4桁程度の数字 + チェックデジットを想定。
- キーはコート番号や試合順ではなく、対戦カードまたは試合そのものを特定できる情報へ紐づける方針。
- 印刷後のコート変更・試合順変更でも別試合を指さないこと、キー衝突・再発行・スナップショット復元・大会複製時の扱いは要検討として残した。
- ドキュメント更新のみのためテストは未実行。

### 受付・結果入力検索画面 第1段階
- `受付・結果入力` 画面を追加し、管理メニューの当日運営入口から移動できるようにした。
- 検索方法:
  - `カテゴリ + 番号`
  - `コート + 第何試合`
- 検索結果はリーグ試合とトーナメント試合を同じ表に並べ、カテゴリ、Stage、リーグ/トーナメント、試合、対戦、進行枠、状態、スコア、結果入力/確認リンクを表示。
- `カテゴリ + 番号` では、リーグ枠の `pair_code` とトーナメント枠の `pair_code/display_order` に紐づく試合を検索する。
- `コート + 第何試合` では、進行表の `Schedule` から該当するリーグ/トーナメント試合を検索する。
- 対戦未確定のトーナメント試合は、検索結果から確認画面へ移動できるが、結果入力画面側で保存不可のままにしている。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.ReceptionMatchSearchTests core.tests.MaintenanceMenuTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 165 tests OK。

### TODO追記: 受付検索画面の位置づけ見直し
- 受付検索画面を少し使った結果、フォーム検索は1回戦目など受付が押しかける時間帯の主導線としては重い可能性が高いと判断。
- 現行検索画面は、迷った時・例外対応・進行表と紙が合わない時の補助導線として据え置く方針を `TODO.md` に追記。
- 混雑時の主導線は、進行表から直接押す導線、短い検索キー、QR読み取りを組み合わせて育てる方針。
- ドキュメント更新のみのためテストは未実行。

### 進行表の受付向け表示改善
- 進行表セルを受付向けの試合カード風表示へ変更。
- コート名と第何試合、状態、カテゴリ、リーグ/トーナメント名、対戦、結果入力、採点票PDF、リーグ表/トーナメント表への導線を見やすくした。
- Stage名は内部上の文脈情報として小さく表示し、運営者が押すべき入口は結果入力・表移動を前面に出した。
- `TODO.md` に、Stageは内部実装上は必要だが、運営画面では「どこの結果がどこの枠に入るか」という作業感覚へ寄せる方針を追記。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_schedule_view_shows_reception_focused_match_cards core.tests.TournamentScheduleBehaviorTests.test_unresolved_tournament_match_schedule_score_sheet_redirects --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 166 tests OK。

### 進行表ヘッダの採点票リンク削除
- 進行表のコート見出しに付いていたコート別採点票PDFリンクを削除し、ただのコート名表示に戻した。
- 当初はコート単位で採点票をまとめて出す入口として置いていたが、現在の運用意図と出力内容がずれており、誤操作のもとになるため。
- 試合セル内の個別 `採点票PDF` リンクは維持。
- リーグ全試合分やトーナメント1回戦分を一括出力する導線は、今後あらためて設計する。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_schedule_view_shows_reception_focused_match_cards --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 166 tests OK。

### 採点票PDF一括出力の明示入口追加
- 管理メニューの当日運営に、`リーグ採点票一括` と `トーナメント1回戦採点票一括` を追加。
- `リーグ採点票一括` は、大会内で進行表に登録されたリーグ試合を、日程区分、コート、試合順で並べてPDF出力する。
- `トーナメント1回戦採点票一括` は、大会内で進行表に登録されたトーナメント1回戦を、日程区分、コート、試合順で並べてPDF出力する。
- 対戦相手が未確定の試合は採点票にできないためスキップする。
- 進行表ヘッダの隠れリンクではなく、採点票出力として明示的な入口にした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.BulkScoreSheetPdfTests core.tests.MaintenanceMenuTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 168 tests OK。

### TODO追記: 採点票一括出力の範囲選択
- `TODO.md` に、採点票PDF一括出力は範囲選択できないと実運用では粗いという判断を追記。
- 最初の予選リーグは事前に全試合を印刷できるが、リーグ結果から次リーグへ進む場合は、結果反映状況によって印刷可能な範囲が変わる。
- 今後は、カテゴリ、リーグ、勝ち上がり先の枠、トーナメント表、日程区分、コートなどから出力範囲を選べる画面を検討する。
- 選択画面では、印刷可能な試合数、対戦未確定でスキップされる試合数、進行表未配置の試合数を事前確認できるようにする。
- ドキュメント更新のみのためテストは未実行。

### Django設定パッケージ名変更
- Djangoプロジェクト設定ディレクトリを `stpro/mysite` から `stpro/config` にリネーム。
- `manage.py`、`settings.py`、`asgi.py`、`wsgi.py`、`urls.py` 内の `mysite` 参照を `config` に変更。
- 確認:
  - `./venv/bin/python stpro/manage.py check`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 166 tests OK。
