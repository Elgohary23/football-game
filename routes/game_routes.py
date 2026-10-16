"""Main game blueprint: navigation, dashboard, calendar, match, progression."""

import json
from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session

bp = Blueprint("main", __name__)

from models import (
    db,
    BoardObjective,
    GameState,
    League,
    Match,
    Player,
    Team,
)
from simulation import (
    get_current_match,
    get_match_report,
    get_pending_actions,
    get_recent_results,
    is_match_day,
    process_promotion_relegation,
    simulate_calendar,
    simulate_day,
)
from services import (
    get_league_table,
    get_season_rounds,
    get_team_rank,
    invalidate_league_cache,
    safe_player_team,
    safe_state,
)
from live_match import (
    advance_live_mins,
    advance_week_if_complete,
    finish_live,
    finish_match_week,
    live_state_payload,
    run_auto_match,
    start_live,
    substitute_player,
)


def _involves_player(match: Match) -> bool:
    return bool(
        match
        and (
            (match.home_team and match.home_team.is_player_team)
            or (match.away_team and match.away_team.is_player_team)
        )
    )


def _player_xi_complete() -> bool:
    """The manager needs 11 starters before a live match may begin.

    Delegates to the hard rules engine (single validation source).
    """
    try:
        from rules_engine import validate_starting_xi
        player_team = Team.query.filter_by(is_player_team=True).first()
        if not player_team:
            return False
        ok, _ = validate_starting_xi(player_team, "league")
        return bool(ok)
    except Exception:
        try:
            player_team = Team.query.filter_by(is_player_team=True).first()
            if not player_team:
                return False
            return (
                Player.query.filter_by(team_id=player_team.id, is_starting=True).count()
                >= 11
            )
        except Exception:
            return False


def _play_or_start_match_day(state: GameState):
    """Run the day's match via the new live engine.

    Player fixtures open the interactive live screen; everything else is
    simulated in auto mode. Returns the match to hand to the UI, if any.
    """
    if not is_match_day(state):
        return None
    match = get_current_match(state)
    if not match or match.played:
        # No league fixture — a cup tie may still be due for the manager.
        try:
            from cup_services import get_current_cup_match
            match = get_current_cup_match(state)
        except Exception:
            match = None
        if not match or match.played:
            return None
    if _involves_player(match):
        if not _player_xi_complete():
            flash("التشكيلة الأساسية ناقصة! اختر 11 لاعباً أساسياً قبل بدء المباراة.")
            return None
        if start_live(match, mode="live"):
            db.session.commit()
            return match
        db.session.rollback()
        # Already running -> the live page self-heals; a failed start must
        # NOT redirect to /live or the user bounces in a dead loop.
        return match if match.live_active else None
    if run_auto_match(match):
        db.session.commit()
        try:
            from cup_services import after_match_played
            after_match_played(match, state)
            db.session.commit()
        except Exception:
            pass
    return None


def _pending_live_match(state: GameState):
    """A live fixture that still needs to be played by the user."""
    match = get_current_match(state)
    if match and _involves_player(match) and not match.played:
        return match
    try:
        from cup_services import get_current_cup_match
        cup = get_current_cup_match(state)
        if cup and _involves_player(cup) and not cup.played:
            return cup
    except Exception:
        pass
    return None


def _player_team_key(match: Match) -> str | None:
    """Which live-state side belongs to the manager's club (IDOR guard)."""
    if match.home_team and match.home_team.is_player_team:
        return "home_xp"
    if match.away_team and match.away_team.is_player_team:
        return "away_xp"
    return None


@bp.route("/")
def index():
    slot = get_active_slot()
    if slot == 0:
        return redirect("/load")

    try:
        state = GameState.query.first()
        if not state:
            return redirect("/load")

        if not state.manager_name or state.manager_name == "المدرب":
            return redirect(f"/setup/{slot}")

        player_team = Team.query.filter_by(is_player_team=True).first()
        if not player_team:
            return redirect(f"/select-division/{slot}")

    except Exception:
        return redirect("/load")

    return redirect("/dashboard")


def get_active_slot() -> int:
    return session.get("active_save", 0)


def _setup_redirect():
    """Redirect incomplete saves to the right setup step.

    Prevents /dashboard (and friends) from hitting safe_player_team()
    -> abort(500) -> errorhandler redirect /load, which looks like
    'loading fails' with no useful message.
    """
    slot = get_active_slot()
    if not slot:
        return redirect("/load")
    try:
        state = GameState.query.first()
        if not state:
            return redirect("/load")
        if not state.manager_name or state.manager_name == "المدرب":
            return redirect(f"/setup/{slot}")
        if not Team.query.filter_by(is_player_team=True).first():
            flash("أكمل اختيار الفريق أولاً!")
            return redirect(f"/select-division/{slot}")
    except Exception:
        return redirect("/load")
    return None


@bp.route("/dashboard")
def dashboard():
    redir = _setup_redirect()
    if redir:
        return redir
    state = safe_state()
    player_team = safe_player_team()

    league_id = player_team.league_id if player_team else None
    league_table = get_league_table(league_id)
    team_rank = get_team_rank(league_id, player_team)

    players = (
        Player.query.filter_by(team_id=player_team.id)
        .options(db.joinedload(Player.team))
        .all()
    )
    current_match = get_current_match(state)
    recent_results = get_recent_results(
        state, league_id if (league_id and player_team) else None
    )
    pending_actions = get_pending_actions(state)
    board_objectives = BoardObjective.query.filter_by(
        team_id=player_team.id, season=state.season_number or 1
    ).all()

    try:
        from cup_services import get_user_cup_statuses
        cup_statuses = get_user_cup_statuses()
    except Exception:
        cup_statuses = []

    return render_template(
        "dashboard.html",
        state=state,
        player_team=player_team,
        players=players,
        team_rank=team_rank,
        current_match=current_match,
        league_table=league_table,
        recent_results=recent_results,
        pending_actions=pending_actions,
        board_objectives=board_objectives,
        cup_statuses=cup_statuses,
    )


@bp.route("/standings")
def standings():
    redir = _setup_redirect()
    if redir:
        return redir
    state = safe_state()
    player_team = safe_player_team()
    requested_league = request.args.get("league_id", type=int)
    league_id = requested_league or (
        player_team.league_id if player_team else None
    )

    league = db.session.get(League, league_id) if league_id else None
    if requested_league and league is None:
        flash("الدوري المطلوب غير موجود! يتم عرض دوريك الحالي.")
        league_id = player_team.league_id if player_team else None
        league = db.session.get(League, league_id) if league_id else None
    league_table = get_league_table(league_id)
    team_rank = get_team_rank(league_id, player_team)

    leagues = League.query.order_by(League.tier).all()

    return render_template(
        "standings.html",
        state=state,
        player_team=player_team,
        league_table=league_table,
        team_rank=team_rank,
        league=league,
        leagues=leagues,
        selected_league_id=league_id,
    )


@bp.route("/calendar")
def calendar():
    redir = _setup_redirect()
    if redir:
        return redir
    state = safe_state()
    player_team = safe_player_team()

    player_matches = (
        Match.query.options(
            db.joinedload(Match.home_team), db.joinedload(Match.away_team)
        )
        .filter(
            (Match.home_team_id == player_team.id)
            | (Match.away_team_id == player_team.id)
        )
        .order_by(Match.date)
        .all()
    )

    player_matches_json = {}
    try:
        from cup_services import fixture_competition
    except Exception:
        fixture_competition = None
    try:
        from config import CUP_BADGE_COLORS
        league_color = CUP_BADGE_COLORS.get("league", "#3498db")
    except Exception:
        league_color = "#3498db"
    for m in player_matches:
        home = m.home_team
        away = m.away_team
        comp = fixture_competition(m) if fixture_competition else None
        entry = {
            "id": m.id,
            "home": home.name if home else "?",
            "away": away.name if away else "?",
            "home_is_player": home.is_player_team if home else False,
            "away_is_player": away.is_player_team if away else False,
            "score": m.result or "—",
            "played": m.played,
            "week": m.week,
            "home_score": m.home_score,
            "away_score": m.away_score,
            "competition": (comp or {}).get("name") or "الدوري",
            "comp_type": (comp or {}).get("type") or "league",
            "comp_round": (comp or {}).get("round") or "",
            "comp_color": (comp or {}).get("color") or league_color,
        }
        # One entry per date (the calendar JSON contract): an unplayed
        # fixture always wins the slot so a live match is never hidden;
        # same-date extras (cup midweek + league weekend) are counted in
        # "more" with their competition badge.
        key = m.date or f"week-{m.week}"
        existing = player_matches_json.get(key)
        if existing is None:
            player_matches_json[key] = entry
            continue
        extra = {"id": 0, "competition": "", "comp_color": league_color}
        if existing["played"] and not entry["played"]:
            extra.update({k: existing[k] for k in ("id", "competition", "comp_color")})
            entry.setdefault("more", []).append(extra)
            entry["more"].extend(existing.get("more", []))
            player_matches_json[key] = entry
        else:
            extra.update({k: entry[k] for k in ("id", "competition", "comp_color")})
            existing.setdefault("more", []).append(extra)
            existing["more"].extend(entry.get("more", []))

    return render_template(
        "calendar.html",
        state=state,
        player_team=player_team,
        player_matches_json=json.dumps(player_matches_json),
        current_date=state.current_date,
    )


@bp.route("/match")
def match_view():
    redir = _setup_redirect()
    if redir:
        return redir
    state = safe_state()
    match = get_current_match(state)
    scouting = None

    if match:
        opp = (
            match.home_team
            if not match.home_team.is_player_team
            else match.away_team
        )
        if opp and opp.players:
            danger_man = max(opp.players, key=lambda p: p.attack)
            starters = [p for p in opp.players if p.is_starting]
            avg_defense = (
                sum(p.defense for p in starters) / len(starters)
                if starters
                else 0
            )
            scouting = {
                "name": opp.name,
                "danger_man_name": danger_man.name if danger_man else "N/A",
                "danger_man_goals": danger_man.goals if danger_man else 0,
                "avg_defense": avg_defense,
                "morale_desc": (
                    "مرتفع"
                    if opp.morale > 80
                    else "مستقر"
                    if opp.morale > 50
                    else "منخفض"
                ),
            }

    return render_template("match.html", match=match, state=state, scouting=scouting)


@bp.route("/advance_day", methods=["GET", "POST"])
def advance_day():
    state = safe_state()
    if state.season_over:
        flash("الموسم انتهى!")
        return redirect("/")

    simulate_day(state)
    match = _play_or_start_match_day(state)
    if match and _involves_player(match):
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("حدث خطأ في تقدم اليوم!")
            return redirect("/")
        return redirect(f"/live/{match.id}")
    if state.current_week >= get_season_rounds(state.user_league_id):
        state.season_over = True
        flash("انتهى الموسم! اضغط ابدأ الموسم الجديد لتطبيق الصعود والهبوط")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في تقدم اليوم!")
        return redirect("/")

    return redirect("/")


@bp.route("/continue", methods=["GET", "POST"])
def continue_game():
    state = safe_state()
    if state.season_over:
        flash("الموسم انتهى!")
        return redirect("/dashboard")

    result = simulate_calendar(7, state)
    if result["matches_played"] > 0:
        flash(f"تم محاكاة {result['matches_played']} مباريات")

    match = _pending_live_match(state)
    if match:
        if not _player_xi_complete():
            flash("التشكيلة الأساسية ناقصة! اختر 11 لاعباً أساسياً قبل بدء المباراة.")
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            return redirect("/dashboard")
        if start_live(match, mode="live"):
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                flash("حدث خطأ في تقدم اليوم!")
                return redirect("/dashboard")
            return redirect(f"/live/{match.id}")
        # Live fixture already started: make sure the simulated days are saved.
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return redirect(f"/live/{match.id}")

    if state.current_week >= get_season_rounds(state.user_league_id):
        state.season_over = True
        flash("انتهى الموسم! اضغط ابدأ الموسم الجديد لتطبيق الصعود والهبوط")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في تقدم اليوم!")
        return redirect("/dashboard")

    return redirect("/dashboard")


@bp.route("/match_report/<int:match_id>")
def match_report(match_id):
    state = safe_state()
    report = get_match_report(match_id)
    if not report:
        flash("المباراة غير موجودة!")
        return redirect("/dashboard")
    return render_template("match_report.html", report=report, state=state)


# --------------------------------------------------------------------------
# Live match (new FM-style interactive engine)
# --------------------------------------------------------------------------
@bp.route("/live/<int:match_id>")
def live_view(match_id):
    try:
        state = safe_state()
        match = db.session.get(Match, match_id)
        if not match or not (match.live_active or not match.played):
            flash("لا توجد مباراة حية!")
            return redirect("/dashboard")

        if not match.live_active:
            try:
                if not _involves_player(match):
                    run_auto_match(match)
                    db.session.commit()
                else:
                    if not _player_xi_complete():
                        flash("التشكيلة الأساسية ناقصة! اختر 11 لاعباً أساسياً قبل بدء المباراة.")
                        return redirect("/dashboard")
                    start_live(match, mode="live")
                    db.session.commit()
            except Exception:
                db.session.rollback()
                flash("تعذر بدء المباراة! حاول مجدداً.")
                return redirect("/dashboard")

        payload = live_state_payload(match)
        return render_template(
            "live.html",
            match=match,
            state=state,
            payload=payload,
        )
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        flash("حدث خطأ في المباراة الحية!")
        return redirect("/dashboard")


@bp.route("/live/<int:match_id>/state")
def live_state(match_id):
    try:
        match = db.session.get(Match, match_id)
        if not match:
            return {"error": "not found"}, 404
        if not _involves_player(match) and not match.played:
            return {"error": "unauthorized"}, 403
        return live_state_payload(match)
    except Exception:
        return {"error": "could not load live state"}, 500


@bp.route("/live/<int:match_id>/advance", methods=["POST"])
def live_advance(match_id):
    try:
        match = db.session.get(Match, match_id)
        if not match or not match.live_active:
            return {"error": "not live"}, 404
        if not _involves_player(match):
            return {"error": "unauthorized"}, 403
        minutes = request.form.get("minutes", type=int, default=1) or 1
        res = advance_live_mins(match, min(15, max(1, minutes)))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            return {"error": "could not save progress"}, 500
        return res
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return {"error": "advance failed"}, 500


@bp.route("/live/<int:match_id>/sub", methods=["POST"])
def live_sub(match_id):
    try:
        match = db.session.get(Match, match_id)
        if not match or not match.live_active:
            return {"error": "not live"}, 404
        if not _involves_player(match):
            return {"error": "unauthorized"}, 403
        team_key = request.form.get("team_key", "home_xp")
        if team_key not in ("home_xp", "away_xp"):
            return {"ok": False, "message": "فريق غير صالح!"}, 400
        if team_key != _player_team_key(match):
            return {"ok": False, "message": "لا يمكنك تبديل لاعبي الفريق الخصم!"}, 403
        player_out = request.form.get("player_out", type=int)
        player_in = request.form.get("player_in", type=int)
        ok, msg = substitute_player(match, team_key, player_out or 0, player_in or 0)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            return {"ok": False, "message": "تعذر حفظ التبديل!"}, 500
        return {"ok": ok, "message": msg}
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return {"ok": False, "message": "حدث خطأ أثناء التبديل!"}, 500


@bp.route("/live/<int:match_id>/finish", methods=["POST"])
def live_finish(match_id):
    try:
        state = safe_state()
        match = db.session.get(Match, match_id)
        if not match:
            return {"error": "not found"}, 404
        if not _involves_player(match):
            return {"error": "unauthorized"}, 403

        # Double-click safe: an already-finished match returns its result
        # without re-running the week simulation a second time.
        if match.played:
            return {
                "finished": True,
                "season_over": bool(state and state.season_over),
                "others_played": 0,
            }

        others_played = 0
        if not match.played:
            live = match.live_state or {}
            cur = live.get("minute", 0) or 0
            if not live.get("finished"):
                advance_live_mins(match, max(1, 90 - cur))
            finish_live(match)
            try:
                from simulation import update_manager_record
                update_manager_record(match, state)
            except Exception as exc:
                import logging

                logging.getLogger(__name__).exception(
                    "update_manager_record failed for match %s: %s", match.id, exc
                )
            try:
                from cup_services import after_match_played
                from config import CUP_MATCH_WEEK as _CUP_WEEK
            except Exception:
                after_match_played = None
                _CUP_WEEK = 0
            if (match.week or 0) == _CUP_WEEK:
                # Cup tie: record the winner / draw the next round instead
                # of simulating the league week.
                if after_match_played is not None:
                    after_match_played(match, state)
            else:
                others_played = finish_match_week(match)
                advance_week_if_complete(match)
            if state.current_week >= get_season_rounds(state.user_league_id):
                state.season_over = True
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            return {"error": "could not save result"}, 500
        return {
            "finished": True,
            "season_over": bool(state and state.season_over),
            "others_played": others_played,
        }
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return {"error": "finish failed"}, 500


@bp.route("/advance_week", methods=["GET", "POST"])
def advance_week():
    state = safe_state()
    if state.season_over:
        flash("الموسم انتهى!")
        return redirect("/")

    days_simmed = 0
    timed_out = False
    while not is_match_day(state) and not state.season_over:
        simulate_day(state)
        days_simmed += 1
        if days_simmed > 14:
            timed_out = True
            break

    match = None if timed_out else _play_or_start_match_day(state)
    if timed_out and match is None and not state.season_over:
        flash("لا توجد مباريات في الأسبوعين القادمين — قد يكون الدوري في فترة توقف.")
    if match and _involves_player(match):
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("حدث خطأ في تقدم الأسبوع!")
            return redirect("/")
        return redirect(f"/live/{match.id}")
    if state.current_week >= get_season_rounds(state.user_league_id):
        state.season_over = True
        flash("انتهى الموسم! اضغط ابدأ الموسم الجديد لتطبيق الصعود والهبوط")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("حدث خطأ في تقدم الأسبوع!")
        return redirect("/")

    return redirect("/")


@bp.route("/simulate_to_date", methods=["POST"])
def simulate_to_date():
    state = safe_state()
    # fetch() callers (calendar sweep) want JSON so the hover can land on
    # the final day exactly when the server finishes; classic form posts
    # keep the flash + redirect behaviour.
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
    )

    def _fail(msg, redirect_to):
        if wants_json:
            flash(msg)
            return jsonify({"ok": False, "message": msg, "redirect": redirect_to}), 400
        flash(msg)
        return redirect(redirect_to)

    if state.season_over:
        return _fail("الموسم انتهى!", "/dashboard")

    target_date_str = request.form.get("target_date")
    if not target_date_str:
        return _fail("تاريخ غير صالح!", "/calendar")

    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        current_date = datetime.strptime(state.current_date, "%Y-%m-%d").date()
        days_to_sim = (target_date - current_date).days
    except Exception:
        return _fail("تاريخ غير صالح!", "/calendar")

    if days_to_sim <= 0:
        return _fail("يرجى اختيار تاريخ في المستقبل!", "/calendar")

    # Cap the fast-forward so a date past the season end can't spin the
    # calendar into uncharted weeks with weird results.
    capped = False
    if days_to_sim > 366:
        days_to_sim = 366
        capped = True

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    matches_played = 0
    if days_to_sim > 0:
        # Calendar fast-forward is a bulk background sim: every fixture in
        # the span (including the manager's) is auto-played in the
        # background and the table/stats/records are updated exactly like a
        # normal match. Pending transfer offers past their deadline expire
        # with no moves in or out, so simulating past any event closes it.
        result = simulate_calendar(days_to_sim, state, background=True)
        matches_played = result.get("matches_played", 0)

    if state.current_week >= get_season_rounds(state.user_league_id):
        state.season_over = True

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return _fail("حدث خطأ في المحاكاة!", "/calendar")

    note = " (تم تحديد المحاكاة بسنة واحدة كحد أقصى)" if capped else ""
    msg = f"تمت المحاكاة! وصلت لتاريخ {state.current_date}، لعبت {matches_played} مباريات{note}"
    if wants_json:
        flash(msg)
        return jsonify(
            {
                "ok": True,
                "message": msg,
                "redirect": "/dashboard",
                "current_date": state.current_date,
                "matches_played": matches_played,
                "capped": capped,
            }
        )
    flash(msg)
    return redirect("/dashboard")


@bp.route("/match_history")
def match_history():
    state = safe_state()
    player_team = safe_player_team()

    player_matches = (
        Match.query.filter(
            (Match.home_team_id == player_team.id)
            | (Match.away_team_id == player_team.id)
        )
        .filter(Match.played == True)
        .order_by(Match.week.desc())
        .limit(30)
        .all()
    )

    return render_template(
        "match_history.html", state=state, player_team=player_team, matches=player_matches
    )


@bp.route("/season_end", methods=["POST"])
def season_end():
    state = safe_state()
    if not state.season_over:
        flash("الموسم لم ينتهِ بعد!")
        return redirect("/dashboard")

    process_promotion_relegation(state)
    from services import start_new_season

    new_fixtures = start_new_season(state)
    state.season_over = False
    state.current_day = 1
    state.current_week = 1
    state.season_number = (state.season_number or 1) + 1

    try:
        db.session.commit()
        invalidate_league_cache()
        flash(
            f"انتهى الموسم {state.season_number - 1}! "
            f"بدأ الموسم الجديد بنجاح ({new_fixtures} مباراة مجدولة)"
        )
    except Exception:
        db.session.rollback()
        flash("خطأ في إنهاء الموسم!")

    return redirect("/dashboard")


@bp.route("/world", methods=["GET", "POST"])
def world_overview():
    """World simulation overview + محاكاة العالم button (Feature C.6)."""
    from sim_levels import simulate_world_week

    state = safe_state()
    player_team = safe_player_team()
    result = None
    if request.method == "POST":
        result = simulate_world_week(state)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        flash(f"تمت محاكاة العالم: {result.get('played', 0)} مباراة")
        return redirect("/world")
    leagues = League.query.order_by(League.tier).all()
    leaders = []
    try:
        from services import get_league_table
        for lg in leagues[:8]:
            rows = get_league_table(lg.id)
            leaders.append({"league": lg, "top": rows[:3]})
    except Exception:
        pass
    return render_template("world.html", state=state, player_team=player_team,
                           leaders=leaders, result=result)