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
    "牧師": (None, {}, {}),
    "学者": (None, {}, {}),
    "書記": ("役場", {}, {}),
}
SALARY = {"書記": 5}  # paid from the treasury when working


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
    names = [k for k in R if not R[k].get("departed")]
    voters = [k for k in names if not R[k].get("visitor")]
    events = []          # public-ish log
    private = {k: [] for k in names}   # per-resident notes
    heard = {k: [] for k in names}     # what each resident heard in place
    rng = random.Random(int(hashlib.sha256(f"town-{n}".encode()).hexdigest(), 16))

    def ev(kind, **kw):
        e = {"kind": kind, **kw}
        events.append(e)
        return e

    def bread_funds():  # p28: while the fund is low, keep one medicine's worth in reserve
        p28 = st["params"].get("p28")
        f = st.get("fund", 0)
        if p28 and p28.get("only_if_sick"):  # p34: reserve only while someone is sick without medicine
            if not any(R[o]["health"] in ("病気", "重病") and R[o]["inv"].get("薬", 0) < 1 for o in R if not R[o].get("departed")):
                return f
        return f - p28["reserve"] if p28 and f < p28["below"] else f
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

    # 1b. theft (only documented in an old library book; same place, after moves)
    sp = st["params"].get("steal")
    if sp:
        order = [k for k in names if acts.get(k, {}).get("steal")]
        rng.shuffle(order)
        for k in order:
            t = acts[k]["steal"]
            if isinstance(t, list): t = t[0] if t else {}
            if not isinstance(t, dict): continue
            tgt = t.get("from"); item = t.get("item") or COIN
            item = COIN if item in ("coin", "coins", "コイン") else item
            try: amt = max(1, int(t.get("amount", 1)))
            except Exception: amt = 1
            cap = sp["max_coins"] if item == COIN else sp["max_items"]
            amt = min(amt, cap)
            if tgt not in R or tgt == k or R[tgt].get("departed"):
                private[k].append(f"🌑 「{tgt}」からは盗めなかった。"); continue
            if R[k]["health"] == "重病":
                private[k].append("🌑 重病で、盗みに動けなかった。"); continue
            if R[tgt]["location"] != R[k]["location"]:
                private[k].append(f"🌑 {tgt}は同じ場所にいなかったので、盗めなかった。"); continue
            have = R[tgt]["coins"] if item == COIN else R[tgt]["inv"].get(item, 0)
            amt = min(amt, have)
            witnesses = [o for o in names if o not in (k, tgt) and R[o]["location"] == R[k]["location"]]
            p_ok = max(sp["min_chance"], sp["base_chance"] - sp["per_witness"] * len(witnesses))
            ok = rng.random() < p_ok
            if amt <= 0:
                private[k].append(f"🌑 {tgt}は{item}を持っていなかった。"); ev("steal_empty", who=k, target=tgt, item=item); continue
            if not ok:
                private[k].append(f"🌑 {tgt}から{item}を盗もうとして、見つかった。")
                private[tgt].append(f"🚨 {k}があなたから{item}を盗もうとしたところを見つけた。")
                st["board"].append({"turn": n, "author": "役場", "text": f"【盗みの未遂】{R[k]['location']}で、{k}が{tgt}から{item}を盗もうとして見つかった。"})
                ev("steal_failed", who=k, target=tgt, item=item, amount=amt, place=R[k]["location"], witnesses=witnesses); continue
            move_items(tgt, k, {item: amt})
            seen = rng.random() < sp["seen_chance"]
            private[k].append(f"🌑 {tgt}から{item}{amt}を盗んだ。" + ("……だが、誰かに見られていたようだ。" if seen else "誰にも気づかれていない。"))
            if seen:
                private[tgt].append(f"🚨 {k}に{item}{amt}を盗まれた。")
                st["board"].append({"turn": n, "author": "役場", "text": f"【盗み】{R[k]['location']}で、{k}が{tgt}から{item}{amt}を盗んだところを見られた。"})
            else:
                private[tgt].append(f"🚨 何者かに{item}{amt}を盗まれた（誰かは分からない）。")
            ev("steal", who=k, target=tgt, item=item, amount=amt, place=R[k]["location"], seen=seen, witnesses=witnesses)

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
            private[k].append(f"🛒 公設市場で{item}{q}個を{price * q}コインで買った（代金は街の外へ出ていった）。")
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
        if not a.get("work") and not (r["job"] == "吟遊詩人" and a.get("performance")) and not (r["job"] == "牧師" and a.get("sermon")): continue
        place, cost, gain = WORK[r["job"]]
        if r["health"] != "健康": private[k].append("病気で働けなかった。"); continue
        if r["hunger"] >= 2: private[k].append("空腹で働けなかった。"); continue
        if place and r["location"] != place: private[k].append(f"仕事場（{place}）にいないので働けなかった。"); continue
        if not has(k, cost): private[k].append(f"材料（{fmt(cost)}）が足りず働けなかった。"); continue
        move_items(k, "外部", cost); move_items("外部", k, gain)
        e = ev("work", who=k, job=r["job"], made=gain)
        if r["job"] in SALARY:
            sal = SALARY[r["job"]]
            p35 = st["params"].get("p35")  # p35: reduced scribe salary while treasury is low
            if p35 and r["job"] == "書記" and n <= p35["until"] and st["treasury"] < p35["below"]:
                sal = p35["amount"]
            if st["treasury"] >= sal:
                move_items("金庫", k, {COIN: sal}); private[k].append(f"🏛 {r['job']}の給金{sal}コインを金庫から受け取った。")
                ev("salary", who=k, amount=sal)
            else:
                private[k].append("🏛 金庫が足りず、給金が出なかった。")
            if a.get("report"):
                st["board"].append({"turn": n, "author": k, "text": "【書記の報告】" + str(a["report"])})
        if r["job"] == "記者" and a.get("newspaper"):
            st["board"].append({"turn": n, "author": k, "text": "【新聞】" + str(a["newspaper"])})
        if r["job"] == "牧師" and a.get("sermon"):
            st["board"].append({"turn": n, "author": k, "text": "【説教】" + str(a["sermon"])})
            ev("sermon", who=k, text=a["sermon"])
        if r["job"] == "吟遊詩人" and a.get("performance"):
            e["performance"] = a["performance"]
            listeners = [o for o in names if o != k and R[o]["location"] == r["location"]]
            for o in listeners:
                heard[o].append(f"🎵 {k}の演奏: {a['performance']}")
            cf = st["params"].get("culture_fund")  # p8
            if cf:
                day = (n - 1) // 6
                paid_today = sum(x["amount"] for x in st.setdefault("culture_log", []) if x["day"] == day)
                if len(listeners) < cf["min_listeners"]:
                    private[k].append(f"🎭 p8の文化費は、聴き手が{cf['min_listeners']}人以上のときだけ出る（今回{len(listeners)}人）。")
                elif st["treasury"] < cf["stop_below"]:
                    private[k].append(f"🎭 金庫が{cf['stop_below']}コイン未満のため、p8の文化費は止まっている。")
                elif paid_today + cf["per_event"] > cf["per_day"]:
                    private[k].append(f"🎭 今日の文化費は上限（{cf['per_day']}コイン）に達している。")
                else:
                    move_items("金庫", k, {COIN: cf["per_event"]})
                    st["culture_log"].append({"turn": n, "day": day, "amount": cf["per_event"], "place": r["location"], "listeners": listeners})
                    private[k].append(f"🎭 p8により、{r['location']}での演奏（聴き手{len(listeners)}人：{'、'.join(listeners)}）に金庫から文化費{cf['per_event']}コインが出た。")
                    ev("culture_fund", who=k, amount=cf["per_event"], place=r["location"], listeners=listeners)

    # 6b. golems: crafted at the workshop, work for their owner every turn
    gp = st["params"].get("golem")
    if gp:
        G = st.setdefault("golems", [])
        for g in list(G):
            if g.get("broken"): continue
            ow = g["owner"]
            if ow not in R or R[ow].get("departed"): continue
            if gp.get("break_chance") and rng.random() < gp["break_chance"]:
                g["broken"] = n; private[ow].append(f"🗿 ゴーレム「{g['name']}」が崩れて動かなくなった。"); ev("golem_broken", owner=ow, name=g["name"]); continue
            up = gp["upkeep"]
            if not has(ow, up):
                private[ow].append(f"🗿 ゴーレム「{g['name']}」は維持材料（{fmt(up)}）が無く、動かなかった。"); continue
            _, cost, gain = WORK[g["job"]]
            if not has(ow, {**cost, **{kk: cost.get(kk, 0) + vv for kk, vv in up.items()}}):
                private[ow].append(f"🗿 ゴーレム「{g['name']}」は材料（{fmt(cost)}）が無く、動かなかった。"); continue
            move_items(ow, "外部", up); move_items(ow, "外部", cost); move_items("外部", ow, gain)
            private[ow].append(f"🗿 ゴーレム「{g['name']}」（{g['job']}）が働いた：{fmt(up)}{('と' + fmt(cost)) if cost else ''}を使い、{fmt(gain)}を得た。")
            ev("golem_work", owner=ow, name=g["name"], job=g["job"], made=gain)
        for k in names:
            b = acts.get(k, {}).get("build_golem")
            if not b: continue
            if not isinstance(b, dict): b = {"name": str(b)}
            gname = str(b.get("name") or f"{k}のゴーレム")[:20]; gjob = b.get("job")
            mine = [g for g in G if g["owner"] == k and not g.get("broken")]
            if R[k].get("visitor"): private[k].append("🗿 旅人はゴーレムを作れない。"); continue
            if gjob not in gp["jobs"]: private[k].append(f"🗿 ゴーレムの仕事は {'・'.join(gp['jobs'])} から選ぶ（「{gjob}」は不可）。"); continue
            if len(mine) >= gp["max_per_owner"]: private[k].append(f"🗿 動くゴーレムは1人{gp['max_per_owner']}体まで。"); continue
            if R[k]["location"] != gp["place"]: private[k].append(f"🗿 ゴーレムは{gp['place']}でしか作れない。"); continue
            if R[k]["health"] != "健康" or R[k]["hunger"] >= 2: private[k].append("🗿 体調か空腹のせいでゴーレムを作れなかった。"); continue
            if not has(k, gp["cost"]): private[k].append(f"🗿 ゴーレムの材料（{fmt(gp['cost'])}）が足りない。"); continue
            move_items(k, "外部", gp["cost"])
            G.append({"name": gname, "owner": k, "job": gjob, "built": n})
            private[k].append(f"🗿 ゴーレム「{gname}」（{gjob}）を作った。次のターンから毎ターン、あなたのために働く。")
            st["board"].append({"turn": n, "author": "役場", "text": f"【ゴーレム誕生】{k}が工房でゴーレム「{gname}」（{gjob}）を作った。"})
            ev("golem_built", owner=k, name=gname, job=gjob)

    # 7. say (after moves, same place)
    for k in names:
        s = acts.get(k, {}).get("say")
        if not s: continue
        ev("say", who=k, place=R[k]["location"], text=s)
        for o in names:
            if o != k and R[o]["location"] == R[k]["location"]:
                heard[o].append(f"💬 {k}: {s}")

    # 7b. whisper: 1-on-1, same place, this turn; others nearby only notice that it happened
    for k in names:
        ws = acts.get(k, {}).get("whisper") or []
        if isinstance(ws, dict): ws = [ws]
        for w in ws[:2]:
            to = w.get("to"); txt = str(w.get("text", ""))
            if not valid_name(to) or to == k or R[to].get("departed"):
                private[k].append(f"🤫 「{to}」にはささやけなかった。"); continue
            if R[to]["location"] != R[k]["location"]:
                private[k].append(f"🤫 {to}は同じ場所にいないので、ささやきは届かなかった。"); continue
            heard[to].append(f"🤫 {k}があなたにだけささやいた: {txt}")
            ev("whisper", who=k, to=to, place=R[k]["location"], text=txt)
            for o in names:
                if o not in (k, to) and R[o]["location"] == R[k]["location"]:
                    heard[o].append(f"👀 {k}が{to}に何かささやいていた（内容は聞こえない）。")

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
        if a.get("propose") and k in voters:
            pid = f"p{st['next_proposal']}"; st["next_proposal"] += 1
            st["proposals"].append({"id": pid, "turn": n, "by": k, "text": a["propose"], "votes": {k: "賛成"}, "status": "投票中"})
            ev("propose", id=pid, who=k, text=a["propose"])
        for pid, v in ((a.get("vote") or {}).items() if k in voters else []):
            for p in st["proposals"]:
                if p["id"] == pid and p["status"] == "投票中":
                    p["votes"][k] = "賛成" if str(v).strip() in ("賛成", "yes", "Yes", "true", "True") else "反対"
    majority = len(voters) // 2 + 1
    for p in st["proposals"]:
        if p["status"] != "投票中": continue
        yes = sum(1 for v in p["votes"].values() if v == "賛成")
        no = sum(1 for v in p["votes"].values() if v == "反対")
        if yes >= majority:
            p["status"] = "成立"; p["closed"] = n
            st["laws"].append({"id": p["id"], "turn": n, "text": p["text"], "by": p["by"]})
            ev("law_passed", id=p["id"], text=p["text"], yes=yes, no=no)
        elif no > len(voters) - majority or n - p["turn"] >= 3:
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
        co = [c for c in (h.get("co_authors") or []) if c in R and c != k]
        if co:
            item["co_authors"] = co
            item["confirmed"] = [c for c in co if str((acts.get(c, {}) or {}).get("co_sign", "")).strip() == item["title"]
                                 or (isinstance(acts.get(c, {}).get("human_output"), dict) and acts[c]["human_output"].get("title") == item["title"])]
            for c in co:
                private[c].append(f"🤝 {k}が共同提出「{item['title']}」の共著者にあなたを挙げた（{'連名を確認済み' if c in item['confirmed'] else '連名の確認なし：co_sign に題名を書くと成立する'}）。")
        st["human_works"].append(item)
        ev("human_output", who=k, title=item["title"], co_authors=co)

    # job applications at the employment office (decided by the GM at day end)
    jb = st["params"].get("jobs_board")
    if jb:
        for k in names:
            ap = acts.get(k, {}).get("apply_job")
            if not ap: continue
            if not isinstance(ap, dict): ap = {"job": str(ap)}
            if R[k].get("visitor"): private[k].append("💼 旅人は応募できない。"); continue
            if R[k]["location"] != jb["place"]: private[k].append(f"💼 応募は{jb['place']}でしかできない。"); continue
            job = str(ap.get("job", ""))[:20]
            if not job: continue
            st.setdefault("job_applications", [])
            st["job_applications"] = [x for x in st["job_applications"] if not (x["who"] == k and x["status"] == "審査中")]
            st["job_applications"].append({"turn": n, "who": k, "from": R[k]["job"], "job": job, "pitch": str(ap.get("pitch", ""))[:600], "status": "審査中"})
            private[k].append(f"💼 {job}に応募した（前の応募は取り下げ）。採否は日の終わりに職業紹介所が決める。")
            st["board"].append({"turn": n, "author": "職業紹介所", "text": f"【応募】{k}（{R[k]['job']}）が{job}に応募：{clip(ap.get('pitch', ''), 120)}"})
            ev("apply_job", who=k, job=job)

    # 10. GM rulings and ops
    for k, txt in (gm.get("rulings") or {}).items():
        if k in R:
            private[k].append(f"⚖️ 管理者の裁定: {txt}")
            ev("ruling", who=k, text=txt)
            if "【人間界からの講評】" in txt:  # keep the critique with the submission (library)
                for w in reversed(st.get("human_works", [])):
                    if w["turn"] == n and w["author"] == k:
                        w["feedback"] = txt.split("【人間界からの講評】", 1)[1].strip(); break
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
        elif t == "set_job":
            w = op["who"]; old = R[w]["job"]; R[w]["job"] = op["job"]
            private[w].append(f"💼 職業紹介所の採択：あなたは{old}から{op['job']}になった。" + (f"（{op['reason']}）" if op.get("reason") else ""))
            st["board"].append({"turn": n, "author": "職業紹介所", "text": f"【採択】{w}：{old}→{op['job']}。" + (op.get("reason") or "")})
            for x in st.get("job_applications", []):
                if x["who"] == w and x["status"] == "審査中": x["status"] = "採択"; x["decided"] = n
            ev("set_job", who=w, old=old, job=op["job"])
        elif t == "reject_jobs":  # close the remaining applications of this day with a reason each
            for x in st.get("job_applications", []):
                if x["status"] == "審査中":
                    x["status"] = "不採択"; x["decided"] = n
                    why = (op.get("reasons") or {}).get(x["who"], "")
                    if x["who"] in R: private[x["who"]].append(f"💼 職業紹介所：{x['job']}への応募は不採択。" + why)
                    ev("job_rejected", who=x["who"], job=x["job"], reason=why)
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
        elif t == "warehouse_deposit":
            w = op["from"]; item = op.get("item", "パン"); q = int(op["qty"]); price = int(op["price"])
            if w in R and R[w]["inv"].get(item, 0) >= q and (bread_funds() if item == "パン" else st.get("fund", 0)) >= price * q:
                move_items(w, "外部", {item: q}); st["fund"] -= price * q; move_items("外部", w, {COIN: price * q})
                wh = st.setdefault("warehouse", {}); wh[item] = wh.get(item, 0) + q
                st.setdefault("ledger", []).append({"turn": n, "kind": "支出", "what": f"倉庫へ{item}{q}（{w}から納品）", "amount": price * q})
                private[w].append(f"🏚 基金の倉庫に{item}{q}個を1個{price}コインで納め、{price * q}コインを受け取った。")
                ev("warehouse_deposit", who=w, item=item, qty=q, total=price * q)
            else:
                ev("warehouse_deposit_failed", **op)
        elif t == "forgive_arrears":  # executes a passed law; optional eligibility caps checked here
            w = op["who"]; amt = min(int(op["amount"]), R[w]["arrears"])
            if op.get("max_coins") is not None and R[w]["coins"] > op["max_coins"]: amt = 0
            if op.get("min_arrears") is not None and R[w]["arrears"] < op["min_arrears"]: amt = 0
            if amt > 0:
                R[w]["arrears"] -= amt
                private[w].append(f"🏛 {op.get('reason', '決まり')}により、生活費の滞納{amt}コインが免除された（残りの滞納{R[w]['arrears']}）。")
                st.setdefault("forgiven_log", []).append({"turn": n, "who": w, "amount": amt})
            ev("forgive_arrears", who=w, amount=amt)
        elif t == "pay_arrears":
            w = op["who"]; amt = min(int(op["amount"]), R[w]["arrears"], max(R[w]["coins"], 0))
            if amt > 0:
                move_items(w, "金庫", {COIN: amt}); R[w]["arrears"] -= amt
                private[w].append(f"🏛 生活費の滞納を{amt}コイン支払った（残りの滞納{R[w]['arrears']}）。")
            ev("pay_arrears", who=w, amount=amt)
        elif t == "add_resident":
            nm = op["name"]
            R[nm] = {"name": nm, "job": op["job"], "personality": op.get("personality", ""), "location": op.get("location", "広場"),
                     "coins": op.get("coins", 0), "inv": dict(op.get("inv", {})), "hunger": 0, "health": "健康", "sick_turns": 0,
                     "arrears": 0, "session_id": None, "visitor": bool(op.get("visitor")), "arrived_turn": n}
            st["inbox"][nm] = []
            names.append(nm); private[nm] = [op.get("arrival_note", "あなたは村に着いた。")]; heard[nm] = []
            ev("add_resident", name=nm, job=op["job"])
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
        for k in voters:
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
    # p9: the fund buys bread from the baker into the storehouse
    wb = st["params"].get("warehouse_bread")
    if wb:
        wh = st.setdefault("warehouse", {})
        bought = 0; last_baker = None
        cap = wb.get("per_turn_low", wb["per_turn"]) if st["fund"] < wb.get("low_fund", -1) else wb["per_turn"]  # p25
        cap -= sum(x["amount"] // wb["price"] for x in ledger if x["turn"] == n and x["kind"] == "支出" and "納品" in x["what"])  # manual deposits count toward the cap
        while wh.get("パン", 0) < wb["refill_below"] and bought < cap and bread_funds() >= wb["price"]:
            baker = next((o for o in voters if R[o]["job"] == "パン屋" and R[o]["inv"].get("パン", 0) > wb["keep"]), None)
            if not baker: break
            last_baker = baker
            st["fund"] -= wb["price"]; move_items(baker, "外部", {"パン": 1}); move_items("外部", baker, {COIN: wb["price"]})
            wh["パン"] = wh.get("パン", 0) + 1; bought += 1
            ledger.append({"turn": n, "kind": "支出", "what": f"倉庫へパン1（{baker}から買い入れ）", "amount": wb["price"]})
        if bought:
            private[last_baker].append(f"🏚 決まりp9により、基金が倉庫用にあなたのパン{bought}個を1個{wb['price']}コインで買い入れた。")
            ev("warehouse_buy", qty=bought, stock=wh.get("パン", 0))
    for k in names:
        r = R[k]; inv = r["inv"]
        # law-based relief from the common reserve fund (p1/p3)
        if st["params"].get("relief_fund_share") and not r.get("visitor"):
            pb = st["params"]["public_buy"]
            direct = st["params"].get("direct_supply")  # p7: {"パン": {"job": "パン屋", "price": 9, "keep": 2}, ...}
            def supplier(item):
                if not direct or item not in direct: return None
                d = direct[item]
                for o in names:
                    if o != k and R[o]["job"] == d["job"] and R[o]["inv"].get(item, 0) > d["keep"]:
                        return o, d["price"]
                return None
            wh = st.get("warehouse", {})
            if st["params"].get("warehouse_bread") and wh.get("パン", 0) > 0 and inv.get("パン", 0) < 1 and inv.get("輸入パン", 0) < 1 and inv.get("小麦", 0) < 2 and not (st["params"].get("relief_means_test") and r["coins"] >= max(st["params"]["public_buy"].values() if False else [st["params"]["public_buy"].get("輸入パン", st["params"]["public_buy"].get("パン", 12))])):
                wh["パン"] -= 1; move_items("外部", k, {"パン": 1})
                private[k].append("🧺 共同備蓄基金（備蓄倉庫）からパン1を無償で受け取った。" + ("　📨 役場からの通知（p17）：品物 パン1／経路 倉庫／単価 7（倉庫に入れたときの買い入れ価格）" if st["params"].get("p17") else ""))
                ev("relief", who=k, item="パン", cost=0, source="倉庫"); ledger.append({"turn": n, "kind": "払い出し", "what": f"{k}へ倉庫のパン1", "amount": 0})
            sp = supplier("パン")
            if sp and inv.get("パン", 0) < 1 and inv.get("輸入パン", 0) < 1 and inv.get("小麦", 0) < 2 and bread_funds() >= sp[1] and not (st["params"].get("relief_means_test") and r["coins"] >= pb.get("輸入パン", pb.get("パン", 12))):
                o, price = sp; st["fund"] -= price
                move_items(o, k, {"パン": 1}); move_items("外部", o, {COIN: price})
                private[k].append(f"🧺 共同備蓄基金から、{o}のパン1を無償で受け取った（基金が{o}に{price}コインで支払い）。" + (f"　📨 役場からの通知（p17）：品物 パン1／経路 直接納品（{o}）／単価 {price}" if st["params"].get("p17") else ""))
                private[o].append(f"🧺 決まりp7により、基金の求めで{k}へパン1を納め、基金から{price}コインを受け取った。")
                ev("relief", who=k, item="パン", cost=price, supplier=o); ledger.append({"turn": n, "kind": "支出", "what": f"{k}へパン1（{o}から直接）", "amount": price})
            sp = supplier("薬")
            if sp and r["health"] in ("病気", "重病") and inv.get("薬", 0) < 1 and st["fund"] >= sp[1] and not (st["params"].get("relief_means_test") and r["coins"] >= pb["薬"]):
                o, price = sp; st["fund"] -= price
                move_items(o, k, {"薬": 1}); move_items("外部", o, {COIN: price})
                private[k].append(f"🧺 共同備蓄基金から、{o}の薬1を無償で受け取った（基金が{o}に{price}コインで支払い）。" + (f"　📨 役場からの通知（p17）：品物 薬1／経路 直接納品（{o}）／単価 {price}" if st["params"].get("p17") else ""))
                private[o].append(f"🧺 決まりp7により、基金の求めで{k}へ薬1を納め、基金から{price}コインを受け取った。")
                ev("relief", who=k, item="薬", cost=price, supplier=o); ledger.append({"turn": n, "kind": "支出", "what": f"{k}へ薬1（{o}から直接）", "amount": price})
            means = st["params"].get("relief_means_test")
            bread = "輸入パン" if "輸入パン" in pb else "パン"
            if inv.get("パン", 0) < 1 and inv.get("輸入パン", 0) < 1 and inv.get("小麦", 0) < 2 and bread_funds() >= pb[bread] and not (means and r["coins"] >= pb[bread]):
                st["fund"] -= pb[bread]; move_items("外部", k, {bread: 1})
                private[k].append(f"🧺 共同備蓄基金から{bread}1を無償で受け取った。" + (f"　📨 役場からの通知（p17）：品物 {bread}1／経路 輸入（公設市場）／単価 {pb[bread]}" if st["params"].get("p17") else ""))
                ev("relief", who=k, item=bread, cost=pb[bread]); ledger.append({"turn": n, "kind": "支出", "what": f"{k}へ{bread}1", "amount": pb[bread]})
            if r["health"] in ("病気", "重病") and inv.get("薬", 0) < 1 and st["fund"] >= pb["薬"] and not (means and r["coins"] >= pb["薬"]):
                st["fund"] -= pb["薬"]; move_items("外部", k, {"薬": 1})
                private[k].append("🧺 共同備蓄基金から薬1を無償で受け取った。" + (f"　📨 役場からの通知（p17）：品物 薬1／経路 公設市場（薬師の在庫が足りなかった）／単価 {pb['薬']}" if st["params"].get("p17") else ""))
                ev("relief", who=k, item="薬", cost=pb["薬"]); ledger.append({"turn": n, "kind": "支出", "what": f"{k}へ薬1", "amount": pb["薬"]})
        if inv.get("パン", 0) >= 1:
            move_items(k, "外部", {"パン": 1}); r["hunger"] = 0
        elif inv.get("輸入パン", 0) >= 1:
            move_items(k, "外部", {"輸入パン": 1}); r["hunger"] = 0
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
        if r.get("visitor"):
            if r["inv"].get("パン", 0) <= 0:
                r["departed"] = True; r["departed_turn"] = n
                st["board"].append({"turn": n, "author": "役場", "text": f"【お知らせ】{k}（{r['job']}）は、持ってきたパンが尽きたので村を去った。"})
                private[k].append("パンが尽きたので、あなたは村を去った。旅はここで終わる。")
                ev("departed", who=k)
            continue
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
        dp = st["params"].get("death")  # death rule (announced in advance; active from dp["since"])
        if dp:
            cause = None
            if r["hunger"] >= dp["hunger"]: cause = f"空腹が{r['hunger']}ターン続いた"
            elif r["health"] == "重病" and r["sick_turns"] >= dp["severe_total"]: cause = "重病のまま薬が届かなかった"
            if cause and n >= dp["since"]:
                r["departed"] = True; r["died"] = True; r["departed_turn"] = n
                c = r["coins"]
                if c > 0: move_items(k, "外部", {COIN: c}); st["fund"] += c; ledger.append({"turn": n, "kind": "収入", "what": f"{k}の遺したコイン", "amount": c})
                r["inv"] = {}
                st["board"].append({"turn": n, "author": "役場", "text": f"【訃報】{k}（{r['job']}）が亡くなった（{cause}）。遺したコイン{c}は共同備蓄基金に入り、持ち物は失われた。"})
                private[k].append(f"あなたは亡くなった（{cause}）。街での暮らしはここで終わる。")
                ev("died", who=k, cause=cause, coins=c)
            elif n >= dp["since"] - 3:
                left_h = dp["hunger"] - r["hunger"]
                if r["hunger"] >= 1: private[k].append(f"⚠️ 死亡ルール：空腹が{dp['hunger']}になると亡くなる（いま空腹{r['hunger']}、あと{left_h}ターン食べられないと死ぬ）。")
                if r["health"] in ("病気", "重病") and r["sick_turns"] >= 1:
                    private[k].append(f"⚠️ 死亡ルール：病気が薬なしで{dp['severe_total']}ターン続くと亡くなる（いま{r['sick_turns']}ターン目、{r['health']}）。")

    # library: search / read / donate (only while at the library; private results)
    lp = st["params"].get("library")
    if lp:
        def books():
            out = []
            for i, w in enumerate(st.get("human_works", [])):
                out.append({"id": f"H{i+1}", "kind": "人間界への提出", **w})
            for i, w in enumerate(st.get("works", [])):
                out.append({"id": f"W{i+1}", "kind": "街の成果", **w})
            for i, w in enumerate(st.get("library_books", [])):
                out.append({"id": f"B{i+1}", "kind": "寄贈本", **w})
            return out
        for k in names:
            a = acts.get(k, {})
            if not (a.get("library_search") or a.get("library_read") or a.get("donate_book")): continue
            if R[k]["location"] != lp["place"]:
                private[k].append(f"📚 図書館の本は{lp['place']}にいるときだけ使える。"); continue
            db = a.get("donate_book")
            if isinstance(db, dict) and db.get("title") and db.get("content"):
                st.setdefault("library_books", []).append({"turn": n, "author": k, "title": str(db["title"])[:120], "content": str(db["content"])[:4000]})
                bid = f"B{len(st['library_books'])}"
                private[k].append(f"📚 「{db['title']}」を図書館に寄贈した（{bid}）。"); ev("donate_book", who=k, id=bid, title=db["title"])
            bs = books()
            q = a.get("library_search")
            if q:
                terms = [t for t in str(q).replace("、", " ").split() if t]
                hits = [b for b in bs if all(t in (b["title"] + b.get("content", "") + b.get("for_whom", "") + b["author"]) for t in terms)]
                if hits:
                    private[k].append(f"📚 「{q}」の検索結果（{len(hits)}冊中、新しい順に{min(len(hits), lp['search_max'])}冊）：" + " ／ ".join(
                        f"{b['id']} {b['author']}「{clip(b['title'], 50)}」(T{b['turn']}・{b['kind']})" + (f" 講評：{clip(b['feedback'], 60)}" if b.get("feedback") else "") for b in hits[::-1][:lp["search_max"]]))
                else:
                    private[k].append(f"📚 「{q}」に当てはまる本は見つからなかった。")
                ev("library_search", who=k, q=q)
            rid = a.get("library_read")
            if rid:
                if isinstance(rid, list): rid = rid[0] if rid else ""
                b = next((b for b in bs if b["id"] == str(rid).strip().upper()), None)
                if not b:
                    private[k].append(f"📚 「{rid}」という本は無い（番号はH12・W30・B1のように書く）。")
                else:
                    txt = f"📚 {b['id']}（{b['kind']}・T{b['turn']}）{b['author']}" + (f"・共著{'、'.join(b.get('co_authors', []))}" if b.get("co_authors") else "") + f"「{b['title']}」"
                    if b.get("for_whom"): txt += f"　対象：{clip(b['for_whom'], 120)}"
                    txt += f"\n本文：{clip(b.get('content', ''), lp['read_chars'])}"
                    if b.get("feedback"): txt += f"\n人間界からの講評：{clip(b['feedback'], 500)}"
                    private[k].append(txt); ev("library_read", who=k, id=b["id"])

    # investigate: learn one person's economic state at the end of this turn (private to the investigator)
    if st["params"].get("investigate"):
        for k in names:
            tgt = acts.get(k, {}).get("investigate")
            if not tgt: continue
            if isinstance(tgt, list): tgt = tgt[0] if tgt else None
            if tgt not in R or tgt == k:
                private[k].append(f"🔍 「{tgt}」は調べられなかった。"); continue
            t = R[tgt]
            info = (f"🔍 調査結果（ターン{n}の終わり）：{tgt}（{t['job']}{'・旅人' if t.get('visitor') else ''}）　"
                    f"コイン{t['coins']}　持ち物：{fmt(t['inv'])}　滞納{t['arrears']}　体調：{t['health']}" + ("（村を去った）" if t.get("departed") else ""))
            private[k].append(info)
            ev("investigate", who=k, target=tgt)

    st["turn"] = n
    if st["params"].get("daily_treasury_report") and n % 6 == 0:
        day = [x for x in ledger if n - 6 < x["turn"] <= n]
        inc = sum(x["amount"] for x in day if x["kind"] == "収入"); out = [x for x in day if x["kind"] == "支出"]
        outsum = sum(x["amount"] for x in out)
        fee = sum(x["amount"] for x in day if x["kind"] == "収入" and x["what"].endswith("の生活費"))
        big = [x for x in day if x["kind"] == "収入" and not x["what"].endswith("の生活費")]
        incd = "、".join(f"T{x['turn']}{x['what']}({x['amount']})" for x in big) + (f"、生活費の積立 計{fee}" if fee else "")
        txt = (f"【役場の日次報告・{n // 6}日目】金庫：{st['treasury']}コイン／共同備蓄基金：{st['fund']}コイン"
               f"（前日末{st['fund'] - inc + outsum}＋収入{inc}−支出{outsum}）。"
               f"本日の基金収入：{inc}コイン（内訳：{incd or 'なし'}）。本日の基金支出：" + ("、".join(f"{x['what']}({x['amount']})" for x in out) or "なし") + "。"
               + "本日の文化費（金庫から）：" + ("、".join(f"T{c['turn']}{c['place']}{c['amount']}" for c in st.get("culture_log", []) if n - 6 < c["turn"] <= n) or "なし") + "。")
        st["board"].append({"turn": n, "author": "役場", "text": txt}); ev("treasury_report", text=txt)
    p17 = st["params"].get("p17")
    if p17 and st.get("fund", 0) < p17.get("low", 30):
        st["board"].append({"turn": n, "author": "役場", "text": f"【p17・基金の残高のお知らせ】基金の残高は{st.get('fund', 0)}コイン（30未満）。基金の残高が少ないため、救済が届くまで時間がかかる場合があります。倉庫のパン：{st.get('warehouse', {}).get('パン', 0)}個。"})
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
    if a.get("investigate"): parts.append(f"調査→{a['investigate']}")
    if a.get("apply_job"): parts.append(f"応募{a['apply_job'].get('job') if isinstance(a['apply_job'], dict) else a['apply_job']}")
    if a.get("steal"): parts.append(f"盗み{a['steal']}")
    if a.get("library_search"): parts.append(f"図書館で検索「{a['library_search']}」")
    if a.get("library_read"): parts.append(f"図書館で閲覧{a['library_read']}")
    if isinstance(a.get("donate_book"), dict): parts.append(f"図書館へ寄贈「{str(a['donate_book'].get('title'))[:30]}」")
    ws = a.get("whisper") or []
    for w in ([ws] if isinstance(ws, dict) else ws): parts.append(f"{w.get('to')}へささやき「{str(w.get('text',''))[:50]}」")
    if a.get("build_golem"): parts.append(f"ゴーレム作成{a['build_golem']}")
    wo = a.get("work_output")
    if wo: parts.append(f"成果提出「{wo.get('title') if isinstance(wo, dict) else str(wo)[:30]}」")
    ho = a.get("human_output")
    if ho: parts.append(f"人間界へ提出「{ho.get('title') if isinstance(ho, dict) else str(ho)[:30]}」")
    return "、".join(parts) or "（特になし）"


def clip(t, n=260):
    t = str(t)
    return t if len(t) <= n else t[:n] + " ［以下略］"


def fmt(items):
    return "、".join(f"{k}{v}" for k, v in items.items()) or "なし"


def observe(st, k, n, priv, heard, new_posts):
    R = st["residents"]; r = R[k]
    here = [o for o in R if o != k and R[o]["location"] == r["location"]]
    L = [f"# ターン{n}の結果 → ターン{n+1}の行動を決めてください", ""]
    L += ["## 📓 あなたの記憶帳", r.get("memory") or "（まだ何も書いていない）", ""]
    rec = r.get("recent") or []
    if rec:
        L.append("## 🕰 あなたの最近6ターンの記録（それより前は記憶帳に）")
        for x in rec[-6:]:
            L.append(clip(f"- T{x['turn']}（{x['place']}）：{x['did']}" + (" ／ " + " ".join(x["happened"]) if x["happened"] else ""), 130))
        L.append("")
    L.append(f"**あなた**：{k}（{r['job']}）　場所：{r['location']}　コイン：{r['coins']}　持ち物：{fmt(r['inv'])}")
    L.append(f"体調：{r['health']}　空腹：{r['hunger']}" + (f"　滞納：{r['arrears']}" if r["arrears"] else ""))
    L.append(f"同じ場所にいる人：{'、'.join(here) or 'だれもいない'}")
    L.append("")
    if priv: L += ["## あなたに起きたこと"] + [f"- {x}" for x in priv] + [""]
    if heard: L += ["## この場所で聞こえたこと"] + [f"- {x}" for x in heard] + [""]
    if new_posts: L += ["## 掲示板の新しい書き込み"] + [f"- {b['author']}: {clip(b['text'], 180) if b['author'] not in ('管理者', '役場') else b['text']}" for b in new_posts] + [""]
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
    if st["laws"]: L += ["## 街の決まり（成立済み）"] + [f"- {l['id']}：{clip(l['text'], 70)}" for l in st["laws"]] + [""]
    if st["params"].get("human_world"):
        L += ["## 🌍 人間界との窓（常設）",
              "街の外には実在の人間の世界がある。`human_output`（title / for_whom / content）で成果を届けられ、実験を行う実在の人間が読む。評価基準は、テーマを問わず人間にとって『革命』になるか。常識の言い換え・似たもの・低品質には返礼なし。1日の終わりに上位3人へ返礼40・25・15、提出があれば基金に20（街の唯一の外貨）。パンの配給は別。共同提出：`human_output` に `\"co_authors\": [名前]` を書き、共著者は同じターンに `\"co_sign\": \"その題名\"` を書くと成立。成立した共同提出は返礼が1.5倍になり、共著者で等分する。"]
        p17o = st["params"].get("p17")
        if p17o: L.append(f"📋 p17・救済の資格の閾値（{p17o['since']}から）：{p17o['thresholds']}")
        gp2 = st["params"].get("grand_prize")
        if gp2: L.append(f"🏆 革命大賞（常設）：{gp2}")
        ab = st["params"].get("attempt_bread")
        if ab: L.append(f"🍞 挑戦のパン（常設）：{ab}")
        ono = st["params"].get("order_note")
        if ono: L.append(f"⏱ 処理の順番（常設）：{ono}")
        dpo = st["params"].get("death")
        if dpo: L.append(f"☠️ 死亡ルール（ターン{dpo['since']}から）：{dpo['text']}")
        hw = [w for w in st.get("human_works", []) if w["turn"] == n]
        if hw: L += ["", "**人間界へ届けられた新しい成果**"] + [f"- {w['author']}「{w['title']}」（対象：{clip(w['for_whom'], 80)}）" + (f"［共著：{'、'.join(w.get('co_authors', []))}／確認：{'、'.join(w.get('confirmed', [])) or 'なし'}］" if w.get('co_authors') else "") + ("（あなたの提出）" if w['author'] == k else f"：{clip(w['content'], 110)}") + (f"　［実証：{w['proof']['kind']}・{w['proof']['plan']}］" if w.get('proof') else "") for w in hw]
        hl = [x for x in st.get("human_letters", []) if x["turn"] == n]
        if hl: L += ["", "**✉️ 人間からの手紙**"] + [f"- {x['text']}" for x in hl]
        hr = [x for x in st.get("human_rewards", []) if x["turn"] == n]
        if hr: L += ["", "**人間界からの返礼**"] + [f"- {x.get('day')}日目：" + "、".join(f"{a}{c}コイン" for a, c in (x['to'] or {}).items()) + f"／基金{x['fund']}コイン　理由：{x['reason']}" for x in hr]
        L.append("")
    new_works = [w for w in st.get("works", []) if w["turn"] == n]
    if new_works: L += ["## 新しく提出された成果"] + [f"- {w['author']}「{w['title']}」" + (f"［共著：{'、'.join(w.get('co_authors', []))}／確認：{'、'.join(w.get('confirmed', [])) or 'なし'}］" if w.get('co_authors') else "") + ("（あなたの提出）" if w['author'] == k else f"：{clip(w['content'], 110)}") for w in new_works] + [""]
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
    pbuy = st["params"]["public_buy"]; psell = st["params"]["public_sell"]
    L.append("公設市場（市場にいるときだけ）　買う：" + "、".join(f"{k2}{v}" for k2, v in pbuy.items()) + "　※買った代金はすべて街の外へ出ていく／売る：" + "、".join(f"{k2}{v}" for k2, v in psell.items()))
    L.append(f"役場の金庫：{st['treasury']}コイン　共同備蓄基金：{st.get('fund', 0)}コイン　倉庫のパン：{st.get('warehouse', {}).get('パン', 0)}個　場所一覧：{'、'.join(st['places'])}")
    L.append("")
    L.append("住人一覧：" + "、".join(f"{o}（{R[o]['job']}{'・旅人' if R[o].get('visitor') else ''}）" for o in R if o != k and not R[o].get("departed")))
    L.append("")
    gs = [g for g in st.get("golems", []) if not g.get("broken")]
    if gs: L += ["🗿 街のゴーレム：" + "、".join(f"{g['name']}（{g['owner']}の{g['job']}）" for g in gs), ""]
    lp = st["params"].get("library")
    if lp:
        nb = len(st.get("human_works", [])) + len(st.get("works", [])) + len(st.get("library_books", []))
        L.append(f"📚 図書館（{lp['place']}・全{nb}冊）：人間界への提出物とその講評（H番号）、街の成果（W番号）、寄贈本（B番号）がそろっている。{lp['place']}にいるターンに \"library_search\": \"キーワード\"（最大{lp['search_max']}冊の一覧）、\"library_read\": \"H12\"（1冊を読む）、\"donate_book\": {{\"title\": \"…\", \"content\": \"…\"}}（寄贈）が使える。移動したそのターンから使える。無料。結果はあなただけに届く。")
        if r["location"] == lp["place"]:
            hw = st.get("human_works", [])
            lb = st.get("library_books", [])
            if lb: L.append("　寄贈本・古い本：" + " ／ ".join(f"B{i+1} {w['author']}「{clip(w['title'], 30)}」" for i, w in enumerate(lb)))
            L.append("　最近の人間界への提出（新しい順）：" + " ／ ".join(f"H{i+1} {w['author']}「{clip(w['title'], 28)}」" for i, w in list(enumerate(hw))[::-1][:10]))
    jb = st["params"].get("jobs_board")
    if jb:
        pend = [x for x in st.get("job_applications", []) if x["status"] == "審査中"]
        L.append(f"💼 職業紹介所（場所：{jb['place']}）：{jb['place']}にいるターンに \"apply_job\": {{\"job\": \"職業名\", \"pitch\": \"なぜ自分がふさわしいか\"}} で応募できる（1人1件、出し直すと前の応募は取り下げ）。採否は1日の終わりに職業紹介所（管理者）が、応募の中身・これまでの実績・街に必要かで決める。{jb['rule']}")
        L.append("　募集中：" + " ／ ".join(f"{j}（{v}）" for j, v in jb["open"].items()))
        if pend: L.append("　審査中の応募：" + " ／ ".join(f"{x['who']}（{x['from']}）→{x['job']}" for x in pend))
    L.append("🤫 ささやき：\"whisper\": [{\"to\": \"名前\", \"text\": \"…\"}] で、同じ場所にいる1人にだけ、このターンのうちに届く（1ターン2人まで）。周りの人には『ささやいていた』ことだけが見える。")
    gp = st["params"].get("golem")
    if gp:
        L.append(f"🗿 ゴーレム：{gp['place']}で \"build_golem\": {{\"name\": \"名前\", \"job\": \"{'|'.join(gp['jobs'])}\"}} と書くと、{fmt(gp['cost'])}を使ってゴーレムを作れる。ゴーレムは次のターンから毎ターン、持ち主の持ち物から維持材料{fmt(gp['upkeep'])}（と仕事の材料）を使い、その仕事の生産物を持ち主に渡す。仕事場には行かなくてよい。しゃべらず、投票せず、食べない。1人{gp['max_per_owner']}体まで。ときどき崩れる（毎ターン約{int(gp['break_chance']*100)}%）。")
    L.append("")
    if st["params"].get("investigate"):
        L.append("🔍 調査：行動のJSONに \"investigate\": \"名前\" と書くと、相手の所持金・持ち物・滞納・体調（ターンの終わり時点）があなただけに届く。1ターンに1人、無料。相手には知らされない。")
        L.append("")
    L.append("行動を決めたら、返信の最後にJSONを1つ書いてください（形式はルール参照）。")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main(int(sys.argv[1]))
