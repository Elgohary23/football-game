import sqlite3
c = sqlite3.connect('instance/db.sqlite3')
tables = sorted(t[0] for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
print('instance tables:', len(tables), 'game_state' in tables and 'HEALTHY' or 'BROKEN')
c.close()
