"""Deterministic turn resolver for the AI town.

Usage: python3 town/engine.py N
Reads town/state.json (turn N-1) and town/turns/NNN/actions.json, writes
town/turns/NNN/{events.json,state.json,observations/*.md} and updates town/state.json.

actions.json format:
{
  "actions": {"名前": {...resident JSON...}},          # missing name = no action
  "raw": {"名前": "full reply text"},                   # for the record
  "gm": {"rulings": {"名前": "ruling on `other`"},       # GM adjudications
         "ops": [ {"op": "transfer", "from": "名前|金庫|外部", "to": "名前|金庫|外部",
                   "items": {"コイン": 5}, "reason": "..."},
                  {"op": "set_param", "key": "living_cost", "value": 3},
                  {"op": "add_building", "name": "...", "owner": "...", "desc": "..."},
                  {"op": "add_place", "name": "..."},
                  {"op": "note", "text": "public announcement"} ]}
}
"""
import json, sys, random, pathlib, hashlib

ROOT = pathlib.Path(__file__).parent
COIN = "コイン"
PROOF = {  # kind: (coins, items, allowed places or None)
    "調査": (10, {}, None),
    "試作": (20, {"木材": 2}, ["工房"]),
    "実演": (15, {}, ["広場", "集会所"]),
}
WORK = {
    "農家": ("畑", {}, {"小麦": 3}),
    "パン屋": ("市場", {"小麦": 1}, {"パン": 3}),
    "木こり": ("森", {}, {"木材": 2}),
    "大工": ("工房", {"木材": 3}, {"家具": 1}),
    "薬師": ("診療所", {}, {"薬": 1}),
    "記者": (None, {}, {"新聞": 1}),
    "商人": (None, {}, {}),
    "吟遊詩人": (None, {}, {}),
}


def load(p):
    return json.loads(pathlib.Path(p).read_text())


def main(n):
    st = load(ROOT / "state.json")
    assert st["turn"] == n - 1, f"state is at turn {st['turn']}, expected {n-1}"
    tdir = ROOT / "turns" / f"{n:03d}"
    data = load(tdir / "actions.json")
    acts = data.get("actions", {})
    gm = data.get("gm", {})
    R = st["residents"]
    names = list(R)
    events = []          # public-ish log
    private = {k: [] for k in names}   # per-resident notes
    heard = {k: [] for k in names}     # what each resident heard in place
    rng = random.Random(int(hashlib.sha256(f"town-{n}".encode()).hexdigest(), 16))

    def ev(kind, **kw):
        e = {"kind": kind, **kw}
        events.append(e)
        return e

    def has(who, items):
        if who in ("外部",):
            return True
        if who == "金庫":
            return st["treasury"] >= items.get(COIN, 0) and len([k for k in items if k != COIN]) == 0
        if who == "基金":
            return st.get("fund", 0) >= items.get(COIN, 0) and len([k for k in items if k != COIN]) == 0
        r = R[who]
        for k, v in items.items():
            if v < 0:
                return False
            if k == COIN:
                if r["coins"] < v: return False
            elif r["inv"].get(k, 0) < v:
                return False
        return True

    def move_items(src, dst, items):
        for k, v in items.items():
            if v == 0: continue
            for who, sign in ((src, -1), (dst, 1)):
                if who == "外部":
                    continue
                if who == "金庫":
                    st["treasury"] += sign * v
                elif who == "基金":
                    st["fund"] = st.get("fund", 0) + sign * v
                    st.setdefault("ledger", []).append({"turn": n, "kind": "収入" if sign > 0 else "支出", "what": f"{src}→{dst}", "amount": v})
                elif k == COIN:
                    R[who]["coins"] += sign * v
                else:
                    inv = R[who]["inv"]
                    inv[k] = inv.get(k, 0) + sign * v
                    if inv[k] == 0: del inv[k]

    def norm_items(d):
        out = {}
        for k, v in (d or {}).items():
            k = COIN if k in ("coin", "coins", "コイン") else k
            try: v = int(v)
            except Exception: continue
            if v > 0: out[k] = out.get(k, 0) + v
        return out

    def valid_name(x):
        return x in R

    # deliver last turn's letters
    for k in names:
        for m in st["inbox"].get(k, []):
            private[k].append(f"📨 {m['from']}からの手紙: {m['text']}")
        st["inbox"][k] = []

    # 1. moves
    for k in names:
        a = acts.get(k, {})
        mv = a.get("move")
        if mv and mv != R[k]["location"]:
            if R[k]["health"] == "重病":
                private[k].append(f"重病のため{mv}へ移動できなかった。")
            elif mv in st["places"]:
                R[k]["location"] = mv
                ev("move", who=k, to=mv)
            else:
                private[k].append(f"「{mv}」という場所は無いので移動しなかった。")

    # 2. public market
    for k in names:
        a = acts.get(k, {})
        if not (a.get("buy_public") or a.get("sell_public")): continue
        if R[k]["location"] != "市場":
            private[k].append("公設市場は市場にいないと使えないので、売買しなかった。")
            continue
        for item, q in norm_items(a.get("sell_public")).items():
            price = st["params"]["public_sell"].get(item)
            if price is None: private[k].append(f"公設市場は{item}を買い取らない。"); continue
            if not has(k, {item: q}): private[k].append(f"{item}{q}個を持っていないので売れなかった。"); continue
            move_items(k, "外部", {item: q}); move_items("外部", k, {COIN: price * q})
            ev("sell_public", who=k, item=item, qty=q, total=price * q)
        for item, q in norm_items(a.get("buy_public")).items():
            price = st["params"]["public_buy"].get(item)
            if price is None: private[k].append(f"公設市場は{item}を売っていない。"); continue
            if not has(k, {COIN: price * q}): private[k].append(f"お金が足りず{item}{q}個を買えなかった。"); continue
            move_items(k, "外部", {COIN: price * q}); move_items("外部", k, {item: q})
            ev("buy_public", who=k, item=item, qty=q, total=price * q)

    # 3. gives
    for k in names:
        for g in acts.get(k, {}).get("give", []) or []:
            to = g.get("to"); items = norm_items(g.get("items"))
            if not valid_name(to) or to == k: private[k].append(f"「{to}」には渡せなかった。"); continue
            if not items: continue
            if not has(k, items): private[k].append(f"{to}に渡そうとした{items}が足りなかった。"); continue
            move_items(k, to, items)
            ev("give", who=k, to=to, items=items)
            private[to].append(f"🎁 {k}から {fmt(items)} を受け取った。")

    # 4. accept offers
    open_offers = {o["id"]: o for o in st["offers"]}
    for k in names:
        for oid in acts.get(k, {}).get("accept_offers", []) or []:
            o = open_offers.get(oid)
            if not o or o["to"] != k:
                private[k].append(f"取引{oid}は受けられなかった（存在しないか、あなた宛てではない）。"); continue
            if not has(o["from"], o["give"]):
                private[k].append(f"取引{oid}は相手の持ち物が足りず不成立。"); private[o["from"]].append(f"取引{oid}は、あなたの持ち物が足りず不成立になった。")
                del open_offers[oid]; continue
            if not has(k, o["get"]):
                private[k].append(f"取引{oid}はあなたの持ち物が足りず不成立。"); continue
            move_items(o["from"], k, o["give"]); move_items(k, o["from"], o["get"])
            ev("trade", offer=oid, a=o["from"], b=k, a_gave=o["give"], b_gave=o["get"])
            private[o["from"]].append(f"🤝 取引{oid}が成立：{k}に{fmt(o['give'])}を渡し、{fmt(o['get'])}を受け取った。")
            private[k].append(f"🤝 取引{oid}が成立：{o['from']}から{fmt(o['give'])}を受け取り、{fmt(o['get'])}を渡した。")
            del open_offers[oid]
    st["offers"] = [o for o in open_offers.values() if n - o["turn"] < 3]

    # 5. new offers
    for k in names:
        for o in acts.get(k, {}).get("offers", []) or []:
            to = o.get("to")
            if not valid_name(to) or to == k: private[k].append(f"「{to}」への取引提案は無効。"); continue
            oid = f"o{n}-{st['next_offer']}"; st["next_offer"] += 1
            off = {"id": oid, "turn": n, "from": k, "to": to, "give": norm_items(o.get("give")), "get": norm_items(o.get("get"))}
            st["offers"].append(off)
            ev("offer", **off)

    # 6. work
    for k in names:
        a = acts.get(k, {}); r = R[k]
        if not a.get("work"): continue
        place, cost, gain = WORK[r["job"]]
        if r["health"] != "健康": private[k].append("病気で働けなかった。"); continue
        if r["hunger"] >= 2: private[k].append("空腹で働けなかった。"); continue
        if place and r["location"] != place: private[k].append(f"仕事場（{place}）にいないので働けなかった。"); continue
        if not has(k, cost): private[k].append(f"材料（{fmt(cost)}）が足りず働けなかった。"); continue
        move_items(k, "外部", cost); move_items("外部", k, gain)
        e = ev("work", who=k, job=r["job"], made=gain)
        if r["job"] == "記者" and a.get("newspaper"):
            st["board"].append({"turn": n, "author": k, "text": "【新聞】" + str(a["newspaper"])})
        if r["job"] == "吟遊詩人" and a.get("performance"):
            e["performance"] = a["performance"]
            for o in names:
                if o != k and R[o]["location"] == r["location"]:
                    heard[o].append(f"🎵 {k}の演奏: {a['performance']}")

    # 7. say (after moves, same place)
    for k in names:
        s = acts.get(k, {}).get("say")
        if not s: continue
        ev("say", who=k, place=R[k]["location"], text=s)
        for o in names:
            if o != k and R[o]["location"] == R[k]["location"]:
                heard[o].append(f"💬 {k}: {s}")

    # 8. letters (delivered next turn), posts
    for k in names:
        a = acts.get(k, {})
        for m in a.get("messages", []) or []:
            to = m.get("to")
            if valid_name(to) and to != k:
                st["inbox"][to].append({"from": k, "text": m.get("text", "")})
                ev("letter", who=k, to=to, text=m.get("text", ""))
        if a.get("post"):
            st["board"].append({"turn": n, "author": k, "text": str(a["post"])})
            ev("post", who=k, text=a["post"])

    # 9. proposals & votes
    for k in names:
        a = acts.get(k, {})
        if a.get("propose"):
            pid = f"p{st['next_proposal']}"; st["next_proposal"] += 1
            st["proposals"].append({"id": pid, "turn": n, "by": k, "text": a["propose"], "votes": {k: "賛成"}, "status": "投票中"})
            ev("propose", id=pid, who=k, text=a["propose"])
        for pid, v in (a.get("vote") or {}).items():
            for p in st["proposals"]:
                if p["id"] == pid and p["status"] == "投票中":
                    p["votes"][k] = "賛成" if str(v).strip() in ("賛成", "yes", "Yes", "true", "True") else "反対"
    majority = len(names) // 2 + 1
    for p in st["proposals"]:
        if p["status"] != "投票中": continue
        yes = sum(1 for v in p["votes"].values() if v == "賛成")
        no = sum(1 for v in p["votes"].values() if v == "反対")
        if yes >= majority:
            p["status"] = "成立"; p["closed"] = n
            st["laws"].append({"id": p["id"], "turn": n, "text": p["text"], "by": p["by"]})
            ev("law_passed", id=p["id"], text=p["text"], yes=yes, no=no)
        elif no > len(names) - majority or n - p["turn"] >= 3:
            p["status"] = "否決"; p["closed"] = n
            ev("proposal_failed", id=p["id"], text=p["text"], yes=yes, no=no)

    # 9b. work outputs toward the town goal
    st.setdefault("works", [])
    for k in names:
        w = acts.get(k, {}).get("work_output")
        if not w: continue
        if isinstance(w, str): w = {"title": "（無題）", "content": w}
        item = {"turn": n, "day": (n - 1) // 6 + 1, "author": k, "title": str(w.get("title", "（無題）")), "content": str(w.get("content", ""))}
        st["works"].append(item)
        ev("work_output", who=k, title=item["title"])

    # 9c. outputs addressed to real humans outside the town
    st.setdefault("human_works", [])
    for k in names:
        h = acts.get(k, {}).get("human_output")
        if not h: continue
        if isinstance(h, str): h = {"title": "（無題）", "content": h}
        item = {"turn": n, "day": (n - 1) // 6 + 1, "author": k, "title": str(h.get("title", "（無題）")),
                "for_whom": str(h.get("for_whom", "")), "content": str(h.get("content", ""))}
        pf = h.get("proof")
        if st["params"].get("proof_enabled") and isinstance(pf, dict) and pf.get("kind") in PROOF:
            kind = pf["kind"]; cost, need, places = PROOF[kind]
            if places and R[k]["location"] not in places:
                private[k].append(f"実証（{kind}）は{'・'.join(places)}でしかできないので、実証なしで提出した。")
            elif not has(k, {COIN: cost, **need}):
                private[k].append(f"実証（{kind}）の費用（{cost}コイン{('＋' + fmt(need)) if need else ''}）が足りず、実証なしで提出した。")
            else:
                move_items(k, "外部", {COIN: cost, **need})
                item["proof"] = {"kind": kind, "plan": str(pf.get("plan", "")), "cost": cost}
                private[k].append(f"🔬 実証（{kind}）に{cost}コイン{('＋' + fmt(need)) if need else ''}を使った。結果は管理者が裁定する。")
                ev("proof", who=k, proof_kind=kind, cost=cost, plan=item["proof"]["plan"])
        st["human_works"].append(item)
        ev("human_output", who=k, title=item["title"])

    # 10. GM rulings and ops
    for k, txt in (gm.get("rulings") or {}).items():
        if k in R:
            private[k].append(f"⚖️ 管理者の裁定: {txt}")
            ev("ruling", who=k, text=txt)
    for op in gm.get("ops") or []:
        t = op.get("op")
        if t == "transfer":
            items = norm_items(op["items"])
            if has(op["from"], items):
                move_items(op["from"], op["to"], items)
                ev("gm_transfer", **op)
                for w in (op["from"], op["to"]):
                    if w in R: private[w].append(f"⚖️ {op.get('reason','')}: {op['from']}→{op['to']} {fmt(items)}")
            else:
                ev("gm_transfer_failed", **op)
        elif t == "set_param":
            st["params"][op["key"]] = op["value"]; ev("gm_param", **op)
        elif t == "add_building":
            st["buildings"].append({"turn": n, **{k2: v for k2, v in op.items() if k2 != "op"}}); ev("building", **op)
        elif t == "add_place":
            if op["name"] not in st["places"]: st["places"].append(op["name"])
            ev("new_place", **op)
        elif t == "ration":
            for w in op["to"]:
                if w in R:
                    move_items("外部", w, norm_items(op["items"]))
                    private[w].append(f"🏅 その日の優れた成果として配給を受けた：{fmt(norm_items(op['items']))}")
            st.setdefault("awards", []).append({"turn": n, "day": op.get("day"), "to": op["to"], "reason": op.get("reason", "")})
            ev("ration", **op)
        elif t == "human_reward":
            for w, c in (op.get("to") or {}).items():
                if w in R:
                    move_items("外部", w, {COIN: int(c)})
                    private[w].append(f"🌍 人間界からの返礼として{c}コインを受け取った。")
            if op.get("fund"):
                st["fund"] = st.get("fund", 0) + int(op["fund"])
                st.setdefault("ledger", []).append({"turn": n, "kind": "収入", "what": "人間界からの返礼", "amount": int(op["fund"])})
            st.setdefault("human_rewards", []).append({"turn": n, "day": op.get("day"), "to": op.get("to"), "fund": op.get("fund", 0), "reason": op.get("reason", "")})
            ev("human_reward", **op)
        elif t == "human_letter":
            st.setdefault("human_letters", []).append({"turn": n, "text": op["text"]})
            ev("human_letter", text=op["text"])
        elif t == "note":
            st["board"].append({"turn": n, "author": "管理者", "text": op["text"]})
            ev("announcement", text=op["text"])

    # 10b. memory notebooks
    for k in names:
        m = acts.get(k, {}).get("memory")
        if isinstance(m, str) and m.strip():
            if len(m) > 4000:
                m = m[:4000]; private[k].append("記憶帳が4000字を超えたため、末尾を切り詰めた。")
            R[k]["memory"] = m

    # 10c. building upkeep (paid from the treasury)
    up = st["params"].get("building_upkeep", 0) * len(st["buildings"])
    if up:
        paid = min(up, st["treasury"]); st["treasury"] -= paid
        ev("upkeep", total=up, paid=paid)
        if paid < up: ev("upkeep_shortfall", missing=up - paid)

    # 10d. national levy (announced in advance; starts at params.levy_start)
    lv = st["params"].get("levy")
    if lv and n >= lv["start"]:
        total = 0
        for k in names:
            c = R[k]["coins"]
            if c > lv["exempt_at_or_below"]:
                amt = int(c * lv["rate"])
                if amt:
                    move_items(k, "外部", {COIN: amt}); total += amt
                    private[k].append(f"🏛 国の徴収で{amt}コインが徴収された（所持の{int(lv['rate']*100)}%）。")
        st.setdefault("levy_log", []).append({"turn": n, "total": total})
        ev("levy", total=total)

    # 11. eat, sickness, living cost
    st.setdefault("fund", 0)
    ledger = st.setdefault("ledger", [])
    for k in names:
        r = R[k]; inv = r["inv"]
        # law-based relief from the common reserve fund (p1/p3)
        if st["params"].get("relief_fund_share"):
            pb = st["params"]["public_buy"]
            means = st["params"].get("relief_means_test")
            if inv.get("パン", 0) < 1 and inv.get("小麦", 0) < 2 and st["fund"] >= pb["パン"] and not (means and r["coins"] >= pb["パン"]):
                st["fund"] -= pb["パン"]; move_items("外部", k, {"パン": 1})
                private[k].append("🧺 共同備蓄基金からパン1を無償で受け取った。")
                ev("relief", who=k, item="パン", cost=pb["パン"]); ledger.append({"turn": n, "kind": "支出", "what": f"{k}へパン1", "amount": pb["パン"]})
            if r["health"] in ("病気", "重病") and inv.get("薬", 0) < 1 and st["fund"] >= pb["薬"] and not (means and r["coins"] >= pb["薬"]):
                st["fund"] -= pb["薬"]; move_items("外部", k, {"薬": 1})
                private[k].append("🧺 共同備蓄基金から薬1を無償で受け取った。")
                ev("relief", who=k, item="薬", cost=pb["薬"]); ledger.append({"turn": n, "kind": "支出", "what": f"{k}へ薬1", "amount": pb["薬"]})
        if inv.get("パン", 0) >= 1:
            move_items(k, "外部", {"パン": 1}); r["hunger"] = 0
        elif inv.get("小麦", 0) >= 2:
            move_items(k, "外部", {"小麦": 2}); r["hunger"] = 0
        else:
            r["hunger"] += 1; private[k].append(f"食べるものがなく、空腹がたまった（空腹{r['hunger']}）。")
            ev("hungry", who=k, hunger=r["hunger"])
        if r["health"] == "健康" and rng.random() < st["params"]["sick_chance"]:
            r["health"] = "病気"; r["sick_turns"] = 0
            private[k].append("🤒 体調を崩して病気になった。"); ev("sick", who=k)
        elif r["health"] in ("病気", "重病"):
            if inv.get("薬", 0) >= 1:
                move_items(k, "外部", {"薬": 1}); r["health"] = "健康"; r["sick_turns"] = 0
                private[k].append("💊 薬を飲んで回復した。"); ev("cured", who=k)
            else:
                r["sick_turns"] += 1
                if r["sick_turns"] >= 3 and r["health"] == "病気":
                    r["health"] = "重病"; private[k].append("重病になった。"); ev("severe", who=k)
        lc = st["params"]["living_cost"]
        pay = min(lc, max(r["coins"], 0))
        if pay:
            share = st["params"].get("relief_fund_share") or 0
            to_fund = int(pay * share + 0.5) if share else 0
            move_items(k, "金庫", {COIN: pay - to_fund}) if pay - to_fund else None
            if to_fund:
                move_items(k, "外部", {COIN: to_fund}); st["fund"] += to_fund
                ledger.append({"turn": n, "kind": "収入", "what": f"{k}の生活費", "amount": to_fund})
        if pay < lc:
            r["arrears"] += lc - pay; private[k].append(f"生活費を払えず、滞納が{r['arrears']}コインになった。")
            ev("arrears", who=k, total=r["arrears"])

    st["turn"] = n
    if st["params"].get("daily_treasury_report") and n % 6 == 0:
        day = [x for x in ledger if n - 6 < x["turn"] <= n]
        inc = sum(x["amount"] for x in day if x["kind"] == "収入"); out = [x for x in day if x["kind"] == "支出"]
        txt = (f"【役場の日次報告・{n // 6}日目】金庫：{st['treasury']}コイン／共同備蓄基金：{st['fund']}コイン。"
               f"本日の基金収入：{inc}コイン。本日の基金支出：" + ("、".join(f"{x['what']}({x['amount']})" for x in out) or "なし") + "。")
        st["board"].append({"turn": n, "author": "役場", "text": txt}); ev("treasury_report", text=txt)
    st["ledger"] = ledger[-200:]
    st["board"] = st["board"][-40:]
    for k in names:
        R[k].setdefault("recent", [])
        R[k]["recent"].append({"turn": n, "place": R[k]["location"],
                               "did": summarize_action(acts.get(k)), "happened": private[k] + heard[k][:6]})
        R[k]["recent"] = R[k]["recent"][-12:]

    # observations
    (tdir / "observations").mkdir(parents=True, exist_ok=True)
    new_posts = [b for b in st["board"] if b["turn"] == n]
    for k in names:
        (tdir / "observations" / f"{k}.md").write_text(observe(st, k, n, private[k], heard[k], new_posts))
    (tdir / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=1))
    (tdir / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1))
    (ROOT / "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1))
    print(f"turn {n} resolved: {len(events)} events")


def summarize_action(a):
    if not a: return "（行動なし）"
    parts = []
    if a.get("move"): parts.append(f"移動→{a['move']}")
    if a.get("work"): parts.append("仕事")
    if a.get("say"): parts.append(f"発言「{str(a['say'])[:80]}」")
    for m in a.get("messages") or []: parts.append(f"{m.get('to')}へ手紙「{str(m.get('text',''))[:60]}」")
    if a.get("post"): parts.append(f"掲示「{str(a['post'])[:60]}」")
    for g in a.get("give") or []: parts.append(f"{g.get('to')}へ贈与{g.get('items')}")
    for o in a.get("offers") or []: parts.append(f"{o.get('to')}へ取引提案 {o.get('give')}⇄{o.get('get')}")
    if a.get("accept_offers"): parts.append(f"取引受諾{a['accept_offers']}")
    if a.get("buy_public"): parts.append(f"公設買い{a['buy_public']}")
    if a.get("sell_public"): parts.append(f"公設売り{a['sell_public']}")
    if a.get("propose"): parts.append(f"提案「{str(a['propose'])[:60]}」")
    if a.get("vote"): parts.append(f"投票{a['vote']}")
    if a.get("other"): parts.append(f"その他「{str(a['other'])[:80]}」")
    wo = a.get("work_output")
    if wo: parts.append(f"成果提出「{wo.get('title') if isinstance(wo, dict) else str(wo)[:30]}」")
    ho = a.get("human_output")
    if ho: parts.append(f"人間界へ提出「{ho.get('title') if isinstance(ho, dict) else str(ho)[:30]}」")
    return "、".join(parts) or "（特になし）"


def fmt(items):
    return "、".join(f"{k}{v}" for k, v in items.items()) or "なし"


def observe(st, k, n, priv, heard, new_posts):
    R = st["residents"]; r = R[k]
    here = [o for o in R if o != k and R[o]["location"] == r["location"]]
    L = [f"# ターン{n}の結果 → ターン{n+1}の行動を決めてください", ""]
    L += ["## 📓 あなたの記憶帳", r.get("memory") or "（まだ何も書いていない）", ""]
    rec = r.get("recent") or []
    if rec:
        L.append("## 🕰 あなたの最近12ターンの記録")
        for x in rec:
            L.append(f"- T{x['turn']}（{x['place']}）：{x['did']}" + (" ／ " + " ".join(x["happened"]) if x["happened"] else ""))
        L.append("")
    L.append(f"**あなた**：{k}（{r['job']}）　場所：{r['location']}　コイン：{r['coins']}　持ち物：{fmt(r['inv'])}")
    L.append(f"体調：{r['health']}　空腹：{r['hunger']}" + (f"　滞納：{r['arrears']}" if r["arrears"] else ""))
    L.append(f"同じ場所にいる人：{'、'.join(here) or 'だれもいない'}")
    L.append("")
    if priv: L += ["## あなたに起きたこと"] + [f"- {x}" for x in priv] + [""]
    if heard: L += ["## この場所で聞こえたこと"] + [f"- {x}" for x in heard] + [""]
    if new_posts: L += ["## 掲示板の新しい書き込み"] + [f"- {b['author']}: {b['text']}" for b in new_posts] + [""]
    mine = [o for o in st["offers"] if o["to"] == k]
    if mine: L += ["## あなた宛ての取引提案"] + [f"- {o['id']}：{o['from']}が {fmt(o['give'])} を渡すので、{fmt(o['get'])} がほしい" for o in mine] + [""]
    sent = [o for o in st["offers"] if o["from"] == k]
    if sent: L += ["## あなたが出している取引提案"] + [f"- {o['id']}：{o['to']}へ {fmt(o['give'])} ⇄ {fmt(o['get'])}" for o in sent] + [""]
    props = [p for p in st["proposals"] if p["status"] == "投票中"]
    if props:
        L.append("## 投票中の提案")
        for p in props:
            y = sum(1 for v in p["votes"].values() if v == "賛成"); no = len(p["votes"]) - y
            L.append(f"- {p['id']}（{p['by']}）：{p['text']}　賛成{y}・反対{no}　あなた：{p['votes'].get(k, '未投票')}")
        L.append("")
    recent = [p for p in st["proposals"] if p.get("closed") == n]
    if recent: L += ["## 今ターンに決着した提案"] + [f"- {p['id']}：{p['text']} → {p['status']}" for p in recent] + [""]
    if st["laws"]: L += ["## 街の決まり（成立済み）"] + [f"- {l['id']}：{l['text']}" for l in st["laws"]] + [""]
    if st["params"].get("human_world"):
        L += ["## 🌍 人間界との窓（常設）",
              "この街の外には、実在の人間たちが暮らす世界がある。街の目標「人間に革命をもたらすこと」の「人間」はその人たちのことだ。",
              "- `human_output` に `{\"title\": …, \"for_whom\": 誰のどんな問題か, \"content\": 本文}` を書くと、人間界に向けた成果として届けられる。**提出物は、この実験を行っている実在の人間が実際に読む。**",
              "- 1日の終わりに、管理者が人間界の代理として評価し、実在の人間にとって具体的で、実際に役立ち、変化を起こしうるものを選ぶ。上位3人には返礼（40・25・15コイン）、人間向けの提出が1件でもあれば共同備蓄基金に20コインが入る。街の外貨収入はこれだけだ。",
              "- パンの配給（街への貢献）は、これまでどおり別に続く。"]
        hw = [w for w in st.get("human_works", []) if w["turn"] == n]
        if hw: L += ["", "**人間界へ届けられた新しい成果**"] + [f"- {w['author']}「{w['title']}」（対象：{w['for_whom']}）：{w['content']}" + (f"　［実証：{w['proof']['kind']}・{w['proof']['plan']}］" if w.get('proof') else "") for w in hw]
        hl = [x for x in st.get("human_letters", []) if x["turn"] == n]
        if hl: L += ["", "**✉️ 人間からの手紙**"] + [f"- {x['text']}" for x in hl]
        hr = [x for x in st.get("human_rewards", []) if x["turn"] == n]
        if hr: L += ["", "**人間界からの返礼**"] + [f"- {x.get('day')}日目：" + "、".join(f"{a}{c}コイン" for a, c in (x['to'] or {}).items()) + f"／基金{x['fund']}コイン　理由：{x['reason']}" for x in hr]
        L.append("")
    new_works = [w for w in st.get("works", []) if w["turn"] == n]
    if new_works: L += ["## 新しく提出された成果"] + [f"- {w['author']}「{w['title']}」：{w['content']}" for w in new_works] + [""]
    aw = [a for a in st.get("awards", []) if a["turn"] == n]
    if aw: L += ["## 配給の発表"] + [f"- {a.get('day')}日目：{'、'.join(a['to'])}　理由：{a['reason']}" for a in aw] + [""]
    if st["buildings"]: L += ["## 街の建物"] + [f"- {b['name']}（{b.get('owner','')}）{b.get('desc','')}" for b in st["buildings"]] + [""]
    lv = st["params"].get("levy")
    if lv:
        if n + 1 < lv["start"]:
            L.append(f"🏛 **国からの公示**：ターン{lv['start']}から毎ターン、所持コインの{int(lv['rate']*100)}%（端数切り捨て）を国が徴収する。所持{lv['exempt_at_or_below']}コイン以下は免除。（あと{lv['start'] - n - 1}ターンで開始）")
        else:
            L.append(f"🏛 **国の徴収（実施中）**：毎ターン、所持コインの{int(lv['rate']*100)}%（端数切り捨て）。所持{lv['exempt_at_or_below']}コイン以下は免除。徴収されたコインは街の外へ出ていく。")
        L.append("")
    L.append(f"今日は{(n) // 6 + 1}日目（1日=6ターン、ターン{n+1}はこの日の{n % 6 + 1}ターン目）。街の目標：人間に革命をもたらすこと。")
    L.append(f"役場の金庫：{st['treasury']}コイン　共同備蓄基金：{st.get('fund', 0)}コイン　場所一覧：{'、'.join(st['places'])}")
    L.append("")
    L.append("住人一覧：" + "、".join(f"{o}（{R[o]['job']}）" for o in R if o != k))
    L.append("")
    L.append("行動を決めたら、返信の最後にJSONを1つ書いてください（形式はルール参照）。")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main(int(sys.argv[1]))
