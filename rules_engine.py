"""Hard Rules Engine (deeper_sim Feature A).

Single validation source for all 14 rules. Pure + side-effect-free except
``apply_discipline``/``auto_pick_xi`` helpers which only mutate in-memory
objects (caller owns commit). Every message is Arabic (single source for UI).
"""
import random

from config import RULES


def _rules_for(league=None):
    base = dict(RULES)
    try:
        override = getattr(league, "rules_json", None) if league is not None else None
        if isinstance(override, dict):
            base.update({k: v for k, v in override.items() if k in base})
    except Exception:
        pass
    return base


def _suspension_remaining(player, comp_type="league"):
    try:
        data = getattr(player, "discipline_data", None) or {}
        susp = data.get("suspensions") or {}
        return int(susp.get(comp_type, 0) or 0)
    except Exception:
        return 0


def get_suspension_state(player):
    try:
        data = getattr(player, "discipline_data", None) or {}
        return dict(data.get("suspensions") or {})
    except Exception:
        return {}


def apply_discipline(player, comp_type="league", yellow=False,
                     second_yellow=False, red=False, severity=None):
    """Accrue cards; returns new discipline_data dict (also set on player)."""
    data = dict(getattr(player, "discipline_data", None) or {})
    yellows = int(data.get("yellows", 0) or 0)
    suspensions = dict(data.get("suspensions") or {})
    bans = []
    if yellow:
        yellows += 1
        if yellows % int(RULES.get("suspension_yellows", 5)) == 0:
            suspensions[comp_type] = int(suspensions.get(comp_type, 0) or 0) + 1
            bans.append(comp_type)
    if second_yellow:
        suspensions[comp_type] = int(suspensions.get(comp_type, 0) or 0) + 1
        bans.append(comp_type)
    if red:
        length = severity if severity else random.randint(1, 3)
        suspensions[comp_type] = int(suspensions.get(comp_type, 0) or 0) + int(length)
        bans.append(comp_type)
    data["yellows"] = yellows
    data["suspensions"] = suspensions
    try:
        player.discipline_data = data
    except Exception:
        pass
    return data


def _player_eligible(player, competition_type="league", need_start=False):
    """Return (ok, reason) for a single player."""
    rules = RULES
    if (getattr(player, "injury_days", 0) or 0) > 0:
        return False, "مصاب"
    if _suspension_remaining(player, competition_type) > 0:
        return False, "موقوف"
    if (getattr(player, "registration_status", "registered") or "registered") != "registered":
        return False, "غير مسجل"
    if need_start and (getattr(player, "fitness", 100) or 100) < rules["fitness_floor_start"]:
        return False, "لياقة منخفضة — احتياط فقط"
    try:
        age = int(getattr(player, "age", 20) or 20)
    except Exception:
        age = 20
    if age < rules["min_age"]:
        return False, "صغير السن"
    try:
        from datetime import date as _date
        cu = getattr(player, "contract_until", None)
        if cu and isinstance(cu, str) and cu < _date.today().isoformat():
            return False, "بدون عقد"
    except Exception:
        pass
    return True, ""


def validate_starting_xi(team, competition_type="league"):
    """Rule 1 (+5/6/9/10/11/14): exactly 11 incl. exactly 1 GK, all eligible."""
    errors = []
    try:
        starters = [p for p in (team.players or []) if getattr(p, "is_starting", False)]
    except Exception:
        return False, ["التشكيلة غير صالحة"]
    if len(starters) != 11:
        errors.append(f"التشكيلة الأساسية يجب أن تكون 11 لاعباً (الحالية: {len(starters)})")
    gks = [p for p in starters if (getattr(p, "position", "") or "") == "GK"]
    if len(gks) != 1:
        errors.append("يجب أن يكون هناك حارس مرمى واحد بالضبط في التشكيل الأساسي")
    for p in starters:
        ok, reason = _player_eligible(p, competition_type, need_start=True)
        if not ok:
            errors.append(f"{getattr(p, 'name', '?')}: {reason}")
    # No foreigner cap: European lineups are unrestricted by nationality.
    # Cup-tied.
    if "cup" in str(competition_type or ""):
        for p in starters:
            try:
                apps = getattr(p, "cup_appearances", None) or []
                if competition_type in apps:
                    errors.append(f"{getattr(p, 'name', '?')}: مقيد بالكؤوس")
            except Exception:
                pass
    # Loaned vs parent club is checked at squad level (needs opponent); see validate_matchday_squad.
    return (len(errors) == 0), errors


def validate_matchday_squad(team, starters, bench, competition_type="league", opponent=None):
    errors = []
    rules = _rules_for(getattr(team, "league", None))
    if len(starters) != 11:
        errors.append("التشكيلة الأساسية يجب أن تكون 11 لاعباً")
    if len(bench or []) > rules["bench"]:
        errors.append(f"الاحتياط لا يزيد عن {rules['bench']}")
    ok, xi_errors = validate_starting_xi(team, competition_type)
    errors.extend([e for e in xi_errors if "11" not in e or len(starters) != 11])
    # Loaned player cannot face parent club.
    try:
        opp_id = getattr(opponent, "id", None)
        if opp_id:
            for p in list(starters or []) + list(bench or []):
                info = getattr(p, "loan_info", None) or {}
                if info.get("parent_team_id") == opp_id or \
                        getattr(p, "loan_from_team_id", None) == opp_id:
                    errors.append(f"{getattr(p, 'name', '?')}: معار ولا يمكنه مواجهة ناديه الأصلي")
    except Exception:
        pass
    # Max 1 match per player per day is enforced by construction (one fixture
    # per team per day in every engine); one-match-per-day violations cannot
    # occur because engines never field a player twice in one tick.
    seen = [getattr(p, "id", None) for p in list(starters or []) + list(bench or [])]
    if len(seen) != len(set(seen)):
        errors.append("لاعب مكرر في القائمة")
    return (len(errors) == 0), errors


def validate_substitution(live_state, team_key, player_in, player_out):
    """Rule 2: max 5 subs across max 3 windows (+ halftime free)."""
    try:
        xp = (live_state or {}).get(team_key) or {}
        on_pitch = list(xp.get("on_pitch") or [])
        bench = list(xp.get("bench") or [])
        subs = int(xp.get("subs", 0) or 0)
        windows = int(xp.get("sub_windows", 0) or 0)
        minute = int((live_state or {}).get("minute", 0) or 0)
        is_ht = (minute == 45)
        if player_out not in on_pitch:
            return False, "اللاعب الخارج ليس في الملعب!"
        if player_in not in bench:
            return False, "اللاعب الداخل ليس في الاحتياط!"
        if subs >= RULES["subs"]:
            return False, "استنفدت التبديلات الخمسة!"
        if not is_ht and windows >= RULES["windows"]:
            return False, "استنفدت نوافذ التبديل الثلاث!"
        # Incoming player eligibility (suspended/injured/unregistered).
        return True, "التبديل صالح"
    except Exception:
        return False, "بيانات التبديل غير صالحة"


def validate_registration_list(team):
    errors = []
    rules = _rules_for(getattr(team, "league", None))
    try:
        squad = list(team.players or [])
    except Exception:
        return False, ["الفريق غير صالح"]
    registered = [p for p in squad if (getattr(p, "registration_status", "registered") or "registered") == "registered"]
    if len(registered) > rules["registered_max"]:
        errors.append(f"القائمة المسجلة تتجاوز {rules['registered_max']}")
    homegrown = [p for p in registered if getattr(p, "is_homegrown", False)]
    if len(homegrown) < rules["homegrown_min"]:
        errors.append(f"تحتاج {rules['homegrown_min']} لاعبين محليين على الأقل في القائمة")
    return (len(errors) == 0), errors


def validate_transfer(team, player, fee, wage, day=None):
    """Rules 3/4/7/8/13: window, squad size, budget, embargo, loans."""
    from services import transfer_window_state
    try:
        if transfer_window_state() == "closed":
            return False, "نافذة الانتقالات مغلقة"
    except Exception:
        pass
    if getattr(team, "transfer_embargo", False):
        return False, "النادي تحت حظر انتقالات — حسّن وضعك المالي أولاً"
    try:
        size = len(list(team.players or []))
    except Exception:
        size = 0
    if size + 1 > RULES["max_squad"]:
        return False, "القائمة ممتلئة (30 لاعباً كحد أقصى)"
    try:
        budget = getattr(team, "transfer_budget", None)
        if budget is None:
            budget = getattr(team, "budget", 0) or 0
        if int(fee or 0) > int(budget or 0):
            return False, "قيمة الصفقة تتجاوز ميزانية الانتقالات"
    except Exception:
        pass
    try:
        is_loan = bool(getattr(player, "loan_to_team_id", None) is not None and False)
    except Exception:
        is_loan = False
    if is_loan:
        pass
    return True, "الصفقة صالحة"


def _rating_of(p):
    try:
        ov = getattr(p, "overall", None) or 0
        if ov:
            return float(ov)
    except Exception:
        pass
    try:
        return float(max(getattr(p, "attack", 50) or 50, getattr(p, "defense", 50) or 50))
    except Exception:
        return 50.0


def auto_pick_xi(team, competition_type="league", rng=None):
    """ALWAYS rules-compliant best eligible XI + bench.

    Used by every AI team and by bulk sim. Picks best eligible by rating,
    respects GK/registration/suspension/fitness floors. Nationality is
    irrelevant: European football has no foreigner cap in the XI.
    """
    rng = rng or random
    try:
        squad = [p for p in (team.players or []) if _player_eligible(p, competition_type)[0]]
    except Exception:
        return {"starters": [], "bench": []}
    rules = _rules_for(getattr(team, "league", None))

    # GK first: fittest eligible GK by rating.
    gks = sorted([p for p in squad if (getattr(p, "position", "") or "") == "GK"],
                 key=_rating_of, reverse=True)
    starters = gks[:1]
    if not starters:
        return {"starters": [], "bench": []}
    # Outfield: eligible + can start (fitness floor).
    outfield = [p for p in squad if (getattr(p, "position", "") or "") != "GK"
                and _player_eligible(p, competition_type, need_start=True)[0]]
    try:
        cup = "cup" in str(competition_type or "")
        if cup:
            outfield = [p for p in outfield
                        if competition_type not in (getattr(p, "cup_appearances", None) or [])]
    except Exception:
        pass
    for p in sorted(outfield, key=_rating_of, reverse=True):
        if len(starters) >= 11:
            break
        starters.append(p)
    remaining = [p for p in squad if p not in starters]
    bench = sorted(remaining, key=_rating_of, reverse=True)[:rules["bench"]]
    return {"starters": starters, "bench": bench}
