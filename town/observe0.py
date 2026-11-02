"""Write turn-0 observations (initial situation) for every resident."""
import json, pathlib, engine
st = json.loads((engine.ROOT / "state.json").read_text())
d = engine.ROOT / "turns" / "000" / "observations"; d.mkdir(parents=True, exist_ok=True)
for k in st["residents"]:
    (d / f"{k}.md").write_text(engine.observe(st, k, 0, ["街での暮らしが始まった。"], [], []))
print("ok")
