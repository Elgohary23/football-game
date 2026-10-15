"""Full Tactical System (deeper_sim Feature B).

TeamProfile is the bridge to simulation: every engine (L1/L2/L3) consumes
it, so tactics matter match-by-match AND in bulk sim.

Mapping table (documented):
- higher defensive line -> offside_risk up, through-ball vulnerability up,
  space won up, possession up slightly.
- pressing_intensity -> opponent pass accuracy down, own fatigue up
  (press_fatigue_mult 1.0..2.2).
- aggressive tackling -> duels won up + cards up (foul_rate_mult up).
- direct/long + target man -> possession down + aerial_threat up.
- counterpress -> regain chance up + defensive gap chance up.
- work_ball_into_box / expressive -> chance_quality up, tempo up -> more
  events but lower quality.
- familiarity scales positive modifiers: mult = 0.5 + familiarity/200.
"""
import hashlib
import json

from config import (
    FAMILIARITY_MAX_PENALTY,
    TACTICAL_FAMILIARITY_PENALTY_START,
)

DEFAULT_TACTICS = {
    "formation": "4-2-3-1",
    "mentality": 3,
    "slots": [],
    "in_possession": {
        "width": "standard", "passing_directness": "standard", "tempo": "standard",
        "play_out_of_defence": False, "work_ball_into_box": True, "crosses": "mixed",
        "be_more_expressive": False, "overlap_left": False, "overlap_right": False,
    },
    "out_of_possession": {
        "defensive_line": "standard", "engagement": "standard",
        "pressing_intensity": 2, "press_trap": "inside",
        "tackling": "normal", "offside_trap": False, "counterpress": False,
    },
    "transitions": {"counter_attack": True, "time_wasting": "normal", "gk_distribution": "short"},
    "set_pieces": {"corner_l": None, "corner_r": None, "fk": None, "pen": None,
                   "att_routine": "mixed", "def_routine": "zonal"},
}

LINE_STEP = {"much_lower": -2, "lower": -1, "standard": 0, "higher": 1, "much_higher": 2}


def tactics_fingerprint(tactics):
    try:
        key = {
            "formation": (tactics or {}).get("formation"),
            "mentality": (tactics or {}).get("mentality"),
            "in_possession": (tactics or {}).get("in_possession"),
            "out_of_possession": (tactics or {}).get("out_of_possession"),
            "transitions": (tactics or {}).get("transitions"),
        }
        blob = json.dumps(key, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "default"


def get_team_tactics(team):
    """Lazy accessor: builds tactics_data from legacy fields on first use."""
    try:
        td = getattr(team, "tactics_data", None)
        if isinstance(td, dict) and td:
            return td
    except Exception:
        pass
    base = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULT_TACTICS.items()}
    try:
        from services import effective_match_tactic, effective_pressing
        pressing = effective_pressing(team)
        tactic = effective_match_tactic(team)
        mapping = {"low": 1, "normal": 2, "high": 3}
        base["out_of_possession"]["pressing_intensity"] = mapping.get(pressing, 2)
        base["mentality"] = {"defensive": 2, "balanced": 3, "attack": 4}.get(tactic, 3)
    except Exception:
        pass
    try:
        team.tactics_data = base
        team.tactic_fingerprint = tactics_fingerprint(base)
    except Exception:
        pass
    return base


def familiarity_mult(team):
    fam = getattr(team, "tactical_familiarity", 100) or 100
    mult = 0.5 + max(0, min(100, fam)) / 200.0
    # Legacy penalty curve kept for the live ratings path.
    if fam < TACTICAL_FAMILIARITY_PENALTY_START:
        mult *= 1.0 - FAMILIARITY_MAX_PENALTY * (1.0 - fam / TACTICAL_FAMILIARITY_PENALTY_START)
    return max(0.5, min(1.0, mult))


def note_match_played(team, level=1):
    """Familiarity growth per match; caller owns commit."""
    try:
        from config import FAMILIARITY_PER_L1, FAMILIARITY_PER_L2, FAMILIARITY_PER_L3
        gain = {1: FAMILIARITY_PER_L1, 2: FAMILIARITY_PER_L2, 3: FAMILIARITY_PER_L3}.get(level, 2)
        team.tactical_familiarity = min(100, int(getattr(team, "tactical_familiarity", 100) or 100) + gain)
    except Exception:
        pass


def on_tactics_changed(team, new_tactics):
    """Fingerprint change drops familiarity to 40; caller owns commit."""
    try:
        from config import FAMILIARITY_RESET_ON_CHANGE
        new_fp = tactics_fingerprint(new_tactics)
        if getattr(team, "tactic_fingerprint", None) != new_fp:
            team.tactic_fingerprint = new_fp
            team.tactical_familiarity = FAMILIARITY_RESET_ON_CHANGE
            team.tactics_data = new_tactics
            return True
        team.tactics_data = new_tactics
        return False
    except Exception:
        return False


def _avg(sharp_list, attr):
    vals = [float(getattr(p, attr, 50) or 50) for p in sharp_list]
    return sum(vals) / max(1, len(vals))


def derive_team_profile(team, players=None):
    """Pure function: XI abilities + tactics + familiarity -> TeamProfile dict."""
    tactics = get_team_tactics(team)
    xi = list(players or []) or [p for p in (getattr(team, "players", None) or [])
                                 if getattr(p, "is_starting", False)]
    n = max(1, len(xi))
    att = sum(float(getattr(p, "attack", 50) or 50) for p in xi) / n
    dfn = sum(float(getattr(p, "defense", 50) or 50) for p in xi) / n
    morale = ((getattr(team, "morale", 50) or 50) - 50) / 50.0

    mentality = int((tactics or {}).get("mentality", 3) or 3)
    ip = ((tactics or {}).get("in_possession", None)) or {}
    oop = ((tactics or {}).get("out_of_possession", None)) or {}
    tr = ((tactics or {}).get("transitions", None)) or {}

    line = str(oop.get("defensive_line", "standard"))
    pressing = int(oop.get("pressing_intensity", 2) or 2)
    tackling = str(oop.get("tackling", "normal"))
    tempo = str(ip.get("tempo", "standard"))
    directness = str(ip.get("passing_directness", "standard"))
    width = str(ip.get("width", "standard"))

    fam_mult = familiarity_mult(team)

    attack_eff = att * (1.0 + (mentality - 3) * 0.06 * fam_mult)
    defense_eff = dfn * (1.0 + (3 - mentality) * 0.04 * fam_mult)

    possession_base = 50.0
    possession_base += {"narrow": -2, "standard": 0, "wide": 2}.get(width, 0)
    possession_base += LINE_STEP.get(line, 0) * 1.5
    if directness in ("direct", "long"):
        possession_base -= 6
    if ip.get("play_out_of_defence"):
        possession_base += 4

    chance_quality = 1.0 + (0.05 if ip.get("work_ball_into_box") else -0.03)
    if ip.get("be_more_expressive"):
        chance_quality += 0.04
    if tempo == "fast":
        chance_quality -= 0.02

    aerial_threat = 1.0 + (0.25 if directness in ("direct", "long") else 0.0)
    counter_threat = 1.0 + (0.3 if tr.get("counter_attack") else 0.0)
    press_resistance = 0.5 + _avg(xi, "passing") / 200.0 + (0.1 if ip.get("play_out_of_defence") else 0.0)
    press_fatigue_mult = min(2.2, max(1.0, 1.0 + (pressing - 2) * 0.3))
    foul_rate_mult = {"cautious": 0.7, "normal": 1.0, "aggressive": 1.5}.get(tackling, 1.0)
    offside_risk = max(0.0, LINE_STEP.get(line, 0) * 0.15 + (0.1 if oop.get("offside_trap") else 0.0))
    set_piece_threat = 1.0
    gk_exposure = 1.0 + max(0, LINE_STEP.get(line, 0)) * 0.12

    # Philosophy tempo/possession knobs nudge the profile so identities play
    # differently even with identical tactics rows.
    try:
        from live_match import _team_philosophy_knobs as _knobs
        kn = _knobs(team)
        possession_base += (float(kn.get("possession_focus", 0.5)) - 0.5) * 8
        attack_eff *= 1.0 + (float(kn.get("attacking_intent", 0.5)) - 0.5) * 0.10
    except Exception:
        pass

    return {
        "attack_eff": round(attack_eff, 2),
        "defense_eff": round(defense_eff, 2),
        "possession_base": round(max(30, min(70, possession_base)), 1),
        "chance_quality": round(chance_quality, 3),
        "aerial_threat": round(aerial_threat, 3),
        "counter_threat": round(counter_threat, 3),
        "press_resistance": round(press_resistance, 3),
        "press_fatigue_mult": round(press_fatigue_mult, 3),
        "foul_rate_mult": round(foul_rate_mult, 3),
        "offside_risk": round(offside_risk, 3),
        "set_piece_threat": round(set_piece_threat, 3),
        "gk_exposure": round(gk_exposure, 3),
        "morale": round(morale, 3),
    }
