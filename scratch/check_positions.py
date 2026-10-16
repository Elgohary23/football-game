import sqlite3

db = sqlite3.connect(r"D:/software projects/football-game/saves/save1.sqlite3")
c = db.cursor()
pt = c.execute("SELECT id, name FROM team WHERE is_player_team = 1").fetchall()
print("player teams:", pt)
tid = pt[0][0]
rows = c.execute(
    "SELECT name, position, detailed_position, is_starting FROM player WHERE team_id=? ORDER BY is_starting DESC, position",
    (tid,),
).fetchall()
for r in rows:
    print(r)
print()
print("--- one AI team ---")
other = c.execute("SELECT id FROM team WHERE is_player_team = 0 LIMIT 1").fetchone()
rows = c.execute(
    "SELECT name, position, detailed_position, is_starting FROM player WHERE team_id=? ORDER BY is_starting DESC, position",
    (other[0],),
).fetchall()
for r in rows:
    print(r)
print()
print("--- coarse-position-only players (detailed NULL/empty) ---")
print(c.execute("SELECT COUNT(*) FROM player WHERE detailed_position IS NULL OR detailed_position=''").fetchone())
