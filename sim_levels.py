"""Tiered simulation levels + world simulation (deeper_sim Feature C).

Contract: simulate(match, home_profile, away_profile, rng) -> MatchResult.
All engines route through finalize_match_result (single funnel). XIs always
come from rules_engine.auto_pick_xi for AI teams and the validated user XI
(with auto_pick fallback + inbox warning) so bulk sim obeys every rule.

Routing:
- any match involving the player's club -> L1 (live/headless minute engine)
- both clubs from the user's league -> L1 headless
- both clubs from the user's tier (other leagues, cups) -> L2
- anything involving higher/lower tiers -> L3
"""
import random

from config import CUP_MATCH_WEEK, ENGINE_BADGES


def _seed_for(match, state=None):
    try:
        season = int(getattr(state, "season_number", 1) or 1)
    except Exception:
        season = 1
    try:
        day = int(getattr(state, "current_day", 1) or 1)
    except Exception:
        day = 1
    try:
        mid = int(getattr(match, "id", 0) or 0)
    except Exception:
        mid = 0
    return mid * 100000 + season * 100 + day


def finalize_match_result(match, result, state=None):
    """Single funnel: score/scorers/ratings, table, manager record, cups.

    ``result`` keys: home_score, away_score, home_events, away_events,
    possession, shots..., xg, level. Safe to call once per match; the
    played flag makes it idempotent via the engines' guards.
    """
    from simulation import update_league_table, update_manager_record
    from tactics import note_match_played
    try:
        match.home_score = int(result.get("home_score", 0) or 0)
        match.away_score = int(result.get("away_score", 0) or 0)
        match.result = f"{match.home_score}-{match.away_score}"
        match.played = True
        match.home_events = result.get("home_events", [])
        match.away_events = result.get("away_events", [])
        for k in ("home_possession", "away_possession", "home_shots", "away_shots",
                  "home_shots_on_target", "away_shots_on_target", "home_corners",
                  "away_corners", "home_fouls", "away_fouls", "home_xg", "away_xg"):
            if k in result:
                setattr(match, k, result[k])
        if result.get("level"):
            try:
                match.engine_level = int(result["level"])
            except Exception:
                pass
        if (match.week or 0) != CUP_MATCH_WEEK:
            update_league_table(match.home_team, match.away_team,
                                match.home_score, match.away_score)
        if state is not None:
            update_manager_record(match, state)
        for t in (match.home_team, match.away_team):
            try:
                if t is not None:
                    note_match_played(t, level=int(result.get("level", 2) or 2))
            except Exception:
                pass
        try:
            from cup_services import after_match_played
            if state is not None:
                after_match_played(match, state)
        except Exception:
            pass
    except Exception:
        pass
    return match


def _seeded_poisson(lam, rng):
    """Deterministic Poisson sample from a ``random.Random`` instance.

    Every engine accepts a seeded RNG so results are reproducible in tests;
    the global ``numpy`` RNG is deliberately avoided here because it cannot
    be seeded per-match without touching global state.
    """
    import math
    lam = max(0.01, float(lam))
    if lam > 8:
        # Normal approximation for large lambda (still rng-local).
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    threshold = math.exp(-lam)
    k, prob = 0, 1.0
    while prob > threshold:
        k += 1
        prob *= rng.random()
    return k - 1


def engine_badge(match):
    lvl = getattr(match, "engine_level", None)
    return ENGINE_BADGES.get(lvl, "")


def get_sim_level(match, user_league=None, user_tier=None):
    """Router (levels.py)."""
    try:
        home, away = match.home_team, match.away_team
        if (home is not None and getattr(home, "is_player_team", False)) or \
           (away is not None and getattr(away, "is_player_team", False)):
            return 1
        if user_league is not None and getattr(home, "league_id", None) == user_league \
                and getattr(away, "league_id", None) == user_league:
            return 1
        ht = getattr(getattr(home, "league", None), "tier", None)
        at = getattr(getattr(away, "league", None), "tier", None)
        if user_tier is not None and ht == user_tier and at == user_tier:
            return 2
        if ht is not None and at is not None and ht == at:
            return 2
        return 3
    except Exception:
        return 2


def _validated_xi(team, competition_type="league", rng=None):
    """Stored user XI if valid, else auto_pick fallback (caller warns inbox)."""
    from rules_engine import auto_pick_xi, validate_starting_xi
    try:
        ok, _ = validate_starting_xi(team, competition_type)
        if ok:
            starters = [p for p in (team.players or []) if getattr(p, "is_starting", False)]
            bench = [p for p in (team.players or []) if not getattr(p, "is_starting", False)][:9]
            return {"starters": starters, "bench": bench, "fallback": False}
    except Exception:
        pass
    pick = auto_pick_xi(team, competition_type, rng)
    pick["fallback"] = True
    return pick


def engine_l1(match, home_profile, away_profile, rng=None):
    """Full minute-by-minute: delegates to the live headless engine."""
    rng = rng or random
    from live_match import run_auto_match
    ok = run_auto_match(match)
    try:
        match.engine_level = 1
    except Exception:
        pass
    return {"ok": bool(ok), "level": 1, "home_score": match.home_score,
            "away_score": match.away_score}


def engine_l2(match, home_profile, away_profile, rng=None):
    """Medium: possession-weighted aggregate, 6-12 key moments."""
    rng = rng or random.Random(_seed_for(match))
    hp = home_profile or {}
    ap = away_profile or {}
    h_xg = max(0.15, min(4.5, (float(hp.get("attack_eff", 60)) / max(1.0, float(ap.get("defense_eff", 60)))) * 1.35
                           * float(hp.get("chance_quality", 1.0))))
    a_xg = max(0.15, min(4.5, (float(ap.get("attack_eff", 60)) / max(1.0, float(hp.get("defense_eff", 60)))) * 1.15
                           * float(ap.get("chance_quality", 1.0))))
    hs = _seeded_poisson(h_xg, rng)
    aws = _seeded_poisson(a_xg, rng)
    hs, aws = max(0, min(7, hs)), max(0, min(7, aws))
    possession = int(max(30, min(70, float(hp.get("possession_base", 50)))))
    return {"ok": True, "level": 2, "home_score": hs, "away_score": aws,
            "home_events": [], "away_events": [],
            "home_possession": possession, "away_possession": 100 - possession,
            "home_shots": 5 + hs * 2, "away_shots": 5 + aws * 2,
            "home_xg": round(h_xg, 2), "away_xg": round(a_xg, 2)}


def engine_l3(match, home_profile, away_profile, rng=None):
    """Superficial: strength-differential Poisson. Respects big gaps."""
    rng = rng or random.Random(_seed_for(match))
    hp = home_profile or {}
    ap = away_profile or {}
    h_lam = max(0.15, min(4.5, 1.45 * float(hp.get("attack_eff", 60)) / max(1.0, float(ap.get("defense_eff", 60)))))
    a_lam = max(0.15, min(4.5, 1.10 * float(ap.get("attack_eff", 60)) / max(1.0, float(hp.get("defense_eff", 60)))))
    hs = _seeded_poisson(h_lam, rng)
    aws = _seeded_poisson(a_lam, rng)
    return {"ok": True, "level": 3, "home_score": max(0, min(7, hs)),
            "away_score": max(0, min(7, aws)), "home_events": [], "away_events": []}


def _player_week_pending(state) -> bool:
    """True when the manager still has an unplayed fixture this week."""
    try:
        from models import Match
        week = int(getattr(state, "current_week", 1) or 1)
        pending = Match.query.filter_by(week=week, played=False).all()
        return any(_involves_player(m) for m in pending)
    except Exception:
        return False


def _live_active() -> bool:
    try:
        from models import Match
        return Match.query.filter_by(live_active=True).count() > 0
    except Exception:
        return False


def _world_rng(state, salt: int = 0):
    try:
        season = int(getattr(state, "season_number", 1) or 1)
        week = int(getattr(state, "current_week", 1) or 1)
    except Exception:
        season, week = 1, 1
    return random.Random((season * 100000 + week * 1000 + salt) * 7919 + 17)


def _assign_goal_scorers(match, rng) -> list:
    """Credit goals to random starters so world sim moves the scoring charts."""
    names: list = []
    try:
        for side, goals in (("home", int(match.home_score or 0)),
                            ("away", int(match.away_score or 0))):
            team = match.home_team if side == "home" else match.away_team
            squad = list(getattr(team, "players", []) or [])
            if not squad or goals <= 0:
                continue
            attackers = [p for p in squad if (p.position or "") in ("FWD", "MID")]
            pool = attackers or squad
            for _ in range(min(goals, 7)):
                scorer = rng.choice(pool)
                try:
                    scorer.goals = int(scorer.goals or 0) + 1
                except Exception:
                    pass
                try:
                    names.append(scorer.name)
                except Exception:
                    pass
            for p in squad:
                try:
                    if p.is_starting:
                        p.matches_played = int(p.matches_played or 0) + 1
                except Exception:
                    pass
    except Exception:
        pass
    return names


def _maybe_ai_transfers(state, rng) -> list:
    """AI-to-AI transfers: direct moves between AI clubs + market ripple."""
    from config import MARKET_VALUE_DECAY_RATE, WORLD_SIM_TRANSFER_CHANCE
    from models import Player, Team, db
    out: list = []
    try:
        week = int(getattr(state, "current_week", 1) or 1)
    except Exception:
        week = 1
    roll = rng.random()
    forced = (week % 4 == 0)
    if roll > WORLD_SIM_TRANSFER_CHANCE and not forced:
        return out
    try:
        ai_teams = Team.query.filter_by(is_player_team=False).all()
        if len(ai_teams) < 2:
            return out
        listed = Player.query.filter_by(transfer_listed=True).all()
        clean = []
        for pl in listed:
            if pl.team_id is None:
                continue
            owner = db.session.get(Team, pl.team_id)
            if owner is not None and not owner.is_player_team:
                clean.append(pl)
        candidate = rng.choice(clean) if clean else None
        if candidate is None:
            pool = Player.query.filter(Player.team_id.isnot(None)).all()
            pool = [p for p in pool if not p.is_starting]
            ai_ids = {t.id for t in ai_teams}
            pool = [p for p in pool if p.team_id in ai_ids]
            if not pool:
                return out
            candidate = rng.choice(pool)
        seller = db.session.get(Team, candidate.team_id)
        buyers = [t for t in ai_teams if t.id != (seller.id if seller else -1)]
        buyers = [t for t in buyers
                  if (t.budget or 0) >= int((candidate.market_value or 100000) * 0.9)]
        if not buyers:
            return out
        buyer = rng.choice(buyers)
        amount = int((candidate.market_value or 100000) * rng.uniform(0.9, 1.2))
        try:
            buyer.budget = int((buyer.budget or 0) - amount)
            if seller is not None:
                seller.budget = int((seller.budget or 0) + amount)
        except Exception:
            pass
        try:
            candidate.team_id = buyer.id
            candidate.transfer_listed = False
            candidate.transfer_requested = False
            candidate.market_value = int((candidate.market_value or 0) * 1.05)
        except Exception:
            pass
        try:
            others = Player.query.filter_by(team_id=buyer.id).all()
            for p in rng.sample(others, min(3, len(others))):
                p.market_value = int((p.market_value or 0)
                                     * (1.0 + MARKET_VALUE_DECAY_RATE))
        except Exception:
            pass
        out.append({"player": candidate.name,
                    "from": seller.name if seller else "?",
                    "to": buyer.name,
                    "amount": amount})
    except Exception:
        pass
    return out


def _board_drift(state) -> int:
    """Nudge board confidence by actual vs reputation-expected rank."""
    from config import WORLD_SIM_BOARD_DRIFT_MAX, WORLD_SIM_BOARD_DRIFT_MIN
    try:
        from models import Team
        from services import get_league_table
        pt = Team.query.filter_by(is_player_team=True).first()
        if pt is None or pt.league_id is None:
            return 0
        rows = get_league_table(pt.league_id)
        actual = next((i for i, r in enumerate(rows, start=1) if r.id == pt.id), None)
        if actual is None:
            return 0
        league_teams = Team.query.filter_by(league_id=pt.league_id).all()
        ordered = sorted(league_teams, key=lambda t: (t.reputation or 0), reverse=True)
        expected = next((i for i, t in enumerate(ordered, start=1) if t.id == pt.id), actual)
        gap = int(expected) - int(actual)
        if gap > 0:
            drift = min(WORLD_SIM_BOARD_DRIFT_MAX,
                        max(WORLD_SIM_BOARD_DRIFT_MIN, min(3, 1 + gap // 2)))
        elif gap < 0:
            drift = -min(WORLD_SIM_BOARD_DRIFT_MAX,
                         max(WORLD_SIM_BOARD_DRIFT_MIN, min(3, 1 + (-gap) // 2)))
        else:
            drift = 0
        if drift:
            state.board_confidence = max(0, min(100,
                                               int(state.board_confidence or 60) + drift))
        return int(drift)
    except Exception:
        return 0


def simulate_world_week(state, rng=None):
    """Advance EVERY competition for the current week (L1/L2/L3 by routing)."""
    import json
    from datetime import datetime, timedelta
    from models import Match, db
    from tactics import derive_team_profile
    played = 0
    levels = {1: 0, 2: 0, 3: 0}
    base_result: dict = {"played": 0, "levels": levels}
    try:
        if _live_active():
            base_result.update({"skipped": True,
                                "reason": "لا يمكن المحاكاة أثناء مباراة حية!"})
            return base_result
        if _player_week_pending(state):
            base_result.update({"skipped": True,
                                "reason": "حل أسبوعك أولاً — العب مباراتك قبل محاكاة العالم!"})
            return base_result
        last = int(getattr(state, "world_sim_last_week", 0) or 0)
        cur_week = int(getattr(state, "current_week", 1) or 1)
        if cur_week <= last:
            base_result.update({"skipped": True,
                                "reason": "تمت محاكاة هذا الأسبوع — تقدم بأسبوعك أولاً!"})
            return base_result
    except Exception:
        pass
    played_matches: list = []
    try:
        user_league = getattr(state, "user_league_id", None)
        user_tier = None
        try:
            from models import Team
            pt = Team.query.filter_by(is_player_team=True).first()
            user_tier = getattr(getattr(pt, "league", None), "tier", None)
        except Exception:
            pass
        matches = Match.query.filter_by(week=getattr(state, "current_week", 1),
                                        played=False).all()
        for m in matches:
            lvl = get_sim_level(m, user_league, user_tier)
            try:
                m.engine_level = lvl
            except Exception:
                pass
            if lvl == 1 and _involves_player(m):
                continue
            if lvl == 1:
                from live_match import run_auto_match as _auto
                if _auto(m):
                    played += 1
                    levels[1] += 1
                    played_matches.append(m)
                continue
            try:
                hp = derive_team_profile(m.home_team)
                ap = derive_team_profile(m.away_team)
            except Exception:
                hp, ap = {}, {}
            mrng = random.Random(_seed_for(m, state))
            res = engine_l2(m, hp, ap, mrng) if lvl == 2 else engine_l3(m, hp, ap, mrng)
            m.home_score, m.away_score = res["home_score"], res["away_score"]
            m.result = f"{m.home_score}-{m.away_score}"
            m.played = True
            if m.home_possession is not None and res.get("home_possession") is not None:
                for k, v in res.items():
                    if k in ("home_possession", "away_possession", "home_shots",
                             "away_shots", "home_xg", "away_xg"):
                        try:
                            setattr(m, k, v)
                        except Exception:
                            pass
            if (m.week or 0) != CUP_MATCH_WEEK:
                try:
                    from simulation import update_league_table
                    update_league_table(m.home_team, m.away_team, m.home_score, m.away_score)
                except Exception:
                    pass
            played += 1
            levels[lvl] += 1
            played_matches.append(m)
            if played % 200 == 0:
                try:
                    db.session.commit()
                except Exception:
                    pass
        try:
            db.session.commit()
        except Exception:
            pass
    except Exception:
        pass
    try:
        rng = rng or _world_rng(state)
    except Exception:
        rng = random
    from config import WORLD_SIM_SACK_CHANCE, WORLD_SIM_UPSET_REP_GAP
    week_scorers: list = []
    for m in played_matches:
        try:
            week_scorers.extend(_assign_goal_scorers(m, rng))
        except Exception:
            pass
    from collections import Counter
    scorer_counts = Counter(week_scorers)
    top_scorers_this_week = [{"name": n, "goals": c}
                             for n, c in scorer_counts.most_common(5)]
    if not top_scorers_this_week:
        try:
            from models import Player as _P
            leaders = (_P.query.filter(_P.team_id.isnot(None))
                       .order_by(_P.goals.desc()).limit(5).all())
            top_scorers_this_week = [{"name": p.name, "goals": int(p.goals or 0)}
                                     for p in leaders]
        except Exception:
            pass
    upsets: list = []
    for m in played_matches:
        try:
            home, away = m.home_team, m.away_team
            if home is None or away is None:
                continue
            hs, aws = int(m.home_score or 0), int(m.away_score or 0)
            if hs == aws:
                continue
            winner, loser = (home, away) if hs > aws else (away, home)
            gap = int(loser.reputation or 0) - int(winner.reputation or 0)
            if gap >= WORLD_SIM_UPSET_REP_GAP:
                upsets.append({"winner": winner.name, "loser": loser.name,
                               "score": f"{hs}-{aws}", "gap": gap})
        except Exception:
            continue
    upsets.sort(key=lambda d: d.get("gap", 0), reverse=True)
    managers_sacked: list = []
    try:
        if rng.random() < WORLD_SIM_SACK_CHANCE:
            from models import League as _L, Team as _T
            leagues = _L.query.all()
            if leagues:
                lg = rng.choice(leagues)
                teams = _T.query.filter_by(league_id=lg.id).all()
                if teams:
                    worst = min(teams, key=lambda t: (t.points or 0,
                                                     (t.goals_for or 0) - (t.goals_against or 0)))
                    if not worst.is_player_team:
                        try:
                            worst.morale = 50
                        except Exception:
                            pass
                        managers_sacked.append({"team": worst.name,
                                                "reason": "سلسلة نتائج مخيبة"})
    except Exception:
        pass
    ai_transfers: list = []
    try:
        ai_transfers = _maybe_ai_transfers(state, rng)
    except Exception:
        pass
    try:
        if rng.random() < 0.25:
            from services import auto_transfer_list_offers
            auto_transfer_list_offers(state)
    except Exception:
        pass
    try:
        if rng.random() < 0.20:
            from simulation import generate_youth_players
            generate_youth_players()
    except Exception:
        pass
    continental_movers: list = []
    try:
        from models import League as _L
        from services import get_league_table
        for lg in _L.query.order_by(_L.tier).limit(8).all():
            rows = get_league_table(lg.id)
            continental_movers.append({"league": lg.name,
                                      "top": [{"name": r.name, "points": r.points}
                                              for r in rows[:4]]})
    except Exception:
        pass
    drift = 0
    try:
        drift = int(_board_drift(state) or 0)
    except Exception:
        pass
    try:
        base = datetime.strptime(state.current_date, "%Y-%m-%d").date() \
            if state.current_date else datetime.today().date()
        state.current_date = (base + timedelta(days=7)).isoformat()
    except Exception:
        pass
    try:
        state.current_day = int(state.current_day or 1) + 7
        state.current_week = int(state.current_week or 1) + 1
    except Exception:
        pass
    try:
        state.world_sim_last_week = int(state.current_week or 1)
    except Exception:
        pass
    summary = {"played": played, "levels": levels,
               "top_scorers_this_week": top_scorers_this_week,
               "upsets": upsets, "managers_sacked": managers_sacked,
               "ai_transfers": ai_transfers,
               "continental_movers": continental_movers,
               "board_drift": drift}
    try:
        state.world_last_summary = json.dumps(summary, ensure_ascii=False)
    except Exception:
        pass
    try:
        from services import emit_world_week_messages
        emit_world_week_messages(state, summary)
    except Exception:
        pass
    try:
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
    return summary


def _involves_player(match):
    try:
        return bool((match.home_team is not None and match.home_team.is_player_team)
                    or (match.away_team is not None and match.away_team.is_player_team))
    except Exception:
        return False
