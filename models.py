from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    attack = db.Column(db.Integer)
    defense = db.Column(db.Integer)
    morale = db.Column(db.Integer)
    is_player_team = db.Column(db.Boolean, default=False)

    players = db.relationship('Player', backref='team', lazy=True)


class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    attack = db.Column(db.Integer)
    defense = db.Column(db.Integer)
    stamina = db.Column(db.Integer)
    fitness = db.Column(db.Integer)
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

    home_team = db.relationship('Team', foreign_keys=[home_team_id])
    away_team = db.relationship('Team', foreign_keys=[away_team_id])