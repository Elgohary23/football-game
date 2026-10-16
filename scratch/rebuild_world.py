"""Rebuild the game world with fixed squad generation (schema v11).

Steps:
1. Back up the existing save1.sqlite3 (language-safe timestamped copy).
2. Drop + rebuild saves/template.db via services.init_template().
3. Replace instance/db.sqlite3 with the fresh template copy.
4. Rebuild save slot 1 via services.create_new_game(1) (same path as the UI).

Run from the repo root:  python scratch/rebuild_world.py
"""
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
from services import (  # noqa: E402
    create_new_game,
    init_template,
    instance_db_path,
    template_db_path,
)

STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")


def backup(path: str) -> str:
    if not os.path.exists(path):
        return ""
    dest = f"{path}.bak-{STAMP}"
    shutil.copy2(path, dest)
    return dest


with app.app_context():
    save1 = os.path.join("saves", "save1.sqlite3")
    print("backup save1:", backup(save1))

    tpl = template_db_path()
    if os.path.exists(tpl):
        os.remove(tpl)
    print("rebuilding template.db ...")
    init_template()

    # Instance DB is served as a copy of the template.
    inst = instance_db_path()
    if os.path.exists(inst):
        os.remove(inst)
    shutil.copy2(tpl, inst)
    print("instance db  :", "rebuilt from template")

    if os.path.exists(save1):
        os.remove(save1)
    with app.test_request_context("/"):
        ok = create_new_game(1)
    print("save slot 1  :", "rebuilt" if ok else "FAILED")

print("done")
