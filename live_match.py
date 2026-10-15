"""Live interactive match engine (new FM-style engine).

Replaces the old quick/medium/deep engine as the active simulation path while
leaving ``simulation.simulate_match`` untouched for future callers.

Highlights:
- minute-by-minute event generation with an interactive ``live`` mode and a
  synchronous ``auto`` mode (AI vs AI / skip).
- set-piece takers, pressing intensity, player roles, time-wasting tactic,
  tactical presets/familiarity, chemistry, captain boost, hidden traits,
  match sharpness, xG and advanced feed.
- live substitutions with early-substitution anger (FM feature).
"""

import logging
import random

from sqlalchemy.orm.attributes import flag_modified

from config import (
    AWAY_GOAL_MULTIPLIER,
    CAPTAIN_MORALE_BOOST,
    CAPTAIN_MORALE_THRESHOLD,
    CHEMISTRY_BASE,
    CHEMISTRY_SAME_REGION_BONUS,
    CHEMISTRY_SAME_REGION_THRESHOLD,
    EVENT_BASE_CHANCE,
    FAMILIARITY_MAX_PENALTY,
    FITNESS_STARTING_DECAY,
    FREEKICK_CHANCE_BOOST,
    GOAL_EXPECTANCY_MAX,
    GOAL_EXPECTANCY_MIN,
    HOME_GOAL_MULTIPLIER,
    INJURY_MIN_DAYS,
    LATE_EVENT_CHANCE,
    LIVE_HALF_MINUTES,
    LIVE_MATCH_MINUTES,
    MAX_FITNESS,
    MAX_GOALS_PER_TEAM,
    PENALTY_ATTACK_WEIGHT,
    PENALTY_BASE_CHANCE,
    PLAYER_ROLES,
    PRESSING_ATTACK_MULT,
    PRESSING_DEFENSE_MULT,
    PRESSING_FATIGUE_MULT,
    PRESSING_POSSESSION_BONUS,
    ROLE_ASSIST_MULT,
    ROLE_DEFENSIVE_MULT,
    ROLE_SCORER_MULT,
    ROLE_TACKLE_MULT,
    SET_PIECE_TAKER_BONUS,
    SHARPNESS_GAIN_FULL,
    SHARPNESS_GAIN_PARTIAL,
    SHARPNESS_MATCH_WEIGHT,
    STARTERS_LIMIT,
    TACTICAL_FAMILIARITY_GAIN,
    TACTICAL_FAMILIARITY_LOSS,
    TACTICAL_FAMILIARITY_PENALTY_START,
    TIME_WASTING_LATE_EVENT_CHANCE,
    TIME_WASTING_LATE_MINUTE,
    TRAIT_BIG_GAME_MULT,
    TRAIT_GETS_FORWARD_MULT,
    TRAIT_HARD_WORKER_DECAY,
    TRAIT_INJURY_PRONE_MULT,
    TRAIT_LONG_SHOTS_MULT,
    TRAIT_SET_PIECE_BOOST,
    TRAIT_TEMPERAMENTAL_CARD_MULT,
    XG_MAX,
    XG_MIN,
)
from models import (
    db,
    Team,
    Player,
    Match,
    Message,
    PlayerRating,
    GameState,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Player + team helpers
# --------------------------------------------------------------------------
def _clamp_goals(value: int) -> int:
    return max(0, min(MAX_GOALS_PER_TEAM, int(value)))


def _player_role(team: Team, player_id: int) -> str:
    fd = team.formation_data
    if isinstance(fd, dict):
        info = fd.get(str(player_id))
        if isinstance(info, dict) and info.get("role") in PLAYER_ROLES:
            return info["role"]
    return "standard"


def _effective_rating(attr: int, sharpness: int) -> float:
    """Sharpness-adjusted ability (FM: match sharpness)."""
    s = max(0, min(100, sharpness or SHARPNESS_GAIN_PARTIAL or 50))
    return (attr or 0) * (1.0 - SHARPNESS_MATCH_WEIGHT + SHARPNESS_MATCH_WEIGHT * s / 100.0)


def _player_overall(p) -> int:
    """Position-weighted overall with legacy fallback."""
    try:
        ov = getattr(p, "overall", None) or 0
        if ov:
            return int(ov)
    except Exception:
        pass
    try:
        return int(max(getattr(p, "attack", 50) or 50, getattr(p, "defense", 50) or 50))
    except Exception:
        return 50


def _team_philosophy_knobs(team: Team) -> dict:
    try:
        from services import get_team_philosophy
        phil = get_team_philosophy(team)
        if phil:
            return {
                "attacking_intent": float(getattr(phil, "attacking_intent", 0.5) or 0.5),
                "pressing_intensity": float(getattr(phil, "pressing_intensity", 0.5) or 0.5),
                "possession_focus": float(getattr(phil, "possession_focus", 0.5) or 0.5),
                "tempo": float(getattr(phil, "tempo", 0.5) or 0.5),
                "defensive_line": float(getattr(phil, "defensive_line", 0.5) or 0.5),
            }
    except Exception:
        pass
    return {"attacking_intent": 0.5, "pressing_intensity": 0.5,
            "possession_focus": 0.5, "tempo": 0.5, "defensive_line": 0.5}


def _formation_shape(team: Team) -> str:
    fd = team.formation_data
    if not isinstance(fd, dict) or not fd:
        return None
    counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    for pid in fd.keys():
        p = team.players and next((x for x in team.players if str(x.id) == str(pid)), None)
        if p and p.position in counts:
            counts[p.position] += 1
    return f"{counts['GK']}-{counts['DEF']}-{counts['MID']}-{counts['FWD']}"


def calculate_chemistry(team: Team, on_pitch) -> int:
    """FM: harmony grows with shared regions and team-player traits."""
    base = CHEMISTRY_BASE
    regions = [p.region for p in on_pitch if p.region]
    for region in set(regions):
        count = regions.count(region)
        if count >= CHEMISTRY_SAME_REGION_THRESHOLD:
            base += count * CHEMISTRY_SAME_REGION_BONUS
    for p in on_pitch:
        if "team_player" in (p.traits or []):
            base += 3
    return min(100, base)


def _captain(team: Team):
    for p in team.players:
        if p.personality == "Leader" or "leadership" in (p.traits or []):
            return p
    return None


def _captain_boost(team: Team) -> None:
    """FM: captain lifts team-mates with very low morale before kick-off."""
    captain = _captain(team)
    if not captain:
        return
    for p in team.players:
        if (p.morale or 50) < CAPTAIN_MORALE_THRESHOLD:
            p.morale = min(100, (p.morale or 50) + CAPTAIN_MORALE_BOOST)


def _apply_familiarity(team: Team) -> float:
    """FM: sudden tactical changes hurt performance for a few games."""
    shape = _formation_shape(team)
    if shape and team.last_formation and team.last_formation != shape:
        fam = (team.tactical_familiarity or 100) - TACTICAL_FAMILIARITY_LOSS
        team.tactical_familiarity = max(0, fam)
    elif shape:
        team.tactical_familiarity = min(100, (team.tactical_familiarity or 100) + TACTICAL_FAMILIARITY_GAIN)
    if shape:
        team.last_formation = shape
    fam = max(0, team.tactical_familiarity or 100)
    if fam >= TACTICAL_FAMILIARITY_PENALTY_START:
        return 1.0
    return 1.0 - FAMILIARITY_MAX_PENALTY * (1.0 - fam / TACTICAL_FAMILIARITY_PENALTY_START)


def _team_ratings(team: Team, on_pitch, chemistry: int) -> tuple[float, float]:
    """Effective attack/defence for both teams incl. pressing+familiarity.

    Uses the position-weighted ``overall`` (not raw attack/defense) plus
    philosophy multipliers.
    """
    if not on_pitch:
        return 0.0, 0.0

    att = sum(_effective_rating(_player_overall(p), p.sharpness) for p in on_pitch)
    dff = sum(_effective_rating(_player_overall(p), p.sharpness) for p in on_pitch)

    role_mult = sum(ROLE_DEFENSIVE_MULT[_player_role(team, p.id)] for p in on_pitch) / max(1, len(on_pitch))
    dff *= role_mult

    n = max(1, len(on_pitch))
    att = (att / n) * 10.0
    dff = (dff / n) * 10.0

    knobs = _team_philosophy_knobs(team)
    # Philosophy influence: attacking intent lifts attack, a high
    # defensive line slightly exposes the defence but adds possession.
    att *= 1.0 + (knobs.get("attacking_intent", 0.5) - 0.5) * 0.30
    dff *= 1.0 + (0.5 - knobs.get("defensive_line", 0.5)) * 0.10

    familiarity = _apply_familiarity(team)
    att *= PRESSING_ATTACK_MULT.get(team.pressing_style or "normal", 1.0)
    dff *= PRESSING_DEFENSE_MULT.get(team.pressing_style or "normal", 1.0)
    att *= familiarity
    dff *= familiarity

    chem = (chemistry - 50) / 50.0 * 0.05
    att *= 1.0 + chem
    dff *= 1.0 + chem

    morale = ((team.morale or 50) - 50) / 50.0 * 0.06
    att *= 1.0 + morale
    dff *= 1.0 + morale
    return att, dff


# --------------------------------------------------------------------------
# Live state helpers
# --------------------------------------------------------------------------
def _eligible_lineup(team: Team) -> list[Player]:
    ordered = sorted(
        [p for p in team.players if p.is_starting and p.injury_days == 0],
        key=lambda p: {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}.get(p.position, 9),
    )
    if len(ordered) < STARTERS_LIMIT:
        extras = [
            p for p in team.players
            if not p.is_starting and p.injury_days == 0 and p not in ordered
        ]
        ordered = ordered + extras[: STARTERS_LIMIT - len(ordered)]
    return ordered[: STARTERS_LIMIT]


def start_live(match: Match, mode: str = "live") -> bool:
    """Prepare a live/auto match. Persists state; caller owns commit."""
    try:
        if match.played or match.live_active:
            return False
        home = match.home_team
        away = match.away_team
        if not home or not away:
            return False

        # AI clubs play according to their identity, not stale columns.
        try:
            from services import effective_match_tactic, effective_pressing
            for _t in (home, away):
                if not getattr(_t, "is_player_team", False):
                    _t.pressing_style = effective_pressing(_t)
                    _t.match_tactic = effective_match_tactic(_t)
        except Exception:
            pass

        _captain_boost(home)
        _captain_boost(away)

        home_lineup = _eligible_lineup(home)
        away_lineup = _eligible_lineup(away)

        h_chem = calculate_chemistry(home, home_lineup)
        a_chem = calculate_chemistry(away, away_lineup)
        home.chemistry = h_chem
        away.chemistry = a_chem

        h_att, h_def = _team_ratings(home, home_lineup, h_chem)
        a_att, a_def = _team_ratings(away, away_lineup, a_chem)

        state = {
            "mode": mode,
            "status": "running",
            "minute": 0,
            "home_xp": {
                "att": h_att, "def": h_def, "chem": h_chem,
                "lineup": [p.id for p in home_lineup],
                "bench": [p.id for p in home.players if p.id not in [x.id for x in home_lineup]],
                "on_pitch": [p.id for p in home_lineup],
                "score": 0,
                "shots": 0, "sot": 0, "corners": 0, "fouls": 0,
                "xg": 0.0, "yellows": 0, "reds": 0,
                "subs": 0,
            },
            "away_xp": {
                "att": a_att, "def": a_def, "chem": a_chem,
                "lineup": [p.id for p in away_lineup],
                "bench": [p.id for p in away.players if p.id not in [x.id for x in away_lineup]],
                "on_pitch": [p.id for p in away_lineup],
                "score": 0,
                "shots": 0, "sot": 0, "corners": 0, "fouls": 0,
                "xg": 0.0, "yellows": 0, "reds": 0,
                "subs": 0,
            },
            "events": [],
            "finished": False,
        }
        match.live_active = True
        match.live_state = state
        return True
    except Exception:
        logger.exception("start_live failed for match %s", getattr(match, "id", "?"))
        return False


def _xpid(state: dict, key: str, team_key: str):
    pass


def _calculate_shot_xg(attacker_rating: float, defender_rating: float, attacking_intent: float = 0.5) -> float:
    base = attacker_rating / max(1.0, defender_rating * 2.0)
    base *= 0.90 + float(attacking_intent or 0.5) * 0.20
    return round(max(XG_MIN, min(XG_MAX, base)), 2)


def _weighted_choice(team: Team, players: list, weights_fn) -> Player | None:
    if not players:
        return None
    weights = weights_fn(team, players)
    return random.choices(players, weights=weights, k=1)[0]


def _pick_event_team(xp_home, xp_away, home_possession: int) -> str:
    return "home" if random.random() < home_possession / 100.0 else "away"


def _shooter_weights(team, on_pitch):
    weights = []
    for p in on_pitch:
        role = _player_role(team, p.id)
        w = ROLE_SCORER_MULT.get(role, 1.0) * max(1.0, _effective_rating(_player_overall(p), p.sharpness))
        if p.position == "FWD":
            w *= 1.6
        elif p.position == "MID":
            w *= 0.8
        if "gets_forward" in (p.traits or []):
            w *= TRAIT_GETS_FORWARD_MULT
        if "long_shots" in (p.traits or []) and p.position == "MID":
            w *= TRAIT_LONG_SHOTS_MULT
        weights.append(w)
    return weights


def _shot_chance(player: Player, opp_def: float, opp_pace_factor: float = 0.0, attacking_intent: float = 0.5) -> tuple[float, float]:
    """Return (xg, on-target probability) for a shooter against a defence."""
    att = _effective_rating(_player_overall(player), player.sharpness)
    if "long_shots" in (player.traits or []) and player.position in ("MID", "FWD"):
        att *= TRAIT_LONG_SHOTS_MULT
    if "big_game_player" in (player.traits or []):
        att *= TRAIT_BIG_GAME_MULT
    # Defensive line interacts with pace: a high line is punished by rapid forwards.
    try:
        pace = max(float(getattr(player, "sprint_speed", 50) or 50),
                   float(getattr(player, "acceleration", 50) or 50))
        att *= 1.0 + max(0.0, (pace - 70) / 200.0) * float(opp_pace_factor or 0.0)
    except Exception:
        pass
    xg = _calculate_shot_xg(att, max(1.0, opp_def), attacking_intent)
    on_target = 0.35 + (xg * 0.5)
    return xg, min(0.95, on_target)


def tick_match(match: Match) -> dict:
    """Advance the live/auto match by one minute, return new events (list).

    NOTE: returned events are NOT stored in ``live_state`` here — the caller
    (``advance_live_mins`` / ``run_auto_match``) must merge them, otherwise
    scorer attribution and match reports stay empty.
    """
    home = match.home_team
    away = match.away_team
    state = match.live_state or {}
    if not home or not away:
        return {"events": [], "finished": True}
    if state.get("finished"):
        return {"events": [], "finished": True}

    event_chance = EVENT_BASE_CHANCE
    try:
        h_t = _team_philosophy_knobs(home).get("tempo", 0.5)
        a_t = _team_philosophy_knobs(away).get("tempo", 0.5)
        event_chance = EVENT_BASE_CHANCE * (0.80 + ((h_t + a_t) / 2.0) * 0.40)
    except Exception:
        pass
    minute = (state.get("minute") or 0) + 1
    if minute > LIVE_MATCH_MINUTES:
        return {"events": [], "finished": True}

    home_xp = state.get("home_xp") or {}
    away_xp = state.get("away_xp") or {}
    if not home_xp or not away_xp:
        return {"events": [], "finished": True}
    h_poss, a_poss = possession_split(match, home_xp, away_xp)
    events = []

    if minute > TIME_WASTING_LATE_MINUTE:
        leader = "home" if home_xp["score"] > away_xp["score"] else (
            "away" if away_xp["score"] > home_xp["score"] else None)
        if leader:
            leader_team = home if leader == "home" else away
            if (leader_team.match_tactic or "balanced") == "time_wasting":
                event_chance = TIME_WASTING_LATE_EVENT_CHANCE
            else:
                event_chance = LATE_EVENT_CHANCE
        else:
            event_chance = LATE_EVENT_CHANCE

    if minute >= LIVE_MATCH_MINUTES:
        # FM: final whistle — force the half/full-time marker and finish.
        if minute == LIVE_MATCH_MINUTES:
            events.append({"minute": minute, "type": "full_time", "team": None})
            state["finished"] = True
            state["status"] = "finished"
        state["minute"] = LIVE_MATCH_MINUTES
        return {"events": events, "minute": minute, "finished": True}

    if neutral_event := _neutral_tick(match, state, minute, events, home_xp, away_xp, h_poss, a_poss):
        state["minute"] = minute
        return {"events": events, "minute": minute, "finished": bool(state.get("finished"))}

    if random.random() >= event_chance:
        state["minute"] = minute
        return {"events": events, "minute": minute, "finished": False}

    team_key = _pick_event_team(home_xp, away_xp, h_poss)
    team, xp = (home, home_xp) if team_key == "home" else (away, away_xp)
    opp_xp = away_xp if team_key == "home" else home_xp

    roll = random.random()
    on_pitch = _on_pitch_players(match, team, xp)
    if not on_pitch:
        state["minute"] = minute
        return {"events": events, "minute": minute, "finished": False}

    if roll < 0.42:
        ev = _shot_event(match, team, team_key, xp, opp_xp, on_pitch, minute)
        if ev:
            events.append(ev)
    elif roll < 0.55:
        xp["corners"] += 1
        ev = {"minute": minute, "type": "corner", "team": team_key_short(team_key)}
        events.append(ev)
    elif roll < 0.72:
        xp["fouls"] += 1
        player = random.choice(on_pitch)
        if (player.personality == "Lazy") and random.random() < 0.2:
            xp["fouls"] += 1
        ev = {"minute": minute, "type": "foul", "team": team_key_short(team_key),
              "player": player.name if player else None}
        if "temperamental" in (player.traits or []):
            ev["card"] = "yellow" if random.random() < 0.4 else None
            if ev["card"]:
                xp["yellows"] += 1
                events.append(ev)
            else:
                events.append(ev)
        else:
            events.append(ev)
    elif roll < 0.82:
        events.extend(_freekick_event(match, team, team_key, xp, opp_xp, on_pitch, minute))
    elif roll < 0.92:
        pdf_events = _penalty_event(match, team, team_key, xp, opp_xp, on_pitch, minute)
        events.extend(pdf_events)
    elif roll < 0.97:
        player = random.choice(on_pitch)
        same = player.team_id == home.id
        ev = {"minute": minute, "type": "yellow_card",
              "team": "home" if same else "away", "player": player.name if player else None}
        if "temperamental" in (player.traits or []) and random.random() < 0.5:
            ev["type"] = "red_card"
            xp["reds"] += 1
        else:
            xp["yellows"] += 1
        events.append(ev)
    else:
        player = random.choice(on_pitch)
        ev = {"minute": minute, "type": "injury", "team": team_key_short(team_key),
              "player": player.name if player else None}
        events.append(ev)

    state["minute"] = minute
    _apply_minute_fatigue(match, team, minute, on_pitch)
    return {"events": events, "minute": minute, "finished": False}


def _apply_minute_fatigue(match, team, minute, on_pitch) -> None:
    mult = PRESSING_FATIGUE_MULT.get(team.pressing_style or "normal", 1.0)
    for p in on_pitch:
        decay = min(0.25, FITNESS_STARTING_DECAY * mult / LIVE_MATCH_MINUTES)
        p.fitness = max(0, p.fitness - decay)


def possession_split(match, home_xp, away_xp) -> tuple[int, int]:
    try:
        home_team = getattr(match, "home_team", None)
        away_team = getattr(match, "away_team", None)
        h_style = (getattr(home_team, "pressing_style", None) or "normal")
        a_style = (getattr(away_team, "pressing_style", None) or "normal")
        base = 50
        base += PRESSING_POSSESSION_BONUS.get(h_style, 0) - PRESSING_POSSESSION_BONUS.get(a_style, 0)
        base += int((((home_xp or {}).get("att", 0)) - ((away_xp or {}).get("att", 0))) * 0.2)
        try:
            h_knobs = _team_philosophy_knobs(home_team)
            a_knobs = _team_philosophy_knobs(away_team)
            base += int((h_knobs.get("possession_focus", 0.5) - a_knobs.get("possession_focus", 0.5)) * 12)
        except Exception:
            pass
        base = max(35, min(65, base))
        return base, 100 - base
    except Exception:
        logger.exception("possession_split failed; falling back to 50-50")
        return 50, 50


def _neutral_tick(match, state, minute, events, home_xp, away_xp, h_poss, a_poss):
    """Half-time & full-time markers served every minute."""
    if minute == LIVE_HALF_MINUTES or minute == LIVE_MATCH_MINUTES:
        events.append(
            {"minute": minute, "type": "half_time" if minute == LIVE_HALF_MINUTES else "full_time",
             "team": None}
        )
        if minute == LIVE_MATCH_MINUTES:
            state["finished"] = True
            home_xp["status"] = away_xp["status"] = "finished"
    return False


def finish_live(match: Match, state=None) -> None:
    """Finalise a fully-played live/auto match into persistent match data."""
    try:
        if match.played:
            return  # idempotency: never apply the same result to the table twice
        home, away = match.home_team, match.away_team
        if home is None or away is None:
            logger.error("finish_live: match %s missing teams", getattr(match, "id", "?"))
            return
        if state is None:
            state = match.live_state or {}
        if not state:
            return

        home_xp, away_xp = state.get("home_xp", {}), state.get("away_xp", {})
        h_score = _clamp_goals(home_xp.get("score", 0))
        a_score = _clamp_goals(away_xp.get("score", 0))

        match.home_score = h_score
        match.away_score = a_score
        match.result = f"{h_score}-{a_score}"
        match.played = True
        match.live_active = False

        h_poss, a_poss = possession_split(match, home_xp, away_xp)
        match.home_possession = h_poss
        match.away_possession = a_poss
        match.home_shots = home_xp.get("shots", 0)
        match.away_shots = away_xp.get("shots", 0)
        match.home_shots_on_target = home_xp.get("sot", 0)
        match.away_shots_on_target = away_xp.get("sot", 0)
        match.home_corners = home_xp.get("corners", 0)
        match.away_corners = away_xp.get("corners", 0)
        match.home_fouls = home_xp.get("fouls", 0)
        match.away_fouls = away_xp.get("fouls", 0)
        match.home_xg = round(home_xp.get("xg", 0.0), 2)
        match.away_xg = round(away_xp.get("xg", 0.0), 2)

        events = state.get("events", [])
        all_home_events = [e for e in events if e.get("team") == "home"]
        all_away_events = [e for e in events if e.get("team") == "away"]
        match.home_events = sorted(all_home_events, key=lambda x: x.get("minute", 0))
        match.away_events = sorted(all_away_events, key=lambda x: x.get("minute", 0))

        _write_player_stats(match, home, away, h_score, a_score, state)
        _apply_match_fatigue_live(match, home, away, state)
        _update_sharpness(match, home, away, state)
        # Cup ties (week == 0) never touch the league table — winners are
        # recorded by cup_services.after_match_played instead.
        try:
            from config import CUP_MATCH_WEEK as _CUP_WEEK
        except Exception:
            _CUP_WEEK = 0
        if (match.week or 0) != _CUP_WEEK:
            from simulation import update_league_table
            update_league_table(home, away, h_score, a_score)

        match.live_state = {"status": "finished", "finished": True, "minute": LIVE_MATCH_MINUTES}
    except Exception:
        logger.exception("finish_live failed for match %s", getattr(match, "id", "?"))
        try:
            match.live_active = False
        except Exception:
            pass


def _on_pitch_players(match: Match, team: Team, xp: dict) -> list[Player]:
    ids = xp.get("on_pitch") or []
    return [p for p in team.players if p.id in ids]


def _shot_event(match, team, team_key, xp, opp_xp, on_pitch, minute):
    player = _weighted_choice(team, on_pitch, _shooter_weights)
    if not player:
        return None
    knobs = _team_philosophy_knobs(team)
    opp_team = match.away_team if team_key == "home_xp" else match.home_team
    opp_line = 0.5
    try:
        opp_line = float(_team_philosophy_knobs(opp_team).get("defensive_line", 0.5) or 0.5)
    except Exception:
        pass
    pace_factor = max(0.0, (opp_line - 0.5) * 1.2)
    xg, on_target = _shot_chance(player, opp_xp.get("def", 5.0),
                                 pace_factor, knobs.get("attacking_intent", 0.5))
    xp["shots"] += 1
    xp["xg"] += xg
    is_target = random.random() < on_target
    ev = {"minute": minute, "type": "shot", "team": team_key_short(team_key),
          "player": player.name if player else None,
          "player_id": player.id if player else None}
    if is_target:
        xp["sot"] += 1
        if random.random() < xg:
            xp["score"] += 1
            ev["type"] = "goal"
            ev["xg"] = xg
            assister = _pick_assister(team, on_pitch, player)
            if assister:
                ev["assist"] = assister.name if assister else None
                ev["assist_id"] = assister.id if assister else None
    else:
        ev["xg"] = xg
    return ev


def _freekick_event(match, team, team_key, xp, opp_xp, on_pitch, minute):
    """Free kick: designated taker or set-piece specialist shoots."""
    taker = None
    if team.freekick_taker_id:
        taker = next((p for p in on_pitch if p.id == team.freekick_taker_id), None)
    if not taker:
        taker = next((p for p in on_pitch if "set_piece_specialist" in (p.traits or [])), None)
    if not taker:
        return []
    att = _effective_rating(_player_overall(taker), taker.sharpness)
    boost = SET_PIECE_TAKER_BONUS
    if "set_piece_specialist" in (taker.traits or []):
        boost += TRAIT_SET_PIECE_BOOST
    score_prob = 0.04 + boost + (att / 1000.0)
    xp["shots"] += 1
    xp["xg"] += score_prob
    ev = {"minute": minute, "type": "shot", "team": team_key_short(team_key), "player": taker.name,
          "player_id": taker.id}
    if random.random() < score_prob:
        xp["sot"] += 1
        xp["score"] += 1
        ev["type"] = "goal"
        ev["xg"] = round(score_prob, 2)
        ev["set_piece"] = True
    return [ev]


def _penalty_event(match, team, team_key, xp, opp_xp, on_pitch, minute) -> list:
    """Penalty kick. Returns a list of events (penalty + goal on success)."""
    taker = None
    if team.penalty_taker_id:
        taker = next((p for p in on_pitch if p.id == team.penalty_taker_id), None)
    if not taker:
        taker = max(on_pitch, key=lambda p: _player_overall(p)) if on_pitch else None
    if not taker:
        return []
    chance = PENALTY_BASE_CHANCE + (_player_overall(taker) or 0) * PENALTY_ATTACK_WEIGHT
    if "set_piece_specialist" in (taker.traits or []):
        chance += TRAIT_SET_PIECE_BOOST
    chance = min(0.95, chance)
    xp["shots"] += 1
    xp["sot"] += 1
    xp["xg"] += chance
    ev = {"minute": minute, "type": "penalty", "team": team_key_short(team_key),
          "player": taker.name, "player_id": taker.id}
    if random.random() < chance:
        xp["score"] += 1
        ev["result"] = "goal"
        goal_ev = {"minute": minute, "type": "goal", "team": team_key_short(team_key),
                   "player": taker.name, "player_id": taker.id, "penalty": True,
                   "xg": round(chance, 2)}
        return [ev, goal_ev]
    ev["result"] = "missed"
    return [ev]


def _pick_assister(team, on_pitch, scorer):
    potential = [p for p in on_pitch if p.id != (scorer.id if scorer else None)]
    if not potential or random.random() > 0.7:
        return None
    weights = []
    for p in potential:
        role = _player_role(team, p.id)
        w = ROLE_ASSIST_MULT.get(role, 1.0) * max(1.0, _player_overall(p))
        if p.position == "MID":
            w *= 1.3
        weights.append(w)
    return random.choices(potential, weights=weights, k=1)[0]


# --------------------------------------------------------------------------
# Live substitutions (FM: substitution anger)
# --------------------------------------------------------------------------
def substitute_player(match: Match, team_key: str, player_out_id: int, player_in_id: int) -> tuple[bool, str]:
    try:
        state = match.live_state or {}
        if state.get("finished"):
            return False, "المباراة انتهت!"
        minute = state.get("minute") or 0
        xp = state.get(team_key) or {}
        on_pitch = list(xp.get("on_pitch") or [])
        bench = list(xp.get("bench") or [])

        if player_out_id not in on_pitch:
            return False, "اللاعب ليس في الملعب!"
        if player_in_id not in bench:
            return False, "اللاعب ليس في قائمة الاحتياط!"

        if team_key == "home_xp":
            team = match.home_team
        else:
            team = match.away_team
        if team is None:
            return False, "الفريق غير موجود!"

        p_out = next((p for p in team.players if p.id == player_out_id), None)
        p_in = next((p for p in team.players if p.id == player_in_id), None)

        if xp.get("subs", 0) >= 3:
            return False, "استنفدت التبديلات الثلاثة!"

        on_pitch.remove(player_out_id)
        bench.append(player_out_id)
        bench.remove(player_in_id)
        on_pitch.append(player_in_id)
        xp["on_pitch"] = on_pitch
        xp["bench"] = bench
        xp["subs"] = xp.get("subs", 0) + 1

        events = state.setdefault("events", [])
        state["events"] = events
        events.append({
            "minute": minute, "type": "sub", "team": team_key_short(team_key),
            "player_out": p_out.name if p_out else "?",
            "player_in": p_in.name if p_in else "?",
        })

        # FM: early substitution (before 45') upsets the player unless injured.
        if minute < LIVE_HALF_MINUTES and p_out and (p_out.injury_days == 0):
            p_out.morale = max(0, (p_out.morale or 50) - 15)
            msg = Message(
                title="استياء لاعب",
                content=f"{p_out.name} غاضب من استبداله المبكر قبل انتهاء الشوط الأول.",
                type="dynamics", created_day=0, urgent=True,
            )
            db.session.add(msg)
        if p_in:
            p_in.sharpness = min(100, (p_in.sharpness or 50) + 2)
        # Same in-place JSON mutation issue as advance_live_mins: flag explicitly
        # or the substitution is silently lost on commit.
        flag_modified(match, "live_state")
        return True, "تم التبديل"
    except Exception:
        logger.exception("substitute_player failed for match %s", getattr(match, "id", "?"))
        return False, "حدث خطأ أثناء التبديل!"


# --------------------------------------------------------------------------
# Post-match bookkeeping
# --------------------------------------------------------------------------
def _apply_match_fatigue_live(match, home, away, state) -> None:
    for team, xp in ((home, state.get("home_xp", {})), (away, state.get("away_xp", {}))):
        mult = PRESSING_FATIGUE_MULT.get(team.pressing_style or "normal", 1.0)
        on_pitch = [p for p in team.players if p.id in (xp.get("on_pitch") or [])]
        for p in on_pitch:
            decay = FITNESS_STARTING_DECAY * mult
            if "hard_worker" in (p.traits or []):
                decay *= TRAIT_HARD_WORKER_DECAY
            p.fitness = max(0, min(MAX_FITNESS, p.fitness - decay))
            p.matches_played += 1
            if random.random() < 0.04 * (1.6 if "injury_prone" in (p.traits or []) else 1):
                p.injury_days = random.randint(INJURY_MIN_DAYS, 28)


def _update_sharpness(match, home, away, state) -> None:
    for team, xp in ((home, state.get("home_xp", {})), (away, state.get("away_xp", {}))):
        on_pitch = [p for p in team.players if p.id in (xp.get("on_pitch") or [])]
        starters = [p for p in team.players if p.id in (xp.get("lineup") or [])]
        for p in team.players:
            if p in on_pitch:
                minutes = 90 if p in starters else 30
                gain = SHARPNESS_GAIN_FULL if minutes > 45 else SHARPNESS_GAIN_PARTIAL
                p.sharpness = min(100, (p.sharpness or 50) + gain)
            else:
                p.sharpness = max(0, (p.sharpness or 50) - 5)


def _event_actor(e: dict, p, id_key: str, name_key: str) -> bool:
    """Match an event to a player, preferring IDs (names collide: generated
    squads often contain teammates with identical names). Events from older
    saves carry names only, so fall back to name matching then."""
    pid = e.get(id_key)
    if pid is not None:
        return str(pid) == str(p.id)
    return e.get(name_key) == p.name


def _write_player_stats(match, home, away, h_score, a_score, state) -> None:
    rows = []
    for team, xp in ((home, state.get("home_xp", {})), (away, state.get("away_xp", {}))):
        on_pitch_ids = set(xp.get("on_pitch") or [])
        lineup_ids = set(xp.get("lineup") or [])
        for p in team.players:
            if p.id not in on_pitch_ids:
                continue
            p_goals = 0
            p_assists = 0
            for e in state.get("events", []):
                if e.get("type") == "goal" and _event_actor(e, p, "player_id", "player"):
                    p_goals += 1
                if _event_actor(e, p, "assist_id", "assist"):
                    p_assists += 1
            p.goals += p_goals
            p.assists += p_assists
            if p_goals > 0 and p.name and ("(شباب)" in p.name or "(Regen)" in p.name):
                team.youth_goals = (team.youth_goals or 0) + p_goals

            minutes = 90 if p.id in lineup_ids else 30
            shots = sum(1 for e in state.get("events", [])
                        if e.get("type") in ("shot", "goal") and _event_actor(e, p, "player_id", "player"))
            passes = random.randint(15, 55)
            tackles = random.randint(1, 7) if p.position in ("DEF", "MID") else random.randint(0, 2)
            if p.position in ("DEF", "MID"):
                tackles = int(tackles * ROLE_TACKLE_MULT[_player_role(team, p.id)])

            rating = 6.0 + random.uniform(-1, 1)
            rating += p_goals * 1.5 + p_assists * 1.0
            rating += (minutes - 60) * 0.01
            rating = max(1.0, min(10.0, rating))

            key_passes = 0
            if p.position == "MID" and random.random() < 0.4:
                key_passes = random.randint(1, 4)
                rating += 0.2
            errors = 0
            if p.position == "DEF" and random.random() < 0.05:
                errors = 1
                rating -= 2.0
            # Re-clamp AFTER key-pass/error adjustments: a hat-trick (10.0)
            # + 0.2 key-pass bonus otherwise exceeds the 1.0-10.0 CHECK and
            # aborts the whole batch transaction on background sims.
            rating = max(1.0, min(10.0, rating))

            rows.append({
                "player_id": p.id,
                "match_id": match.id,
                "rating": round(rating, 1),
                "goals": p_goals,
                "assists": p_assists,
                "shots": shots,
                "passes_completed": passes,
                "tackles": tackles,
                "minutes_played": minutes,
                "key_passes": key_passes,
                "errors": errors,
            })
    if rows:
        db.session.bulk_insert_mappings(PlayerRating, rows)


def team_key_short(team_key: str) -> str:
    return "home" if team_key == "home_xp" else "away"


# --------------------------------------------------------------------------
# Higher level drivers
# --------------------------------------------------------------------------
def run_auto_match(match: Match) -> bool:
    """Play a whole match synchronously (AI vs AI or skip)."""
    try:
        if not start_live(match, mode="auto"):
            return False
        for _ in range(LIVE_MATCH_MINUTES):
            res = tick_match(match)
            # tick_match only returns new events; persist them so finish_live
            # can credit scorers/assists and build the match report.
            if res.get("events"):
                state = match.live_state or {}
                state.setdefault("events", []).extend(res["events"])
                match.live_state = state
                # In-place JSON mutation is invisible to SQLAlchemy — without
                # this flag the events are silently lost on commit.
                flag_modified(match, "live_state")
            if (match.live_state or {}).get("finished"):
                break
        else:
            flag_modified(match, "live_state")
        finish_live(match)
        return True
    except Exception:
        logger.exception("run_auto_match failed for match %s", getattr(match, "id", "?"))
        try:
            match.live_active = False
        except Exception:
            pass
        return False


def reset_stuck_live(match: Match) -> None:
    """Clear a wedged live flag (e.g. after a crash mid-match) so the fixture
    can be started again instead of freezing the UI."""
    try:
        match.live_active = False
        match.live_state = None
    except Exception:
        logger.exception("reset_stuck_live failed for match %s", getattr(match, "id", "?"))


def finish_match_week(match: Match) -> int:
    """After the player's match ends, complete every other fixture that week."""
    played = 0
    try:
        # Cup ties live outside the weekly league cycle (week == 0 groups
        # every cup together) — they are driven by the cup engine instead.
        if (match.week or 0) == 0:
            return 0
        others = Match.query.filter_by(week=match.week, played=False).all()
        for m in others:
            if m.id == match.id:
                continue
            try:
                if run_auto_match(m):
                    played += 1
            except Exception:
                logger.exception("finish_match_week: auto match %s failed", getattr(m, "id", "?"))
                continue
    except Exception:
        logger.exception("finish_match_week failed for week %s", getattr(match, "week", "?"))
    return played


def advance_week_if_complete(match: Match) -> None:
    """Roll ``current_week`` forward once every fixture of the week is done."""
    try:
        # Cup ties are not part of the weekly league cycle.
        if (match.week or 0) == 0:
            return
        remaining = Match.query.filter_by(week=match.week, played=False).count()
        if remaining > 0:
            return
        state = GameState.query.first()
        if state:
            state.current_week = (state.current_week or 1) + 1
    except Exception:
        logger.exception("advance_week_if_complete failed for week %s",
                         getattr(match, "week", "?"))


def advance_live_mins(match: Match, minutes: int) -> dict:
    """Advance a live match by ``minutes``, collecting events."""
    try:
        collected = []
        for _ in range(minutes):
            res = tick_match(match)
            collected.extend(res.get("events", []))
            if res.get("finished"):
                break
        # Persist so the live feed, match report and post-match scorer
        # attribution (finish_live) all see the same events.
        state = match.live_state or {}
        if collected:
            state.setdefault("events", []).extend(collected)
        # tick_match mutates the JSON dict in place, which SQLAlchemy does not
        # flag as dirty (not even on same-object reassignment). Without this,
        # advancing through quiet minutes (no events) is silently lost on commit.
        flag_modified(match, "live_state")
        return {"events": collected, "minute": (match.live_state or {}).get("minute", 0),
                "finished": bool((match.live_state or {}).get("finished"))}
    except Exception:
        logger.exception("advance_live_mins failed for match %s", getattr(match, "id", "?"))
        return {"events": [], "minute": (match.live_state or {}).get("minute", 0),
                "finished": False, "error": "advance failed"}


def _players_meta(match: Match) -> dict:
    meta = {}
    for team in (match.home_team, match.away_team):
        if not team:
            continue
        home_ids = set((match.live_state or {}).get("home_xp", {}).get("on_pitch", []))
        away_ids = set((match.live_state or {}).get("away_xp", {}).get("on_pitch", []))
        on_pitch_ids = home_ids if team.id == match.home_team_id else away_ids
        for p in team.players:
            try:
                ov = _player_overall(p)
            except Exception:
                ov = 50
            meta[str(p.id)] = {
                "name": p.name,
                "position": p.position,
                "rating": round(float(ov), 1),
                "on_pitch": p.id in on_pitch_ids,
            }
    return meta


def live_state_payload(match: Match) -> dict:
    """Serialisable state for the live UI. Never raises — returns a safe
    fallback payload so the frontend shows an error instead of hanging."""
    try:
        state = match.live_state or {}
        home_xp = state.get("home_xp", {}) or {}
        away_xp = state.get("away_xp", {}) or {}
        h_poss, a_poss = possession_split(match, home_xp, away_xp)
        try:
            player_team = Team.query.filter_by(is_player_team=True).first()
        except Exception:
            player_team = None
        player_key = "home_xp"
        try:
            if player_team and match.home_team and match.home_team.id != player_team.id:
                player_key = "away_xp"
        except Exception:
            pass
        return {
        "minute": state.get("minute", 0),
        "finished": bool(state.get("finished")),
        "home_score": home_xp.get("score", 0),
        "away_score": away_xp.get("score", 0),
        "home_possession": h_poss,
        "away_possession": a_poss,
        "home_xg": round(home_xp.get("xg", 0.0), 2),
        "away_xg": round(away_xp.get("xg", 0.0), 2),
        "home_shots": home_xp.get("shots", 0),
        "away_shots": away_xp.get("shots", 0),
        "home_sot": home_xp.get("sot", 0),
        "away_sot": away_xp.get("sot", 0),
        "home_yellows": home_xp.get("yellows", 0),
        "away_yellows": away_xp.get("yellows", 0),
        "home_reds": home_xp.get("reds", 0),
        "away_reds": away_xp.get("reds", 0),
        "home_bench": home_xp.get("bench", []),
        "away_bench": away_xp.get("bench", []),
        "home_onpitch": home_xp.get("on_pitch", []),
        "away_onpitch": away_xp.get("on_pitch", []),
        "home_subs": home_xp.get("subs", 0),
        "away_subs": away_xp.get("subs", 0),
        "player_team_key": player_key,
        "players": _players_meta(match),
        "score": f"{home_xp.get('score', 0)}-{away_xp.get('score', 0)}",
        "events": state.get("events", []) or [],
    }
    except Exception:
        logger.exception("live_state_payload failed for match %s",
                         getattr(match, "id", "?"))
        return {"minute": 0, "finished": False, "home_score": 0,
                "away_score": 0, "score": "0-0", "events": [],
                "error": "could not load live state"}