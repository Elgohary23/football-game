"""Save-slot / new-game blueprint (setup flow)."""

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
)

bp = Blueprint("save", __name__)

from config import MAX_SAVE_SLOTS
from models import db, GameState, League, Team
from services import (
    calculate_team_tier,
    create_new_game,
    delete_save_file,
    get_active_slot,
    get_budget_for_tier,
    get_slot_metadata,
    get_slot_uri,
    refresh_db_engine,
    slot_exists,
    switch_to_slot,
)


@bp.route("/load")
def load_game():
    from services import ensure_saves_dir

    ensure_saves_dir()
    active = get_active_slot()
    slots = []
    for i in range(1, MAX_SAVE_SLOTS + 1):
        exists = slot_exists(i)
        meta = get_slot_metadata(i) if exists else {}
        slots.append({"id": i, "exists": exists, **meta})

    # NOTE: rendering the menu must NOT repoint the DB engine. Switching
    # slots is an explicit action handled by the POST endpoints below; a GET
    # with global side effects silently moved every subsequent request (and
    # every user) onto a different database file.
    if active > 0 and slot_exists(active):
        try:
            state = GameState.query.first()
            player_team = Team.query.filter_by(is_player_team=True).first()
        except Exception:
            session.pop("active_save", None)
            active = 0
            state = None
            player_team = None
    else:
        if active > 0:
            session.pop("active_save", None)
        active = 0
        state = None
        player_team = None

    return render_template(
        "load.html", slots=slots, active=active, state=state, player_team=player_team
    )


@bp.route("/load/<int:slot_id>", methods=["POST"])
def load_slot(slot_id):
    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")

    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")

    # Resume where the save left off instead of assuming it is complete.
    # An unfinished save (no manager name / no club chosen) would otherwise
    # hit /dashboard -> safe_player_team() aborts 500 -> redirect /load loop.
    try:
        state = GameState.query.first()
        if not state:
            return redirect(f"/setup/{slot_id}")
        if not state.manager_name or state.manager_name == "المدرب":
            return redirect(f"/setup/{slot_id}")
        player_team = Team.query.filter_by(is_player_team=True).first()
        if not player_team:
            return redirect(f"/select-division/{slot_id}")
    except Exception:
        return redirect("/load")

    return redirect("/dashboard")


@bp.route("/new/<int:slot_id>", methods=["POST"])
def new_game(slot_id):
    if slot_id < 1 or slot_id > MAX_SAVE_SLOTS:
        flash("رقم Slot غير صالح!")
        return redirect("/load")

    if slot_exists(slot_id):
        flash("هذا الحفظ مشغول! احذفه أولاً.")
        return redirect("/load")

    if not create_new_game(slot_id):
        flash("فشل في إنشاء لعبة جديدة!")
        return redirect("/load")

    return redirect(f"/setup/{slot_id}")


@bp.route("/setup/<int:slot_id>", methods=["GET", "POST"])
def setup_coach(slot_id):
    if slot_id < 1 or slot_id > MAX_SAVE_SLOTS:
        flash("رقم Slot غير صالح!")
        return redirect("/load")

    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")

    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")

    if request.method == "POST":
        manager_name = request.form.get("manager_name", "").strip()
        manager_nationality = request.form.get("manager_nationality", "").strip()
        if not manager_name:
            manager_name = "المدرب"
        if not manager_nationality:
            manager_nationality = "غير محدد"

        state = GameState.query.first()
        if state:
            try:
                state.manager_name = manager_name
                state.manager_nationality = manager_nationality
                db.session.commit()
            except Exception:
                db.session.rollback()

        return redirect(f"/select-division/{slot_id}")

    return render_template("setup.html", slot_id=slot_id)


@bp.route("/select-division/<int:slot_id>")
def select_division(slot_id):
    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")

    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")

    state = GameState.query.first()
    leagues = League.query.order_by(League.tier).all()
    return render_template(
        "select_division.html", slot_id=slot_id, leagues=leagues, state=state
    )


@bp.route("/select-team/<int:slot_id>/<int:league_id>")
def select_team(slot_id, league_id):
    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")

    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")

    league = db.session.get(League, league_id)
    if not league:
        flash("الدوري غير موجود!")
        return redirect(f"/select-division/{slot_id}")

    teams = Team.query.filter_by(league_id=league_id).all()

    enriched = []
    for team in teams:
        tier_data = calculate_team_tier(team.id)
        enriched.append(
            {
                "id": team.id,
                "name": team.name,
                "league": league.name,
                "tier": tier_data["tier"],
                "rating": tier_data["rating"],
                "attack": tier_data["attack"],
                "defense": tier_data["defense"],
                # Show the budget the manager will ACTUALLY get: manager_form
                # overwrites team.budget via get_budget_for_tier(), so the
                # stored value on an unpicked template club would mislead.
                "budget": get_budget_for_tier(league.tier),
            }
        )

    state = GameState.query.first()
    return render_template(
        "select_team.html", slot_id=slot_id, teams=enriched, league=league, state=state
    )


@bp.route("/new-manager/<int:slot_id>", methods=["GET", "POST"])
def new_manager(slot_id):
    if slot_id < 1 or slot_id > MAX_SAVE_SLOTS:
        flash("رقم Slot غير صالح!")
        return redirect("/load")

    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")

    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")

    if request.method == "POST":
        manager_name = request.form.get("manager_name", "").strip()
        if not manager_name:
            manager_name = "المدرب"

        state = GameState.query.first()
        if state:
            try:
                state.manager_name = manager_name
                db.session.commit()
            except Exception:
                db.session.rollback()

        return redirect(f"/select-team/{slot_id}")

    return render_template("new_manager.html", slot_id=slot_id)


@bp.route("/exit")
def exit_to_menu():
    session.pop("active_save", None)
    return redirect("/load")


@bp.route("/delete/<int:slot_id>", methods=["POST"])
def delete_game(slot_id):
    active = get_active_slot()
    if active == slot_id:
        session.pop("active_save", None)

    target_uri = get_slot_uri(slot_id)
    if current_app.config.get("SQLALCHEMY_DATABASE_URI") == target_uri:
        db.session.remove()
        refresh_db_engine("sqlite:///db.sqlite3")

    if not delete_save_file(slot_id):
        flash("فشل في حذف الحفظ!")
    else:
        flash("تم حذف الحفظ!")

    return redirect("/load")


@bp.route("/manager-form/<int:slot_id>/<int:team_id>", methods=["GET", "POST"])
def manager_form(slot_id, team_id):
    if slot_id < 1 or slot_id > MAX_SAVE_SLOTS:
        flash("رقم Slot غير صالح!")
        return redirect("/load")

    if not slot_exists(slot_id):
        flash("الحفظ غير موجود!")
        return redirect("/load")

    if not switch_to_slot(slot_id):
        flash("فشل في تحميل الحفظ!")
        return redirect("/load")

    team = db.session.get(Team, team_id)
    if not team:
        flash("الفريق غير موجود!")
        return redirect(f"/select-division/{slot_id}")

    if request.method != "POST":
        state = GameState.query.first()
        return render_template(
            "manager_form.html", slot_id=slot_id, team=team, state=state
        )

    # Bulk-clear first; synchronize_session="fetch" + expire_all() so no
    # stale in-memory Team object still claims is_player_team=True.
    Team.query.update({Team.is_player_team: False}, synchronize_session="fetch")
    db.session.expire_all()
    team = db.session.get(Team, team_id)
    team.is_player_team = True
    db.session.flush()

    # Budget follows the club's league tier, so picking a Premier League
    # club vs a League Two club gives the correct starting funds even if
    # the save was created from an older template with stale budgets.
    team.budget = get_budget_for_tier(team.league.tier if team.league else None)

    state = GameState.query.first()
    if state:
        state.user_league_id = team.league_id
        manager_name = request.form.get("manager_name", "").strip()
        if manager_name:
            state.manager_name = manager_name

    from simulation import ensure_board_objectives
    if state is not None:
        ensure_board_objectives(team, state)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("فشل في حفظ البيانات!")
        return redirect(f"/select-division/{slot_id}")

    flash(f"مرحباً {state.manager_name if state else ''}! تم اختيار فريق {team.name} بنجاح!")
    return redirect("/dashboard")