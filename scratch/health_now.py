import os, shutil, sqlite3
tpl = 'saves/template.db'
inst = 'instance/db.sqlite3'
tmp = inst + '.tmp'
if os.path.exists(tmp):
    os.remove(tmp)
shutil.copy2(tpl, tmp)
# sanity: tmp must contain game_state before swapping
conn = sqlite3.connect(tmp)
has = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='game_state'").fetchone()
conn.close()
assert has, 'template copy lacks game_state, refusing swap'
os.replace(tmp, inst)
print('instance db restored from template')
