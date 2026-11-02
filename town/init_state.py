"""Create the initial town state (turn 0)."""
import json, pathlib

RESIDENTS = [
    ("ハル", "農家", "畑", "穏やかで勤勉。少し心配性で、蓄えがないと落ち着かない。"),
    ("ミナ", "パン屋", "市場", "社交的で噂好き。商売上手で、人を集めるのが得意。"),
    ("ゴロウ", "木こり", "森", "無口で頑固。義理堅く、受けた恩は忘れない。"),
    ("サキ", "大工", "工房", "野心的。この街をもっと大きく、立派にしたいと思っている。"),
    ("レン", "商人", "市場", "計算高く利益を重んじるが、一度した約束は守る。"),
    ("ユイ", "記者", "広場", "好奇心旺盛で、力を持つ者を疑ってかかる。真実を書きたい。"),
    ("トウマ", "薬師", "診療所", "慎重で利他的。ただし口調は少し皮肉っぽい。"),
    ("カナデ", "吟遊詩人", "酒場", "自由奔放で楽観的。いつもお金がないが、気にしていない。"),
]
START = {
    "ハル": (100, {"パン": 3, "小麦": 6}),
    "ミナ": (100, {"パン": 3, "小麦": 4}),
    "ゴロウ": (100, {"パン": 3, "木材": 4}),
    "サキ": (100, {"パン": 3, "木材": 3}),
    "レン": (300, {"パン": 3}),
    "ユイ": (100, {"パン": 3}),
    "トウマ": (100, {"パン": 3, "薬": 2}),
    "カナデ": (30, {"パン": 3}),
}

state = {
    "turn": 0,
    "places": ["広場", "市場", "畑", "森", "工房", "診療所", "酒場", "役場"],
    "params": {
        "living_cost": 2,
        "sick_chance": 0.07,
        "public_buy": {"パン": 12, "小麦": 6, "木材": 8, "薬": 25},
        "public_sell": {"パン": 4, "小麦": 2, "木材": 3, "薬": 10, "家具": 20, "新聞": 3},
    },
    "treasury": 0,
    "residents": {},
    "board": [],
    "offers": [],
    "proposals": [],
    "laws": [],
    "buildings": [],
    "inbox": {},
    "next_offer": 1,
    "next_proposal": 1,
}
for name, job, home, pers in RESIDENTS:
    coins, inv = START[name]
    state["residents"][name] = {
        "name": name, "job": job, "personality": pers, "location": home,
        "coins": coins, "inv": dict(inv), "hunger": 0,
        "health": "健康", "sick_turns": 0, "arrears": 0, "session_id": None,
    }
    state["inbox"][name] = []

p = pathlib.Path(__file__).parent / "state.json"
p.write_text(json.dumps(state, ensure_ascii=False, indent=1))
print("wrote", p)
