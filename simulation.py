"""Football Manager Simulation Engine.

Match simulation, player statistics, game-state progression, calendar events,
transfers with position-aware AI, player development, youth academy, coaching
staff effects, training and promotion/relegation.
"""

import json
import logging
import random
from typing import Optional

import numpy as np
from sqlalchemy import update

from config import (
    DAILY_FITNESS_RECOVERY,
    DAYS_PER_WEEK,
    DEVELOPMENT_UPDATE_DAYS,
    FITNESS_DECAY_LAZY,
    FITNESS_DECAY_PROFESSIONAL,
    FITNESS_STARTING_DECAY,
    GOAL_EXPECTANCY_MAX,
    GOAL_EXPECTANCY_MIN,
    HOME_ATTACK_BONUS,
    HOME_GOAL_MULTIPLIER,
    AWAY_GOAL_MULTIPLIER,
    INJURY_CHANCE_HARD,
    INJURY_CHANCE_SOFT,
    INJURY_MAX_DAYS_HARD,
    INJURY_MAX_DAYS_SOFT,
    INJURY_MIN_DAYS,
    MARKET_PERF_ASSIST,
    MARKET_PERF_GOAL,
    MARKET_VALUE_DECAY_RATE,
    MARKET_VALUE_GROWTH_CAP,
    MARKET_VALUE_MAX,
    MARKET_VALUE_MIN,
    MAX_FITNESS,
    MAX_GOALS_PER_TEAM,
    MAX_SQUAD_SIZE,
    OLD_DATA_KEEP_PLAYED_MATCHES,
    POSITION_REQUIREMENTS,
    PROMOTION_BONUS_PER_TIER,
    PROMOTION_RELEGATION_SPOTS,
    RANDOM_GOAL_RANGE,
    REGEN_AGE_MIN,
    REGEN_AGE_RANGE,
    REPUTATION_TOLERANCE,
    SCORE_DIVISOR,
    SEASON_RESET_PLAYER_STATS,
    STARTERS_LIMIT,
    TRANSFER_OFFER_DEADLINE_DAYS,
    TRANSFER_OFFER_PRICE_RANGE,
    YOUTH_ACADEMY_AGE,
    YOUTH_GENERATION_CHANCE,
    YOUTH_RETIREMENT_AGE,
    # --- new FM systems ---
    BOARD_CONFIDENCE_PENALTY,
    BOARD_CONFIDENCE_REWARD,
    BOARD_INTERVENTION_MULTIPLE,
    BOARD_INTERVENTION_MIN_VALUE,
    BOARD_OBJECTIVES_MAX,
    BOARD_OBJECTIVE_TIERS,
    FREE_AGENT_MAX,
    FREE_AGENT_WEEKLY_CHANCE,
    MENTORING_MAX_AGE_MENTEE,
    MENTORING_PERSONALITY_CHANCE,
    MENTORING_TRAIT_CHANCE,
    PHYSICAL_DECLINE_AGE,
    PHYSICAL_DECLINE_MAX,
    TECHNICAL_DECLINE_AGE,
    TECHNICAL_DECLINE_MAX,
    TRAINING_COMPLAINT_INTENSITY,
    TRAINING_COMPLAINT_MORALE_LOSS,
)
from models import (
    db,
    Team,
    Player,
    Match,
    Message,
    PlayerRating,
    TransferOffer,
    GameState,
    League,
    BoardObjective,
    Mentoring,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Team strength + staff
# --------------------------------------------------------------------------
def get_lineup_strength(team: Team) -> tuple[float, float]:
    """Thin wrapper kept for call-site compatibility (Tip 5).

    The logic now lives on ``Team.get_strength()``.
    """
    if not team:
        return 0.0, 0.0
    return team.get_strength()


# --------------------------------------------------------------------------
# Match simulation (merged quick/medium/deep -> single entry point, Tip 4)
# --------------------------------------------------------------------------
def _clamp_goals(value: int) -> int:
    return max(0, min(MAX_GOALS_PER_TEAM, int(value)))


def _apply_match_fatigue(home: Team, away: Team, injury_chance: float, injury_max_days: int) -> None:
    staff_home = home.get_staff_bonuses()
    staff_away = away.get_staff_bonuses()
    for p in home.players + away.players:
        if not p.is_starting:
            continue
        staff = staff_home if p.team_id == home.id else staff_away
        decay = FITNESS_STARTING_DECAY
        if p.personality == "Lazy":
            decay = FITNESS_DECAY_LAZY
        elif p.personality == "Professional":
            decay = FITNESS_DECAY_PROFESSIONAL
        decay = max(3, decay - int(staff["fitness"] * 2))
        p.fitness = max(0, min(MAX_FITNESS, p.fitness - decay))
        p.matches_played += 1
        if random.random() < injury_chance and staff["injury_reduction"] < 1.0:
            p.injury_days = random.randint(INJURY_MIN_DAYS, injury_max_days)


def simulate_match(match: Match, state: Optional[GameState] = None, mode: str = "quick") -> bool:
    """Simulate one match. ``mode`` switches simulation depth (Tip 4).

    No ``commit`` inside: callers own the transaction so a whole round/week
    can be flushed with a single batch commit (Tip 8).
    """
    try:
        if match.played:
            return False  # idempotency: replaying a finished match must not double-count points
        home = match.home_team
        away = match.away_team
        if not home or not away:
            return False

        h_att, h_def = get_lineup_strength(home)
        a_att, a_def = get_lineup_strength(away)

        if mode == "quick":
            # Dampened Poisson expectation (Tip 22): caps prevent 8-2 blowouts.
            home_exp = max(
                GOAL_EXPECTANCY_MIN,
                min(GOAL_EXPECTANCY_MAX, (h_att / max(1.0, a_def)) * HOME_GOAL_MULTIPLIER),
            )
            away_exp = max(
                GOAL_EXPECTANCY_MIN,
                min(GOAL_EXPECTANCY_MAX, (a_att / max(1.0, h_def)) * AWAY_GOAL_MULTIPLIER),
            )
            match.home_score = _clamp_goals(np.random.poisson(home_exp))
            match.away_score = _clamp_goals(np.random.poisson(away_exp))
            match.result = f"{match.home_score}-{match.away_score}"
            match.played = True
            update_league_table(home, away, match.home_score, match.away_score)
            return True

        home_score = _clamp_goals(
            int((h_att * HOME_ATTACK_BONUS - a_def) / SCORE_DIVISOR)
            + random.randint(-1, RANDOM_GOAL_RANGE)
        )
        away_score = _clamp_goals(
            int((a_att - h_def) / SCORE_DIVISOR)
            + random.randint(-1, RANDOM_GOAL_RANGE)
        )

        match.home_possession = random.randint(40, 60)
        match.away_possession = 100 - match.home_possession

        if mode == "medium":
            match.home_shots = random.randint(3, 15)
            match.away_shots = random.randint(3, 15)
            match.home_shots_on_target = random.randint(1, match.home_shots)
            match.away_shots_on_target = random.randint(1, match.away_shots)
            match.home_corners = random.randint(2, 10)
            match.away_corners = random.randint(2, 10)
            match.home_fouls = random.randint(5, 18)
            match.away_fouls = random.randint(5, 18)
        else:  # deep
            match.home_shots = random.randint(5, 22)
            match.away_shots = random.randint(5, 22)
            match.home_shots_on_target = max(
                home_score, random.randint(int(match.home_shots * 0.3), match.home_shots)
            )
            match.away_shots_on_target = max(
                away_score, random.randint(int(match.away_shots * 0.3), match.away_shots)
            )
            match.home_corners = random.randint(2, 12)
            match.away_corners = random.randint(2, 12)
            match.home_fouls = random.randint(5, 20)
            match.away_fouls = random.randint(5, 20)

        if mode == "medium":
            home_events, away_events = generate_match_events(home_score, away_score, None)
        else:
            home_events, away_events = generate_deep_match_events(
                match, home, away, home_score, away_score
            )

        match.home_score = home_score
        match.away_score = away_score
        match.result = f"{home_score}-{away_score}"
        match.played = True
        match.home_events = home_events
        match.away_events = away_events

        process_match_player_stats(match, home, away, home_score, away_score)

        injury_chance = INJURY_CHANCE_HARD if mode == "deep" else INJURY_CHANCE_SOFT
        injury_max = INJURY_MAX_DAYS_HARD if mode == "deep" else INJURY_MAX_DAYS_SOFT
        _apply_match_fatigue(home, away, injury_chance, injury_max)

        update_league_table(home, away, home_score, away_score)
        return True

    except Exception as e:
        db.session.rollback()
        logger.error("Simulation failed for match %s: %s", getattr(match, "id", "?"), e)
        return False


def match_director(match: Match, state: GameState) -> bool:
    """Pick the right simulation depth, then run it (Tip 4)."""
    home = match.home_team
    away = match.away_team

    if home.is_player_team or away.is_player_team:
        mode = "deep"
    elif state.user_league_id and home.league_id == state.user_league_id:
        mode = "medium"
    else:
        mode = "quick"

    result = simulate_match(match, state, mode=mode)
    update_manager_record(match, state)
    return result


def generate_match_events(h_score, a_score, state) -> tuple[list, list]:
    h_ev = [{"type": "goal", "minute": random.randint(1, 90)} for _ in range(h_score)]
    a_ev = [{"type": "goal", "minute": random.randint(1, 90)} for _ in range(a_score)]
    return h_ev, a_ev


def generate_deep_match_events(match, home, away, h_score, a_score):
    all_events = []
    home_starters = [p for p in home.players if p.is_starting and p.injury_days == 0]
    away_starters = [p for p in away.players if p.is_starting and p.injury_days == 0]

    goal_minutes_h = sorted([random.randint(1, 90) for _ in range(h_score)])
    goal_minutes_a = sorted([random.randint(1, 90) for _ in range(a_score)])

    for minute in goal_minutes_h:
        scorer = _pick_scorer(home_starters)
        assister = _pick_assister(home_starters, scorer) if random.random() < 0.7 else None
        event = {"type": "goal", "minute": minute, "player": scorer.name if scorer else "Unknown",
                 "team": "home"}
        if assister:
            event["assist"] = assister.name
        all_events.append(event)

    for minute in goal_minutes_a:
        scorer = _pick_scorer(away_starters)
        assister = _pick_assister(away_starters, scorer) if random.random() < 0.7 else None
        event = {"type": "goal", "minute": minute, "player": scorer.name if scorer else "Unknown",
                 "team": "away"}
        if assister:
            event["assist"] = assister.name
        all_events.append(event)

    num_yellows = random.randint(2, 8)
    for _ in range(num_yellows):
        minute = random.randint(1, 90)
        team_starters = home_starters if random.random() < (match.home_possession / 100) else away_starters
        player = random.choice(team_starters) if team_starters else None
        team = "home" if team_starters == home_starters else "away"
        all_events.append({
            "type": "yellow_card", "minute": minute,
            "player": player.name if player else "Unknown", "team": team
        })

    if random.random() < 0.3:
        minute = random.randint(1, 90)
        team_starters = home_starters if random.random() < 0.5 else away_starters
        player = random.choice(team_starters) if team_starters else None
        team = "home" if team_starters == home_starters else "away"
        all_events.append({
            "type": "red_card", "minute": minute,
            "player": player.name if player else "Unknown", "team": team
        })

    num_shots = random.randint(8, 20)
    for _ in range(num_shots):
        minute = random.randint(1, 90)
        team_starters = home_starters if random.random() < (match.home_possession / 100) else away_starters
        player = random.choice(team_starters) if team_starters else None
        team = "home" if team_starters == home_starters else "away"
        all_events.append({
            "type": "shot", "minute": minute,
            "player": player.name if player else "Unknown", "team": team
        })

    num_corners = random.randint(4, 12)
    for _ in range(num_corners):
        minute = random.randint(1, 90)
        team = "home" if random.random() < (match.home_possession / 100) else "away"
        all_events.append({"type": "corner", "minute": minute, "team": team})

    num_fouls = random.randint(8, 20)
    for _ in range(num_fouls):
        minute = random.randint(1, 90)
        team_starters = home_starters if random.random() < 0.5 else away_starters
        player = random.choice(team_starters) if team_starters else None
        team = "home" if team_starters == home_starters else "away"
        all_events.append({
            "type": "foul", "minute": minute,
            "player": player.name if player else "Unknown", "team": team
        })

    home_events = [e for e in all_events if e.get("team") == "home"]
    away_events = [e for e in all_events if e.get("team") == "away"]
    home_events.sort(key=lambda x: x.get("minute", 0))
    away_events.sort(key=lambda x: x.get("minute", 0))

    return home_events, away_events


def _pick_scorer(starters):
    if not starters:
        return None
    weights = []
    for p in starters:
        w = 1
        if p.position == "FWD":
            w = 10
        elif p.position == "MID":
            w = 3
        weights.append(w)
    return random.choices(starters, weights=weights, k=1)[0]


def _pick_assister(starters, scorer):
    potential = [p for p in starters if p.id != scorer.id] if scorer else starters
    if not potential:
        return None
    weights = []
    for p in potential:
        w = 1
        if p.position == "MID":
            w = 10
        elif p.position == "FWD":
            w = 4
        elif p.position == "DEF":
            w = 2
        weights.append(w)
    return random.choices(potential, weights=weights, k=1)[0]


# --------------------------------------------------------------------------
# Player stats + ratings (bulk insert, Tip 9)
# --------------------------------------------------------------------------
def process_match_player_stats(match: Match, home: Team, away: Team, home_score: int, away_score: int) -> None:
    def simulate_team_stats(team: Team, goals_count: int) -> tuple[dict[int, int], dict[int, int]]:
        m_goals = {}
        m_assists = {}
        starters = [p for p in team.players if p.is_starting and p.injury_days == 0]
        if not starters:
            return m_goals, m_assists

        for _ in range(goals_count):
            s_weights = []
            for p in starters:
                w = 1
                if p.position == "FWD":
                    w = 10
                elif p.position == "MID":
                    w = 3
                s_weights.append(w)
            scorer = random.choices(starters, weights=s_weights, k=1)[0]
            m_goals[scorer.id] = m_goals.get(scorer.id, 0) + 1

            if random.random() < 0.7:
                potential_assisters = [p for p in starters if p.id != scorer.id]
                if potential_assisters:
                    a_weights = []
                    for p in potential_assisters:
                        w = 1
                        if p.position == "MID":
                            w = 10
                        elif p.position == "FWD":
                            w = 4
                        elif p.position == "DEF":
                            w = 2
                        a_weights.append(w)
                    assister = random.choices(potential_assisters, weights=a_weights, k=1)[0]
                    m_assists[assister.id] = m_assists.get(assister.id, 0) + 1

        return m_goals, m_assists

    home_goals, home_assists = simulate_team_stats(home, home_score)
    away_goals, away_assists = simulate_team_stats(away, away_score)

    rating_rows = []
    for team, t_goals, t_assists in [(home, home_goals, home_assists), (away, away_goals, away_assists)]:
        for p in team.players:
            if p.is_starting and p.injury_days == 0:
                p_goals = t_goals.get(p.id, 0)
                p_assists = t_assists.get(p.id, 0)

                p.goals += p_goals
                p.assists += p_assists
                if p_goals > 0 and p.name and ("(شباب)" in p.name or "(Regen)" in p.name):
                    team.youth_goals = (team.youth_goals or 0) + p_goals

                shots = random.randint(0, 5) if p.position == "FWD" else random.randint(0, 2)
                passes = random.randint(15, 55)
                tackles = random.randint(0, 8) if p.position in ["DEF", "MID"] else random.randint(0, 3)

                rating = 6.0 + random.uniform(-1, 1)
                if p_goals > 0:
                    rating += p_goals * 1.5
                if p_assists > 0:
                    rating += p_assists * 1.0
                if shots > 3:
                    rating += 0.3
                if tackles > 5:
                    rating += 0.2
                rating = max(1.0, min(10.0, rating))

                rating_rows.append({
                    "player_id": p.id,
                    "match_id": match.id,
                    "rating": round(rating, 1),
                    "goals": p_goals,
                    "assists": p_assists,
                    "shots": shots,
                    "passes_completed": passes,
                    "tackles": tackles,
                    "minutes_played": 90,
                })

    if rating_rows:
        # Single multi-row INSERT (Tip 9).
        db.session.bulk_insert_mappings(PlayerRating, rating_rows)


def calculate_player_ratings(match, home, away, h_score, a_score) -> None:
    rows = []
    for team in [home, away]:
        for p in team.players:
            if p.is_starting and p.injury_days == 0:
                rating = 6.0 + random.uniform(-1, 1)
                if p.goals > 0:
                    rating += p.goals * 1.5
                rating = max(1.0, min(10.0, rating))
                rows.append({
                    "player_id": p.id,
                    "match_id": match.id,
                    "rating": round(rating, 1),
                })
    if rows:
        db.session.bulk_insert_mappings(PlayerRating, rows)


def assign_scorers(team: Team, goals_count: int) -> None:
    if goals_count <= 0:
        return
    starters = [p for p in team.players if p.is_starting and p.injury_days == 0]
    if not starters:
        return
    for _ in range(goals_count):
        weights = []
        for p in starters:
            w = 1
            if p.position == "FWD":
                w = 10
            elif p.position == "MID":
                w = 3
            weights.append(w)
        scorer = random.choices(starters, weights=weights, k=1)[0]
        scorer.goals += 1


def update_manager_record(match: Match, state: GameState) -> None:
    if not state:
        return
    home = match.home_team
    away = match.away_team
    if not home or not away:
        return
    player_team_id = None
    for t in [home, away]:
        if t.is_player_team:
            player_team_id = t.id
            break
    if player_team_id is None:
        return
    is_home = (player_team_id == home.id)
    h_score = match.home_score or 0
    a_score = match.away_score or 0
    if is_home:
        if h_score > a_score:
            state.manager_wins = (state.manager_wins or 0) + 1
        elif a_score > h_score:
            state.manager_losses = (state.manager_losses or 0) + 1
        else:
            state.manager_draws = (state.manager_draws or 0) + 1
    else:
        if a_score > h_score:
            state.manager_wins = (state.manager_wins or 0) + 1
        elif h_score > a_score:
            state.manager_losses = (state.manager_losses or 0) + 1
        else:
            state.manager_draws = (state.manager_draws or 0) + 1


def update_league_table(home: Team, away: Team, h_score: int, a_score: int) -> None:
    home.goals_for += h_score
    home.goals_against += a_score
    away.goals_for += a_score
    away.goals_against += h_score

    if h_score > a_score:
        home.points += 3
        home.wins += 1
        away.losses += 1
        home.morale = min(100, home.morale + 5)
        away.morale = max(0, away.morale - 3)
    elif a_score > h_score:
        away.points += 3
        away.wins += 1
        home.losses += 1
        away.morale = min(100, away.morale + 5)
        home.morale = max(0, home.morale - 3)
    else:
        home.points += 1
        away.points += 1
        home.draws += 1
        away.draws += 1

    # Table data changed: drop the cached league table immediately so the
    # next read (same day, e.g. right after a live match) is never stale.
    try:
        from services import invalidate_league_cache
        invalidate_league_cache()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Transfers (position-aware AI, Tip 25)
# --------------------------------------------------------------------------
def _weakest_line(squad) -> tuple[str, dict]:
    vals = {}
    for pos, _count in POSITION_REQUIREMENTS:
        ps = [p for p in squad if p.position == pos]
        if not ps:
            vals[pos] = 0.0
            continue
        if pos in ("GK", "DEF"):
            vals[pos] = sum(p.defense for p in ps) / len(ps)
        elif pos == "FWD":
            vals[pos] = sum(p.attack for p in ps) / len(ps)
        else:
            vals[pos] = (sum(p.attack for p in ps) + sum(p.defense for p in ps)) / (2 * len(ps))
    weakest = min(vals, key=vals.get)
    return weakest, vals


def simulate_ai_transfers(state: GameState) -> None:
    if state.current_day % DAYS_PER_WEEK != 0:
        return

    # Phase 1 (item 6): AI clubs also respect the window — no permanent
    # deals when it is closed. Free-agent generation above is unaffected.
    try:
        from services import (
            get_player_valuation,
            is_deadline_day,
            transfer_window_state,
        )
        window_closed = transfer_window_state(state) == "closed"
        deadline = is_deadline_day(state)
    except Exception:
        window_closed, deadline = False, False
    if window_closed:
        return

    # Phase 1 (item 7): deadline day — clubs panic-buy, twice the activity.
    rounds = 2 if deadline else 1
    for _ in range(rounds):
        _ai_transfer_round(state, deadline)


def _ai_transfer_round(state: GameState, deadline: bool = False) -> None:
    try:
        from services import get_player_valuation
    except Exception:
        get_player_valuation = None

    ai_teams = Team.query.filter_by(is_player_team=False).all()
    for team in ai_teams:
        squad = Player.query.filter_by(team_id=team.id).all()
        positions_needed = []
        for pos, count in POSITION_REQUIREMENTS:
            if len([p for p in squad if p.position == pos]) < count:
                positions_needed.append(pos)

        if not positions_needed:
            continue

        weakest, line_vals = _weakest_line(squad)
        target_pos = weakest if weakest in positions_needed else random.choice(positions_needed)
        our_avg = line_vals[target_pos]

        candidates = Player.query.filter(
            Player.team_id != team.id,
            Player.position == target_pos,
            Player.team_id.isnot(None),
        ).all()
        candidates = [c for c in candidates if c.market_value < team.budget]
        if not candidates:
            continue

        def candidate_gain(c: Player) -> float:
            return c.rating_for() - our_avg

        upgraded = [c for c in candidates if candidate_gain(c) > 0]
        pool = upgraded or candidates
        # Phase 1 (items 9-10): listed / requesting players are hotter targets.
        listed = [c for c in pool if c.transfer_listed or c.transfer_requested]
        target = max(listed or pool, key=lambda c: candidate_gain(c) + (c.potential - c.rating_for()) * 0.3)

        if team.reputation >= (target.team.reputation if target.team else 0) - REPUTATION_TOLERANCE:
            low, high = TRANSFER_OFFER_PRICE_RANGE
            # Phase 4 (item 20): price on dynamic valuation, not a fixed number.
            base = get_player_valuation(target) if get_player_valuation else target.market_value
            amount = int(base * random.uniform(low, high))

            # FM: board intervention — unrejectable mega bids force a sale.
            if target.team and target.team.is_player_team:
                league = target.team.league
                if (
                    amount > target.market_value * BOARD_INTERVENTION_MULTIPLE
                    and amount >= BOARD_INTERVENTION_MIN_VALUE
                    and league and league.tier > 1
                ):
                    target.team.budget = (target.team.budget or 0) + amount
                    from_team_id = target.team_id
                    target.team_id = team.id
                    target.is_starting = False
                    offer = TransferOffer(
                        player_id=target.id,
                        from_team_id=from_team_id,
                        to_team_id=team.id,
                        amount=amount,
                        status='accepted',
                        created_day=state.current_day,
                        deadline_day=state.current_day,
                    )
                    db.session.add(offer)
                    db.session.add(Message(
                        title="تدخل الإدارة",
                        content=f"قُبل عرض {team.name} لشراء {target.name} بقيمة {amount:,} لأنه تجاوز المطلوب بفارق كبير.",
                        type="transfer", created_day=state.current_day, urgent=True,
                    ))
                    continue

            try:
                from services import is_deadline_day as _is_dd
                from config import TRANSFER_DEADLINE_EXPIRY_DAYS as _dd_exp
                expiry = _dd_exp if (_is_dd(state) or deadline) else TRANSFER_OFFER_DEADLINE_DAYS
            except Exception:
                expiry = TRANSFER_OFFER_DEADLINE_DAYS
            offer = TransferOffer(
                player_id=target.id,
                from_team_id=target.team_id,
                to_team_id=team.id,
                amount=amount,
                status='pending',
                created_day=state.current_day,
                deadline_day=state.current_day + expiry,
                wage_offer=int((target.wage or 0) * 1.1),
                contract_years=3,
            )
            db.session.add(offer)

            if target.team and target.team.is_player_team:
                msg = Message(
                    title="عرض انتقال وارد",
                    content=f"وصل عرض من {team.name} لشراء {target.name} مقابل {amount}",
                    type="transfer",
                    created_day=state.current_day,
                    urgent=True,
                )
                db.session.add(msg)


def generate_free_agents(state: GameState) -> None:
    """FM: players without contracts appear on the market for wage-only deals."""
    current = Player.query.filter_by(team_id=None).count()
    if current >= FREE_AGENT_MAX:
        return
    if random.random() > FREE_AGENT_WEEKLY_CHANCE:
        return

    from procedural import generate_player_name, generate_player_stats, generate_player_traits

    region = random.choice(["Europe", "Middle_East", "South_America"])
    pos = random.choices(["GK", "DEF", "MID", "FWD"], weights=[1, 3, 3, 2], k=1)[0]
    name = generate_player_name(region)
    stats = generate_player_stats(tier_level=3, position=pos, region=region)
    stats["age"] = max(20, stats["age"])
    free = Player(
        name=name,
        position=pos,
        attack=stats["attack"],
        defense=stats["defense"],
        potential=stats["potential"],
        age=stats["age"],
        height=stats["height"],
        weight=stats["weight"],
        personality=stats["personality"],
        region=region,
        market_value=stats["market_value"],
        team_id=None,
        is_starting=False,
        traits=generate_player_traits(),
    )
    db.session.add(free)


def check_training_complaints(state: GameState) -> None:
    """FM: Lazy players resent relentless high-intensity training."""
    if (state.current_day or 0) % DAYS_PER_WEEK != 0:
        pass
    for team in Team.query.all():
        if (team.training_intensity or 5) <= TRAINING_COMPLAINT_INTENSITY:
            continue
        for p in Player.query.filter_by(team_id=team.id).all():
            if p.personality == "Lazy" and random.random() < 0.35:
                p.morale = max(0, (p.morale or 50) - TRAINING_COMPLAINT_MORALE_LOSS)
                db.session.add(Message(
                    title="شكوى لاعب",
                    content=f"اللاعب {p.name} مرهق من كثافة التدريب المرتفعة.",
                    type="dynamics", created_day=state.current_day,
                ))


def process_mentoring_monthly(state: GameState) -> None:
    """FM: a veteran mentor can shape a young player's personality and traits."""
    for pair in Mentoring.query.all():
        mentor = db.session.get(Player, pair.mentor_id)
        mentee = db.session.get(Player, pair.mentee_id)
        if not mentor or not mentee:
            continue
        if mentee.age > MENTORING_MAX_AGE_MENTEE:
            db.session.delete(pair)
            continue
        if mentor.personality == "Leader" and random.random() < MENTORING_PERSONALITY_CHANCE:
            mentee.personality = "Leader"
        mentor_traits = mentor.traits or []
        mentee_traits = list(mentee.traits or [])
        for t in mentor_traits:
            if t not in mentee_traits and random.random() < MENTORING_TRAIT_CHANCE:
                mentee_traits.append(t)
        mentee.traits = mentee_traits


# --------------------------------------------------------------------------
# Board objectives (FM: management challenges beyond winning)
# --------------------------------------------------------------------------
def ensure_board_objectives(team: Team, state: GameState) -> None:
    season = state.season_number or 1
    existing = BoardObjective.query.filter_by(team_id=team.id, season=season).count()
    if existing >= BOARD_OBJECTIVES_MAX:
        return
    for obj_type in ("avg_age", "youth_goals", "league_position"):
        if existing >= BOARD_OBJECTIVES_MAX:
            break
        spec = BOARD_OBJECTIVE_TIERS.get(obj_type)
        if not spec:
            continue
        target = spec["target"]
        if obj_type == "league_position":
            team_count = Team.query.filter_by(league_id=team.league_id).count()
            target = max(2, min(team_count, int(team_count * 0.35)))
        db.session.add(BoardObjective(
            team_id=team.id, season=season, objective_type=obj_type, target_value=target,
        ))
    db.session.commit()


def check_board_objectives(state: GameState) -> list[Message]:
    team = Team.query.filter_by(is_player_team=True).first()
    results = []
    if not team:
        return results
    for obj in BoardObjective.query.filter_by(team_id=team.id, season=state.season_number).all():
        spec = BOARD_OBJECTIVE_TIERS.get(obj.objective_type)
        achieved = _objective_achieved(team, state, obj, spec)
        obj.achieved = bool(achieved)
        if achieved:
            state.board_confidence = min(100, (state.board_confidence or 60) + BOARD_CONFIDENCE_REWARD)
            results.append(Message(
                title="هدف تحقق!",
                content=f"أتممت هدف الموسم: {_objective_label(obj)}",
                type="board", created_day=state.current_day, urgent=True,
            ))
        else:
            state.board_confidence = max(0, (state.board_confidence or 60) - BOARD_CONFIDENCE_PENALTY)
            results.append(Message(
                title="فشل في تحقيق هدف",
                content=f"لم تحقق هدف الموسم: {_objective_label(obj)}",
                type="board", created_day=state.current_day, urgent=True,
            ))
    return results


def _objective_label(obj: BoardObjective) -> str:
    labels = {
        "avg_age": "تخفيض متوسط عمر الفريق",
        "youth_goals": "أهداف من لاعبي الأكاديمية",
        "league_position": "المركز المتوقع في الدوري",
    }
    return labels.get(obj.objective_type, obj.objective_type)


def _objective_achieved(team: Team, state: GameState, obj: BoardObjective, spec: dict) -> bool:
    if obj.objective_type == "avg_age":
        ages = [p.age for p in team.players if p.age]
        avg = sum(ages) / len(ages) if ages else 0
        return avg <= (spec or {}).get("target", 26.0)
    if obj.objective_type == "league_position":
        try:
            from services import get_team_rank
            rank = get_team_rank(team.league_id, team)
            return rank is not None and rank <= int(obj.target_value)
        except Exception:
            return False
    if obj.objective_type == "youth_goals":
        return (team.youth_goals or 0) >= int(obj.target_value)
    return False


# --------------------------------------------------------------------------
# Daily / weekly progression (bulk updates, Tips 14 & 26)
# --------------------------------------------------------------------------
def update_market_values() -> None:
    """Performance-driven market value adjustments (Tip 26)."""
    for p in Player.query.all():
        base = p.market_value or MARKET_VALUE_MIN
        perf = p.goals * MARKET_PERF_GOAL + p.assists * MARKET_PERF_ASSIST
        growth = int(min(perf, base * MARKET_VALUE_GROWTH_CAP))
        if p.position == "GK":
            growth = int(min(growth, (p.matches_played or 0) * 500))
        decay = int(base * MARKET_VALUE_DECAY_RATE) if (p.matches_played or 0) == 0 else 0
        p.market_value = max(MARKET_VALUE_MIN, min(MARKET_VALUE_MAX, base + growth - decay))


def simulate_day(state: GameState) -> None:
    from datetime import datetime, timedelta

    try:
        if state.current_date:
            curr_dt = datetime.strptime(state.current_date, "%Y-%m-%d").date()
            state.current_date = (curr_dt + timedelta(days=1)).isoformat()
    except Exception:
        pass

    state.current_day += 1

    # Bulk daily fitness / injury updates (Tip 14).
    db.session.execute(
        update(Player)
        .where(Player.fitness < MAX_FITNESS)
        .values(fitness=Player.fitness + DAILY_FITNESS_RECOVERY)
    )
    db.session.execute(
        update(Player)
        .where(Player.injury_days > 0)
        .values(injury_days=Player.injury_days - 1)
    )

    if state.current_day % DEVELOPMENT_UPDATE_DAYS == 0:
        update_player_evolution()
        for team in Team.query.all():
            train_team_monthly(team)

    if state.current_day % DEVELOPMENT_UPDATE_DAYS == 0:
        generate_youth_players()
        process_mentoring_monthly(state)

    if state.current_day % DAYS_PER_WEEK == 0:
        update_market_values()
        generate_free_agents(state)
        from services import process_scouting_weekly

        process_scouting_weekly(state)

    if state.current_day % DAYS_PER_WEEK == 0:
        check_training_complaints(state)

    # --- Full transfer system: daily contract/loan/debt ticks ---
    try:
        from services import (
            activate_precontracts,
            auto_transfer_list_offers,
            expire_contracts,
            maybe_rival_bid,
            pay_installments_due,
            return_expired_loans,
        )
        expire_contracts(state)
        return_expired_loans(state)
        pay_installments_due(state)
        activate_precontracts(state)
        # NOTE: offer expiry stays in simulate_calendar (bulk fast-forward)
        # only. Expiring here would void pending offers during normal daily
        # play step-throughs.
        if state.current_day % DAYS_PER_WEEK == 0:
            auto_transfer_list_offers(state)
            maybe_rival_bid(state)
    except Exception:
        logger.exception("transfer system daily tick failed")

    simulate_ai_transfers(state)


def update_player_evolution() -> None:
    players = Player.query.all()
    for p in players:
        growth_boost = 0
        decay_boost = 0

        if p.team_id:
            team = db.session.get(Team, p.team_id)
            if team:
                staff = team.get_staff_bonuses()
                growth_boost = staff["attack"] + staff["defense"]
                decay_boost = staff["fitness"] * 0.5

        if p.age < 24:
            if p.attack < p.potential:
                gain = random.randint(0, 1) + (1 if random.random() < (growth_boost * 0.05) else 0)
                p.attack = min(p.potential, p.attack + gain)
            if p.defense < p.potential:
                gain = random.randint(0, 1) + (1 if random.random() < (growth_boost * 0.05) else 0)
                p.defense = min(p.potential, p.defense + gain)
        elif p.age >= 30:
            # FM aging curve: physical attributes crater while technical hold.
            severity = max(1, int((p.age - 30) ** 1.5))
            decline_chance = min(0.95, severity / 40.0 + 0.15)
            if random.random() < decline_chance:
                loss = max(1, severity // 6)
                p.attack = max(30, p.attack - random.randint(0, loss))
                p.defense = max(30, p.defense - random.randint(0, loss))

        # --- FM: aging curve (physical vs technical split) ---
        if p.age >= PHYSICAL_DECLINE_AGE:
            for key in ("speed", "stamina"):
                loss = random.randint(1, PHYSICAL_DECLINE_MAX)
                setattr(p, key, max(30, (getattr(p, key) or 50) - loss))
            if random.random() < 0.3:
                p.fitness = max(30, p.fitness - random.randint(1, 2))
        elif p.age >= TECHNICAL_DECLINE_AGE:
            pass  # technical decline below

        if p.age >= TECHNICAL_DECLINE_AGE:
            if random.random() < decline_chance:
                p.technique = max(30, (p.technique or 50) - random.randint(0, TECHNICAL_DECLINE_MAX))
        elif 24 <= p.age < TECHNICAL_DECLINE_AGE:
            if random.random() < 0.15:
                p.technique = min(99, (p.technique or 50) + 1)


def generate_youth_players() -> None:
    from procedural import generate_player_name, generate_player_stats

    player_team = Team.query.filter_by(is_player_team=True).first()
    if not player_team:
        return

    level = player_team.youth_facilities or 3
    if random.random() > (level * YOUTH_GENERATION_CHANCE):
        return

    if len(player_team.players) >= MAX_SQUAD_SIZE:
        return

    pos = random.choices(["GK", "DEF", "MID", "FWD"], weights=[1, 3, 3, 2], k=1)[0]
    region = player_team.players[0].region if player_team.players else random.choice(["Europe", "Middle_East", "South_America"])

    tier_level = min(2, max(1, level // 3 + 1))
    stats = generate_player_stats(tier_level=tier_level, position=pos, region=region)
    new_name = generate_player_name(region)

    youth = Player(
        name=f"{new_name} (شباب)",
        position=pos,
        attack=stats["attack"],
        defense=stats["defense"],
        potential=stats["potential"],
        age=YOUTH_ACADEMY_AGE,
        height=stats["height"],
        weight=stats["weight"],
        personality=stats["personality"],
        region=region,
        market_value=stats["market_value"],
        team_id=player_team.id,
        is_starting=False,
    )
    db.session.add(youth)

    state = GameState.query.first()
    msg = Message(
        title="لاعب شاب جديد",
        content=f"انضم {new_name} ({pos}) من أكاديمية الشباب إلى الفريق!",
        type="youth",
        created_day=state.current_day if state else 0,
    )
    db.session.add(msg)


# --------------------------------------------------------------------------
# Season lifecycle (cleanup, Tip 13)
# --------------------------------------------------------------------------
def cleanup_old_data() -> None:
    """Garbage collect read messages + stale played match records (Tip 13)."""
    Message.query.filter_by(read=True).delete()

    recent_ids = [
        row[0]
        for row in db.session.query(Match.id)
        .filter(Match.played.is_(True))
        .order_by(Match.id.desc())
        .limit(OLD_DATA_KEEP_PLAYED_MATCHES)
    ]

    if recent_ids:
        stale = db.session.query(Match.id).filter(
            Match.played.is_(True), ~Match.id.in_(recent_ids)
        )
        db.session.query(PlayerRating).filter(PlayerRating.match_id.in_(stale)).delete(
            synchronize_session=False
        )
        db.session.query(Match).filter(
            Match.played.is_(True), ~Match.id.in_(recent_ids)
        ).delete(synchronize_session=False)
    else:
        played_ids = db.session.query(Match.id).filter(Match.played.is_(True))
        db.session.query(PlayerRating).filter(PlayerRating.match_id.in_(played_ids)).delete(
            synchronize_session=False
        )
        db.session.query(Match).filter(Match.played.is_(True)).delete(synchronize_session=False)


def process_season_end(state: GameState) -> None:
    from procedural import generate_player_name, generate_player_stats

    # Phase 3 (item 15): settle performance bonuses BEFORE stats reset.
    # FFP ledgers reset for the new season.
    try:
        from services import check_performance_bonuses
        check_performance_bonuses(state)
    except Exception:
        logger.exception("performance bonuses settlement failed")
    for team in Team.query.all():
        team.season_spend = 0
        team.season_income = 0

    players = Player.query.all()
    retired_count = 0
    for p in players:
        if p.age > YOUTH_RETIREMENT_AGE:
            retirement_chance = (p.age - YOUTH_RETIREMENT_AGE) * 0.15
            if random.random() < retirement_chance:
                region = p.region
                pos = p.position
                team_id = p.team_id

                db.session.delete(p)
                retired_count += 1

                new_name = generate_player_name(region)
                stats = generate_player_stats(tier_level=2, position=pos, region=region)

                regen = Player(
                    name=f"{new_name} (Regen)",
                    position=pos,
                    attack=stats["attack"],
                    defense=stats["defense"],
                    potential=stats["potential"],
                    age=REGEN_AGE_MIN + random.randint(0, REGEN_AGE_RANGE),
                    height=stats["height"],
                    weight=stats["weight"],
                    personality=stats["personality"],
                    region=region,
                    market_value=stats["market_value"],
                    team_id=team_id,
                    is_starting=False,
                )
                db.session.add(regen)

    if SEASON_RESET_PLAYER_STATS:
        db.session.execute(
            update(Player).values(goals=0, assists=0, matches_played=0)
        )
        db.session.execute(
            update(Team).values(youth_goals=0)
        )

    # FM: evaluate board objectives and sprinkle new free agents.
    for msg in check_board_objectives(state):
        db.session.add(msg)
    for _ in range(3):
        generate_free_agents(state)

    cleanup_old_data()
    logger.info("Season ended: %s players retired and regens created.", retired_count)


def process_promotion_relegation(state: GameState) -> None:
    leagues = League.query.order_by(League.tier).all()
    if len(leagues) < 2:
        return

    messages_to_send = []

    # Snapshot every final table BEFORE moving anyone. The old code sorted
    # and moved one league pair at a time: SQLAlchemy autoflush made the
    # just-moved clubs visible to the next pair's query, and their zeroed
    # points dropped them to the bottom — so PL relegated sides fell through
    # Championship/League One straight into League Two in a single summer.
    tables: dict[int, list] = {}
    for league in leagues:
        tables[league.id] = Team.table_sort_pipeline(
            Team.query.filter_by(league_id=league.id).all()
        )

    # Plan all moves first: each club moves at most one division per season.
    # moves maps team.id -> (destination league, origin league, direction).
    moves: dict[int, tuple] = {}
    for i in range(len(leagues) - 1):
        upper_league = leagues[i]
        lower_league = leagues[i + 1]
        upper_teams = tables.get(upper_league.id, [])
        lower_teams = tables.get(lower_league.id, [])
        if not upper_teams or not lower_teams:
            continue
        spots = min(PROMOTION_RELEGATION_SPOTS, len(upper_teams), len(lower_teams))
        if spots <= 0:
            continue
        for team in upper_teams[-spots:]:
            if team.id not in moves:
                moves[team.id] = (lower_league, upper_league, "down")
        for team in lower_teams[:spots]:
            if team.id not in moves:
                moves[team.id] = (upper_league, lower_league, "up")

    for team_id, (dest_league, origin_league, direction) in moves.items():
        team = db.session.get(Team, team_id)
        if team is None:
            continue
        team.league_id = dest_league.id
        team.points = 0
        team.wins = 0
        team.losses = 0
        team.draws = 0
        team.goals_for = 0
        team.goals_against = 0

        if direction == "down":
            team.morale = max(20, team.morale - 15)
            if team.is_player_team:
                messages_to_send.append(Message(
                    title="هبوط الفريق!",
                    content=f"هبط فريقك {team.name} من {origin_league.name} إلى {dest_league.name}.",
                    type="season", urgent=True, created_day=state.current_day
                ))
                state.trophies = max(0, (state.trophies or 0))
        else:
            team.morale = min(100, team.morale + 20)
            # Higher divisions pay more: tier 1 (PL) earns the biggest bonus.
            team.budget += PROMOTION_BONUS_PER_TIER * (5 - dest_league.tier)
            if team.is_player_team:
                messages_to_send.append(Message(
                    title="صعود الفريق!",
                    content=f"صعد فريقك {team.name} من {origin_league.name} إلى {dest_league.name}! مكافأة مالية.",
                    type="season", urgent=True, created_day=state.current_day
                ))
                state.trophies = (state.trophies or 0) + 1

    if not messages_to_send:
        top_side = Team.table_sort_pipeline(
            Team.query.filter_by(league_id=leagues[0].id).all()
        )
        winner = top_side[0] if top_side else None
        if winner and winner.is_player_team:
            messages_to_send.append(Message(
                title="بطولة الدوري!",
                content=f"تهانينا! فاز فريقك {winner.name} ببطولة الدوري!",
                type="season", urgent=True, created_day=state.current_day
            ))
            state.trophies = (state.trophies or 0) + 1

    for msg in messages_to_send:
        db.session.add(msg)

    process_season_end(state)


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------
def is_match_day(state: GameState) -> bool:
    return state.current_day % DAYS_PER_WEEK == 0


def expire_transfer_offers(state: GameState) -> int:
    """Expire pending offers past their deadline (background fast-forward).

    A long simulation that runs past an offer's ``deadline_day`` must NOT
    leave the squad in limbo or force moves in/out. Expired offers become
    ``rejected`` so the squad is untouched — the transfer window simply
    closes with no business done. The caller owns commit.
    """
    try:
        current = state.current_day or 0
        expired = (
            TransferOffer.query.filter(
                TransferOffer.status == "pending",
                TransferOffer.deadline_day.isnot(None),
                TransferOffer.deadline_day < current,
            ).all()
        )
        for offer in expired:
            offer.status = "rejected"
        return len(expired)
    except Exception:
        logger.exception("expire_transfer_offers failed")
        return 0


def _match_involves_player(match: Match) -> bool:
    try:
        home = match.home_team
        away = match.away_team
        return bool(
            (home is not None and home.is_player_team)
            or (away is not None and away.is_player_team)
        )
    except Exception:
        return False


def simulate_calendar(days: int, state: GameState, background: bool = False) -> dict:
    """Fast-forward ``days`` days.

    ``background=False`` (default) keeps the classic behaviour used by
    ``/continue``: the manager's own fixture is left unplayed and the
    fast-forward stops at the manager's match day so it can be played live.

    ``background=True`` (used by ``/simulate_to_date``) auto-plays EVERY
    fixture — including the manager's — with the same engines a normal
    match uses (live auto engine for the manager's club, ``match_director``
    for the rest), updating the table, player stats, fatigue and the
    manager record. Expired transfer offers are rejected so a long
    fast-forward past any event (e.g. a transfer window) closes it with no
    moves in or out of the squad.
    """
    res = {"matches_played": 0, "expired_offers": 0}
    # Lazy import: live_match imports from simulation at call time, so a
    # top-level import here would be circular.
    run_auto = None
    if background:
        try:
            from live_match import run_auto_match as run_auto
        except Exception:
            run_auto = None
    for _ in range(days):
        simulate_day(state)
        res["expired_offers"] += expire_transfer_offers(state)
        if is_match_day(state):
            matches = Match.query.filter_by(week=state.current_week, played=False).all()
            if background:
                for m in matches:
                    if m.played:
                        continue
                    # Per-match SAVEPOINT: one bad fixture (e.g. a rare
                    # integrity error) rolls back to here instead of
                    # poisoning the whole multi-week batch transaction.
                    savepoint = db.session.begin_nested()
                    try:
                        if _match_involves_player(m) and run_auto is not None:
                            if run_auto(m):
                                res["matches_played"] += 1
                            update_manager_record(m, state)
                        elif match_director(m, state):
                            res["matches_played"] += 1
                        savepoint.commit()
                    except Exception:
                        try:
                            savepoint.rollback()
                        except Exception:
                            pass
                        logger.exception(
                            "Background match %s failed", getattr(m, "id", "?")
                        )
                state.current_week += 1
                continue
            player_pending = get_current_match(state)
            for m in matches:
                if player_pending and m.id == player_pending.id:
                    continue  # FM: leave the manager's fixture for live play
                match_director(m, state)
                res["matches_played"] += 1
            if player_pending and not player_pending.played:
                break  # stop the fast-forward at the manager's match day
            state.current_week += 1

    # Single batch commit for the whole simulated stretch (Tip 8).
    db.session.commit()
    return res


def get_current_match(state) -> Optional[Match]:
    return Match.query.filter_by(week=state.current_week, played=False).join(
        Team, Match.home_team_id == Team.id
    ).filter(Team.is_player_team.is_(True)).first() or \
        Match.query.filter_by(week=state.current_week, played=False).join(
            Team, Match.away_team_id == Team.id
        ).filter(Team.is_player_team.is_(True)).first()


def get_recent_results(state, league_id: Optional[int] = None) -> list:
    query = Match.query.filter(Match.played.is_(True))
    if league_id:
        query = query.join(Team, Match.home_team_id == Team.id).filter(Team.league_id == league_id)
    return query.order_by(Match.id.desc()).limit(10).all()


def get_messages() -> list:
    return Message.query.all()


def get_unread_count() -> int:
    try:
        return Message.query.filter_by(read=False).count()
    except Exception:
        return 0


def mark_message_read(mid) -> None:
    m = db.session.get(Message, mid)
    if m:
        m.read = True
        db.session.commit()


def get_match_report(mid) -> Optional[dict]:
    m = db.session.get(Match, mid)
    if not m:
        return None

    home_ratings = db.session.query(PlayerRating).join(Player).filter(
        PlayerRating.match_id == mid,
        Player.team_id == m.home_team_id,
    ).all()

    away_ratings = db.session.query(PlayerRating).join(Player).filter(
        PlayerRating.match_id == mid,
        Player.team_id == m.away_team_id,
    ).all()

    home_events = m.home_events or []
    away_events = m.away_events or []

    home_events = sorted(home_events, key=lambda x: x.get("minute", 0))
    away_events = sorted(away_events, key=lambda x: x.get("minute", 0))

    return {
        "match": m,
        "home": m.home_team,
        "away": m.away_team,
        "home_events": home_events,
        "away_events": away_events,
        "home_possession": m.home_possession,
        "away_possession": m.away_possession,
        "home_ratings": home_ratings,
        "away_ratings": away_ratings,
    }


def get_pending_actions(state) -> dict:
    return {"match_day": is_match_day(state), "messages": get_unread_count()}


def get_top_scorers(league_id: int, limit: int = 20) -> list:
    return db.session.query(
        Player.id, Player.name, Player.position, Player.team_id,
        db.func.sum(PlayerRating.goals).label('total_goals'),
        db.func.count(PlayerRating.id).label('matches'),
    ).join(Team, Player.team_id == Team.id
    ).join(PlayerRating, PlayerRating.player_id == Player.id
    ).join(Match, PlayerRating.match_id == Match.id
    ).filter(
        Team.league_id == league_id,
        Match.played.is_(True),
    ).group_by(Player.id
    ).order_by(db.desc('total_goals')
    ).limit(limit).all()


def get_top_assists(league_id: int, limit: int = 20) -> list:
    return db.session.query(
        Player.id, Player.name, Player.position, Player.team_id,
        db.func.sum(PlayerRating.assists).label('total_assists'),
        db.func.count(PlayerRating.id).label('matches'),
    ).join(Team, Player.team_id == Team.id
    ).join(PlayerRating, PlayerRating.player_id == Player.id
    ).join(Match, PlayerRating.match_id == Match.id
    ).filter(
        Team.league_id == league_id,
        Match.played.is_(True),
    ).group_by(Player.id
    ).order_by(db.desc('total_assists')
    ).limit(limit).all()


def train_team_monthly(team: Team) -> None:
    focus = team.training_focus or "balanced"
    intensity = team.training_intensity or 5
    staff = team.get_staff_bonuses()
    facilities_level = team.training_facilities or 3

    players = Player.query.filter_by(team_id=team.id).all()
    for p in players:
        if p.age > YOUTH_RETIREMENT_AGE:
            continue
        growth_chance = 0.3 + (facilities_level * 0.05) + (intensity * 0.03) + staff["attack"] * 0.02
        if p.age < 24:
            growth_chance += 0.2

        if focus == "attack" and p.position in ["FWD", "MID"]:
            if p.attack < p.potential and random.random() < growth_chance:
                p.attack = min(p.potential, p.attack + random.randint(0, 2))
        elif focus == "defense" and p.position in ["DEF", "GK", "MID"]:
            if p.defense < p.potential and random.random() < growth_chance:
                p.defense = min(p.potential, p.defense + random.randint(0, 2))
        elif focus == "physical":
            p.fitness = min(100, p.fitness + intensity)
            p.stamina = min(100, (p.stamina or 50) + random.randint(1, 3))
        else:
            if p.attack < p.potential and random.random() < growth_chance:
                p.attack = min(p.potential, p.attack + random.randint(0, 1))
            if p.defense < p.potential and random.random() < growth_chance:
                p.defense = min(p.potential, p.defense + random.randint(0, 1))

    db.session.commit()