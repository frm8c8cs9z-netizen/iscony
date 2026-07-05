# Screen Inventory

このメモは、現在の画面・URLを「一般公開用」「当日運営」「事前準備」「メンテナンス」「旧実装/整理候補」に棚卸しするための作業メモ。

現時点の認識:
- 一般利用者向けの完成画面はまだない。
- 現在の多くの画面は、開発初期の確認画面、当日運営、事前準備、メンテナンスが混在している。
- まず既存画面を分類し、使う画面・隠す画面・削除候補を分けてから、一般公開用画面を別に設計する。

## 分類

### 一般公開用候補

まだ本格的な一般公開画面としては未整備。

| URL name | URL | 現状 | 方針 |
| --- | --- | --- | --- |
| `tournament_list` | `/` | 公開中大会一覧。最小限の一覧表示のみ。 | 一般公開トップ候補。ただし管理導線とは分けたい。 |
| `tournament_detail` | `/tournament/<code>/` | 大会トップだが、管理メニュー・大会設定・カテゴリ追加など運営導線が混在。 | 将来は公開用大会トップと管理用大会トップを分離する。 |
| `category_stage_overview` | `/category/<id>/stages/` | カテゴリ内のStage一覧。リーグ表・トーナメント表への入口があり、運営確認にも使っている。 | 一般公開向けにはStage用語を弱め、カテゴリ内の試合一覧/結果一覧として再設計する候補。 |
| `tournament_bracket_detail` | `/tournament/<code>/bracket/<id>/` | トーナメント表表示。メンテナンス導線や採点票PDF導線も含む。 | 公開用トーナメント表の土台。運営リンクを隠した公開表示が必要。 |
| `category_detail` | `/category/<id>/` | リーグ表だが、結果入力・順位計算・追加対戦・順位入力など開発/運営導線が混在。 | 一般向けではなく開発初期の名残。公開用リーグ表は別途作るか、表示モードを分ける。 |

### 当日運営で使う画面

| URL name | URL | 用途 | 方針 |
| --- | --- | --- | --- |
| `maintenance_menu` | `/tournament/<code>/maintenance/` | 当日運営・準備・設定への入口。 | 当面の管理トップ。今後も作業目的別に整理する。 |
| `schedule_view` | `/tournament/<code>/schedule/` | 試合進行表。結果入力・採点票PDF・表確認への入口。 | 当日運営の主導線候補。タブレット対応と操作モード化を検討。 |
| `court_status` | `/tournament/<code>/courts/` | コート状況確認。 | 進行状況確認用として維持。進行表との役割分担は後で整理。 |
| `reception_match_search` | `/tournament/<code>/reception/search/` | 受付で試合を探して結果入力する補助画面。 | 混雑時の主導線ではなく、迷った時・例外対応の補助として維持。 |
| `input_match_score` | `/match/<id>/score/` | リーグ結果入力。 | 当日運営用。公開画面には出さない。 |
| `input_tournament_match_score` | `/tournament/<code>/tournament-match/<id>/score/` | トーナメント結果入力。 | 当日運営用。未確定試合の保存不可ガードを維持。 |
| `bulk_score_sheet_select` | `/tournament/<code>/score-sheets/` | 採点票一括出力範囲選択。 | 当日運営で使う。コート別・リーグ別・トーナメント別の確認を続ける。 |
| `score_sheet_pdf` | `/schedule/<id>/score-sheet/` | 進行表上の1試合分採点票PDF。 | 個別出力として維持。未確定カードは出力しない。 |
| `court_score_sheets_pdf` | `/court/<id>/score-sheets/` | コート別採点票PDF。 | 一括出力画面から利用。直URLは互換用。 |
| `league_score_sheets_pdf` | `/tournament/<code>/score-sheets/league/` | リーグ採点票PDF。 | 一括出力画面から利用。直URLは互換用。 |
| `tournament_first_round_scheduled_score_sheets_pdf` | `/tournament/<code>/score-sheets/tournament-first-round/` | トーナメント回戦別採点票PDF。 | 名前は1回戦だが `round` 指定にも対応。将来URL名を整理したい。 |
| `update_schedule_status` | `/schedule/<id>/status/<status>/` | 呼出/試合中/完了更新。 | 当日運営用。権限導入時はオペレーター権限に含める。 |
| `move_schedule` | `/schedule/<id>/move/` | 試合順・コート移動。 | 当日運営用。将来は進行表上のタップ/ドラッグ操作に寄せる。 |

### 事前準備で使う画面

| URL name | URL | 用途 | 方針 |
| --- | --- | --- | --- |
| `import_participants` | `/tournament/<code>/import/participants/` | 参加者CSV取込。 | 推奨準備導線として維持。 |
| `import_stage_slots` | `/tournament/<code>/import/stage-slots/` | Stage枠CSV取込。 | 推奨準備導線として維持。ただし画面文言はStage前面から少し運営感覚へ寄せたい。 |
| `export_stage_slots` | `/tournament/<code>/export/stage-slots/` | 現在のStage枠CSV出力。 | 再取込・調整用として維持。 |
| `import_schedule` | `/tournament/<code>/import/schedule/` | 試合進行CSV取込。 | 推奨準備導線として維持。 |
| `download_csv_format` | `/tournament/<code>/import/format/<type>/` | CSVフォーマット出力。 | 事前準備補助として維持。 |
| `clone_tournament` | `/tournament/<code>/clone/` | 大会複製。 | 実運用で便利。設定・テンプレートの複製範囲は今後拡張。 |
| `tournament_settings` | `/tournament/<code>/settings/` | 大会デフォルト表示設定など。 | 事前準備/設定として維持。採点票テンプレート設定も将来ここに寄せる候補。 |
| `order_maintenance` | `/tournament/<code>/maintenance/order/` | カテゴリ・リーグ表示順。 | 表示設定として維持。 |
| `court_order_maintenance` | `/tournament/<code>/maintenance/court-order/` | コート表示順。 | 表示設定として維持。 |

### Stage・結果反映・復元

| URL name | URL | 用途 | 方針 |
| --- | --- | --- | --- |
| `advancement_source_list` | `/tournament/<code>/advancement-sources/` | 進出元一覧・入れ替え。 | Stage反映前後の確認用として維持。文言は運営感覚へ寄せたい。 |
| `swap_advancement_source` | `/tournament/<code>/advancement-sources/swap/` | 進出元入れ替えPOST。 | 権限導入時は慎重に制限する。 |
| `apply_stage_results` | `/stage/<id>/apply-results/` | Stage結果を後続枠へ反映。 | 重要操作。スナップショット連動と確認導線を維持。 |
| `tournament_snapshot_list` | `/tournament/<code>/snapshots/` | 大会スナップショット一覧。 | 事故対応用として維持。 |
| `category_snapshot_list` | `/category/<id>/snapshots/` | 旧カテゴリ別スナップショット入口。 | 現在は大会スナップショット運用に寄せた互換導線。整理候補。 |
| `restore_*` | `/snapshot/<id>/restore/...` | スナップショット復元。 | 大会全体/カテゴリ/日程区分などの復元入口。今後「結果のみ復元」を検討。 |

### トーナメント運営・設定

| URL name | URL | 用途 | 方針 |
| --- | --- | --- | --- |
| `bracket_list` | `/tournament/<code>/brackets/` | トーナメント一覧・個別設定入口。 | 運営/設定画面として維持。公開用一覧とは分けたい。 |
| `edit_tournament_bracket` | `/tournament/<code>/bracket/<id>/edit/` | トーナメント表示設定編集。 | 設定画面として維持。 |
| `tournament_match_maintenance` | `/tournament/<code>/bracket/<id>/maintenance/` | トーナメント試合メンテナンス。 | 運営/開発寄り。公開画面には出さない。 |
| `edit_tournament_match` | `/tournament/<code>/tournament-match/<id>/edit/` | トーナメント試合編集。 | 高度なメンテナンス。通常運営導線からは目立たせない。 |
| `add_tournament_match_schedule` | `/tournament/<code>/tournament-match/<id>/add-schedule/` | 未配置トーナメント試合を進行表へ追加。 | 進行メンテナンス用として維持。 |
| `tournament_schedule_view` | `/tournament/<code>/bracket/<id>/schedule/` | トーナメント別進行表示。 | 現在の利用頻度を確認。進行表本体で足りるなら整理候補。 |
| `tournament_match_score_sheet` | `/tournament/<code>/tournament-match/<id>/score-sheet/` | ブラウザ表示用トーナメント採点票。 | PDF出力に寄せるなら整理候補。 |
| `tournament_match_score_sheet_pdf` | `/tournament/<code>/tournament-match/<id>/score-sheet-pdf/` | トーナメント1試合PDF。 | 個別PDFとして維持。ただし進行表ベースの採点票と役割整理が必要。 |

### 旧実装・整理候補

| URL name | URL | 現状 | 方針 |
| --- | --- | --- | --- |
| `import_pairs` | `/tournament/<code>/import/pairs/` | Stage対応前の旧リーグ枠CSV取込。 | 高度なメンテナンスに隔離済み。実運用で不要なら削除候補。 |
| `pair_search` | `/pair-search/` | リーグ枠検索。大会横断の古い補助検索。 | 受付検索に役割を寄せ、削除または管理者向け隠し機能化を検討。 |
| `generate_group_matches` | `/category/<id>/generate-matches/` | リーグ戦生成。 | 開発/手入力作成導線の名残。通常運用ではCSV推奨なら隠す。 |
| `add_extra_round_robin_match` | `/group/<id>/extra-match/` | リタイア対応などの追加対戦作成。 | 運営機能として必要。ただし公開リーグ表とは分離。 |
| `calculate_category_ranking` | `/category/<id>/ranking/` | 順位計算。 | 運営用。公開画面には出さない。 |
| `edit_group_ranking` | `/group/<id>/ranking/edit/` | 順位手入力。 | 運営用。公開画面には出さない。 |
| `league_entry_action` / `retire_pair` / `cancel_retire_pair` | `/pair/...` | リーグ枠操作・リタイア。 | 運営用。公開画面には出さない。 |
| `pair_maintenance` / `edit_pair` | `/category/<id>/pairs/maintenance/`, `/pair/<id>/edit/` | リーグ枠メンテナンス。 | 事前準備/高度メンテとして維持。 |
| `add_tournament_bracket` | `/tournament/<code>/bracket/add/` | 手入力でトーナメント追加。 | 高度なメンテナンスに隔離済み。手入力作成導線として再設計候補。 |
| `generate_tournament_matches` | `/tournament/<code>/bracket/<id>/generate/` | トーナメント枠一括作成。 | 手入力作成導線の中に整理する候補。 |
| `import_tournament_schedule` | `/tournament/<code>/bracket/<id>/import/schedule/` | トーナメント個別進行CSV。 | 現行推奨の試合進行CSVに統合できるなら削除候補。 |
| `import_bracket_seeds` | `/tournament/<code>/bracket/<id>/import-seeds/` | シードCSV取込。 | 現在のStage枠CSV/位置CSVで代替できるか確認。整理候補。 |
| `import_bracket_positions` / `download_bracket_positions_sample` | `/tournament/<code>/bracket/<id>/import/positions/` | トーナメント位置CSV取込。 | 実運用で必要なら維持。Stage枠CSVとの役割重複を確認。 |
| `add_category` | `/tournament/<code>/category/add/` | カテゴリ手入力追加。 | CSVなし準備導線として将来整理。通常運用では目立たせない。 |

## 整理判定

旧実装・整理候補は、すぐ削除するものとしてではなく、次の4種類に分けて扱う。

### 将来のオンライン作成UIに再利用

最終的にリーグもトーナメントもオンラインで作成できるようにするため、現在の画面が粗くても、ドメイン処理や編集単位として再利用できるものは残す。

| URL name | 扱い | 理由 |
| --- | --- | --- |
| `add_category` | 維持・再設計 | CSVなしで大会を作る場合のカテゴリ作成入口になる。 |
| `pair_maintenance` / `edit_pair` | 維持・再設計 | リーグ枠作成、参加者貼り付け、枠修正の土台になる。 |
| `generate_group_matches` | 維持・再設計 | リーグ枠から総当たり試合を作る処理として必要になる。 |
| `add_tournament_bracket` | 維持・再設計 | トーナメント表を画面から作る入口になる。 |
| `edit_tournament_bracket` | 維持 | トーナメント表の表示・設定調整で今後も必要。 |
| `generate_tournament_matches` | 維持・再設計 | トーナメント枠から試合を生成する処理として必要になる。 |
| `edit_tournament_match` | 維持・再設計 | トーナメント枠、対戦番号、進出元、表示位置の手修正に必要。 |

### 当日事故対応として維持

通常導線では目立たせないが、現場で結果、順位、リタイア、進行表に問題が起きた時の復旧手段として残す。

| URL name | 扱い | 理由 |
| --- | --- | --- |
| `add_extra_round_robin_match` | 維持 | リタイアや順位決定で追加対戦が必要になった時の対応。 |
| `calculate_category_ranking` | 維持 | リーグ順位の再計算や確認に必要。 |
| `edit_group_ranking` | 維持 | 同率判定など、自動判定だけでは決められない順位の手入力に必要。 |
| `league_entry_action` / `retire_pair` / `cancel_retire_pair` | 維持 | リーグ枠のリタイア、復帰、対象外化などの事故対応に必要。 |
| `add_tournament_match_schedule` | 維持 | 未配置になったトーナメント試合を進行表へ戻す時に必要。 |
| `move_schedule` | 維持 | 当日のコート変更・試合順変更に必要。 |
| `restore_*` | 維持 | 誤入力や誤反映から戻すための重要な安全装置。 |

### 互換用として一時維持

現行の推奨導線ではないが、過去のCSVやデータ移行、確認作業でまだ役に立つ可能性があるもの。一定期間使わなければ削除候補に回す。

| URL name | 扱い | 理由 |
| --- | --- | --- |
| `import_pairs` | 一時維持 | Stage対応前の旧リーグ枠CSV取込。現行はStage枠CSVを推奨。 |
| `import_tournament_schedule` | 一時維持 | トーナメント個別進行CSV。現行は大会全体の試合進行CSVを推奨。 |
| `import_bracket_seeds` | 一時維持 | シードCSV取込。Stage枠CSVや位置CSVで代替できるか確認する。 |
| `import_bracket_positions` / `download_bracket_positions_sample` | 一時維持 | トーナメント位置調整に必要か、Stage枠CSVと役割が重複するか確認する。 |
| `category_snapshot_list` | 一時維持 | 現在は大会スナップショット中心の運用へ寄せているため、互換入口として扱う。 |

### 削除または隠し機能化を検討

将来のオンライン作成UIや当日事故対応に直結しにくいもの。すぐ削除せず、参照元と実運用での使用有無を確認してから判断する。

| URL name | 扱い | 理由 |
| --- | --- | --- |
| `pair_search` | 削除または隠し機能化 | 大会横断の古い補助検索。受付検索と役割が重なる。 |
| `tournament_schedule_view` | 整理候補 | 進行表本体でトーナメント別確認が足りるなら不要になる可能性がある。 |
| `tournament_match_score_sheet` | 整理候補 | ブラウザ表示用採点票。PDF導線に寄せるなら役割が薄い。 |
| `tournament_match_score_sheet_pdf` | 維持または統合 | 個別PDFとしては有用だが、進行表ベースの採点票PDFと役割整理が必要。 |

## 直近の整理方針

1. `category_detail` は一般向けではなく、開発初期のリーグ確認/運営画面として扱う。
2. 一般公開用リーグ表は別URLで作るか、既存URLに公開/管理モードを分けるか検討する。
3. `tournament_detail` は現在管理導線が混ざっているため、公開用大会トップと管理トップを分ける。
4. 旧CSV取込・旧検索・手入力生成系は、整理判定に沿って「将来の作成UI」「当日事故対応」「互換用」「削除候補」に分ける。
5. 公開用画面を作る時は、操作リンクを出さず、結果確認・順位確認・トーナメント表確認に限定する。

## 次に作るなら

- 公開用大会トップ
  - 大会名、カテゴリ一覧、公開中のリーグ/トーナメントへの入口だけを表示。
- 公開用カテゴリ結果画面
  - Stageという内部用語を弱め、リーグ結果・トーナメント結果をカテゴリ内でたどれるようにする。
- 公開用リーグ表
  - 結果入力、順位計算、追加対戦作成、順位入力、リタイア操作を出さない。
  - 追加対戦・順位対象外試合は読み取り専用で表示する。
- 公開用トーナメント表
  - 既存SVGを土台に、採点票PDFやメンテナンス導線を出さない。
