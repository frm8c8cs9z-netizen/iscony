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

### TODO追記: 大会オーナーと当日オペレーター権限
- `TODO.md` のユーザ・権限管理に、大会オーナーと当日オペレーターの役割分担を追記。
- 大会オーナーは、大会設定、参加者、リーグ/トーナメント枠、追加試合、CSV取込、スナップショット復元など大会構造に関わる操作を担当する想定。
- 当日オペレーターは、得点記録、呼出/試合中/完了、進行による試合順変更、採点票出力など当日の運営操作を担当する想定。
- 追加試合やリタイア差し替えのような中間操作は、どちらの権限に含めるか要検討として残した。
- ドキュメント更新のみのためテストは未実行。

### TODO追記: タブレット運用と表示崩れ防止
- `TODO.md` の管理UIに、PCだけでなくタブレットでも当日オペレーションを回せるようにする横断課題を追記。
- 対象は、進行表、リーグ表、トーナメント表、結果入力画面、受付検索画面、採点票出力画面。
- 進行表・リーグ表・トーナメント表は横幅が大きいため、横スクロール、固定幅、折り返し、表示優先順位を調整し、文字や操作ボタンが重ならないことを重視する。
- タップ操作を前提に、結果入力、採点票PDF、呼出/試合中/完了、移動などのボタンサイズと間隔も確認する。
- 将来的にはPlaywright等でタブレット相当幅のスクリーンショット確認を行い、表示崩れを検出できるようにする。
- ドキュメント更新のみのためテストは未実行。

### TODO追記: 進行表の操作モード切り替え
- `TODO.md` に、タブレット操作を前提とした進行表のモード切り替え案を追記。
- 例として、`採点入力モード`、`進行状態モード`、`試合順変更モード`、`採点票出力モード` を想定。
- 採点入力モードでは試合カードのタップで結果入力へ進み、進行状態モードでは呼出/試合中/完了を操作する。
- 試合順変更モードでは、ドラッグアンドドロップまたはタップ選択で試合順・コート割を変更できるようにする。
- モード分離により、採点入力中に誤って試合順を動かす、進行状態を誤更新する、といった事故を防ぐ狙い。
- ドキュメント更新のみのためテストは未実行。

### TODO追記: 採点票OCRとアナリティクス
- `TODO.md` に、低優先の将来機能として、紙の採点票を撮影してOCRで結果やゲームごとのカウントをデータ化する案を追記。
- 目的は当日運営の主導線ではなく、証跡保存、入力内容の照合、試合内容のアナリティクス。
- OCR対象は、最終スコア、各ゲームのカウント、リタイア/棄権などの記載、可能であれば署名や確認印の有無。
- 読み取り結果は自動反映せず、人間が確認・修正してから保存する前提。
- QRや欄外検索キーと組み合わせ、撮影画像を対象試合へ紐づける方針。
- ドキュメント更新のみのためテストは未実行。

### Django設定パッケージ名変更
- Djangoプロジェクト設定ディレクトリを `stpro/mysite` から `stpro/config` にリネーム。
- `manage.py`、`settings.py`、`asgi.py`、`wsgi.py`、`urls.py` 内の `mysite` 参照を `config` に変更。
- 確認:
  - `./venv/bin/python stpro/manage.py check`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 166 tests OK。

### 採点票一括出力の範囲選択
- 管理メニューの採点票出力入口を、直接PDFではなく `採点票一括出力` の範囲選択画面へ変更。
- 選択画面では、リーグ全体、トーナメント1回戦全体、リーグ別、トーナメント別/回戦別の出力範囲を表示する。
- 各範囲に `印刷可能`、`未確定で除外`、`未配置` の件数を出し、PDFを開く前に出力対象を確認できるようにした。
- 既存のリーグ一括PDFは `group` クエリでリーグ別に絞り込み可能にした。
- 既存のトーナメント1回戦一括PDFは `bracket` と `round` クエリでトーナメント表/回戦別に絞り込み可能にした。既存の全体1回戦URLとファイル名は互換維持。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.BulkScoreSheetPdfTests core.tests.MaintenanceMenuTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 171 tests OK。

### トーナメントS試合の赤線表示条件調整
- 実データ `出雲大社カップ2026 / 一般男子 / 決勝トーナメント` で、2回戦から始まるS由来の枠が結果前から赤線表示される問題を確認。
- 対象例: `M7`, `M8`, `M9`, `M11`, `M12`, `M15`, `M16`, `M17`, `M18`, `M19`, `M20`。
- DB上では対象M試合にスコアや `winner` は入っておらず、S試合側の赤線判定が「次試合の反対側に相手がいる」だけで成立していたことが原因。
- S試合の赤線は、次試合で同じ選手が勝者になった場合、または次試合にスコアが入力された場合だけ表示するように変更。
- これにより、未入力の2回戦枠は赤くせず、試合後はシードが勝っても負けても「そこまで進出した線」は残せる。
- 確認:
  - 実データで `S2`, `S4`, `S6`, `S9`, `S11`, `S15`, `S17`, `S19`, `S21`, `S23`, `S25` の赤線判定が `False` になることを確認。
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentAdvancementTests core.tests.TournamentScheduleBehaviorTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 172 tests OK。

### Stage再取込保護のS試合winner除外
- 実データ `出雲大社カップ2026` で、`一般男子`、`シニア男子一部`、`シニア男子二部`、`シニア男子三部` の `決勝トーナメント` をStage CSV再取込しようとすると、トーナメント結果入力済みとしてブロックされる問題を確認。
- 該当Stageではスコア入力は0件で、`winner` が入っていたのはS試合の自動bye通過だけだった。
- Stage再取込保護では、S試合の `winner` だけを実結果扱いから除外するよう変更。
- スコア入力、リタイアなど通常ではない結果、S以外の `winner` は従来どおり再取込をブロックする。
- 実データの該当4Stageで `_stage_reimport_protection_errors` が空になることを確認。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.ImportStageSlotsCsvTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 173 tests OK。

### 進行表の空コート列非表示
- 試合進行表で、日程区分ごとに試合が1つもないコート列を表示しないように変更。
- 例えば午前ブロックに1番コートの試合だけ、午後ブロックに2番コートの試合だけがある場合、それぞれのブロックで必要なコート列だけ表示する。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 188 tests OK。
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 174 tests OK。

### 採点票一括出力のコート別出力
- `採点票一括出力` 画面に、日程区分とコートごとのPDF出力行を追加。
- 各行に `印刷可能`、`未確定で除外`、`登録済み` の件数を表示し、リーグ/トーナメント混在のコートでも印刷可能数を正しく数えるようにした。
- 既存のコート別PDF出力は `schedule_block` クエリで日程区分ごとに絞り込めるようにし、絞り込み時のファイル名にブロックIDを含めるようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.BulkScoreSheetPdfTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 175 tests OK。

### 採点票一括出力画面の件数表示整理
- `採点票一括出力` 画面の表示順を、現場で使いやすい `コート別`、`リーグ別`、`トーナメント別`、`全体出力` の順に変更。
- 件数見出しを `進行表登録`、`出力対象`、`未確定`、`未配置` に統一し、コート別・リーグ別・トーナメント別で同じ感覚で読めるようにした。
- コート別は進行表上の範囲なので、未配置欄は `ー` と表示する。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.BulkScoreSheetPdfTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 175 tests OK。

### 採点票PDFのカテゴリ名自動調整
- 採点票PDFのカテゴリ名が長い場合に、25mm程度のカテゴリ欄へ収まるようフォントサイズを自動調整する処理を追加。
- 7ptまで縮小しても一行に収まらない場合は、カテゴリ名を二段に分け、二段それぞれが収まるよう5ptまで調整する。
- 短いカテゴリ名は従来どおり12pt一行で描画する。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.BulkScoreSheetPdfTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 177 tests OK。

### TODO追記: 採点票テンプレートDB管理
- `TODO.md` に、採点票テンプレートPDFと印字位置を大会ごとにDB管理する将来案を追記。
- 現在 `constants.py` に定数として置いている採点票諸元は、新規大会作成時のデフォルト値として扱う方針にした。
- 最終的には、ユーザがPDFテンプレートを登録し、カテゴリ、コート、回戦、選手名、所属、QR、検索キーなどの印刷アイテムを配置できるインターフェースを検討する。
- ドキュメント更新のみのためテストは未実行。

### 採点票PDFの未確定進出元枠除外
- 実データ `出雲大社カップ2026 / 第一本部 / 1コート / 第11試合 / M1` で、未反映の進出元枠 `S2` と `O2` が採点票PDFに出力される問題を確認。
- `pair1/pair2` は存在するが、`AdvancementSource` 付きで `participant` 未設定の仮枠だったため、実ペア未確定として扱う必要があった。
- 採点票出力可否の共通判定を追加し、未反映の進出元枠を含む試合は個別PDF・一括PDF・件数表示のすべてで出力対象外にした。
- 実データの該当試合で `is_schedule_score_sheet_printable` が `False` になることを確認。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.BulkScoreSheetPdfTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 178 tests OK。

### 画面・URL棚卸し
- `docs/screen_inventory.md` を追加し、現在のURL/画面を分類した。
- 現時点では一般利用者向けの完成画面はまだなく、既存画面は開発初期の確認画面、当日運営、事前準備、メンテナンスが混在しているという前提で整理。
- 分類:
  - 一般公開用候補
  - 当日運営で使う画面
  - 事前準備で使う画面
  - Stage・結果反映・復元
  - トーナメント運営・設定
  - 旧実装・整理候補
- `category_detail` は一般向けではなく、開発初期のリーグ確認/運営画面として扱い、公開用リーグ表は別途分離する方針を明記。
- ドキュメント更新のみのためテストは未実行。

### 画面整理判定の追記
- `docs/screen_inventory.md` に、旧実装・整理候補をさらに分ける「整理判定」を追記。
- すぐ削除せず、次の4種類に分けて扱う方針にした。
  - 将来のオンライン作成UIに再利用
  - 当日事故対応として維持
  - 互換用として一時維持
  - 削除または隠し機能化を検討
- リーグ枠作成、総当たり生成、トーナメント枠作成、トーナメント試合生成/編集などは、将来オンラインで大会を作成する時の土台として残す方針に整理。
- 古いCSV取込や大会横断検索などは、現行導線との重複を確認しながら一時維持または削除候補として扱う。
- ドキュメント更新のみのためテストは未実行。

### 見えるUI導線の整理
- 機能やURL/viewは削除せず、通常画面で目に入りすぎる導線を整理した。
- 全体ナビから旧実装の `リーグ枠検索` を外し、高度なメンテナンス側へ移動。
- 大会トップから `カテゴリ追加` を外し、管理メニュー内の高度なメンテナンスへ集約。
- 管理メニューの `高度なメンテナンス` を、`docs/screen_inventory.md` の整理判定に合わせて次の4つに分けた。
  - 将来のオンライン作成UIに再利用
  - 当日事故対応として維持
  - 互換用として一時維持
  - 削除または隠し機能化を検討
- 確認:
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 178 tests OK。

### 公開用カテゴリ結果画面の最小実装
- 一般利用者向けの第一歩として、読み取り専用のカテゴリ結果画面 `public_category_results` を追加。
- URLは `tournament/<code>/category/<category_id>/public/` とし、大会コードとカテゴリIDの組み合わせで対象カテゴリを取得する。
- 大会トップのカテゴリ一覧リンクを、運営用の `category_stage_overview` ではなく公開用カテゴリ結果画面へ変更。
- 公開用カテゴリ結果画面では、Stageの内部操作を出さず、リーグ/トーナメントの進行状況と表への入口だけを表示。
- 既存の運営用 `category_stage_overview`、`category_detail`、`tournament_bracket_detail` は残し、公開用リーグ表・公開用トーナメント表の分離は次段階で扱う。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 182 tests OK。
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 180 tests OK。

### 公開用カテゴリ結果画面のStage順縦並び表示
- 参加者がカテゴリ内の結果を流れで追えるよう、公開用カテゴリ結果画面をリンク一覧から実表示へ変更。
- Stageの `display_order` 順に、リーグ表とトーナメント表を縦に並べて表示するようにした。
- リーグStageでは、対象Stage内の各リーグ表をその場に表示し、追加試合や同率順位決定補助表も読み取り専用で表示する。
- トーナメントStageでは、対象Stage内の各トーナメント表をSVGでその場に表示する。
- 既存の運営用リーグ表データ作成処理とトーナメントSVG作成処理を再利用できるように小さく共通化した。
- 公開用SVGでは、内部的な結果入力URLを使わず、テキストとして描画して操作リンクを出さない。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 180 tests OK。

### 公開用トーナメントSVGの左起点表示
- 公開用カテゴリ結果画面内のトーナメントSVGが表示領域いっぱいに引き伸ばされ、画面中央寄りに見える状態を調整。
- `.svg-bracket` の `min-width: 100%` をやめ、SVG本来の幅で左起点に表示するようにした。
- 大きいトーナメントは従来どおり横スクロールで確認する。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 180 tests OK。

### 未確定進出元枠のSVGラベル位置調整
- トーナメントSVGで、未確定の進出元枠が2行表示モードのY座標計算に乗り、左側の表示順番号と縦位置がずれる問題を修正。
- 実ペア未確定の `AdvancementSource` 付き枠は、参加者名ではなく枠ラベルとして扱い、1行表示で番号と同じY基準に揃えるようにした。
- 決勝位置に表示される未確定進出元枠も同じく1行表示にした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 181 tests OK。

### TODO追記: 公開用Stage状態表示
- `TODO.md` の一般利用者向け画面に、公開用カテゴリ結果画面のStage状態表示を整理する課題を追記。
- 後続Stageに未反映でまだ始まっていないStageまで `進行中` と見える問題を整理対象にした。
- `結果待ち`、`未着手`、`進行中`、`結果確定` など、参加者が状況を直感的に理解できる文言へ寄せる方針を記載。
- ドキュメント更新のみのためテストは未実行。

### 公開結果画面と進行表の相互リンク
- 公開用カテゴリ結果画面と試合進行表を、試合単位のアンカーで相互移動できるようにした。
- 公開結果画面では、リーグ表のスコアセルから該当する進行表セル `schedule-<id>` へ移動できるようにした。
- 公開結果画面のトーナメントSVGでは、試合ラベルから該当する進行表セルへ移動できるようにした。
- 試合進行表では、各進行セルに `schedule-<id>` を付け、各試合カードの `結果表示` から公開結果画面内の `round-robin-match-<id>` / `tournament-match-<id>` へ戻れるようにした。
- ハッシュ付きURLで移動した時に、対象が縦横とも画面中央付近に来るよう共通スクリプトと `:target` のハイライトを追加。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests core.tests.TournamentScheduleBehaviorTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 183 tests OK。
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 181 tests OK。

### 公開リーグ表の進行表リンク表示調整
- 公開用カテゴリ結果画面のリーグ表で、進行表へのリンクがボタン風に強く見えすぎていたため、スコアセル内の自然なリンク表示へ調整。
- 上部の `試合進行表へ` はボタン風のまま残し、リーグ表内ではスコアそのものを押せる見た目に寄せた。
- 未入力だが進行表に配置済みのセルは、ボタン風の `進行` 表示ではなく `-` をリンクとして表示するようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 181 tests OK。

### TODO追記: 進行表の指定審判
- `TODO.md` の管理UIに、進行表で試合ごとの指定審判を保持する課題を追記。
- 指定審判あり/なしの両方を扱い、特にリーグ戦では同リーグ内で試合のない選手・ペアを審判にする運用を想定する方針を記載。
- 進行表、採点票PDF、将来の電子スコアシートで指定審判を確認できるようにすること、試合順やコート変更時にも紐づきが崩れないようにすることを整理した。
- 一般参加者向けの進行表にも指定審判を表示し、参加選手が自分の審判担当を確認できるようにする方針を追記。
- ドキュメント更新のみのためテストは未実行。

### 公開用Stage状態表示の改善
- 公開用カテゴリ結果画面で、Stage状態を `結果確定` / `進行中` の2択から、`これから`、`結果待ち`、`進行中`、`結果確定` の4状態へ変更。
- 後続Stageの進出元枠が未反映で、まだ試合結果が入っていない場合は `結果待ち` と表示するようにした。
- 試合結果や順位が一部でも入っているStageは `進行中`、全リーグ順位または全トーナメント試合が完了しているStageは `結果確定` と表示する。
- トーナメントの両者リタイア結果も、公開状態判定では結果入力済みとして扱うようにした。
- `TODO.md` に、第1段階として4状態表示を実装済みであることを追記。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 182 tests OK。

### 一般参加者向け進行表の追加
- 一般参加者向けの読み取り専用進行表 `public_schedule_view` を追加。
- URLは `/tournament/<code>/public-schedule/` とし、既存の運営用 `schedule_view` と進行表データ作成処理を共有するようにした。
- 公開用進行表では、結果入力、採点票PDF、試合移動などの運営操作を出さず、各試合カードから公開カテゴリ結果画面の該当試合へ移動できる `結果表示` だけを出す。
- 公開カテゴリ結果画面から進行表へ戻るリンクと、リーグ表/トーナメントSVG内の進行表アンカーリンクを公開進行表へ向け替えた。
- 大会トップの `試合進行表` リンクも公開進行表へ向け替え、参加者が運営用進行表へ入りにくくした。
- `docs/screen_inventory.md` に、公開カテゴリ結果画面と公開進行表を第一段階の一般公開画面として追記。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests core.tests.TournamentScheduleBehaviorTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 183 tests OK。

### 公開リーグ表の未入力リンク修正
- 公開用カテゴリ結果画面のリーグ表で、進行表に配置済みの未入力試合セルが空文字リンクになり、見た目上押しにくい問題を修正。
- 未入力だが公開進行表にリンクできるセルは、必ず `-` をリンク文字として表示するようにした。
- 公開進行表へのリンクURLと `-` 表示をテストで確認するようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 184 tests OK。

### 公開進行表のカード全体リンク化
- 一般参加者向け進行表で、各試合カード内の `結果表示` テキストリンクを廃止。
- 公開進行表ではカード全体を公開カテゴリ結果画面の該当試合アンカーへリンクするようにし、セル内の操作要素を減らした。
- カード全体リンクでも見た目が崩れないように、公開進行表用のリンクスタイルとフォーカス表示を追加。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_is_read_only --keepdb`
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests core.tests.TournamentScheduleBehaviorTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 184 tests OK。

### 公開進行表のDB問い合わせ削減
- 公開リーグ表から公開進行表へ移動する際、進行表生成が遅くなりやすい原因を修正。
- これまでは進行表の各マスごとに `Schedule` を検索していたため、日程区分ごとの `試合順 x コート数` に比例してDB問い合わせが増えていた。
- `Schedule` を一括取得し、`(日程区分, コート, 試合順)` の辞書にしてから表を組み立てるよう変更。
- 進行表表示で使うリーグ枠・トーナメント枠の進出元情報も `select_related` で先読みし、参加者名表示時の追加問い合わせを抑えた。
- マス数が増えても進行表データ生成のクエリ数が増えないことをテストで確認するようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_schedule_block_tables_do_not_query_per_cell --keepdb`
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests core.tests.TournamentScheduleBehaviorTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 185 tests OK。

### 公開画面の短時間キャッシュ設定
- 一般参加者向けの読み取り専用画面に、設定値で調整できる短時間キャッシュを追加。
- `settings.py` に `PUBLIC_VIEW_CACHE_SECONDS = 5` を追加し、負荷や即時性に応じて `0` で無効化、`10` などで延長できるようにした。
- 対象は公開カテゴリ結果画面 `public_category_results` と公開進行表 `public_schedule_view` のみとし、管理画面や結果入力画面には適用しない。
- テストではキャッシュ有効時に同じHTMLが返ること、`PUBLIC_VIEW_CACHE_SECONDS = 0` で即時反映されることを確認するようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_uses_configured_cache_timeout core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_cache_can_be_disabled --keepdb`
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests core.tests.TournamentScheduleBehaviorTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 187 tests OK。

### 設定パラメータ説明メモの追加
- `docs/settings.md` を追加し、`settings.py` に持っている運用パラメータの説明を記載。
- `PUBLIC_VIEW_CACHE_SECONDS` は対象画面、対象外画面、`0` / `5` / `10` / `15` 秒以上の調整目安を整理。
- 既存の `ENABLE_AUTO_OPERATION_SNAPSHOT` についても、自動スナップショットの目的と無効化の考え方を記載。
- ドキュメント更新のみのためテストは未実行。

### 公開リーグ表の追加試合表示
- 公開用カテゴリ結果画面のリーグ表下に、追加試合を読み取り専用で表示する挙動を確認・補強。
- 既存の `meeting_number > 1` だけでなく、`counts_for_ranking=False` のリーグ試合も追加試合欄へ表示するようにした。
- 表示項目は、対戦ペア、回数、結果、順位計算対象/対象外、理由。
- `TODO.md` に、第1段階として公開用カテゴリ結果画面で追加試合表示を実装済みであることを追記。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 188 tests OK。

### 公開進行表の固定見出しと未入力表示整理
- 一般参加者向け進行表で、横スクロール・縦スクロール時に確認しやすいよう、コート見出し行と試合順列を sticky 表示にした。
- 公開進行表では、結果未入力の試合に `未入力` バッジを表示しないようにした。
- 完了、試合中、呼出済、結果入力済みの状態表示は引き続き表示する。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_is_read_only --keepdb`
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests core.tests.CategoryStageOverviewTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 188 tests OK。

### 進行表のブロック別固定見出し化
- 運営用進行表と一般参加者向け進行表で、コート見出し行と試合順列を固定表示する共通スタイルを追加。
- 各 `schedule_block` の表を独立したスクロール領域にし、ブロックをまたぐと固定見出しが自然に切り替わるようにした。
- 公開進行表側に持っていた固定表示CSSは共通スタイルへ移し、運営用進行表にも同じ表示を適用した。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 188 tests OK。

### 公開進行表とカテゴリ結果の往復導線
- 一般参加者向け進行表の試合カードからカテゴリ結果画面へ移動する際、`from_schedule` を付けて元の進行表セルを保持するようにした。
- カテゴリ結果画面の `試合進行表へ` リンクは、元の進行表セルが分かる場合は `#schedule-...` 付きで戻るようにした。
- `from_schedule` はビュー側で同一大会内の `Schedule` に限定してからテンプレートへ渡すようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_is_read_only --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 188 tests OK。

### TODO追記: 一般向け表示の次課題
- 一般利用者向け表示を核にする前提で、トーナメント結果入力後や確認時の表示を `トーナメント表のみ` / `カード型表示` から大会設定で選べる案を `TODO.md` に追記。
- 一般利用者向けリーグ表について、勝者ゲーム数の丸囲み表示、勝敗色分けの有無、勝者色・敗者色を大会設定で持つ案を `TODO.md` に追記。
- ドキュメント更新のみのためテストは未実行。

### TODO追記: 公開URLのハッシュ化
- 一般利用者向け公開URLを予測困難にするため、大会ごとの公開トークン/ハッシュを第一段階として検討する内容を `TODO.md` に追記。
- カテゴリ単位・試合単位のトークン化は、公開制御は細かくなるが管理が重くなるため、必要性が出てから検討する方針として整理。
- 公開/非公開、トークン再発行、旧トークン無効化、QRやスコアシート欄外キーとの関係も追記。
- ドキュメント更新のみのためテストは未実行。

### TODO追記: テスト協力者からの再現用データ
- いろいろな人にテストしてもらう前提で、エラーや表示崩れ、結果反映のズレを開発側で再現するための再現用スナップショット/サポート用エクスポートを `TODO.md` に追記。
- 大会構成、参加者、Stage、結果、順位、進行表、リタイア、差し替え履歴、採点票設定などを別大会として取り込める形を検討する。
- 個人情報の匿名化、送付前確認、アプリ/端末/発生画面/直前操作などのメタ情報を含める案も追記。
- ドキュメント更新のみのためテストは未実行。

### 公開URLの大会トークン化
- `Tournament.public_token` を追加し、既存大会にはマイグレーションでランダムな公開トークンを付与するようにした。
- 公開大会トップ、公開カテゴリ結果、公開進行表を `/public/<token>/...` 形式のURLでも表示できるようにした。
- 既存の大会コードURLは互換用として残しつつ、公開画面内のカテゴリ一覧、進行表、カテゴリ結果へのリンクはトークンURLへ寄せた。
- `is_public=False` の大会は、公開トークンURLでも404になるようにした。
- Django admin の大会一覧/検索で `public_token` を確認できるようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py makemigrations --check --dry-run`
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_is_read_only core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_uses_configured_cache_timeout core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_cache_can_be_disabled --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 190 tests OK。

### DB操作: 公開URLトークンマイグレーション適用
- `./venv/bin/python stpro/manage.py migrate` を実行し、`core.0033_tournament_public_token` を適用。
- 既存大会 6 件に `public_token` が付与され、空トークンが 0 件であることを確認。

### 大会設定画面への公開URL/QR追加
- 大会設定画面に、トークン付きの一般公開URL、公開画面を開くリンク、QR印刷リンク、公開トークン表示を追加。
- 一般公開URLのQRコードをA4 PDFで出力する `public_tournament_qr_pdf` を追加。
- QR PDFは既存依存の `reportlab` で生成し、追加ライブラリなしで印刷できるようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.MaintenanceMenuTests.test_tournament_settings_page_groups_display_settings core.tests.MaintenanceMenuTests.test_public_tournament_qr_pdf_outputs_printable_pdf --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 191 tests OK。

### 公開URLのカテゴリトークン化
- `Category.public_token` を追加し、公開カテゴリ結果URLを `/public/<大会トークン>/category/<カテゴリトークン>/` 形式にした。
- 既存の `/public/<大会トークン>/category/<カテゴリID>/` は互換用として残し、公開画面内のリンクはカテゴリトークン版へ寄せた。
- カテゴリ作成時に `c_...` 形式のランダムトークンを自動採番し、Django admin のカテゴリ一覧/検索でも確認できるようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py makemigrations --check --dry-run`
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests core.tests.TournamentScheduleBehaviorTests --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 191 tests OK。

### DB操作: カテゴリ公開トークンマイグレーション適用
- `./venv/bin/python stpro/manage.py migrate` を実行し、`core.0034_category_public_token` を適用。
- 既存カテゴリ 18 件に `public_token` が付与され、空トークンが 0 件であることを確認。

### 公開URL再発行
- 大会設定画面に、公開URLを再発行するボタンを追加。
- 再発行時は `Tournament.public_token` を新しいランダム値へ差し替え、旧公開URLを無効化するようにした。
- 公開ページの短時間キャッシュで旧URLが残らないよう、再発行時にキャッシュをクリアするようにした。
- 再発行後はQRコードも印刷し直す必要がある旨を画面に表示。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.MaintenanceMenuTests.test_tournament_settings_can_regenerate_public_token --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 192 tests OK。

### 大会設定画面の一般公開セクション整理
- 大会設定画面の一般公開まわりを、公開状態、公開URL、通常操作、危険操作に分けて表示するよう整理。
- `is_public` の状態に応じて `公開中` / `非公開` を表示し、非公開時は公開画面リンクを押せない表示にした。
- QR印刷と公開画面リンクは通常操作としてまとめ、公開URL再発行は危険操作として下段に分離。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.MaintenanceMenuTests.test_tournament_settings_page_groups_display_settings core.tests.MaintenanceMenuTests.test_tournament_settings_page_shows_private_public_state core.tests.MaintenanceMenuTests.test_tournament_settings_can_regenerate_public_token --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 193 tests OK。

### `is_public` の意味を一般公開制御へ整理
- 管理側の大会一覧と大会詳細では `is_public` による絞り込みをやめ、非公開大会でも管理・編集できるようにした。
- `is_public` は一般参加者向け公開URLを表示できるかどうかの制御として維持。
- 大会設定画面から一般公開を停止/有効化できるボタンを追加し、切替時は公開ページキャッシュをクリアするようにした。
- 管理側の大会一覧では非公開大会に `非公開` ラベルを表示。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.MaintenanceMenuTests.test_tournament_settings_page_shows_private_public_state core.tests.MaintenanceMenuTests.test_private_tournament_remains_visible_to_management_views core.tests.MaintenanceMenuTests.test_tournament_settings_can_update_public_visibility core.tests.CategoryStageOverviewTests.test_public_token_urls_respect_public_flag --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 195 tests OK。

### 公開URLコピー操作の追加
- 大会設定画面の公開URL欄に `コピー` ボタンを追加。
- HTTPS環境では Clipboard API を使い、非対応環境ではURL欄を選択してコピーしやすくするフォールバックにした。
- コピー成功時は `コピーしました`、フォールバック時は `選択しました` を一時表示するようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.MaintenanceMenuTests.test_tournament_settings_page_groups_display_settings core.tests.MaintenanceMenuTests.test_tournament_settings_page_shows_private_public_state --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 195 tests OK。

### 一般公開トップ画面のスマホ向け整理
- 公開トップ画面を管理側の大会詳細と分岐し、一般参加者向けの入口として整えた。
- 公開画面では大会コードを表示せず、大会名、一般参加者向け表示、試合進行表、カテゴリ別結果を中心にした。
- 試合進行表を大きめの主導線にし、カテゴリ一覧はスマホでも押しやすいリンク一覧にした。
- 管理側の大会詳細は従来どおり大会コード、管理メニュー、大会設定、カテゴリ一覧を表示。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests.test_tournament_detail_links_to_public_category_results core.tests.CategoryStageOverviewTests.test_public_token_urls_show_read_only_pages --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 195 tests OK。

### 公開カテゴリ結果画面の戻り導線整理
- 公開カテゴリ結果画面の上部に、`カテゴリ一覧` と `試合進行表` をまとめたナビを配置。
- 進行表から `from_schedule` 付きで遷移した場合は、進行表ボタンを `元の試合へ戻る` と表示し、元の `#schedule-...` へ戻る動きを維持。
- スマホではナビボタンが縦に並ぶようにし、押しやすさを優先。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.CategoryStageOverviewTests.test_public_category_results_are_read_only core.tests.MaintenanceMenuTests.test_tournament_settings_page_groups_display_settings --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 195 tests OK。

### 公開進行表からカテゴリ結果への導線強化
- 一般参加者向け進行表の試合カード下部に `カテゴリ結果を見る` を表示。
- セル全体クリックは維持しつつ、どこへ移動するかが分かる見た目にした。
- 管理用に見える `結果表示` 文言は引き続き表示しない。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_is_read_only --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 195 tests OK。

### 公開進行表のセル内リンク文言取り下げ
- `カテゴリ結果を見る` を各試合カードへ表示する案は、セルごとに文言が増えてしつこく見えるため取り下げ。
- セル全体クリックは維持し、リンク感はカードのホバー/フォーカス時の背景・アウトラインで控えめに示す方針へ変更。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_is_read_only --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 195 tests OK。

### 公開進行表上部のカテゴリ一覧ボタン
- 一般参加者向け進行表の上部に、公開カテゴリ結果画面と同じトーンの `カテゴリ一覧` ボタンを配置。
- 既存の見出し右側リンクは外し、見出し直下の押しやすいナビとして表示するようにした。
- スマホではボタンが横幅いっぱいに近い形で表示されるようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_is_read_only --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 195 tests OK。

### 公開進行表カードの視認性調整
- セル内に文言を増やさず、試合カードの余白、枠線、左線、ホバー背景で押せる見た目を調整。
- カテゴリ名を強い行にし、リーグ/トーナメント名・試合名・ステージ名は補助行として表示するよう整理。
- 呼出済、試合中、完了はカード左線の色でも状態が分かるようにした。
- 確認:
  - `./venv/bin/python stpro/manage.py test core.tests.TournamentScheduleBehaviorTests.test_public_schedule_view_is_read_only --keepdb`
  - `./venv/bin/python stpro/manage.py test core --keepdb`
  - 最終確認時点で `core` は 195 tests OK。
