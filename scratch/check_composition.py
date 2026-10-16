import sqlite3
from collections import Counter

db = sqlite3.connect(r"D:/software projects/football-game/saves/save1.sqlite3")
c = db.cursor()

teams = c.execute("SELECT id, name FROM team ORDER BY id").fetchall()
no_lb = no_rb = no_gk = weird_xi = 0
examples = []
for tid, name in teams:
    rows = c.execute(
        "SELECT position, detailed_position, is_starting FROM player WHERE team_id=?",
        (tid,),
    ).fetchall()
    det = [r[1] for r in rows if r[1]]
    counts = Counter(det)
    if "LB" not in counts:
        no_lb += 1
        examples.append((name, dict(counts)))
    if "RB" not in counts:
        no_rb += 1
    if "GK" not in counts:
        no_gk += 1
    # XI sanity: coarse counts on starting flag
    xi = Counter(r[0] for r in rows if r[2])
    if xi.get("GK", 0) != 1:
        weird_xi += 1

print(f"teams={len(teams)} squads_without_LB={no_lb} without_RB={no_rb} without_GK={no_gk}")
print(f"squads whose starting XI does not have exactly 1 GK: {weird_xi}")
print("example squads missing LB:")
for name, counts in examples[:8]:
    print("  ", name, counts)
