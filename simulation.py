"""Football Manager Simulation Engine.

This module handles match simulation, player statistics, game state progression,
calendar events, transfers, and player development.
"""

import json
import logging
import random
from typing import Optional

from models import db, Team, Player, Match, Message, PlayerRating, TransferOffer

logger = logging.getLogger(__name__)


def get_lineup_strength(team: Team) -> tuple[float, float]:
    """Calculate team strength based on starting XI.

    Applies penalty for incomplete squads and morale bonus.

    Args:
        team: The team to calculate strength for.

    Returns:
        Tuple of (attack_strength, defense_strength).
    """
    if not team or not team.players:
        return 0.0, 0.0

    starters = [
        p for p in team.players
        if p.is_starting and p.injury_days == 0
    ]

    if not starters:
        return 0.0, 0.0

    num_starters = len(starters)

    if num_starters < 11:
        penalty = num_starters / 11
        base_att = sum(p.attack for p in starters) * penalty
        base_def = sum(p.defense for p in starters) * penalty
    else:
        base_att = sum(p.attack for p in starters)
        base_def = sum(p.defense for p in starters)

    morale_bonus = team.morale // 20

    return (base_att / 11) + morale_bonus, (base_def / 11) + morale_bonus


def assign_scorers(team: Team, goals_count: int) -> None:
    """Distribute goals to scorers based on position weights.

    Forwards have 2x chance, midfielders have 1x chance.

    Args:
        team: The scoring team.
        goals_count: Number of goals to assign.
    """
    if goals_count <= 0 or not team or not team.players:
        return

    potential_scorers = [
        p for p in team.players
        if p.is_starting and p.position in ["FWD", "MID"]
    ]

    if not potential_scorers:
        goal_scorer = random.choice(team.players)
        goal_scorer.goals += goals_count
        return

    for _ in range(goals_count):
        weights = [2 if p.position == "FWD" else 1 for p in potential_scorers]
        scorer = random.choices(potential_scorers, weights=weights, k=1)[0]
        scorer.goals += 1


def update_league_table(home: Team, away: Team, h_score: int, a_score: int) -> None:
    """Update league statistics after a match.

    Args:
        home: Home team.
        away: Away team.
        h_score: Home team goals.
        a_score: Away team goals.
    """
    if not home or not away:
        logger.warning("Invalid teams in update_league_table")
        return

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


def generate_match_events(home_score: int, away_score: int, game_state) -> tuple[list, list]:
    """Generate match events like goals, cards, momentum changes."""
    home_events = []
    away_events = []
    
    for _ in range(home_score):
        minute = random.randint(1, 90)
        home_events.append({
            "type": "goal",
            "minute": minute,
            "description": f"هدف في الدقيقة {minute}"
        })
    
    for _ in range(away_score):
        minute = random.randint(1, 90)
        away_events.append({
            "type": "goal",
            "minute": minute,
            "description": f"هدف في الدقيقة {minute}"
        })
    
    for _ in range(random.randint(0, 3)):
        minute = random.randint(1, 90)
        home_events.append({
            "type": "card",
            "minute": minute,
            "description": f"بطاقة صفراء في الدقيقة {minute}"
        })
    
    for _ in range(random.randint(0, 3)):
        minute = random.randint(1, 90)
        away_events.append({
            "type": "card",
            "minute": minute,
            "description": f"بطاقة صفراء في الدقيقة {minute}"
        })
    
    return home_events, away_events


def calculate_player_ratings(match: Match, home: Team, away: Team, h_score: int, a_score: int) -> None:
    """Calculate and save player ratings for a match."""
    for player in home.players:
        if player.is_starting:
            base_rating = 5.0
            
            if player.position == "FWD":
                base_rating += (player.attack / 20)
            elif player.position == "MID":
                base_rating += ((player.attack + player.defense) / 40)
            elif player.position == "DEF":
                base_rating += (player.defense / 20)
            elif player.position == "GK":
                base_rating += (player.defense / 20)
            
            if player.goals > 0:
                base_rating += player.goals * 1.5
            
            if player.assists > 0:
                base_rating += player.assists * 1.0
            
            if player.fitness < 50:
                base_rating -= 1.0
            
            base_rating += random.uniform(-1, 1)
            base_rating = max(1.0, min(10.0, base_rating))
            
            rating = PlayerRating(
                player_id=player.id,
                match_id=match.id,
                rating=round(base_rating, 1),
                goals=player.goals,
                assists=player.assists
            )
            db.session.add(rating)
    
    for player in away.players:
        if player.is_starting:
            base_rating = 5.0
            
            if player.position == "FWD":
                base_rating += (player.attack / 20)
            elif player.position == "MID":
                base_rating += ((player.attack + player.defense) / 40)
            elif player.position == "DEF":
                base_rating += (player.defense / 20)
            elif player.position == "GK":
                base_rating += (player.defense / 20)
            
            if player.goals > 0:
                base_rating += player.goals * 1.5
            
            if player.assists > 0:
                base_rating += player.assists * 1.0
            
            if player.fitness < 50:
                base_rating -= 1.0
            
            base_rating += random.uniform(-1, 1)
            base_rating = max(1.0, min(10.0, base_rating))
            
            rating = PlayerRating(
                player_id=player.id,
                match_id=match.id,
                rating=round(base_rating, 1),
                goals=player.goals,
                assists=player.assists
            )
            db.session.add(rating)


def simulate_match(match: Match) -> bool:
    """Simulate a football match between two teams.

    Args:
        match: The match to simulate.

    Returns:
        True if successful, False otherwise.
    """
    try:
        home = db.session.get(Team, match.home_team_id)
        away = db.session.get(Team, match.away_team_id)

        if not home or not away:
            logger.error(f"Invalid teams for match {match.id}")
            return False

        h_att, h_def = get_lineup_strength(home)
        a_att, a_def = get_lineup_strength(away)

        home_score = max(
            0,
            int((h_att * 1.1 - a_def) / 4) + random.randint(-1, 2)
        )
        away_score = max(
            0,
            int((a_att - h_def) / 4) + random.randint(-1, 2)
        )

        home_events, away_events = generate_match_events(home_score, away_score, None)
        
        home_possession = random.randint(40, 60)
        away_possession = 100 - home_possession

        match.result = f"{home_score}-{away_score}"
        match.played = True
        match.home_score = home_score
        match.away_score = away_score
        match.home_events = json.dumps(home_events)
        match.away_events = json.dumps(away_events)
        match.home_possession = home_possession
        match.away_possession = away_possession

        update_league_table(home, away, home_score, away_score)
        
        for p in home.players:
            p.goals = 0
            p.assists = 0
        
        for p in away.players:
            p.goals = 0
            p.assists = 0
        
        assign_scorers(home, home_score)
        assign_scorers(away, away_score)
        
        calculate_player_ratings(match, home, away, home_score, away_score)

        all_players = home.players + away.players
        for p in all_players:
            if p.is_starting:
                p.fitness = max(0, p.fitness - 10)
                p.matches_played += 1

        db.session.commit()
        logger.info(f"Match {match.id}: {match.result}")
        return True

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error simulating match: {e}")
        return False


def simulate_transfer_offers(game_state) -> None:
    """Simulate transfer offers for player team."""
    if not game_state:
        return
    
    player_team = Team.query.filter_by(is_player_team=True).first()
    if not player_team:
        return
    
    if random.random() < 0.3:
        starters = [p for p in player_team.players if p.is_starting]
        if starters:
            player = random.choice(starters)
            
            ai_teams = Team.query.filter_by(is_player_team=False).all()
            if ai_teams:
                buyer = random.choice(ai_teams)
                amount = player.attack * 50000 + random.randint(100000, 500000)
                deadline = game_state.current_day + random.randint(3, 7)
                
                offer = TransferOffer(
                    player_id=player.id,
                    from_team_id=player_team.id,
                    to_team_id=buyer.id,
                    amount=amount,
                    status='pending',
                    created_day=game_state.current_day,
                    deadline_day=deadline
                )
                db.session.add(offer)
                
                message = Message(
                    title="عرض انتقال",
                    content=f"فريق {buyer.name} يعرض {amount} دولار للتعاقد مع {player.name}",
                    type="transfer",
                    created_day=game_state.current_day,
                    urgent=False
                )
                db.session.add(message)
                db.session.commit()


def generate_injury_message(player: Player, game_state) -> None:
    """Generate injury message for a player."""
    severity = random.choice(["خفيف", "متوسط", "شديد"])
    days = random.randint(3, 15) if severity == "شديد" else random.randint(1, 7)
    
    message = Message(
        title=f"إصابة {player.name}",
        content=f"اللاعب {player.name} تعرض لإجمةة {severity}. سيغيب عن الملاعب لمدة {days} أيام.",
        type="injury",
        created_day=game_state.current_day,
        urgent=True if severity == "شديد" else False
    )
    db.session.add(message)
    db.session.commit()


def simulate_day(game_state) -> None:
    """Advance game by one day with player recovery.

    Args:
        game_state: Current game state.
    """
    if not game_state:
        return

    game_state.current_day += 1

    players = Player.query.all()
    for player in players:
        if player.fitness < 100:
            player.fitness = min(100, player.fitness + 5)

        if random.random() < 0.05 and player.injury_days > 0:
            player.injury_days = max(0, player.injury_days - 1)
        
        if random.random() < 0.02 and player.injury_days == 0:
            player.injury_days = random.randint(3, 10)
            generate_injury_message(player, game_state)
        
        if random.random() < 0.1 and player.injury_days == 0:
            player.attack = min(99, player.attack + 1)
            player.defense = min(99, player.defense + 1)

    teams = Team.query.all()
    for team in teams:
        if random.random() < 0.1:
            team.morale = min(100, team.morale + 2)
    
    simulate_transfer_offers(game_state)
    
    pending_offers = TransferOffer.query.filter_by(status='pending').all()
    for offer in pending_offers:
        if offer.deadline_day <= game_state.current_day:
            if random.random() < 0.5:
                offer.status = 'accepted'
                message = Message(
                    title="انتقال مقبول",
                    content=f"تم قبول عرض الانتقال بقيمة {offer.amount} دولار",
                    type="transfer",
                    created_day=game_state.current_day,
                    urgent=False
                )
                db.session.add(message)
            else:
                offer.status = 'rejected'
            db.session.commit()

    db.session.commit()


def is_match_day(game_state) -> bool:
    """Check if current day is a match day.

    Args:
        game_state: Current game state.

    Returns:
        True if match day, False otherwise.
    """
    if not game_state:
        return False
    return game_state.current_day % 7 == 0


def is_transfer_deadline(game_state) -> bool:
    """Check if current day is near transfer deadline."""
    if not game_state:
        return False
    return game_state.current_day % 30 == 25


def should_stop_calendar(game_state) -> tuple[bool, str]:
    """Check if calendar should stop for user intervention.
    
    Returns:
        Tuple of (should_stop, reason)
    """
    if is_match_day(game_state):
        return True, "match_day"
    
    if is_transfer_deadline(game_state):
        return True, "transfer_deadline"
    
    messages = Message.query.filter_by(read=False).all()
    urgent_messages = [m for m in messages if m.urgent and m.created_day == game_state.current_day]
    if urgent_messages:
        return True, "urgent_message"
    
    pending_offers = TransferOffer.query.filter_by(status='pending').all()
    player_team = Team.query.filter_by(is_player_team=True).first()
    player_offers = [o for o in pending_offers if o.from_team_id == player_team.id]
    if player_offers:
        for offer in player_offers:
            if offer.deadline_day - game_state.current_day <= 2:
                return True, "transfer_deadline_soon"
    
    return False, ""


def get_pending_actions(game_state) -> dict:
    """Get pending actions that need user intervention."""
    actions = {"match_day": False, "transfer_deadline": False, "messages": False}
    
    if is_match_day(game_state):
        actions["match_day"] = True
    
    if is_transfer_deadline(game_state):
        actions["transfer_deadline"] = True
    
    unread = Message.query.filter_by(read=False).count()
    if unread > 0:
        actions["messages"] = unread
    
    pending_offers = TransferOffer.query.filter_by(status='pending').count()
    if pending_offers > 0:
        actions["pending_offers"] = pending_offers
    
    return actions


def simulate_calendar(days: int, game_state) -> dict:
    """Simulate multiple days at once.
    
    Args:
        days: Number of days to simulate.
        game_state: Current game state.
        
    Returns:
        Dict with simulation results.
    """
    results = {
        "days_advanced": 0,
        "matches_played": 0,
        "stopped": False,
        "stop_reason": ""
    }
    
    for _ in range(days):
        simulate_day(game_state)
        
        if is_match_day(game_state):
            match = get_current_match(game_state)
            if match:
                simulate_match(match)
                results["matches_played"] += 1
                if game_state.current_week >= 10:
                    game_state.season_over = True
                    break
        
        should_stop, reason = should_stop_calendar(game_state)
        if should_stop:
            results["stopped"] = True
            results["stop_reason"] = reason
            break
        
        results["days_advanced"] += 1
    
    db.session.commit()
    return results


def get_current_match(game_state) -> Optional[Match]:
    """Get the current match for the week.

    Args:
        game_state: Current game state.

    Returns:
        The current match or None.
    """
    if not game_state:
        return None

    match = Match.query.filter_by(
        week=game_state.current_week,
        played=False
    ).first()

    return match


def get_recent_results(game_state) -> list:
    """Get recent match results.

    Args:
        game_state: Current game state.

    Returns:
        List of recent matches.
    """
    if not game_state:
        return []

    matches = Match.query.filter(
        Match.week < game_state.current_week,
        Match.played == True
    ).order_by(Match.week.desc()).limit(10).all()

    return matches


def get_match_report(match_id: int) -> Optional[dict]:
    """Get detailed match report.
    
    Args:
        match_id: The match ID.
        
    Returns:
        Dict with match details, events, and player ratings.
    """
    match = db.session.get(Match, match_id)
    if not match:
        return None
    
    home = match.home_team
    away = match.away_team
    
    home_events = json.loads(match.home_events) if match.home_events else []
    away_events = json.loads(match.away_events) if match.away_events else []
    
    home_ratings = PlayerRating.query.filter_by(match_id=match_id).join(Player).filter(Player.team_id == home.id).all()
    away_ratings = PlayerRating.query.filter_by(match_id=match_id).join(Player).filter(Player.team_id == away.id).all()
    
    return {
        "match": match,
        "home": home,
        "away": away,
        "home_events": home_events,
        "away_events": away_events,
        "home_possession": match.home_possession,
        "away_possession": match.away_possession,
        "home_ratings": home_ratings,
        "away_ratings": away_ratings
    }


def get_messages() -> list:
    """Get all messages, unread first."""
    return Message.query.order_by(Message.urgent.desc(), Message.read, Message.created_day.desc()).all()


def get_unread_count() -> int:
    """Get count of unread messages."""
    return Message.query.filter_by(read=False).count()


def mark_message_read(message_id: int) -> None:
    """Mark a message as read."""
    message = db.session.get(Message, message_id)
    if message:
        message.read = True
        db.session.commit()