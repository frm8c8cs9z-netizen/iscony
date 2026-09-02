# Participant DB設計メモ

このメモは、参加者DBの再設計について、議論した内容と今後決める内容を一か所に集めるための作業メモです。

新規作成・編集画面、CSV取込、リーグ表、トーナメント表、採点票PDF、公開表示、指定審判、変更履歴に影響するため、実装前にここで方針を固めます。

## 現時点の整理

### Participantの意味

`Participant` は、選手個人そのものではなく、カテゴリ内の出場枠として扱います。

- ダブルスでは、1つの `Participant` に player1 と player2 が入る。
- シングルスでは、player1 のみを使い、player2 は空にできる。
- 団体戦は将来的に別構造が必要になる可能性があるため、今回の第1弾では対象外にする。

この整理により、リーグ枠やトーナメント枠は今まで通り `Participant` を参照できます。
そのうえで、`Participant` の内部に持つ選手情報を今より細かくします。

### 当初エントリーと当日出場者

当日、ペアの片方だけが変更になることがあるため、当初エントリー情報と当日出場者情報は分けて持ちます。

基本方針:

- 表示や試合運営では、原則として有効な現在情報を使う。
- `original_*` は最初の登録状態を表す。
- `current_*` は当日情報の完全コピーではなく、当日選手変更がある場合の上書き値として扱う。
- `current_*` が `NULL` なら未設定とみなし、`original_*` を読む。
- 当日選手変更の有無は `has_day_player_change` を手動ON/OFFして管理する。
- `has_day_player_change = False` の場合は、`current_*` に値が残っていても表示には使わない。
- `has_day_player_change = True` の場合は、`current_*` が入っている項目だけ上書き表示し、未入力の項目は `original_*` を読む。
- 所属だけの誤りや表記修正は、当日変更ではなく所属値の編集として扱う。
- 変更履歴は別モデルで持つ方向にする。

第1弾では、次のように当初エントリーと当日出場者を別カラムで持つ方針にします。

```text
Participant
  original_player1_name
  original_player1_short_name

  original_player2_name
  original_player2_short_name

  current_player1_name
  current_player1_short_name

  current_player2_name
  current_player2_short_name

  has_day_player_change
```

既存データは、移行時に `original_*` へ入れ、`current_*` は未設定にします。
これにより、当日変更がない参加者は `original_*` だけで表現できます。

この形にする理由:

- 当初は誰だったかを後から確認できる。
- 当日は誰が出ているかを表示や試合運営の正として使える。
- 当日選手変更のON/OFFを明示できる。
- 変更がない参加者について、同じ値を `original_*` と `current_*` に二重保持しなくて済む。

変更履歴は別モデルで扱います。
つまり、`original_*` は最初の登録状態、`current_*` は当日選手変更の上書き値、履歴モデルはいつ誰が何を変えたかを追う役割に分けます。

## 次に議論すること

### 1. 既存カラムとの関係

現在の `Participant` には、概ね次のような既存カラムがあります。

- `organization`
- `player1_name`
- `player2_name`

第1弾では、既存コードへの影響を小さくするため、既存カラムをすぐ削除せず、互換用として残す案が安全です。

検討点:

- `player1_name` / `player2_name` を current 表示の正として残すか。
- 新しい `current_*` を正として、既存プロパティをそこへ寄せるか。
- 旧カラムを残す期間をどうするか。

採用方針:

- 第1マイグレーションでは、新しい `original_*` / `current_*` カラムを追加する。
- 同じマイグレーションで、既存 `player1_name` / `player2_name` / `organization` を `original_*` へコピーする。
- `current_*` は未設定にし、変更が入った場合だけ値を持たせる。
- 表示、CSV取込、採点票PDF、大会複製、スナップショットの参照を、有効表示値を返すヘルパやプロパティへ寄せる。
- 参照先の切り替えが終わったら、第2マイグレーションで旧 `player1_name` / `player2_name` / `organization` を削除する。
- 旧カラムは「いつか消す」ではなく、この一連のParticipant再設計作業内で削除まで進める。

第1弾で追加するカラム:

```text
original_player1_name
original_player1_short_name

original_player2_name
original_player2_short_name

current_player1_name
current_player1_short_name

current_player2_name
current_player2_short_name

has_day_player_change
```

カラムの基本属性:

- `original_player1_name` は必須にする。
- `original_player2_name` はシングルス対応のため空欄を許容する。
- `original_*_short_name` は空欄を許容し、未入力時は表示時に自動生成する。
- `current_*` はすべて `NULL` を許容し、`NULL` の場合は上書きなしとして扱う。
- `has_day_player_change` はデフォルト `False` とする。

移行ルール:

```text
player1_name -> original_player1_name
player2_name -> original_player2_name

organization -> player1_org1
organization -> player2_org1
```

`current_*` は `NULL` で初期化し、短縮表示名は表示時に有効値ヘルパで補完します。
`has_day_player_change` は `False` で初期化します。
既存データの `org2` から `org5` に相当する情報は存在しないため、移行時は値を作りません。

### 2. 選手ごとの所属

現状はペア単位の `organization` ですが、今後は選手ごとの所属を持ちます。

理由:

- 選手1と選手2で所属が異なるペアがある。
- 中学校全国大会などで、都道府県、学校名、クラブ名など複数の所属表示が必要になる可能性がある。
- 採点票やトーナメント表で、表示方法を大会ごとに変えたくなる。

個人戦では所属マスタ化すると入力・管理コストが重くなるため、第1弾では所属マスタを作りません。
代わりに、大会ごとの所属項目定義と、参加者ごとの所属値を別テーブルで持ちます。

採用方針:

```text
TournamentOrganizationField
  tournament
  code
  label
  display_order
  is_active

ParticipantOrganizationValue
  participant
  player_no
  field
  original_value
```

`TournamentOrganizationField` は大会単位で持ち、カテゴリ単位にはしません。
第1段階では `org1` から `org5` までを正式対応範囲とします。

例:

- `org1` = 所属1
- `org2` = 所属2
- `org3` = 所属3
- `org4` = 所属4
- `org5` = 所属5

表示側では、どの所属項目を使うか、どの順で出すか、選手名の横に出すか下に出すかを後から設定できるようにします。

既存の `organization` は、移行時に `org1` の `ParticipantOrganizationValue` として player1 / player2 の両方へコピーする方針です。
シングルスなど player2 が存在しない場合は、player2 側の所属値は作りません。

`ParticipantOrganizationValue` は、値があるものだけ作ります。
該当する値行が存在しない場合は空白扱いにします。

一意条件:

```text
participant + player_no + field
```

同じ参加者、同じ選手、同じ所属項目の値は1行だけにします。

Player1の所属だけが入力された場合:

- player2 が存在する場合は、保存時に player2 にも同じ所属値を作る。
- CSV取込でも、今後作る入力画面でも同じ補完ルールにする。
- player2 が存在しない場合は、player2 側の所属値は作らない。

この補完により、表示側に「player2 の所属が空なら player1 を読む」という特別ルールを持ち込まずに済みます。
同じ所属を1つにまとめて表示するか、選手ごとに表示するかは、将来の表示設定側で扱います。

#### 所属項目定義

固定の `organization1` から `organization4` ではなく、大会ごとに所属項目定義を持ち、参加者ごとの所属値を別テーブルで持つ案を採用します。

候補:

```text
TournamentOrganizationField
  tournament
  code
  label
  display_order
  is_active

ParticipantOrganizationValue
  participant
  player_no
  field
  original_value
```

`code` はユーザー入力させず、`org1` から `org5` の範囲で自動採番します。
`label` は画面表示用の日本語名で、初期値は `所属1` から `所属5` とします。
将来的には、大会設定画面で `label` を変更できるようにします。

この形にすると、大会ごとに「地域ブロック」「県名」「学校名」「チーム名」「短縮所属名」などを扱えます。
トーナメント表、リーグ表、採点票PDFでは、どの所属項目をどの順番で、氏名の横または下に表示するかを大会設定で決められるようになります。

一方で、DB構造としては柔軟になりますが、変更入力フォームの考慮量が増えます。
特に当日変更では、選手名、短縮名、複数所属項目の original/current をどの単位で編集するかを分かりやすく設計する必要があります。

フォーム設計で守ること:

- 画面上では、当初情報と当日変更情報の違いが一目で分かるようにする。
- `current_*` が `NULL` の場合は「当日変更なし」として扱う。
- 当日変更を解除する操作を明示的に用意する。
- 変更入力では、全項目を毎回入力させず、変更したい選手項目だけ上書きできるようにする。
- 所属項目が多い大会でも、入力欄が横に広がりすぎないようにする。
- CSV取込、画面編集、履歴記録で同じ所属項目定義を参照する。

この案を採用する場合、固定 `organization1-4` カラム案より実装範囲は広がるため、CSV取込形式、編集画面、表示設定とセットで設計してから実装します。

### 3. 短縮表示名

現在の短縮名は、名前をスペースで区切って先頭を使う処理に依存しています。

これは、同一チーム内に同姓がいる場合に弱いです。

今後は、短縮表示に使う名前をDBに持たせます。

採用方針:

- フルネームは必須にする。
- 短縮表示名は任意にする。
- 短縮表示名が空の場合は、既存相当の自動生成で補完表示する。
- CSVや画面では短縮表示名を入力できるようにする。
- シングルスでは player2 系の名前と短縮表示名は空欄を許容する。

候補カラム:

```text
Participant
  original_player1_name
  original_player1_short_name
  original_player2_name
  original_player2_short_name

  current_player1_name
  current_player1_short_name
  current_player2_name
  current_player2_short_name
```

例:

- フルネーム: `山田 太郎`
- 短縮表示名: `山田`
- 同姓がいる場合: `山田太` のように手動で識別可能にする。

### 4. 変更履歴との関係

当日変更の現在状態は `Participant` に持たせます。
一方で、いつ誰が何を変えたかは、別の変更履歴モデルで追います。

採用方針:

- `current_*` は当日選手変更の上書き値として扱う。
- 当日選手変更は `has_day_player_change` を手動ON/OFFして管理する。
- 選手名が変わらない所属や短縮表示名の修正は、原則として元データの編集として扱う。
- 所属は `ParticipantOrganizationValue.original_value` を編集する。
- DB本体は現在有効な値を持つだけにする。
- 変更の意味づけは履歴側で持つ。

例:

```text
original_player1_name = 山田 太郎
current_player1_name = 佐藤 太郎
has_day_player_change = True
```

この場合、表示上の player1 は `佐藤 太郎` になります。
履歴側では、これを「当日選手変更」などの操作種別として記録します。

履歴対象の候補:

- 参加者変更
- 所属変更
- 結果修正
- 試合順・コート変更
- 後続Stageへの反映、消し戻し、再反映
- リーグ枠、トーナメント枠、進出元条件の変更
- CSV取込、再取込
- スナップショット復元

履歴は将来の undo の土台にできます。
ただし、undo は後続試合に結果が入っていないなど、安全条件が揃う場合だけ許可する方針です。

### 5. CSVとの関係

参加者DBを変えると、参加者CSVの項目も変わります。

検討点:

- 旧CSVを一定期間読めるようにするか。
- ここで新CSV形式へ切り替えるか。
- `organization` を残す場合、選手別所属との関係をどう扱うか。
- シングルスでは player2 系を空欄許容にする。

採用方針:

- CSV取込では、原則として当初エントリー情報だけを入力する。
- 取込時は `original_*` と `ParticipantOrganizationValue.original_value` に値を入れる。
- `current_*` にはコピーしない。
- 当日選手変更は、画面操作で `has_day_player_change` をONにし、`current_*` に上書き値を入れる。
- 表示時は `has_day_player_change=True` かつ `current_*` が `NULL` でなければそれを使い、それ以外は `original_*` を使う。
- 所属はペア共通入力を標準とし、CSVの第1段階正式ヘッダは `org1` から `org5` とする。
- `organization1` から `organization5` は、人間が意味を読みやすい別名ヘッダとして受け付ける。
- `player1_org1` から `player1_org5`、`player2_org1` から `player2_org5` は、選手ごとに所属を分けたい場合の上級ヘッダとして受け付ける。
- 旧 `organization` は `org1` として扱う。
- `player1_orgN` のみがあり、player2 が存在し、`player2_orgN` が空の場合は、player2 にも同じ所属値を保存する。
- `orgN` または `organizationN` があり、player2 が存在する場合は、player1 / player2 の両方に同じ所属値を保存する。
- 所属項目定義が存在しない場合は、必要な `orgN` を自動生成する。
- `org1` から `org5` の上限は、当面 `PARTICIPANT_ORGANIZATION_MAX_COUNT` で固定し、将来は大会ごとの利用項目数や有効項目に寄せる。

第1段階の運用整理:

- DB上は player1 / player2 それぞれの所属値を持てる構造にする。
- ただし通常運用・通常画面では、当面は player2 側の所属を個別入力・個別表示の対象にしない。
- 参加者作成・編集画面では、所属入力欄はペア共通の `org1` から `org5` を基本にする。
- ペア共通の所属値は、保存時に player1 / player2 の両方へ同じ値として保存する。
- 表示上は、当面 player1 側の `org1` から `org5` をペアの代表所属として扱う。
- player2 側の所属値は、将来「選手ごとに所属が異なる大会」や「表示設定で player1 / player2 の所属を分ける大会」に対応するための受け皿として残す。
- `player1_orgN` / `player2_orgN` はCSV取込の上級ヘッダとして受け付けるが、第1段階の通常CSVテンプレート・通常入力画面では前面に出さない。
- 旧 `organization` や `orgN` で取り込んだ場合は、ペア共通所属として扱い、player2 がいる場合は player2 側にも同じ値を保存する。

第1段階の標準ヘッダ:

```text
org1
org2
org3
org4
org5
```

読み取り互換ヘッダ:

```text
organization1
organization2
organization3
organization4
organization5

player1_org1
player1_org2
player1_org3
player1_org4
player1_org5

player2_org1
player2_org2
player2_org3
player2_org4
player2_org5
```

### 6. 表示の正データ

リーグ表、トーナメント表、採点票PDF、公開表示は、同じ表示元を使うべきです。

基本方針:

- 表示は有効表示値を使う。
- 選手名と短縮名は、`has_day_player_change=True` の場合だけ `current_*` を表示候補にする。
- `has_day_player_change=False` の場合は、`current_*` に値があっても `original_*` を表示する。
- 所属は `ParticipantOrganizationValue` の `org1` から `org5` を表示候補にする。
- 所属値がない項目は空白扱いにする。
- 当日変更表示は `has_day_player_change=True` を基準にする。
- 表示ブロック設定は、Participantの正データとは分ける。

第1弾では、画面やPDFが `original_*` / `current_*` を直接読まないように、`Participant` 側に有効表示値を返すプロパティを用意します。

候補:

```text
effective_player1_name
effective_player2_name
effective_player1_short_name
effective_player2_short_name
effective_player1_organizations
effective_player2_organizations
effective_organization
effective_display_name
effective_short_name
has_day_player_change
has_player1_override
has_player2_override
```

責務:

- `effective_player*_name` は、`has_day_player_change=True` かつ `current_player*_name` が `NULL` でなければそれを返し、それ以外は `original_player*_name` を返す。
- `effective_player*_short_name` は、当日変更ON時の短縮表示名、当初短縮表示名、氏名からの自動生成の順で補完する。
- `effective_player*_organizations` は、選手ごとの `org1` から `org5` を返す。値行が存在しない所属項目は空白扱いにする。
- `effective_organization` は、既存表示との互換用に、当面は `org1` を中心に返す。複数所属表示へ進める段階で表示設定側へ寄せる。
- `effective_display_name` / `effective_short_name` は、既存の `display_name` / `short_name` 相当の組み立て結果を返す。
- `has_*_override` は、当面は `has_day_player_change` と `current_*` の入力有無を組み合わせて判定する。

既存の `display_name` / `short_name` は、移行期間中は残してもよいが、内部では上記 `effective_*` を使うように寄せます。

### 7. 大会複製との関係

大会複製では、基本的に構造を複製し、結果や履歴は複製しません。

ただし、Byeの `winner_id` は単なる結果ではなく構造上の勝ち上がり情報として扱うため、欠落しないようにします。

Participantについては、複製時に当初情報と当日情報をどう扱うか決める必要があります。

現時点の候補:

- 複製先では、当初情報を複製する。
- 複製元の当日変更情報と当日変更履歴は原則として引き継がない。
- 所属項目定義と所属値は複製する。

## 第1弾の実装候補

まずは、次の範囲に絞るのが安全です。

1. `Participant` に選手ごとの当初/当日情報と `has_day_player_change` を追加する。
2. `TournamentOrganizationField` と `ParticipantOrganizationValue` を追加する。
3. 既存データを `original_*` へ移行し、`current_*` は未設定、`has_day_player_change=False` にする。
4. 既存 `organization` を `org1` として player1 / player2 の所属値へ移行する。
5. 表示系の参照を段階的に有効表示値ヘルパへ寄せる。
6. CSV取込では、標準ヘッダとして `org1` から `org5` を扱い、互換ヘッダとして `organization1` から `organization5`、`player1_org1` から `player1_org5`、`player2_org1` から `player2_org5` を扱う。
7. リーグ表、トーナメント表、採点票PDFで表示が崩れないことを確認する。

指定審判、汎用変更履歴、大会設定モデル整理は、第1弾の後に分けて進めます。

## 実装前チェックリスト

実装に入る前に、次の点を確認します。

- `TournamentOrganizationField` は大会単位で作る。カテゴリ単位にはしない。
- `code` はユーザー入力させず、`org1` から `org5` までを自動採番する。
- `label` は初期値を `所属1` から `所属5` とし、将来画面で変更可能にする。
- `ParticipantOrganizationValue` は値があるものだけ作り、値行がなければ空白扱いにする。
- 所属値の一意条件は `participant + player_no + field` とする。
- 既存 `organization` は移行時に `player1_org1` と `player2_org1` へコピーする。
- player2 が存在しない場合は、player2 側の所属値を作らない。
- CSV取込と入力画面の両方で、player1 の所属だけが入力され player2 が空なら、player2 に同じ値を保存する。
- 第1段階の通常画面では、player2 側の所属を個別編集対象にせず、ペア共通所属として player1 側の入力欄を扱う。
- ペア共通所属として入力した値は、player1 / player2 の両方の `ParticipantOrganizationValue` に保存する。
- 表示は当面 player1 側の所属値を代表値として使う。
- player2 側の所属値は、将来の選手別所属表示・個別所属編集のために保持する。
- CSVの第1段階正式ヘッダは `org1` から `org5` とする。
- `organization1` から `organization5` は別名として読み取る。
- `player1_org1` から `player1_org5`、`player2_org1` から `player2_org5` は選手別所属を使う場合の上級ヘッダとして読み取る。
- 日本語ヘッダ対応は第1段階では実装しない。
- `current_*` はCSV取込では設定しない。
- 当日選手変更は `has_day_player_change` を手動ON/OFFし、ONのときだけ `current_*` を表示候補にする。
- 旧 `player1_name` / `player2_name` / `organization` は移行完了後に削除するが、第1段階では互換のため一時的に残す。

## 未決事項

- 既存 `player1_name` / `player2_name` / `organization` をどのマイグレーションで削除するか。
- 短縮表示名をCSV必須にするか、未入力時だけ自動生成するか。
- 当日変更の表示方法を、色、印、注記のどれにするか。
- 参加者変更履歴モデルを第1弾に含めるか、次段階に分けるか。
- スナップショット復元時に、新旧カラム差をどう扱うか。

## 残した部分の実装タイミング

### 第1弾: DBの受け皿と互換表示

今回の実装範囲です。

- `Participant` に `original_*` / `current_*` / `has_day_player_change` を追加する。
- `TournamentOrganizationField` と `ParticipantOrganizationValue` を追加する。
- 既存 `player1_name` / `player2_name` / `organization` を新構造へ移行する。
- CSV取込で `player1_org1` から `player1_org5`、`player2_org1` から `player2_org5` を受け取れるようにする。
- 表示の入口である `display_name` / `short_name` / `display_organization` を有効表示値へ寄せる。
- 大会複製で所属項目定義と所属値を引き継ぐ。
- 既存画面の互換のため、旧 `player1_name` / `player2_name` / `organization` はまだ残す。

### 第2弾: 手入力・編集画面

参加者、ステージ、進行をCSV以外から作成・編集できる画面を作る段階で実装します。

- 参加者作成・編集画面で `org1` から `org5` を入力できるようにする。
- player1 の所属だけ入力された場合、player2 が存在すれば同じ値を補完する。
- 当日選手変更画面で `has_day_player_change` と `current_*` を扱う。
- 所属だけの修正は `ParticipantOrganizationValue.original_value` の編集として扱う。
- トーナメント枠編集、リーグ枠編集の選択肢を大会・カテゴリ内に制限する。

### 第3弾: 表示設定

トーナメント表、リーグ表、採点票PDFの表示を大会ごとに調整できるようにする段階で実装します。

- 所属項目 `label` を画面から編集できるようにする。
- 表示に使う所属項目、並び順、結合方法を設定できるようにする。
- 氏名、短縮名、所属、entry_code の表示ブロック設定を整理する。
- 当日選手変更ありの表示方法を、色、印、注記から選べるようにする。

### 第4弾: 履歴・Undo・旧カラム削除

運用中の事故対応や一般公開に向けて、履歴管理を強化する段階で実装します。

- 参加者変更、結果修正、進行変更、後続Stage反映、CSV取込、復元の履歴を持つ。
- 条件が整えば履歴からUndoできるようにする。
- スナップショット復元時に、新しい参加者・所属構造も対象に含める。
- 全画面の参照が新構造へ移った後で、旧 `player1_name` / `player2_name` / `organization` を削除する。
