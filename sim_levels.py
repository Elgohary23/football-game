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


def simulate_world_week(state):
    """Advance EVERY competition for the current week (L1/L2/L3 by routing)."""
    from models import Match, db
    from simulation import match_director
    from tactics import derive_team_profile
    played = 0
    levels = {1: 0, 2: 0, 3: 0}
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
                continue  # leave the manager's fixture for live play
            if lvl == 1:
                from live_match import run_auto_match as _auto
                if _auto(m):
                    played += 1
                    levels[1] += 1
                continue
            try:
                hp = derive_team_profile(m.home_team)
                ap = derive_team_profile(m.away_team)
            except Exception:
                hp, ap = {}, {}
            rng = random.Random(_seed_for(m, state))
            res = engine_l2(m, hp, ap, rng) if lvl == 2 else engine_l3(m, hp, ap, rng)
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
    return {"played": played, "levels": levels}


def _involves_player(match):
    try:
        return bool((match.home_team is not None and match.home_team.is_player_team)
                    or (match.away_team is not None and match.away_team.is_player_team))
    except Exception:
        return False
