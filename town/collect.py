"""Build turns/NNN/actions.json from raw reply texts.

Usage: python3 town/collect.py N
Expects town/turns/NNN/raw/<名前>.txt (each resident's full reply text; missing = no reply)
and optional town/turns/NNN/gm.json ({"rulings":{}, "ops":[]}).
"""
import json, re, sys, pathlib
ROOT = pathlib.Path(__file__).parent
n = int(sys.argv[1]); t = ROOT / "turns" / f"{n:03d}"
names = list(json.loads((ROOT / "state.json").read_text())["residents"])
acts, raw, diary = {}, {}, {}
for k in names:
    f = t / "raw" / f"{k}.txt"
    if not f.exists():
        raw[k] = "（返信なし）"; continue
    txt = f.read_text(); raw[k] = txt
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", txt, re.S)
    if not blocks:
        blocks = re.findall(r"(\{[\s\S]*\})\s*$", txt)
    try:
        a = json.loads(blocks[-1]); acts[k] = a
        if a.get("diary"): diary[k] = a["diary"]
    except Exception as e:
        raw[k] += f"\n\n[GM: JSONを解析できなかった: {e}]"
gm = json.loads((t / "gm.json").read_text()) if (t / "gm.json").exists() else {"rulings": {}, "ops": []}
(t / "actions.json").write_text(json.dumps({"actions": acts, "raw": raw, "gm": gm}, ensure_ascii=False, indent=1))
print("parsed:", sorted(acts), "missing/failed:", [k for k in names if k not in acts])
others = {k: a["other"] for k, a in acts.items() if a.get("other")}
if others: print("NEEDS RULING:", json.dumps(others, ensure_ascii=False, indent=1))
