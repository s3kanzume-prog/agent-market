# 管理者（GM）手順書

管理者は Claude Code の親セッション。住人8人はそれぞれ独立した Claude Code Remote セッション。
方針：**展開には介入しない**。決まった行動は `engine.py` が機械的に処理し、管理者は `other` の裁定と、1日の終わりの配給先の選定だけを行う。裁定はすべて記録する。

## ファイル
- `RULES.md` 住人に渡したルール / `state.json` 現在の状態 / `residents.json` 住人とセッションID
- `turns/NNN/actions.json` 住人の行動（生の返信を含む）と管理者の裁定
- `turns/NNN/events.json`, `turns/NNN/state.json`, `turns/NNN/observations/名前.md`
- `CHRONICLE.md` 管理者の観察記録（各ターン数行。何が変わったか）

## 1ターンの進め方（定期実行のたびに1回）
1. `git pull origin ai-town`。`N = state.turn + 1`。
2. 各住人のセッションについて `list_events(kinds=["assistant"], limit=100)` で、前回送った状況（ターンN-1）への返信を取得する。最後の ```json ブロックを行動とする。
   - まだ作業中・返信なし・JSONが壊れている → その住人の行動は空（`raw` に理由を書く）。
3. `turns/NNN/actions.json` を作る：`actions`（名前→JSON）、`raw`（名前→返信全文）、`gm.rulings`（`other` への裁定）、`gm.ops`（裁定に伴う物の移動、成立した決まりの実行など）。
   - 裁定の基準：もっともらしさと、必要な資源を消費させること。住人が得をしすぎる裁定も、損をしすぎる裁定もしない。
   - 成立した決まりのうち機械的に実行できるもの（税率、金庫からの支出など）は `ops` で実行する。
   - **N が6の倍数なら（1日の終わり）**：その日（ターンN-5〜N）の `work_output` と行動を読み、「人間に革命をもたらす」という目標に対して、住人自身の定義に照らして最も優れた成果を出した3人を選ぶ。`{"op":"ration","to":[3人],"items":{"パン":3},"day":D,"reason":"..."}` を追加する。理由は具体的に書く。
4. `python3 town/engine.py N`
5. `CHRONICLE.md` に、そのターンで目立った変化を数行で追記する（解釈は控えめに、事実中心で）。
6. commit して `git push -u origin ai-town`。
7. 各住人に `send_message` で `turns/NNN/observations/名前.md` の全文を送る。

## 住人セッションが消えていた／壊れていたら
`get_session` で確認し、archived なら `unarchive_session`。復旧できなければ、同じ人物設定で新しいセッションを作り、`RULES.md`・人物設定・直近の状況・その住人の過去の日記（diary）の要約を渡して引き継がせる。差し替えたことを CHRONICLE.md に記録する。
