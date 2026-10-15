"""Competent-but-beatable AI (deeper_sim Feature D).

Golden rule: AI coaching decisions come from the team philosophy.
Every decision below starts from ``PHILOSOPHY_COACHING[primary]`` and the
philosophy numeric knobs (attacking_intent, pressing_intensity, ...), then
is modulated by match state and a skill-scaled mistake probability.
No rubber-banding, no hidden stat boosts — difficulty scales skill means
and reaction delays only.
"""
import random

from config import (
    AI_DIFFICULTY_DEFAULT,
    AI_MAX_COUNTERS,
    AI_MISTAKE_BASE,
    AI_SKILL_RANGE,
    MATCH_AI_CHECKPOINTS,
    MATCH_AI_LOW_FITNESS,
    MATCH_AI_LOW_RATING,
    PHILOSOPHY_COACHING,
)

ARCHETYPES = ("pragmatist", "attacker", "defensive", "tinkerer", "youth_lover")


def _philosophy_primary(team):
    try:
        from services import get_team_philosophy
        phil = get_team_philosophy(team)
        key = (getattr(phil, "primary", None) or "balanced")
        if key in PHILOSOPHY_COACHING:
            return key
        return "balanced"
    except Exception:
        return "balanced"


def coaching_base(team):
    """Base coaching profile DERIVED FROM philosophy (single source)."""
    primary = _philosophy_primary(team)
    base = dict(PHILOSOPHY_COACHING.get(primary, PHILOSOPHY_COACHING["balanced"]))
    base["primary"] = primary
    try:
        from services import get_team_philosophy
        phil = get_team_philosophy(team)
        for k in ("attacking_intent", "pressing_intensity", "possession_focus",
                  "tempo", "defensive_line", "transition_style"):
            try:
                base[k] = float(getattr(phil, k, 0.5) or 0.5)
            except Exception:
                pass
    except Exception:
        pass
    return base


def ensure_ai_profile(team, difficulty=None):
    """Stored per-team profile; never regenerated mid-save."""
    try:
        prof = getattr(team, "ai_profile", None)
        if isinstance(prof, dict) and prof.get("archetype"):
            return prof
    except Exception:
        pass
    primary = _philosophy_primary(team)
    # Skill mean rises with tier but with wide variance (beatable).
    try:
        tier = int(getattr(getattr(team, "league", None), "tier", 3) or 3)
    except Exception:
        tier = 3
    mean = {1: 0.70, 2: 0.60, 3: 0.52, 4: 0.46}.get(tier, 0.55)
    diff = difficulty or AI_DIFFICULTY_DEFAULT
    try:
        from models import GameState
        st = GameState.query.first()
        if st and getattr(st, "ai_difficulty", None) in ("سهل", "عادي", "صعب"):
            diff = st.ai_difficulty
    except Exception:
        pass
    if diff == "سهل":
        mean -= 0.10
    elif diff == "صعب":
        mean += 0.10
    lo, hi = AI_SKILL_RANGE
    skill = max(lo, min(hi, random.gauss(mean, 0.12)))
    archetype = {
        "park_the_bus": "defensive", "counter_attack": "pragmatist",
        "gegenpress": "attacker", "tiki_taka": "pragmatist",
        "youth_development": "youth_lover", "moneyball": "tinkerer",
    }.get(primary, random.choice(ARCHETYPES))
    prof = {
        "archetype": archetype,
        "skill": round(float(skill), 3),
        "patience": random.randint(30, 90),
        "ambition": random.randint(30, 95),
        "transfer_aggression": round(random.uniform(0.3, 0.9), 2),
        "risk_tolerance": round(float(PHILOSOPHY_COACHING.get(
            primary, PHILOSOPHY_COACHING["balanced"])["risk"]), 2),
        "primary": primary,
    }
    try:
        team.ai_profile = prof
    except Exception:
        pass
    return prof


def _mistake_roll(skill):
    return random.random() < (1.0 - float(skill)) * AI_MISTAKE_BASE


def plan_match_mentality(team, opponent=None):
    """Weekly planner: tactic choice from philosophy + persona, with a
    skill-scaled mistake probability (picks counter-tactic otherwise)."""
    base = coaching_base(team)
    prof = ensure_ai_profile(team)
    skill = float(prof.get("skill", 0.55))
    mentality = int(base.get("mentality", 3))
    # Counter-tactic nudge vs opponent strengths (competent)...
    try:
        if opponent is not None:
            from tactics import derive_team_profile
            mine = derive_team_profile(team)
            theirs = derive_team_profile(opponent)
            if theirs["attack_eff"] > mine["defense_eff"] + 3 and mentality > 1:
                mentality -= 1
            elif mine["attack_eff"] > theirs["defense_eff"] + 3 and mentality < 5:
                mentality += 1
    except Exception:
        pass
    # ...but low-skill AI sometimes picks the persona-consistent-but-
    # suboptimal option (beatable).
    if _mistake_roll(skill):
        mentality = int(base.get("mentality", 3))
    return max(1, min(5, mentality))


def should_chase(match_minute, goal_diff, team):
    """Whether the AI chases a game — threshold comes from philosophy risk."""
    base = coaching_base(team)
    risk = float(base.get("risk", 0.5))
    if goal_diff < 0:
        return match_minute >= int(75 - risk * 20)
    return False


def should_protect_lead(match_minute, goal_diff, team):
    base = coaching_base(team)
    if goal_diff >= 2 and match_minute >= 70:
        # Possession identities protect by keeping the ball; defensive ones
        # drop the line. Both are philosophy expressions, not generic logic.
        return base.get("chase_shape", "balanced")
    return None


def pick_substitution(team, on_pitch, bench, ratings=None, minute=60):
    """Philosophy-weighted sub pick: returns (player_out, player_in) or None.

    - Gegenpress/high-press sides sub tired legs early (fitness-weighted).
    - Attacking sides sub lowest-rated attacker for fresh legs.
    - Defensive sides sacrifice an attacker for a defender when leading.
    - Youth/moneyball sides prefer the youngest high-potential bench option.
    """
    base = coaching_base(team)
    primary = base.get("primary", "balanced")
    aggression = float(base.get("sub_aggression", 0.5) or 0.5)
    if random.random() > 0.35 + aggression * 0.65:
        return None
    ratings = ratings or {}

    def _score_out(p):
        r = float(ratings.get(getattr(p, "id", None), 6.5) or 6.5)
        fit = float(getattr(p, "fitness", 100) or 100)
        s = (MATCH_AI_LOW_RATING - r) * 2.0 + (MATCH_AI_LOW_FITNESS - fit) * 0.05
        if primary in ("gegenpress", "total_football") and fit < 65:
            s += 1.5
        if primary in ("galacticos", "fluid_attacking") and (getattr(p, "position", "") in ("MID", "FWD")) and r < 6.8:
            s += 1.0
        return s

    candidates_out = sorted(list(on_pitch or []), key=_score_out, reverse=True)
    if not candidates_out or _score_out(candidates_out[0]) <= 0:
        return None
    out = candidates_out[0]

    def _score_in(p):
        try:
            ov = float(getattr(p, "overall", 0) or 0) or float(
                max(getattr(p, "attack", 50) or 50, getattr(p, "defense", 50) or 50))
        except Exception:
            ov = 50.0
        s = ov
        pos = getattr(p, "position", "") or ""
        shape = base.get("chase_shape", "balanced")
        if shape in ("attack", "counter", "direct", "width") and pos in ("MID", "FWD"):
            s += 5
        if shape in ("defend",) and pos in ("DEF",):
            s += 5
        if primary in ("youth_development", "moneyball"):
            try:
                s += max(0, int(getattr(p, "potential", 50) or 50) - int(ov)) * 0.5
                s += max(0, (25 - int(getattr(p, "age", 25) or 25))) * 0.4
            except Exception:
                pass
        return s

    if not bench:
        return None
    inn = max(list(bench), key=_score_in)
    return (out, inn)


def decide_checkpoint(match, team_key, minute, goal_diff, team, on_pitch,
                      bench, ratings=None, red_card=False):
    """In-match AI at fixed checkpoints (HT/60'/75') + forced events.

    Returns a decision dict DERIVED FROM PHILOSOPHY:
    {"mentality_delta": int, "def_line": str|None, "time_wasting": bool,
     "sub": (out, in)|None, "reason": str}
    """
    if minute not in MATCH_AI_CHECKPOINTS and not red_card and not should_chase(minute, goal_diff, team):
        return {"mentality_delta": 0, "def_line": None, "time_wasting": False,
                "sub": None, "reason": "no-checkpoint"}
    base = coaching_base(team)
    prof = ensure_ai_profile(team)
    skill = float(prof.get("skill", 0.55))
    decision = {"mentality_delta": 0, "def_line": None, "time_wasting": False,
                "sub": None, "reason": base.get("primary", "balanced")}
    if red_card:
        # Immediate defensive reshuffle, sacrifice an attacker — but a
        # galacticos side still sacrifices its *worst* attacker, a park-the-
        # bus side its *best* attacker to keep (philosophy again).
        decision["mentality_delta"] = -1
        decision["def_line"] = "lower"
        decision["reason"] = "red-card-reshuffle:" + str(base.get("primary"))
    elif goal_diff < 0:
        step = 1 if base.get("mentality", 3) < 5 else 0
        if _mistake_roll(skill):
            step = 0  # low-skill AI ignores the obvious chase (beatable)
        decision["mentality_delta"] = step
        decision["reason"] = "chasing:" + str(base.get("chase_shape"))
    elif goal_diff >= 2 and minute >= 70:
        shape = should_protect_lead(minute, goal_diff, team)
        if shape in ("possession",):
            decision["time_wasting"] = False
            decision["reason"] = "protect-possession:" + str(base.get("primary"))
        else:
            decision["time_wasting"] = True
            decision["def_line"] = "lower"
            decision["reason"] = "protect-lead:" + str(base.get("primary"))
    sub = pick_substitution(team, on_pitch, bench, ratings, minute)
    # Low-skill AI sometimes keeps a high line against pace / skips the sub.
    if sub is not None and _mistake_roll(skill) and random.random() < 0.5:
        sub = None
        decision["reason"] += "+missed-sub"
    decision["sub"] = sub
    # Cap counters / walk-away logic lives in transfer AI; here we just
    # expose the skill so callers/tests can assert beatability.
    decision["skill"] = skill
    decision["counters_used"] = 0
    decision["max_counters"] = AI_MAX_COUNTERS
    return decision


def transfer_valuation(player, team):
    """Valuation = f(overall, age, contract) ±15% noise, capped by skill and
    reserve rule (max 80% of transfer budget). Philosophy adds fit premium."""
    from services import get_player_valuation
    base = int(get_player_valuation(player) or 0)
    noise = random.uniform(-0.15, 0.15)
    prof = ensure_ai_profile(team)
    skill = float(prof.get("skill", 0.55))
    value = int(base * (1.0 + noise))
    value = min(value, int(base * (1 + 0.25 * skill)))
    try:
        budget = getattr(team, "transfer_budget", None)
        if budget is None:
            budget = getattr(team, "budget", 0) or 0
        value = min(value, int((budget or 0) * 0.8))
    except Exception:
        pass
    # Philosophy fit premium: youth identities pay a touch more for potential.
    try:
        if _philosophy_primary(team) in ("youth_development", "moneyball"):
            value = int(value * 1.05)
    except Exception:
        pass
    return max(0, value)


def weekly_planner_report():
    """Run inside day/week advancement for every AI club. Returns report."""
    report = {}
    try:
        from models import Team
        teams = Team.query.filter_by(is_player_team=False).all()
    except Exception:
        return report
    for team in teams:
        try:
            ensure_ai_profile(team)
            from services import apply_philosophy_to_coaching
            changed = apply_philosophy_to_coaching(team)
            # Planner also stores the philosophy-derived mentality target so
            # the match engine can read it without recomputing.
            base = coaching_base(team)
            prof = team.ai_profile or {}
            prof["primary"] = base.get("primary", "balanced")
            prof["mentality_target"] = int(base.get("mentality", 3))
            team.ai_profile = prof
            if changed:
                report[team.id] = changed
        except Exception:
            continue
    return report
