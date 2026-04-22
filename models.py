from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True)
    morale = db.Column(db.Integer, default=70)
    budget = db.Column(db.Integer, default=1000000)
    
    points = db.Column(db.Integer, default=0)
    goals_for = db.Column(db.Integer, default=0)
    goals_against = db.Column(db.Integer, default=0)
    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    draws = db.Column(db.Integer, default=0)
    
    is_player_team = db.Column(db.Boolean, default=False)
    players = db.relationship('Player', backref='team', lazy=True)


class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    position = db.Column(db.String(10))
    
    attack = db.Column(db.Integer)
    defense = db.Column(db.Integer)
    
    fitness = db.Column(db.Integer, default=100)
    injury_days = db.Column(db.Integer, default=0)
    
    is_starting = db.Column(db.Boolean, default=False)
    
    goals = db.Column(db.Integer, default=0)
    assists = db.Column(db.Integer, default=0)
    matches_played = db.Column(db.Integer, default=0)
    
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'))


class GameState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    current_day = db.Column(db.Integer, default=1)
    current_week = db.Column(db.Integer, default=1)
    season_over = db.Column(db.Boolean, default=False)


class Match(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    home_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    away_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    week = db.Column(db.Integer)
    played = db.Column(db.Boolean, default=False)
    result = db.Column(db.String(20), nullable=True)
    
    home_score = db.Column(db.Integer, default=0)
    away_score = db.Column(db.Integer, default=0)
    
    home_events = db.Column(db.Text, nullable=True)
    away_events = db.Column(db.Text, nullable=True)
    
    home_possession = db.Column(db.Integer, default=50)
    away_possession = db.Column(db.Integer, default=50)
    
    home_team = db.relationship('Team', foreign_keys=[home_team_id])
    away_team = db.relationship('Team', foreign_keys=[away_team_id])


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100))
    content = db.Column(db.Text)
    type = db.Column(db.String(20))
    read = db.Column(db.Boolean, default=False)
    created_day = db.Column(db.Integer)
    urgent = db.Column(db.Boolean, default=False)


class PlayerRating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    match_id = db.Column(db.Integer, db.ForeignKey('match.id'))
    rating = db.Column(db.Float)
    goals = db.Column(db.Integer, default=0)
    assists = db.Column(db.Integer, default=0)
    
    player = db.relationship('Player')


class TransferOffer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'))
    from_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    to_team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    amount = db.Column(db.Integer)
    status = db.Column(db.String(20), default='pending')
    created_day = db.Column(db.Integer)
    deadline_day = db.Column(db.Integer)
    
    player = db.relationship('Player', foreign_keys=[player_id])
    from_team = db.relationship('Team', foreign_keys=[from_team_id])
    to_team = db.relationship('Team', foreign_keys=[to_team_id])