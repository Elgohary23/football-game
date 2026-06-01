"""Football Manager Simulation Engine.

This module handles match simulation, player statistics, game state progression,
calendar events, transfers, and player development.
"""

import json
import logging
import random
from typing import Optional
import numpy as np

from models import db, Team, Player, Match, Message, PlayerRating, TransferOffer, GameState

logger = logging.getLogger(__name__)


def calculate_team_tier(team_id: int) -> dict:
    """
    Calculate team tier/rating without starting a game.
    Uses the best 11 players based on position-appropriate stats:
    - FWD/MID: attack stat
    - DEF/GK: defense stat
    """
    team = db.session.get(Team, team_id)
    if not team or not team.players:
        return {"tier": "D", "rating": 0, "attack": 0, "defense": 0, "squad_depth": 0}

    all_players = list(team.players)
    available = [p for p in all_players if p.injury_days == 0]

    def get_position_rating(player):
        if player.position in ["FWD", "MID"]:
            return player.attack
        return player.defense

    available.sort(key=get_position_rating, reverse=True)

    best_11 = available[:11]
    if len(best_11) < 11:
        return {"tier": "D", "rating": 0, "attack": 0, "defense": 0, "squad_depth": len(available)}

    attack = sum(p.attack for p in best_11) / 11
    defense = sum(p.defense for p in best_11) / 11
    overall = (attack + defense) / 2

    squad_depth = len([p for p in available if p not in best_11])

    if overall >= 75:
        tier = "S"
    elif overall >= 65:
        tier = "A"
    elif overall >= 55:
        tier = "B"
    elif overall >= 45:
        tier = "C"
    else:
        tier = "D"

    return {
        "tier": tier,
        "rating": round(overall, 1),
        "attack": round(attack, 1),
        "defense": round(defense, 1),
        "squad_depth": squad_depth,
        "best_11": best_11
    }


def get_lineup_strength(team: Team) -> tuple[float, float]:
    """Calculate team strength based on starting XI."""
    if not team or not team.players:
        return 0.0, 0.0

    starters = [
        p for p in team.players
        if p.is_starting and p.injury_days == 0
    ]

    if not starters:
        # Penalty for no starters
        return 10.0, 10.0

    num_starters = len(starters)
    penalty = 1.0
    if num_starters < 11:
        penalty = num_starters / 11

    base_att = sum(p.attack for p in starters) * penalty
    base_def = sum(p.defense for p in starters) * penalty

    # Personality Bonus
    leader_bonus = 0
    if any(p.personality == "Leader" for p in starters):
        leader_bonus = 5

    morale_bonus = (team.morale // 20) + leader_bonus

    return (base_att / 11) + morale_bonus, (base_def / 11) + morale_bonus


def match_director(match: Match, state: GameState) -> bool:
    home = match.home_team
    away = match.away_team
    
    if home.is_player_team or away.is_player_team:
        result = simulate_match_deep(match)
    elif state.user_league_id and home.league_id == state.user_league_id:
        result = simulate_match_medium(match)
    else:
        result = simulate_match_quick(match)
    
    update_manager_record(match, state)
    return result


def simulate_match_quick(match: Match) -> bool:
    """Abstract Macro Simulation using Poisson distribution."""
    home = match.home_team
    away = match.away_team
    
    h_att, h_def = get_lineup_strength(home)
    a_att, a_def = get_lineup_strength(away)
    
    # Expected goals based on strength ratios
    home_exp = max(0.2, (h_att / max(1, a_def)) * 1.3)
    away_exp = max(0.2, (a_att / max(1, h_def)) * 1.1)
    
    match.home_score = np.random.poisson(home_exp)
    match.away_score = np.random.poisson(away_exp)
    match.result = f"{match.home_score}-{match.away_score}"
    match.played = True
    
    update_league_table(home, away, match.home_score, match.away_score)
    db.session.commit()
    return True


def simulate_match_medium(match: Match) -> bool:
    """Medium Fidelity: Calculates scorers but ignores deep player ratings."""
    home = match.home_team
    away = match.away_team
    
    h_att, h_def = get_lineup_strength(home)
    a_att, a_def = get_lineup_strength(away)
    
    match.home_score = max(0, int((h_att * 1.1 - a_def) / 5) + random.randint(-1, 2))
    match.away_score = max(0, int((a_att - h_def) / 5) + random.randint(-1, 2))
    
    match.result = f"{match.home_score}-{match.away_score}"
    match.played = True
    
    assign_scorers(home, match.home_score)
    assign_scorers(away, match.away_score)
    
    update_league_table(home, away, match.home_score, match.away_score)
    db.session.commit()
    return True


def simulate_match_deep(match: Match) -> bool:
    """Micro-Fidelity: Detailed events, ratings, and fitness impact."""
    # Existing simulate_match logic with enhancements
    try:
        home = match.home_team
        away = match.away_team

        h_att, h_def = get_lineup_strength(home)
        a_att, a_def = get_lineup_strength(away)

        home_score = max(0, int((h_att * 1.1 - a_def) / 4) + random.randint(-1, 2))
        away_score = max(0, int((a_att - h_def) / 4) + random.randint(-1, 2))

        home_events, away_events = generate_match_events(home_score, away_score, None)
        
        match.home_possession = random.randint(40, 60)
        match.away_possession = 100 - match.home_possession

        match.result = f"{home_score}-{away_score}"
        match.played = True
        match.home_score = home_score
        match.away_score = away_score
        match.home_events = json.dumps(home_events)
        match.away_events = json.dumps(away_events)

        update_league_table(home, away, home_score, away_score)
        
        # Reset current match stats
        for p in home.players + away.players:
            p.goals = 0
            p.assists = 0
        
        assign_scorers(home, home_score)
        assign_scorers(away, away_score)
        
        calculate_player_ratings(match, home, away, home_score, away_score)

        # Fitness impact
        for p in home.players + away.players:
            if p.is_starting:
                decay = 10
                if p.personality == "Lazy": decay = 15
                if p.personality == "Professional": decay = 8
                p.fitness = max(0, p.fitness - decay)
                p.matches_played += 1

        db.session.commit()
        return True

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in deep simulation: {e}")
        return False


def assign_scorers(team: Team, goals_count: int) -> None:
    """Distribute goals based on weights."""
    if goals_count <= 0: return
    
    starters = [p for p in team.players if p.is_starting]
    if not starters: return
    
    for _ in range(goals_count):
        weights = []
        for p in starters:
            w = 1
            if p.position == "FWD": w = 10
            elif p.position == "MID": w = 3
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
    """Update standings."""
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


def generate_match_events(h_score, a_score, state) -> tuple[list, list]:
    h_ev = [{"type": "goal", "minute": random.randint(1, 90)} for _ in range(h_score)]
    a_ev = [{"type": "goal", "minute": random.randint(1, 90)} for _ in range(a_score)]
    return h_ev, a_ev


def calculate_player_ratings(match, home, away, h_score, a_score) -> None:
    for team in [home, away]:
        for p in team.players:
            if p.is_starting:
                rating = 6.0 + random.uniform(-1, 1)
                if p.goals > 0: rating += p.goals * 1.5
                rating = max(1.0, min(10.0, rating))
                db.session.add(PlayerRating(player_id=p.id, match_id=match.id, rating=round(rating, 1)))


def simulate_ai_transfers(state: GameState) -> None:
    """AI teams proactively fill squad gaps."""
    if state.current_day % 7 != 0: return # Only check weekly
    
    ai_teams = Team.query.filter_by(is_player_team=False).all()
    for team in ai_teams:
        # Check depth
        squad = Player.query.filter_by(team_id=team.id).all()
        positions_needed = []
        for pos, count in [("GK", 1), ("DEF", 4), ("MID", 4), ("FWD", 2)]:
            current = len([p for p in squad if p.position == pos])
            if current < count:
                positions_needed.append(pos)
        
        if positions_needed:
            # Look for a player to buy
            target_pos = random.choice(positions_needed)
            # Find a player in the market (e.g. from other teams or free agents)
            target = Player.query.filter(Player.team_id != team.id, Player.position == target_pos).order_by(db.func.random()).first()
            
            if target and target.market_value < team.budget:
                # Decide if to offer
                # Check reputation
                if team.reputation >= (target.team.reputation if target.team else 0) - 10:
                    amount = int(target.market_value * random.uniform(0.9, 1.2))
                    
                    # Create Offer
                    offer = TransferOffer(
                        player_id=target.id,
                        from_team_id=target.team_id,
                        to_team_id=team.id,
                        amount=amount,
                        status='pending',
                        created_day=state.current_day,
                        deadline_day=state.current_day + 3
                    )
                    db.session.add(offer)
                    
                    if target.team and target.team.is_player_team:
                        msg = Message(
                            title="عرض انتقال وارد",
                            content=f"وصل عرض من {team.name} لشراء {target.name} مقابل {amount}",
                            type="transfer",
                            created_day=state.current_day,
                            urgent=True
                        )
                        db.session.add(msg)
    db.session.commit()

def simulate_day(state: GameState) -> None:
    state.current_day += 1
    # Recovery
    players = Player.query.all()
    for p in players:
        p.fitness = min(100, p.fitness + 5)
        if p.injury_days > 0:
            p.injury_days -= 1
            
    # Monthly evolution
    if state.current_day % 30 == 0:
        update_player_evolution()
        
    # AI Transfers
    simulate_ai_transfers(state)


def update_player_evolution() -> None:
    """Monthly player development/decay logic."""
    players = Player.query.all()
    for p in players:
        # Growth (16-24)
        if p.age < 24:
            if p.attack < p.potential:
                p.attack = min(p.potential, p.attack + random.randint(0, 1))
            if p.defense < p.potential:
                p.defense = min(p.potential, p.defense + random.randint(0, 1))
        # Decay (31+)
        elif p.age > 31:
            p.attack = max(30, p.attack - random.randint(0, 1))
            p.defense = max(30, p.defense - random.randint(0, 1))
            
    db.session.commit()


from procedural import generate_player_name, generate_player_stats

def process_season_end(state: GameState) -> None:
    """Handles retirement and regens at the end of a season."""
    players = Player.query.all()
    retired_count = 0
    for p in players:
        # Decision to retire
        if p.age > 33:
            retirement_chance = (p.age - 33) * 0.15
            if random.random() < retirement_chance:
                # Store info for Regen
                region = p.region
                pos = p.position
                team_id = p.team_id
                
                # Delete player (or mark as retired)
                db.session.delete(p)
                retired_count += 1
                
                # Create Regen
                new_name = generate_player_name(region)
                stats = generate_player_stats(tier_level=2, position=pos, region=region)
                
                regen = Player(
                    name=f"{new_name} (Regen)",
                    position=pos,
                    attack=stats["attack"],
                    defense=stats["defense"],
                    potential=stats["potential"],
                    age=16 + random.randint(0, 2),
                    height=stats["height"],
                    weight=stats["weight"],
                    personality=stats["personality"],
                    region=region,
                    market_value=stats["market_value"],
                    team_id=team_id,
                    is_starting=False
                )
                db.session.add(regen)
    
    db.session.commit()
    logger.info(f"Season ended: {retired_count} players retired and regens created.")


def is_match_day(state: GameState) -> bool:
    return state.current_day % 7 == 0


def simulate_calendar(days: int, state: GameState) -> dict:
    res = {"matches_played": 0}
    for _ in range(days):
        simulate_day(state)
        if is_match_day(state):
            matches = Match.query.filter_by(week=state.current_week, played=False).all()
            for m in matches:
                match_director(m, state)
                res["matches_played"] += 1
            state.current_week += 1
    return res


def get_current_match(state) -> Optional[Match]:
    return Match.query.filter_by(week=state.current_week, played=False).join(Team, Match.home_team_id == Team.id).filter(Team.is_player_team == True).first() or \
           Match.query.filter_by(week=state.current_week, played=False).join(Team, Match.away_team_id == Team.id).filter(Team.is_player_team == True).first()


def get_recent_results(state) -> list:
    return Match.query.filter(Match.played == True).order_by(Match.id.desc()).limit(10).all()


def get_messages() -> list:
    return Message.query.all()


def get_unread_count() -> int:
    try:
        return Message.query.filter_by(read=False).count()
    except Exception:
        return 0


def mark_message_read(mid) -> None:
    m = db.session.get(Message, mid)
    if m: m.read = True; db.session.commit()


def get_match_report(mid) -> Optional[dict]:
    m = db.session.get(Match, mid)
    if not m: return None
    
    home_ratings = db.session.query(PlayerRating).join(Player).filter(
        PlayerRating.match_id == mid,
        Player.team_id == m.home_team_id
    ).all()
    
    away_ratings = db.session.query(PlayerRating).join(Player).filter(
        PlayerRating.match_id == mid,
        Player.team_id == m.away_team_id
    ).all()
    
    return {
        "match": m,
        "home": m.home_team,
        "away": m.away_team,
        "home_events": json.loads(m.home_events) if m.home_events else [],
        "away_events": json.loads(m.away_events) if m.away_events else [],
        "home_possession": m.home_possession,
        "away_possession": m.away_possession,
        "home_ratings": home_ratings,
        "away_ratings": away_ratings
    }


def get_pending_actions(state) -> dict:
    return {"match_day": is_match_day(state), "messages": get_unread_count()}