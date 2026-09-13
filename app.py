"""Application entry point.

Keeps only app wiring: config, CSRF, context processor, error handlers and the
startup bootstrap. All routes live in the ``routes`` package and all business
rules live in ``services`` / ``simulation`` (Tips 1 & 3).
"""

import os
import shutil
import sqlite3

from flask import Flask, flash, redirect, request
from flask_wtf import CSRFProtect

from config import MAX_SAVE_SLOTS
from models import db, GameState, Team
from services import (
    ensure_saves_dir,
    get_active_slot,
    init_template,
    instance_db_path,
    refresh_db_engine,
    setup_logging,
    template_db_path,
)
from simulation import get_unread_count


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///db.sqlite3"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.secret_key = os.environ.get("SECRET_KEY", "football_secret_key_prod")

    db.init_app(app)
    CSRFProtect(app)

    from routes import register_blueprints

    register_blueprints(app)

    @app.context_processor
    def inject_globals():
        ctx = {
            "unread_count": 0,
            "manager_name": "",
            "manager_nationality": "",
            "player_team_name": "",
            "current_week": 0,
            "current_day": 0,
        }
        if get_active_slot() == 0:
            return ctx
        try:
            ctx["unread_count"] = get_unread_count()
            state = GameState.query.first()
            if state:
                ctx["manager_name"] = state.manager_name or ""
                ctx["manager_nationality"] = state.manager_nationality or ""
                ctx["current_week"] = state.current_week or 0
                ctx["current_day"] = state.current_day or 0
            player_team = Team.query.filter_by(is_player_team=True).first()
            if player_team:
                ctx["player_team_name"] = player_team.name or ""
        except Exception:
            pass
        return ctx

    @app.errorhandler(405)
    def method_not_allowed(e):
        flash("حدث خطأ في الطلب، تم إعادتك للرئيسية.")
        return redirect("/")

    @app.errorhandler(404)
    def not_found(e):
        flash("الصفحة غير موجودة!")
        return redirect("/")

    @app.errorhandler(500)
    def server_error(e):
        if request.path == "/load":
            return "حدث خطأ جسيم في الخادم. يرجى المحاولة لاحقاً.", 500
        flash("حدث خطأ في الخادم. تم إعادتك لصفحة الحفظ.")
        return redirect("/load")

    return app


app = create_app()


def _needs_rebuild() -> bool:
    template_db = template_db_path()
    if not os.path.exists(template_db):
        return False
    try:
        conn = sqlite3.connect(template_db)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='staff'"
        )
        has_staff = cursor.fetchone()
        cursor.execute("PRAGMA table_info(player);")
        player_columns = [row[1] for row in cursor.fetchall()]
        cursor.execute("PRAGMA table_info(game_state);")
        gs_columns = [row[1] for row in cursor.fetchall()]
        conn.close()
        if not has_staff or "training_focus" not in player_columns or "start_date" not in gs_columns:
            return True
    except Exception:
        return True
    return False


def _copy_template_to_instance() -> None:
    db_path = instance_db_path()
    copy_needed = not os.path.exists(db_path) or os.path.getsize(db_path) == 0
    if not copy_needed:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='game_state'"
            )
            copy_needed = cursor.fetchone() is None
            conn.close()
        except Exception:
            copy_needed = True

    if copy_needed:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        if os.path.exists(db_path):
            os.remove(db_path)
        shutil.copy2(template_db_path(), db_path)


if __name__ == "__main__":
    setup_logging()
    ensure_saves_dir()
    template_db = template_db_path()

    if _needs_rebuild():
        print("[INFO] Old database schema detected. Rebuilding template and save files...")
        try:
            if os.path.exists(template_db):
                os.remove(template_db)
            db_path = instance_db_path()
            if os.path.exists(db_path):
                os.remove(db_path)
            save_dir = os.path.join(os.path.abspath("saves"))
            for i in range(1, MAX_SAVE_SLOTS + 1):
                path = os.path.join(save_dir, f"save{i}.sqlite3")
                if os.path.exists(path):
                    os.remove(path)
        except Exception:
            pass

    with app.app_context():
        if not os.path.exists(template_db):
            init_template()
        _copy_template_to_instance()

    app.run(debug=True, port=5000)