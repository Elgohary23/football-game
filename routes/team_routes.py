"""Team management blueprint: squad, formation, staff, facilities, transfers."""

import json

from flask import Blueprint, flash, jsonify, redirect, render_template, request

bp = Blueprint("team", __name__)

from config import (
    MAX_FACILITY_LEVEL,
    PRESET_TYPES,
    TRAINING_COST_PER_LEVEL,
    YOUTH_COST_PER_LEVEL,
)
from models import db, Player, PlayerRating, ScoutReport, Staff, Team, TacticPreset
from services import (
    apply_formation,
    assign_scout_to_region,
    calculate_team_tier,
    fire_staff_member,
    generate_staff_pool,
    get_display_rating,
    get_scout_report,
    hire_staff_member,
    purchase_player,
    safe_player_team,
    safe_state,
    unassign_scout,
    validate_formation,
)


@bp.route("/squad")
def squad():
    state = safe_state()
    player_team = safe_player_team()
    players = Player.query.filter_by(team_id=player_team.id).all()

    from models import Mentoring

    mentoring = (
        Mentoring.query.filter_by(team_id=player_team.id).all()
    )
    veterans = [p for p in players if p.age >= 30 and p.personality in ("Leader", "Professional", "Loyal")]
    prospects = [p for p in players if p.age <= 23]

    starters = [p for p in players if p.is_starting and p.injury_days == 0]
    bench = [p for p in players if not p.is_starting or p.injury_days > 0]

    tiers_data = calculate_team_tier(player_team.id)

    return render_template(
        "squad.html",
        state=state,
        player_team=player_team,
        players=players,
        starters=starters,
        bench=bench,
        mentoring=mentoring,
        veterans=veterans,
        prospects=prospects,
        avg_attack=tiers_data["attack"],
        avg_defense=tiers_data["defense"],
        tier=tiers_data["tier"],
        rating=tiers_data["rating"],
    )


@bp.route("/toggle_starter/<int:player_id>", methods=["POST"])
def toggle_starter(player_id):
    player = db.session.get(Player, player_id)
    player_team = safe_player_team()

    if not player or player.team_id != player_team.id:
        return jsonify({"success": False, "message": "اللاعب غير موجود!"}), 404

    if player.injury_days > 0:
        return (
            jsonify(
                {"success": False, "message": "هذا اللاعب مصاب ولا يمكنه البدء!"}
            ),
            400,
        )

    current_starters = Player.query.filter_by(
        team_id=player.team_id, is_starting=True
    ).count()

    if not player.is_starting and current_starters >= 11:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "لديك بالفعل 11 لاعباً في التشكيلة الأساسية!",
                }
            ),
            400,
        )

    player.is_starting = not player.is_starting
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return (
            jsonify(
                {"success": False, "message": "حدث خطأ أثناء تحديث التشكيلة!"}
            ),
            500,
        )

    return jsonify(
        {
            "success": True,
            "is_starting": player.is_starting,
            "message": "تم تحديث التشكيلة!",
        }
    )


@bp.route("/formation")
def formation():
    state = safe_state()
    player_team = safe_player_team()
    players = Player.query.filter_by(team_id=player_team.id).all()

    players_data = []
    for p in players:
        players_data.append(
            {
                "id": p.id,
                "name": p.name,
                "position": p.position,
                "attack": p.attack,
                "defense": p.defense,
                "injury_days": p.injury_days,
                "fitness": p.fitness,
                "is_starting": p.is_starting,
            }
        )

    formation_data = player_team.formation_data
    if not isinstance(formation_data, dict):
        formation_data = {}

    presets = TacticPreset.query.filter_by(team_id=player_team.id).all()

    return render_template(
        "formation.html",
        state=state,
        player_team=player_team,
        players=players_data,
        formation_data=json.dumps(formation_data),
        presets=presets,
    )


@bp.route("/formation/save", methods=["POST"])
def save_formation():
    state = safe_state()
    player_team = safe_player_team()

    data = request.get_json()
    if not data or "positions" not in data:
        return jsonify({"error": "بيانات غير صالحة"}), 400

    ok, error = validate_formation(player_team, data["positions"])
    if not ok:
        return jsonify({"error": error}), 400

    apply_formation(player_team, data["positions"])

    try:
        db.session.commit()
        return jsonify({"success": True, "message": "تم حفظ التشكيلة!"})
    except Exception:
        db.session.rollback()
        return jsonify({"error": "فشل في حفظ التشكيلة"}), 500


# --- FM: tactical instructions (pressing + match tactic) -------------
@bp.route("/formation/tactics", methods=["POST"])
def save_tactics():
    player_team = safe_player_team()
    pressing = request.form.get("pressing_style", "normal")
    tactic = request.form.get("match_tactic", "balanced")
    player_team.pressing_style = pressing
    player_team.match_tactic = tactic
    db.session.commit()
    flash("تم حفظ التعليمات التكتيكية!")
    return redirect("/formation")


# --- FM: set-piece takers ---------------------------------------------
@bp.route("/formation/takers", methods=["POST"])
def save_takers():
    player_team = safe_player_team()
    squad = {p.id: p.name for p in Player.query.filter_by(team_id=player_team.id).all()}

    pid_p = request.form.get("penalty_taker_id", type=int)
    pid_f = request.form.get("freekick_taker_id", type=int)
    if pid_p in squad:
        player_team.penalty_taker_id = pid_p
    if pid_f in squad:
        player_team.freekick_taker_id = pid_f
    db.session.commit()
    flash("تم تحديث منفذي الكرات الثابتة!")
    return redirect("/formation")


# --- FM: tactical presets ---------------------------------------------
@bp.route("/formation/preset/save", methods=["POST"])
def save_preset():
    player_team = safe_player_team()
    preset_type = request.form.get("preset_type", "standard")
    name = request.form.get("name") or "خطة محفوظة"
    positions = None
    if isinstance(player_team.formation_data, dict) and player_team.formation_data:
        positions = player_team.formation_data
    preset = TacticPreset(
        team_id=player_team.id,
        preset_type=preset_type if preset_type in PRESET_TYPES else "standard",
        name=name,
        formation_data=positions,
        pressing_style=player_team.pressing_style or "normal",
        match_tactic=player_team.match_tactic or "balanced",
    )
    db.session.add(preset)
    db.session.commit()
    flash(f"تم حفظ الخطة «{preset.name}»!")
    return redirect("/formation")


@bp.route("/formation/preset/<int:preset_id>/load", methods=["POST"])
def load_preset(preset_id):
    player_team = safe_player_team()
    preset = db.session.get(TacticPreset, preset_id)
    if not preset or preset.team_id != player_team.id:
        flash("الخطة غير موجودة!")
        return redirect("/formation")

    if isinstance(preset.formation_data, dict) and preset.formation_data:
        ok, error = validate_formation(player_team, preset.formation_data)
        if not ok:
            flash(f"لا يمكن تطبيق الخطة: {error}")
            return redirect("/formation")
        apply_formation(player_team, preset.formation_data)
    player_team.pressing_style = preset.pressing_style or "normal"
    player_team.match_tactic = preset.match_tactic or "balanced"
    db.session.commit()
    flash(f"تم تطبيق الخطة «{preset.name}»!")
    return redirect("/formation")


@bp.route("/formation/preset/<int:preset_id>/delete", methods=["POST"])
def delete_preset(preset_id):
    player_team = safe_player_team()
    preset = db.session.get(TacticPreset, preset_id)
    if preset and preset.team_id == player_team.id:
        db.session.delete(preset)
        db.session.commit()
        flash("تم حذف الخطة!")
    return redirect("/formation")


# --- FM: mentoring (veteran develops a young player) ------------------
@bp.route("/mentoring/add", methods=["POST"])
def mentoring_add():
    player_team = safe_player_team()
    from models import Mentoring

    mentor_id = request.form.get("mentor_id", type=int)
    mentee_id = request.form.get("mentee_id", type=int)
    mentor = db.session.get(Player, mentor_id)
    mentee = db.session.get(Player, mentee_id)
    if not mentor or not mentee or mentor.team_id != player_team.id or mentee.team_id != player_team.id:
        flash("اللاعبان غير صالحين!")
        return redirect("/squad")

    exists = Mentoring.query.filter_by(team_id=player_team.id, mentee_id=mentee_id).first()
    if exists:
        flash("هذا الشاب لديه مرشد بالفعل!")
        return redirect("/squad")

    db.session.add(Mentoring(
        team_id=player_team.id,
        mentor_id=mentor_id,
        mentee_id=mentee_id,
        started_day=(safe_state().current_day or 0),
    ))
    db.session.commit()
    flash(f"أصبح {mentor.name} مرشداً لـ {mentee.name}!")
    return redirect("/squad")


@bp.route("/mentoring/remove/<int:mentoring_id>", methods=["POST"])
def mentoring_remove(mentoring_id):
    from models import Mentoring

    m = db.session.get(Mentoring, mentoring_id)
    if m:
        db.session.delete(m)
        db.session.commit()
        flash("تم إنهاء علاقة التوجيه!")
    return redirect("/squad")


# --- FM: player swap deals --------------------------------------------
@bp.route("/swap_offer/<int:target_id>", methods=["POST"])
def swap_offer(target_id):
    from services import swap_offer as run_swap

    state = safe_state()
    player_team = safe_player_team()
    target = db.session.get(Player, target_id)
    offered_id = request.form.get("offered_player_id", type=int)
    offered = db.session.get(Player, offered_id) if offered_id else None
    cash = request.form.get("cash", type=float) or 0.0
    ok, message = run_swap(player_team, target, offered, cash, state.current_day)
    db.session.commit()
    flash(message)
    return redirect("/scouting")


# --- FM: player role assignment ---------------------------------------
@bp.route("/formation/role", methods=["POST"])
def save_role():
    from config import PLAYER_ROLES

    player_team = safe_player_team()
    player_id = request.form.get("player_id", type=int)
    role = request.form.get("role", "standard")
    if role not in PLAYER_ROLES:
        role = "standard"
    fd = player_team.formation_data
    if not isinstance(fd, dict):
        fd = {}
    pid = str(player_id)
    entry = fd.get(pid)
    if isinstance(entry, dict):
        entry["role"] = role
    elif pid in fd:
        fd[pid] = {"x": 50, "y": 50, "role": role}
    else:
        fd[pid] = {"x": 50, "y": 50, "role": role}
    player_team.formation_data = fd
    db.session.commit()
    return jsonify({"success": True, "role": role, "player_id": player_id})


# --- FM: player comparison ---------------------------------------------
@bp.route("/compare")
def compare():
    state = safe_state()
    player_team = safe_player_team()

    players = Player.query.order_by(Player.attack.desc()).limit(200).all()
    a_id = request.args.get("player_a", type=int)
    b_id = request.args.get("player_b", type=int)
    if a_id == b_id:
        b_id = None

    a = db.session.get(Player, a_id) if a_id else None
    b = db.session.get(Player, b_id) if b_id else None

    comparison = None
    if a and b:
        comparison = {
            "a": {
                "id": a.id, "name": a.name, "position": a.position,
                "age": a.age, "rating": get_display_rating(a, player_team),
                "attack": a.attack, "defense": a.defense, "potential": a.potential,
                "speed": a.speed or 50, "stamina": a.stamina or 50,
                "technique": a.technique or 50, "sharpness": a.sharpness or 50,
                "goals": a.goals or 0, "assists": a.assists or 0,
                "market_value": a.market_value,
            },
            "b": {
                "id": b.id, "name": b.name, "position": b.position,
                "age": b.age, "rating": get_display_rating(b, player_team),
                "attack": b.attack, "defense": b.defense, "potential": b.potential,
                "speed": b.speed or 50, "stamina": b.stamina or 50,
                "technique": b.technique or 50, "sharpness": b.sharpness or 50,
                "goals": b.goals or 0, "assists": b.assists or 0,
                "market_value": b.market_value,
            },
        }

    return render_template(
        "compare.html",
        state=state,
        player_team=player_team,
        players=players,
        comparison=comparison,
        a_id=a_id,
        b_id=b_id,
    )


@bp.route("/player_profile/<int:player_id>")
def player_profile(player_id):
    player = db.session.get(Player, player_id)
    if not player:
        return {"error": "not found"}, 404

    ratings = (
        PlayerRating.query.filter_by(player_id=player_id)
        .order_by(PlayerRating.id.desc())
        .limit(10)
        .all()
    )
    ratings_data = [
        {
            "rating": r.rating,
            "goals": r.goals,
            "assists": r.assists,
            "match_id": r.match_id,
        }
        for r in ratings
    ]

    avg_rating = (
        round(sum(r.rating for r in ratings) / len(ratings), 1)
        if ratings
        else 0
    )

    return {
        "id": player.id,
        "name": player.name,
        "position": player.position,
        "attack": player.attack,
        "defense": player.defense,
        "potential": player.potential,
        "age": player.age,
        "height": player.height,
        "weight": player.weight,
        "personality": player.personality,
        "region": player.region,
        "market_value": player.market_value,
        "fitness": player.fitness,
        "injury_days": player.injury_days,
        "goals": player.goals,
        "assists": player.assists,
        "matches_played": player.matches_played,
        "stamina": player.stamina,
        "technique": player.technique,
        "speed": player.speed,
        "sharpness": player.sharpness or 0,
        "morale": player.morale or 0,
        "traits": player.traits or [],
        "youth_goals": (player.team.youth_goals if player.team else 0),
        "avg_rating": avg_rating,
        "recent_ratings": ratings_data,
    }


@bp.route("/training")
def training():
    state = safe_state()
    player_team = safe_player_team()
    players = Player.query.filter_by(team_id=player_team.id).all()
    return render_template(
        "training.html", state=state, player_team=player_team, players=players
    )


@bp.route("/training/save", methods=["POST"])
def save_training():
    state = safe_state()
    player_team = safe_player_team()

    focus = request.form.get("focus", "balanced")
    try:
        intensity = int(request.form.get("intensity", 5))
        intensity = max(1, min(10, intensity))
    except Exception:
        intensity = 5

    player_team.training_focus = focus
    player_team.training_intensity = intensity

    try:
        db.session.commit()
        flash("تم حفظ إعدادات التدريب بنجاح!")
    except Exception:
        db.session.rollback()
        flash("فشل في حفظ إعدادات التدريب!")

    return redirect("/training")


@bp.route("/staff")
def staff():
    state = safe_state()
    player_team = safe_player_team()

    team_staff = Staff.query.filter_by(team_id=player_team.id).all()
    available_staff = Staff.query.filter_by(team_id=None).all()

    return render_template(
        "staff.html",
        state=state,
        player_team=player_team,
        team_staff=team_staff,
        available_staff=available_staff,
    )


@bp.route("/staff/hire/<int:staff_id>", methods=["POST"])
def hire_staff(staff_id):
    state = safe_state()
    player_team = safe_player_team()

    s = db.session.get(Staff, staff_id)
    ok, message = hire_staff_member(player_team, s)
    flash(message)

    db.session.commit()
    return redirect("/staff")


@bp.route("/staff/fire/<int:staff_id>", methods=["POST"])
def fire_staff(staff_id):
    state = safe_state()
    player_team = safe_player_team()

    s = db.session.get(Staff, staff_id)
    ok, message = fire_staff_member(player_team, s)
    flash(message)

    db.session.commit()
    return redirect("/staff")


@bp.route("/staff/generate", methods=["POST"])
def generate_staff():
    state = safe_state()
    ok, message = generate_staff_pool()
    flash(message)

    db.session.commit()
    return redirect("/staff")


@bp.route("/facilities")
def facilities():
    state = safe_state()
    player_team = safe_player_team()

    upgrade_cost_training = (player_team.training_facilities or 3) * TRAINING_COST_PER_LEVEL
    upgrade_cost_youth = (player_team.youth_facilities or 3) * YOUTH_COST_PER_LEVEL

    return render_template(
        "facilities.html",
        state=state,
        player_team=player_team,
        upgrade_cost_training=upgrade_cost_training,
        upgrade_cost_youth=upgrade_cost_youth,
    )


@bp.route("/upgrade_facility/<facility_type>", methods=["POST"])
def upgrade_facility(facility_type):
    state = safe_state()
    player_team = safe_player_team()

    if facility_type == "training":
        level = player_team.training_facilities or 3
        cost = level * TRAINING_COST_PER_LEVEL
        if level >= MAX_FACILITY_LEVEL:
            flash("مرافق التدريب في أقصى مستوى بالفعل!")
        elif player_team.budget >= cost:
            player_team.training_facilities = level + 1
            player_team.budget -= cost
            flash(f"تم ترقية مرافق التدريب إلى المستوى {level + 1}!")
        else:
            flash("الميزانية غير كافية!")
    elif facility_type == "youth":
        level = player_team.youth_facilities or 3
        cost = level * YOUTH_COST_PER_LEVEL
        if level >= MAX_FACILITY_LEVEL:
            flash("أكاديمية الشباب في أقصى مستوى بالفعل!")
        elif player_team.budget >= cost:
            player_team.youth_facilities = level + 1
            player_team.budget -= cost
            flash(f"تم ترقية أكاديمية الشباب إلى المستوى {level + 1}!")
        else:
            flash("الميزانية غير كافية!")

    db.session.commit()
    return redirect("/facilities")


@bp.route("/medical")
def medical():
    state = safe_state()
    player_team = safe_player_team()
    players = Player.query.filter_by(team_id=player_team.id).all()

    injured = [p for p in players if p.injury_days > 0]
    fit = [p for p in players if p.injury_days == 0]

    return render_template(
        "medical.html",
        state=state,
        player_team=player_team,
        injured=injured,
        fit=fit,
    )


@bp.route("/scouting")
def scouting():
    from config import PRESSING_STYLES, SCOUT_REGIONS

    state = safe_state()
    player_team = safe_player_team()

    other_players = (
        Player.query.filter(Player.team_id != player_team.id)
        .order_by(Player.market_value.desc())
        .all()
    )
    free_agents = Player.query.filter_by(team_id=None).order_by(
        Player.market_value.desc()
    ).all()

    scouts = Staff.query.filter_by(team_id=player_team.id, role="scout").all()
    squad = Player.query.filter_by(team_id=player_team.id).all()

    ratings = {}
    for p in list(other_players) + list(free_agents):
        ratings[p.id] = get_display_rating(p, player_team)

    return render_template(
        "scouting.html",
        state=state,
        player_team=player_team,
        players=other_players,
        free_agents=free_agents,
        ratings=ratings,
        scouts=scouts,
        squad=squad,
        regions=SCOUT_REGIONS,
    )


@bp.route("/scout/assign/<int:staff_id>", methods=["POST"])
def scout_assign(staff_id):
    player_team = safe_player_team()
    staff = db.session.get(Staff, staff_id)
    region = request.form.get("region", "Europe")
    ok, message = assign_scout_to_region(player_team, staff, region)
    flash(message)
    db.session.commit()
    return redirect("/scouting")


@bp.route("/scout/unassign/<int:staff_id>", methods=["POST"])
def scout_unassign(staff_id):
    player_team = safe_player_team()
    staff = db.session.get(Staff, staff_id)
    if staff and staff.team_id == player_team.id:
        unassign_scout(staff)
        db.session.commit()
        flash("تم إلغاء المهمة!")
    return redirect("/scouting")


@bp.route("/buy_player/<int:player_id>", methods=["POST"])
def buy_player(player_id):
    state = safe_state()
    player_team = safe_player_team()

    target = db.session.get(Player, player_id)
    sell_on = request.form.get("sell_on", type=float) or 0.0
    sell_on = 0.0 if sell_on > 0.3 else sell_on
    ok, message = purchase_player(player_team, target, state.current_day, sell_on)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء الشراء!")

    flash(message)
    return redirect("/scouting")