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
    Message,
    Player,
    Team,
    TransferOffer,
)
from simulation import (
    get_current_match,
    get_match_report,
    get_pending_actions,
    get_recent_results,
    get_top_assists,
    get_top_scorers,
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
from narrative import (
    EVENT_CATEGORY_META,
    PRIORITY_CRITICAL,
    PRIORITY_META,
    build_calendar_events,
    compute_form,
    critical_notifications,
    notification_priority,
    pre_match_storylines,
    priority_meta,
    season_overview,
    season_storylines,
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

    # --- Dashboard widgets (all best-effort: never break the hub) ---
    last_match = None
    form_guide = []
    top_scorers = []
    youth_prospects = []
    injured_players = []
    transfer_counts = {"listed": 0, "requested": 0, "loans": 0, "free_agents": 0}
    finance_summary = {"spend": 0, "income": 0, "wage_bill": 0}
    try:
        last_match = (
            Match.query.filter(
                ((Match.home_team_id == player_team.id) | (Match.away_team_id == player_team.id)),
                Match.played == True,
            )
            .order_by(Match.week.desc())
            .first()
        )
    except Exception:
        last_match = None
    try:
        played = (
            Match.query.filter(
                ((Match.home_team_id == player_team.id) | (Match.away_team_id == player_team.id)),
                Match.played == True,
            )
            .order_by(Match.week.desc())
            .limit(5)
            .all()
        )
        for m in played:
            if m.home_team_id == player_team.id:
                form_guide.append("W" if (m.home_score or 0) > (m.away_score or 0) else ("D" if (m.home_score or 0) == (m.away_score or 0) else "L"))
            else:
                form_guide.append("W" if (m.away_score or 0) > (m.home_score or 0) else ("D" if (m.away_score or 0) == (m.home_score or 0) else "L"))
        form_guide = form_guide[::-1]
    except Exception:
        form_guide = []
    try:
        from simulation import get_top_scorers
        top_scorers = (get_top_scorers(league_id) or [])[:5]
    except Exception:
        top_scorers = []
    try:
        youth_prospects = sorted(
            [p for p in players if (p.age or 99) <= 21],
            key=lambda p: (p.potential or 0),
            reverse=True,
        )[:4]
    except Exception:
        youth_prospects = []
    try:
        injured_players = [p for p in players if (p.injury_days or 0) > 0][:5]
    except Exception:
        injured_players = []
    try:
        transfer_counts["listed"] = sum(1 for p in players if p.transfer_listed)
        transfer_counts["requested"] = sum(1 for p in players if p.transfer_requested)
        transfer_counts["loans"] = sum(1 for p in players if p.loan_to_team_id or p.loan_from_team_id)
        transfer_counts["free_agents"] = Player.query.filter_by(team_id=None).count()
    except Exception:
        pass
    try:
        finance_summary = {
            "spend": player_team.season_spend or 0,
            "income": player_team.season_income or 0,
            "wage_bill": sum(p.wage or 0 for p in players),
        }
    except Exception:
        pass

    # --- Narrative layer (fix.txt): storylines + focus alerts ---
    try:
        form_recent = list(form_guide[::-1])  # most-recent-first for streaks
        storylines = season_storylines(
            state, player_team, team_rank=team_rank,
            league_size=len(league_table), squad=players, form=form_recent,
        )
    except Exception:
        storylines = []
    top_story = storylines[0] if storylines else None
    try:
        alerts = critical_notifications(state, player_team)
    except Exception:
        alerts = []

    return render_template(
        "dashboard.html",
        state=state,
        player_team=player_team,
        players=players,
        team_rank=team_rank,
        current_match=current_match,
        next_match=current_match,
        last_match=last_match,
        form_guide=form_guide,
        top_scorers=top_scorers,
        youth_prospects=youth_prospects,
        injured_players=injured_players,
        transfer_counts=transfer_counts,
        finance_summary=finance_summary,
        league_table=league_table,
        recent_results=recent_results,
        pending_actions=pending_actions,
        board_objectives=board_objectives,
        cup_statuses=cup_statuses,
        storylines=storylines,
        top_story=top_story,
        critical_alerts=alerts,
        priority_meta=priority_meta,
    )


@bp.route("/standings")
def standings():
    redir = _setup_redirect()
    if redir:
        return redir
    state = safe_state()
    player_team = safe_player_team()
    requested_league = request.args.get("league_id", type=int)

    # Merged standings + statistics page: list of countries, each with its
    # league divisions, showing each division's table, top scorers and top
    # assists.
    leagues = League.query.order_by(League.country, League.tier).all()
    countries = {}
    for lg in leagues:
        countries.setdefault(lg.country or "غير محدد", []).append(lg)

    league_data = {}
    for lg in leagues:
        table = get_league_table(lg.id)
        teams_dict = {
            t.id: t.name
            for t in Team.query.filter_by(league_id=lg.id).all()
        }
        league_data[lg.id] = {
            "table": table,
            "scorers": get_top_scorers(lg.id),
            "assists": get_top_assists(lg.id),
            "teams_dict": teams_dict,
        }

    selected_league = (
        db.session.get(League, requested_league) if requested_league else None
    )
    if requested_league and selected_league is None:
        flash("الدوري المطلوب غير موجود! يتم عرض دوريك الحالي.")
        selected_league = (
            db.session.get(League, player_team.league_id) if player_team else None
        )
    if selected_league is None and player_team:
        selected_league = db.session.get(League, player_team.league_id)

    team_rank = (
        get_team_rank(selected_league.id, player_team)
        if selected_league and player_team
        else None
    )
    try:
        from config import WORLD_SIM_CONTINENTAL_SPOTS
        continental_spots = int(WORLD_SIM_CONTINENTAL_SPOTS)
    except Exception:
        continental_spots = 4

    return render_template(
        "standings.html",
        state=state,
        player_team=player_team,
        countries=countries,
        league_data=league_data,
        leagues=leagues,
        selected_league=selected_league,
        team_rank=team_rank,
        continental_spots=continental_spots,
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
        from config import CALENDAR_DOT_COLORS, CUP_BADGE_COLORS
        league_color = CUP_BADGE_COLORS.get("league", "#3498db")
        played_dot = CALENDAR_DOT_COLORS.get("played", "#6b7280")
    except Exception:
        CUP_BADGE_COLORS = {}
        CALENDAR_DOT_COLORS = {}
        league_color = "#3498db"
        played_dot = "#6b7280"

    grouped = {}
    for m in player_matches:
        home = m.home_team
        away = m.away_team
        comp = fixture_competition(m) if fixture_competition else None
        comp_type = (comp or {}).get("comp_type") or (m.comp_type or "league")
        brand_color = (comp or {}).get("color") or CUP_BADGE_COLORS.get(comp_type, league_color)
        dot_color = (comp or {}).get("dot_color") or CALENDAR_DOT_COLORS.get(
            comp_type, league_color
        )
        if m.played:
            dot_color = played_dot
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
            "comp_type": comp_type,
            "comp_round": (comp or {}).get("round") or "",
            "comp_color": brand_color,
            "dot_color": dot_color,
            "matchday": (comp or {}).get("matchday"),
        }
        key = m.date or f"week-{m.week}"
        grouped.setdefault(key, []).append(entry)

    # One entry per date (the calendar JSON contract): the first unplayed
    # fixture wins the slot and every same-date fixture is listed in
    # "matches" (used by the dot renderer + day-details panel).
    for key, entries in grouped.items():
        entries.sort(key=lambda e: (bool(e["played"]), e["id"]))
        primary = dict(entries[0])
        primary["matches"] = entries
        primary["more"] = entries[1:]
        player_matches_json[key] = primary

    # The in-page simulation sweep refreshes the dots/details without a full
    # navigation: return the same JSON contract plus the new game date.
    if request.args.get("format") == "json":
        return jsonify(
            {
                "ok": True,
                "current_date": state.current_date,
                "match_data": player_matches_json,
            }
        )

    # --- Calendar redesigned as a Season Timeline (fix.txt) ---
    # Full narrative event feed (matches + transfers + injuries + contracts
    # + board + training + milestones) plus the season-overview snapshot and
    # the top storyline. All best-effort: a narrative failure leaves the
    # calendar fully functional with just the fixtures.
    league_id = player_team.league_id
    team_rank = get_team_rank(league_id, player_team)
    try:
        played_matches = (
            Match.query.filter(
                (Match.home_team_id == player_team.id)
                | (Match.away_team_id == player_team.id),
                Match.played.is_(True),
            )
            .order_by(Match.week.desc())
            .limit(5)
            .all()
        )
        form = compute_form(played_matches, player_team.id, 5)
    except Exception:
        form = []

    try:
        calendar_events = build_calendar_events(
            state, player_team, player_matches, competition_for=fixture_competition
        )
    except Exception:
        calendar_events = []

    try:
        overview = season_overview(
            state, player_team, team_rank, form, calendar_events
        )
    except Exception:
        overview = {}

    try:
        squad = Player.query.filter_by(team_id=player_team.id).all()
        storylines = season_storylines(
            state, player_team, team_rank=team_rank,
            league_size=len(get_league_table(league_id)), squad=squad, form=form,
        )
    except Exception:
        storylines = []
    top_story = storylines[0] if storylines else None

    return render_template(
        "calendar.html",
        state=state,
        player_team=player_team,
        player_matches_json=json.dumps(player_matches_json),
        calendar_events_json=json.dumps(calendar_events),
        event_categories=EVENT_CATEGORY_META,
        season_overview=overview,
        storylines=storylines,
        top_story=top_story,
        team_rank=team_rank,
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
    player_team = safe_player_team()

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

    try:
        match_storylines = pre_match_storylines(match, state, player_team) if match else []
    except Exception:
        match_storylines = []

    return render_template(
        "match.html",
        match=match,
        state=state,
        scouting=scouting,
        match_storylines=match_storylines,
    )


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
        return jsonify(
            {
                "ok": True,
                "message": msg,
                "redirect": "/calendar",
                "current_date": state.current_date,
                "matches_played": matches_played,
                "capped": capped,
            }
        )
    flash(msg)
    return redirect("/calendar")


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


# --------------------------------------------------------------------------
# Management hubs (inhanceUI): board / tactics / transfers /
# club / settings + global search API. Thin glue only — data comes from
# models/services, exactly like every other route in this blueprint.
# --------------------------------------------------------------------------
@bp.route("/board")
def board_center():
    from models import BoardObjective
    redir = _setup_redirect()
    if redir:
        return redir
    state = safe_state()
    player_team = safe_player_team()
    objectives = BoardObjective.query.filter_by(
        team_id=player_team.id, season=state.season_number or 1
    ).all()
    players = Player.query.filter_by(team_id=player_team.id).all()
    try:
        avg_age = sum(p.age or 0 for p in players) / max(1, len(players))
    except Exception:
        avg_age = 0
    rank = get_team_rank(player_team.league_id, player_team)
    return render_template("board.html", state=state, player_team=player_team,
                           players=players, objectives=objectives,
                           avg_age=avg_age, team_rank=rank)


@bp.route("/tactics")
def tactics_center():
    from tactics import get_team_tactics
    from models import TacticPreset, Player
    from config import PRESET_FORMATIONS
    import json
    redir = _setup_redirect()
    if redir:
        return redir
    state = safe_state()
    player_team = safe_player_team()
    try:
        tactics_data = get_team_tactics(player_team)
    except Exception:
        tactics_data = {"mentality": 3}
    presets = TacticPreset.query.filter_by(team_id=player_team.id).all()
    players = Player.query.filter_by(team_id=player_team.id).all()
    players_data = []
    for p in players:
        players_data.append({
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
            "role": getattr(p, "role", "") or "",
            "role_suitability": getattr(p, "role_suitability", 0) or 0,
            "role_fit": getattr(p, "role_fit", 0) or 0,
        })
    formation_data = player_team.formation_data
    if not isinstance(formation_data, dict):
        formation_data = {}
    return render_template("tactics.html", state=state, player_team=player_team,
                           tactics_data=tactics_data, presets=presets, players=players_data,
                           formation_data=json.dumps(formation_data),
                           formations=PRESET_FORMATIONS)


@bp.route("/transfers")
def transfers_center():
    from models import TransferOffer
    redir = _setup_redirect()
    if redir:
        return redir
    state = safe_state()
    player_team = safe_player_team()
    try:
        incoming = TransferOffer.query.filter_by(to_team_id=player_team.id).order_by(TransferOffer.id.desc()).limit(20).all()
    except Exception:
        incoming = []
    try:
        outgoing = TransferOffer.query.filter_by(from_team_id=player_team.id).order_by(TransferOffer.id.desc()).limit(20).all()
    except Exception:
        outgoing = []
    squad = Player.query.filter_by(team_id=player_team.id).all()
    listed = [p for p in squad if p.transfer_listed]
    requested = [p for p in squad if p.transfer_requested]
    loans = [p for p in squad if p.loan_to_team_id or p.loan_from_team_id]
    return render_template("transfers.html", state=state, player_team=player_team,
                           incoming=incoming, outgoing=outgoing, listed=listed,
                           requested=requested, loans=loans)


@bp.route("/club")
def club_center():
    redir = _setup_redirect()
    if redir:
        return redir
    state = safe_state()
    player_team = safe_player_team()
    players = Player.query.filter_by(team_id=player_team.id).all()
    staff = []
    try:
        from models import Staff
        staff = Staff.query.filter_by(team_id=player_team.id).all()
    except Exception:
        pass
    rank = get_team_rank(player_team.league_id, player_team)
    table = get_league_table(player_team.league_id)
    return render_template("club.html", state=state, player_team=player_team,
                           players=players, staff=staff, team_rank=rank,
                           league_table=table)


@bp.route("/settings")
def settings_center():
    redir = _setup_redirect()
    if redir:
        return redir
    state = safe_state()
    player_team = safe_player_team()
    difficulties = []
    try:
        from config import AI_DIFFICULTIES
        difficulties = list(AI_DIFFICULTIES)
    except Exception:
        pass
    return render_template("settings.html", state=state, player_team=player_team,
                           difficulties=difficulties)


@bp.route("/api/search")
def global_search():
    redir = _setup_redirect()
    if redir:
        return jsonify({"error": "no active save"}), 400
    player_team = safe_player_team()
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"players": [], "teams": [], "staff": []})
    like = f"%{q}%"
    out = {"players": [], "teams": [], "staff": []}
    try:
        rows = Player.query.filter(Player.name.ilike(like)).limit(8).all()
        out["players"] = [{"id": p.id, "name": p.name,
                           "pos": p.detailed_position or p.position,
                           "team": p.team.name if p.team else "—"} for p in rows]
    except Exception:
        pass
    try:
        rows = Team.query.filter(Team.name.ilike(like)).limit(8).all()
        out["teams"] = [{"id": t.id, "name": t.name,
                         "mine": bool(t.is_player_team)} for t in rows]
    except Exception:
        pass
    try:
        from models import Staff
        rows = Staff.query.filter(Staff.name.ilike(like)).limit(8).all()
        out["staff"] = [{"id": s.id, "name": s.name, "role": s.role} for s in rows]
    except Exception:
        pass
    return jsonify(out)