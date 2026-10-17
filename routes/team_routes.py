"""Team management blueprint: squad, formation, staff, facilities, transfers."""

import json

from flask import Blueprint, flash, jsonify, redirect, render_template, request

bp = Blueprint("team", __name__)

from config import (
    FORMATION_LAYOUT_KEY,
    MAX_FACILITY_LEVEL,
    PRESET_FORMATIONS,
    PRESET_TYPES,
    TRAINING_COST_PER_LEVEL,
    YOUTH_COST_PER_LEVEL,
)
from models import db, Player, PlayerRating, ScoutReport, Staff, Team, TacticPreset
from services import (
    apply_formation,
    assign_players_to_slots,
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
    starters = [p for p in players if p.is_starting and p.injury_days == 0]

    tiers_data = calculate_team_tier(player_team.id)

    return render_template(
        "squad.html",
        state=state,
        player_team=player_team,
        players=players,
        starters=starters,
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

    # Hard rules engine: suspended / unregistered / low-fitness players are
    # unselectable (single validation source; messages come from the engine).
    turning_on = not player.is_starting
    if turning_on:
        try:
            from rules_engine import _player_eligible
            ok, reason = _player_eligible(player, "league", need_start=True)
            if not ok:
                return jsonify({"success": False, "message": reason}), 400
        except Exception:
            pass

    # Flip-then-verify: count AFTER the change (post-flush) so two rapid
    # clicks can't both pass a stale pre-check and leave 12 starters.
    turning_on = not player.is_starting
    if turning_on:
        current_starters = Player.query.filter_by(
            team_id=player.team_id, is_starting=True
        ).count()
        if current_starters >= 11:
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
        db.session.flush()
        if turning_on:
            fresh_count = Player.query.filter_by(
                team_id=player.team_id, is_starting=True
            ).count()
            if fresh_count > 11:
                player.is_starting = False
                db.session.rollback()
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": "لديك بالفعل 11 لاعباً في التشكيلة الأساسية!",
                        }
                    ),
                    400,
                )
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
                "detailed_position": p.detailed_position or p.position,
                "attack": p.attack,
                "defense": p.defense,
                "overall": p.overall,
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
        formations=PRESET_FORMATIONS,
        presets=presets,
    )


@bp.route("/formation/save", methods=["POST"])
def save_formation():
    state = safe_state()
    player_team = safe_player_team()

    data = request.get_json()
    if not data or "positions" not in data:
        return jsonify({"error": "بيانات غير صالحة"}), 400

    positions = data.get("positions") or {}
    ok, error = validate_formation(player_team, positions)
    if not ok:
        return jsonify({"error": error}), 400

    # Hard rules engine on the candidate XI (foreign cap/suspensions/etc.).
    try:
        from rules_engine import validate_starting_xi as _valid_xi
        ids = set()
        for k in (positions or {}).keys():
            try:
                ids.add(int(k))
            except Exception:
                pass  # FORMATION_LAYOUT_KEY and similar reserved keys stay out
        if len(ids) == 11:
            prev = {p.id: p.is_starting for p in player_team.players}
            for p in player_team.players:
                p.is_starting = p.id in ids
            ok2, errs = _valid_xi(player_team, "league")
            for p in player_team.players:
                p.is_starting = prev.get(p.id, False)
            if not ok2:
                return jsonify({"error": errs[0] if errs else error}), 400
    except Exception:
        pass

    apply_formation(player_team, positions)

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
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("فشل في حفظ التعليمات التكتيكية!")
        return redirect("/formation")
    flash("تم حفظ التعليمات التكتيكية!")
    return redirect("/formation")


# --- FM: set-piece takers ---------------------------------------------
@bp.route("/formation/takers", methods=["POST"])
def save_takers():
    player_team = safe_player_team()
    squad = {p.id: p.name for p in Player.query.filter_by(team_id=player_team.id).all()}

    # Empty string ("— تلقائي —") clears back to automatic; a numeric id
    # must belong to the squad, otherwise reject instead of ignoring.
    raw_p = (request.form.get("penalty_taker_id") or "").strip()
    raw_f = (request.form.get("freekick_taker_id") or "").strip()
    try:
        pid_p = int(raw_p) if raw_p else None
        pid_f = int(raw_f) if raw_f else None
    except ValueError:
        flash("قيمة غير صالحة لمنفذي الكرات الثابتة!")
        return redirect("/formation")
    if pid_p is not None and pid_p not in squad:
        flash("مسدد ركلات الجزاء المختار ليس من فريقك!")
        return redirect("/formation")
    if pid_f is not None and pid_f not in squad:
        flash("مسدد الركلات الحرة المختار ليس من فريقك!")
        return redirect("/formation")
    player_team.penalty_taker_id = pid_p
    player_team.freekick_taker_id = pid_f
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("فشل في تحديث منفذي الكرات الثابتة!")
        return redirect("/formation")
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
    if not positions:
        flash("احفظ التشكيلة على اللوحة أولاً قبل حفظها كخطة!")
        return redirect("/formation")
    existing = TacticPreset.query.filter_by(
        team_id=player_team.id, name=name
    ).first()
    if existing:
        existing.preset_type = preset_type if preset_type in PRESET_TYPES else "standard"
        existing.formation_data = positions
        existing.pressing_style = player_team.pressing_style or "normal"
        existing.match_tactic = player_team.match_tactic or "balanced"
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("فشل في تحديث الخطة!")
            return redirect("/formation")
        flash(f"تم تحديث الخطة «{existing.name}»!")
        return redirect("/formation")
    preset = TacticPreset(
        team_id=player_team.id,
        preset_type=preset_type if preset_type in PRESET_TYPES else "standard",
        name=name,
        formation_data=positions,
        pressing_style=player_team.pressing_style or "normal",
        match_tactic=player_team.match_tactic or "balanced",
    )
    db.session.add(preset)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("فشل في حفظ الخطة!")
        return redirect("/formation")
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
    else:
        flash("الخطة المحفوظة لا تحتوي على تشكيلة صالحة!")
        return redirect("/formation")
    player_team.pressing_style = preset.pressing_style or "normal"
    player_team.match_tactic = preset.match_tactic or "balanced"
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("فشل في تطبيق الخطة!")
        return redirect("/formation")
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
    state = safe_state()
    from models import Mentoring

    mentor_id = request.form.get("mentor_id", type=int)
    mentee_id = request.form.get("mentee_id", type=int)
    mentor = db.session.get(Player, mentor_id)
    mentee = db.session.get(Player, mentee_id)
    if not mentor or not mentee or mentor.team_id != player_team.id or mentee.team_id != player_team.id:
        flash("اللاعبان غير صالحين!")
        return redirect("/squad")
    if mentor_id == mentee_id:
        flash("لا يمكن للاعب أن يوجّه نفسه!")
        return redirect("/squad")
    if (mentor.age or 0) < 28 or (mentee.age or 0) > 24:
        flash("التوجيه يتطلب مرشداً مخضرماً (28+) وشاباً (24 أو أقل)!")
        return redirect("/squad")
    if (mentor.age or 0) <= (mentee.age or 0):
        flash("يجب أن يكون المرشد أكبر سناً من اللاعب الشاب!")
        return redirect("/squad")

    exists = Mentoring.query.filter_by(team_id=player_team.id, mentee_id=mentee_id).first()
    if exists:
        flash("هذا الشاب لديه مرشد بالفعل!")
        return redirect("/squad")

    db.session.add(Mentoring(
        team_id=player_team.id,
        mentor_id=mentor_id,
        mentee_id=mentee_id,
        started_day=(state.current_day if state and state.current_day else 0),
    ))
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("فشل في بدء علاقة التوجيه!")
        return redirect("/squad")
    flash(f"أصبح {mentor.name} مرشداً لـ {mentee.name}!")
    return redirect("/squad")


@bp.route("/mentoring/remove/<int:mentoring_id>", methods=["POST"])
def mentoring_remove(mentoring_id):
    from models import Mentoring

    player_team = safe_player_team()
    m = db.session.get(Mentoring, mentoring_id)
    if m:
        if m.team_id != player_team.id:
            flash("لا يمكنك إنهاء توجيه فريق آخر!")
            return redirect("/squad")
        db.session.delete(m)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("فشل في إنهاء علاقة التوجيه!")
            return redirect("/squad")
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
    cash = max(0.0, cash)
    ok, message = run_swap(player_team, target, offered, cash, state.current_day)
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/scouting")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء المبادلة!")
        return redirect("/scouting")
    flash(message)
    return redirect("/scouting")


# --- FM: player role assignment ---------------------------------------
@bp.route("/formation/role", methods=["POST"])
def save_role():
    from config import PLAYER_ROLES
    from sqlalchemy.orm.attributes import flag_modified

    player_team = safe_player_team()
    player_id = request.form.get("player_id", type=int)
    role = request.form.get("role", "standard")
    if role not in PLAYER_ROLES:
        role = "standard"
    # Missing/foreign ids previously stored a garbage "None" key in
    # formation_data — reject them instead of polluting the XI.
    player = db.session.get(Player, player_id) if player_id else None
    if not player or player.team_id != player_team.id:
        return jsonify({"success": False, "message": "اللاعب غير موجود في فريقك!"}), 404
    pid = str(player.id)
    fd = player_team.formation_data
    if not isinstance(fd, dict):
        fd = {}
    else:
        fd = dict(fd)
    entry = fd.get(pid)
    if isinstance(entry, dict):
        entry["role"] = role
    elif pid in fd:
        fd[pid] = {"x": 50, "y": 50, "role": role}
    else:
        fd[pid] = {"x": 50, "y": 50, "role": role}
    player_team.formation_data = fd
    flag_modified(player_team, "formation_data")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "message": "فشل في حفظ الدور!"}), 500
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
                "attack": a.attack, "defense": a.defense,
                "overall": (a.overall or max(a.attack or 0, a.defense or 0)),
                "potential": a.potential,
                "speed": a.speed or 50, "stamina": a.stamina or 50,
                "technique": a.technique or 50, "sharpness": a.sharpness or 50,
                "goals": a.goals or 0, "assists": a.assists or 0,
                "market_value": a.market_value,
            },
            "b": {
                "id": b.id, "name": b.name, "position": b.position,
                "age": b.age, "rating": get_display_rating(b, player_team),
                "attack": b.attack, "defense": b.defense,
                "overall": (b.overall or max(b.attack or 0, b.defense or 0)),
                "potential": b.potential,
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

    try:
        top_attrs = [
            {"attr": k, "value": int(getattr(player, k, 50) or 50)}
            for k, _ in (player.top_overall_contributors(5) if hasattr(player, "top_overall_contributors") else [])
        ]
    except Exception:
        top_attrs = []
    try:
        overall_val = int(player.overall or max(player.attack or 0, player.defense or 0) or 50)
    except Exception:
        overall_val = 50

    return {
        "id": player.id,
        "name": player.name,
        "position": player.position,
        "detailed_position": getattr(player, "detailed_position", None) or player.position,
        "attack": player.attack,
        "defense": player.defense,
        "overall": overall_val,
        "top_attributes": top_attrs,
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
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/staff")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء التعيين!")
        return redirect("/staff")
    flash(message)
    return redirect("/staff")


@bp.route("/staff/fire/<int:staff_id>", methods=["POST"])
def fire_staff(staff_id):
    state = safe_state()
    player_team = safe_player_team()

    s = db.session.get(Staff, staff_id)
    ok, message = fire_staff_member(player_team, s)
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/staff")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء الإقالة!")
        return redirect("/staff")
    flash(message)
    return redirect("/staff")


@bp.route("/staff/generate", methods=["POST"])
def generate_staff():
    state = safe_state()
    ok, message = generate_staff_pool()
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/staff")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء توليد الطاقم!")
        return redirect("/staff")
    flash(message)
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
    else:
        flash("نوع المرفق غير صالح!")
        return redirect("/facilities")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء الترقية!")
        return redirect("/facilities")
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
    from services import get_transfer_status

    state = safe_state()
    player_team = safe_player_team()

    other_players = (
        Player.query.filter(Player.team_id != player_team.id)
        .order_by(Player.market_value.desc())
        .limit(100)
        .all()
    )
    free_agents = (
        Player.query.filter_by(team_id=None)
        .order_by(Player.market_value.desc())
        .limit(100)
        .all()
    )

    scouts = Staff.query.filter_by(team_id=player_team.id, role="scout").all()
    squad = Player.query.filter_by(team_id=player_team.id).all()

    ratings = {}
    for p in list(other_players) + list(free_agents):
        ratings[p.id] = get_display_rating(p, player_team)

    transfer_status = get_transfer_status(state)
    buyback_ids = set()
    try:
        from models import BuyBackClause
        buyback_ids = {c.player_id for c in BuyBackClause.query.filter_by(club_id=player_team.id).all()}
    except Exception:
        pass

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
        transfer_status=transfer_status,
        buyback_ids=buyback_ids,
    )


@bp.route("/scout/assign/<int:staff_id>", methods=["POST"])
def scout_assign(staff_id):
    player_team = safe_player_team()
    staff = db.session.get(Staff, staff_id)
    region = request.form.get("region", "Europe")
    ok, message = assign_scout_to_region(player_team, staff, region)
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/scouting")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء تعيين الكشاف!")
        return redirect("/scouting")
    flash(message)
    return redirect("/scouting")


@bp.route("/scout/unassign/<int:staff_id>", methods=["POST"])
def scout_unassign(staff_id):
    player_team = safe_player_team()
    staff = db.session.get(Staff, staff_id)
    if staff and staff.team_id == player_team.id:
        unassign_scout(staff)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("حدث خطأ أثناء إلغاء المهمة!")
            return redirect("/scouting")
        flash("تم إلغاء المهمة!")
    return redirect("/scouting")


@bp.route("/buy_player/<int:player_id>", methods=["POST"])
def buy_player(player_id):
    state = safe_state()
    player_team = safe_player_team()

    target = db.session.get(Player, player_id)
    sell_on = request.form.get("sell_on", type=float) or 0.0
    sell_on = max(0.0, min(0.3, sell_on))
    # Full FIFA-style terms (all optional, backward compatible).
    wage = request.form.get("wage", type=int) or 0
    years = request.form.get("years", type=int) or 3
    signing_bonus = request.form.get("signing_bonus", type=int) or 0
    release_clause = request.form.get("release_clause", type=int) or None
    installments = request.form.get("installments", type=int) or 1
    buy_back = request.form.get("buy_back", type=int) or None
    first_refusal = bool(request.form.get("first_refusal"))
    perf_bonus = request.form.get("perf_bonus", type=int) or 0
    perf_target = request.form.get("perf_target", type=int) or 0
    ok, message = purchase_player(
        player_team, target, state.current_day, sell_on,
        wage=wage, years=years, signing_bonus=signing_bonus,
        release_clause=release_clause, installments=installments,
        buy_back_price=buy_back, first_refusal=first_refusal,
        performance_bonus=perf_bonus, performance_target=perf_target,
    )
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/scouting")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء الشراء!")
        return redirect("/scouting")

    flash(message)
    return redirect("/scouting")


# --- Full transfer system: windows, lists, loans, contracts --------------
@bp.route("/transfer/window/<mode>", methods=["POST"])
def transfer_window(mode):
    from services import set_transfer_window

    state = safe_state()
    message = set_transfer_window(state, mode)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء تحديث النافذة!")
        return redirect("/scouting")
    flash(message)
    return redirect("/scouting")


@bp.route("/player/<int:player_id>/list", methods=["POST"])
def transfer_list(player_id):
    from services import list_transfer

    player_team = safe_player_team()
    player = db.session.get(Player, player_id)
    ok, message = list_transfer(player_team, player)
    if not ok:
        db.session.rollback()
    else:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            message = "حدث خطأ!"
    flash(message)
    return redirect("/squad")


@bp.route("/player/<int:player_id>/unlist", methods=["POST"])
def transfer_unlist(player_id):
    from services import unlist_transfer

    player_team = safe_player_team()
    player = db.session.get(Player, player_id)
    ok, message = unlist_transfer(player_team, player)
    if not ok:
        db.session.rollback()
    else:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            message = "حدث خطأ!"
    flash(message)
    return redirect("/squad")


@bp.route("/player/<int:player_id>/request_transfer", methods=["POST"])
def transfer_request(player_id):
    from services import request_transfer

    state = safe_state()
    player_team = safe_player_team()
    player = db.session.get(Player, player_id)
    ok, message = request_transfer(player_team, player, state.current_day)
    if not ok:
        db.session.rollback()
    else:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            message = "حدث خطأ!"
    flash(message)
    return redirect("/squad")


@bp.route("/player/<int:player_id>/withdraw_request", methods=["POST"])
def transfer_withdraw(player_id):
    from services import withdraw_transfer_request

    player_team = safe_player_team()
    player = db.session.get(Player, player_id)
    ok, message = withdraw_transfer_request(player_team, player)
    if not ok:
        db.session.rollback()
    else:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            message = "حدث خطأ!"
    flash(message)
    return redirect("/squad")


@bp.route("/free_agent/<int:player_id>/sign", methods=["POST"])
def free_agent_sign(player_id):
    from services import sign_free_agent

    state = safe_state()
    player_team = safe_player_team()
    target = db.session.get(Player, player_id)
    wage = request.form.get("wage", type=int) or 0
    years = request.form.get("years", type=int) or 3
    bonus = request.form.get("signing_bonus", type=int) or 0
    ok, message = sign_free_agent(player_team, target, state.current_day,
                                  wage=wage, years=years, signing_bonus=bonus)
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/scouting")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء التعاقد!")
        return redirect("/scouting")
    flash(message)
    return redirect("/scouting")


@bp.route("/loan_offer/<int:player_id>", methods=["POST"])
def loan_offer(player_id):
    from services import create_bid

    state = safe_state()
    player_team = safe_player_team()
    target = db.session.get(Player, player_id)
    days = request.form.get("loan_days", type=int) or 180
    fee = request.form.get("loan_fee", type=int) or 0
    buy_option = request.form.get("buy_option", type=int) or None
    ok, message = create_bid(player_team, target, 0, state.current_day,
                             is_loan=True, loan_days=days,
                             loan_fee=fee, buy_option=buy_option)
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/scouting")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء إرسال عرض الإعارة!")
        return redirect("/scouting")
    flash(message)
    return redirect("/messages")


@bp.route("/precontract/<int:player_id>", methods=["POST"])
def precontract(player_id):
    from services import make_precontract

    state = safe_state()
    player_team = safe_player_team()
    target = db.session.get(Player, player_id)
    wage = request.form.get("wage", type=int) or 0
    years = request.form.get("years", type=int) or 3
    ok, message = make_precontract(player_team, target, state.current_day,
                                   wage=wage, years=years)
    if not ok:
        db.session.rollback()
    else:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            message = "حدث خطأ!"
    flash(message)
    return redirect("/scouting")


@bp.route("/release_clause/<int:player_id>", methods=["POST"])
def release_clause_buy(player_id):
    from services import trigger_release_clause

    state = safe_state()
    player_team = safe_player_team()
    target = db.session.get(Player, player_id)
    wage = request.form.get("wage", type=int) or 0
    years = request.form.get("years", type=int) or 3
    ok, message = trigger_release_clause(player_team, target, state.current_day,
                                         wage=wage, years=years)
    if not ok:
        db.session.rollback()
        flash(message)
        return redirect("/scouting")
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ أثناء تفعيل الشرط الجزائي!")
        return redirect("/scouting")
    flash(message)
    return redirect("/scouting")


@bp.route("/loan/<int:player_id>/buy", methods=["POST"])
def loan_buy(player_id):
    from services import exercise_loan_buy

    state = safe_state()
    player_team = safe_player_team()
    player = db.session.get(Player, player_id)
    ok, message = exercise_loan_buy(player_team, player, state.current_day)
    if not ok:
        db.session.rollback()
    else:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            message = "حدث خطأ!"
    flash(message)
    return redirect("/squad")


@bp.route("/buyback/<int:player_id>/exercise", methods=["POST"])
def buyback_exercise(player_id):
    from services import exercise_buy_back

    state = safe_state()
    player_team = safe_player_team()
    player = db.session.get(Player, player_id)
    ok, message = exercise_buy_back(player_team, player, state.current_day)
    if not ok:
        db.session.rollback()
    else:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            message = "حدث خطأ!"
    flash(message)
    return redirect("/scouting")


@bp.route("/formation/ability-fill", methods=["POST"])
def formation_ability_fill():
    """Spread by ability: fill the chosen preset layout's empty slots.

    Returns assignments only — nothing is persisted — so the manager reviews
    the staged XI on the pitch and confirms with 'حفظ التشكيلة' like any
    manual drag. Empty slots stay empty when no compatible (non-injured)
    player remains; goalkeepers never fill outfield slots and vice versa.
    """
    player_team = safe_player_team()
    data = request.get_json(silent=True) or {}
    layout_key = (data.get("layout") or "").strip()
    fmt = next((f for f in PRESET_FORMATIONS if f["key"] == layout_key), None)
    if fmt is None:
        return jsonify({"success": False, "error": "خطة جاهزة غير معروفة!"}), 400

    exclude_ids = []
    for raw in (data.get("exclude_ids") or []):
        try:
            exclude_ids.append(int(raw))
        except (TypeError, ValueError):
            pass

    assignments, unfilled = assign_players_to_slots(
        player_team.players, fmt["slots"], exclude_ids=exclude_ids
    )
    return jsonify({
        "success": True,
        "layout": fmt["key"],
        "label": fmt["label"],
        "assignments": assignments,
        "unfilled": unfilled,
    })


@bp.route("/formation/auto-pick", methods=["POST"])
def formation_auto_pick():
    """Auto-pick button (rules engine): best rules-compliant XI + bench.

    With a body {"layout": "<preset key>"} the XI is also distributed across
    that layout's slots by ability, so the saved pitch keeps coordinates and
    the layout key. Without it the legacy coordinate-less behaviour runs.
    """
    from rules_engine import auto_pick_xi

    player_team = safe_player_team()
    data = request.get_json(silent=True) or {}
    layout = next(
        (f for f in PRESET_FORMATIONS if f["key"] == (data.get("layout") or "")),
        None,
    )

    pick = auto_pick_xi(player_team, "league")
    starters = pick.get("starters") or []
    if len(starters) != 11:
        return jsonify({"success": False, "message": "لا يوجد 11 لاعباً مؤهلاً!"}), 400

    if layout:
        placed, _unfilled = assign_players_to_slots(starters, layout["slots"])
        positions = {FORMATION_LAYOUT_KEY: layout["key"]}
        for a in placed:
            positions[str(a["player_id"])] = {"x": a["x"], "y": a["y"]}
    else:
        positions = {str(p.id): {} for p in starters}
    apply_formation(player_team, positions)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "message": "فشل في حفظ التشكيلة!"}), 500
    return jsonify({"success": True, "message": "تم اختيار التشكيلة تلقائياً!",
                    "ids": [p.id for p in starters]})


@bp.route("/tactics/save", methods=["POST"])
def save_full_tactics():
    """FM-style full tactics save (validated server-side, capped PIs)."""
    from tactics import get_team_tactics, on_tactics_changed

    player_team = safe_player_team()
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"success": False, "message": "بيانات غير صالحة"}), 400
    base = get_team_tactics(player_team)
    merged = dict(base)
    for key in ("formation", "mentality", "in_possession", "out_of_possession",
                "transitions", "set_pieces", "slots"):
        if key in payload:
            merged[key] = payload[key]
    try:
        mentality = int(merged.get("mentality", 3))
        merged["mentality"] = max(1, min(5, mentality))
    except Exception:
        merged["mentality"] = 3
    try:
        slots = merged.get("slots") or []
        for s in slots:
            pis = (s.get("pi") or [])[:3]
            s["pi"] = pis
        merged["slots"] = slots
    except Exception:
        pass
    changed = on_tactics_changed(player_team, merged)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"success": False, "message": "فشل في حفظ الخطة!"}), 500
    return jsonify({"success": True, "familiarity_reset": bool(changed),
                    "familiarity": getattr(player_team, "tactical_familiarity", 100)})


@bp.route("/difficulty", methods=["POST"])
def set_difficulty():
    """AI difficulty (scales skill means + delays only, never hidden mods)."""
    from config import AI_DIFFICULTIES

    state = safe_state()
    mode = (request.form.get("mode") or "عادي").strip()
    if mode not in AI_DIFFICULTIES:
        flash("مستوى صعوبة غير صالح!")
        return redirect("/dashboard")
    state.ai_difficulty = mode
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("فشل في حفظ الصعوبة!")
        return redirect("/dashboard")
    flash(f"مستوى صعوبة الذكاء الاصطناعي: {mode}")
    return redirect("/dashboard")