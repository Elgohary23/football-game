"""Deeper-sim tests: rules engine, tactics, tiered engines, philosophy AI."""
from models import db, GameState, Match, Team


def _player_team():
    return Team.query.filter_by(is_player_team=True).first()


def _opponent():
    return Team.query.filter_by(is_player_team=False).first()


def _spawn_team(league, name):
    """AI club with a legal 15-man squad (11 starting) for engine tests."""
    from models import Player
    team = Team(name=name, morale=70, budget=1000000, reputation=60,
                league_id=league.id)
    db.session.add(team)
    db.session.flush()
    positions = ["GK"] + ["DEF"] * 4 + ["MID"] * 6 + ["FWD"] * 4
    for i, pos in enumerate(positions):
        db.session.add(Player(
            name=f"{name} P{i}", position=pos, detailed_position=pos,
            attack=65, defense=65, overall=65, potential=80, age=24,
            height=180, weight=75, market_value=100000, team_id=team.id,
            is_starting=(i < 11), stamina=80, technique=60, speed=60))
    db.session.flush()
    return team


def _tier_fixture(league, week=30, tag="A"):
    """One unplayed AI-vs-AI fixture inside ``league``."""
    home = _spawn_team(league, f"{league.name} {tag} H")
    away = _spawn_team(league, f"{league.name} {tag} A")
    m = Match(home_team_id=home.id, away_team_id=away.id, week=week, played=False)
    db.session.add(m)
    db.session.commit()
    return m


class TestRulesEngine:
    def test_valid_xi_passes(self, ctx):
        from rules_engine import validate_starting_xi
        team = _player_team()
        # Seed world starts 11 (incl. 2 GKs); normalize to a valid XI first.
        gks = [p for p in team.players if p.position == "GK"]
        others = [p for p in team.players if p.position != "GK"]
        for p in team.players:
            p.is_starting = False
            p.injury_days = 0
            p.discipline_data = None
        for p in [gks[0]] + others[:10]:
            p.is_starting = True
        ok, errs = validate_starting_xi(team, "league")
        assert ok is True and errs == []

    def test_xi_requires_11_and_gk(self, ctx):
        from rules_engine import validate_starting_xi
        team = _player_team()
        for p in team.players[5:]:
            p.is_starting = False
        ok, errs = validate_starting_xi(team, "league")
        assert ok is False and errs

    def test_injured_unselectable(self, ctx):
        from rules_engine import validate_starting_xi
        team = _player_team()
        starter = next(p for p in team.players if p.is_starting)
        starter.injury_days = 5
        ok, errs = validate_starting_xi(team, "league")
        assert ok is False

    def test_suspended_unselectable(self, ctx):
        from rules_engine import validate_starting_xi
        team = _player_team()
        starter = next(p for p in team.players if p.is_starting)
        starter.discipline_data = {"yellows": 0, "suspensions": {"league": 1}}
        ok, _ = validate_starting_xi(team, "league")
        assert ok is False

    def test_sub_windows(self, ctx):
        from rules_engine import validate_substitution
        state = {"minute": 10,
                 "home_xp": {"on_pitch": [1], "bench": [2], "subs": 0, "sub_windows": 3}}
        ok, msg = validate_substitution(state, "home_xp", 2, 1)
        assert ok is False and "نوافذ" in msg
        state["home_xp"]["sub_windows"] = 0
        ok, _ = validate_substitution(state, "home_xp", 2, 1)
        assert ok is True
        # Halftime is a free window.
        state2 = {"minute": 45,
                  "home_xp": {"on_pitch": [1], "bench": [2], "subs": 4, "sub_windows": 3}}
        ok, _ = validate_substitution(state2, "home_xp", 2, 1)
        assert ok is True

    def test_auto_pick_compliant(self, ctx):
        from rules_engine import auto_pick_xi, validate_starting_xi
        team = _opponent()
        pick = auto_pick_xi(team, "league")
        assert len(pick["starters"]) == 11
        assert sum(1 for p in pick["starters"] if p.position == "GK") == 1

    def test_no_foreigner_cap_in_xi(self, ctx):
        """European lineups are unrestricted by nationality (Bosman)."""
        from rules_engine import validate_starting_xi
        team = _player_team()
        gks = [p for p in team.players if p.position == "GK"]
        others = [p for p in team.players if p.position != "GK"]
        for p in team.players:
            p.is_starting = False
            p.injury_days = 0
            p.discipline_data = None
            p.nationality = "South_America"
            p.region = "South_America"
        for p in [gks[0]] + others[:10]:
            p.is_starting = True
        ok, errs = validate_starting_xi(team, "league")
        assert ok is True and errs == []

    def test_auto_pick_ignores_nationality(self, ctx):
        """The best XI is picked on ability, not passport."""
        from models import League, Player
        from rules_engine import auto_pick_xi

        league = League(name="Bosman League", tier=1, reputation=70,
                        country="England")
        db.session.add(league)
        db.session.flush()
        team = Team(name="Bosman FC", morale=70, league_id=league.id)
        db.session.add(team)
        db.session.flush()

        def _add(name, rating, nation):
            db.session.add(Player(
                name=name, position="MID", detailed_position="CM",
                attack=rating, defense=rating, overall=rating,
                potential=rating, age=25, nationality=nation, region=nation,
                team_id=team.id, is_starting=False))

        db.session.add(Player(name="Dom GK", position="GK", overall=60,
                              attack=30, defense=60, age=25,
                              nationality="England", region="Europe",
                              team_id=team.id, is_starting=True))
        for i in range(8):  # clearly the best outfield players
            _add(f"Star {i}", 90, "South_America")
        for i in range(7):
            _add(f"Local {i}", 70, "England")
        db.session.flush()

        pick = auto_pick_xi(team, "league")
        assert len(pick["starters"]) == 11
        assert sum(1 for p in pick["starters"] if p.position == "GK") == 1
        foreign = [p for p in pick["starters"] if p.nationality == "South_America"]
        # The old 5-foreigner cap used to bench three of the eight stars.
        assert len(foreign) == 8

    def test_bulk_property_no_violations(self, ctx):
        """Bulk auto-picks: 11 starters, 1 GK, no injured/suspended."""
        import random
        from rules_engine import auto_pick_xi
        rng = random.Random(7)
        for team in Team.query.all():
            outfield = [p for p in team.players if p.position != "GK"]
            for p in team.players:
                p.injury_days = 0
                p.discipline_data = None
            # Fixed light injuries: exactly 2 outfield players per team.
            for p in outfield[:2]:
                p.injury_days = 3
            pick = auto_pick_xi(team, "league", rng)
            assert len(pick["starters"]) == 11
            assert sum(1 for p in pick["starters"] if p.position == "GK") == 1
            assert all(p.injury_days == 0 for p in pick["starters"])

    def test_discipline_accrual(self, ctx):
        from rules_engine import apply_discipline, get_suspension_state
        from models import Player
        p = Player.query.first()
        p.discipline_data = None
        for _ in range(5):
            apply_discipline(p, "league", yellow=True)
        assert get_suspension_state(p).get("league", 0) >= 1


class TestTactics:
    def test_profile_keys(self, ctx):
        from tactics import derive_team_profile
        prof = derive_team_profile(_player_team())
        for k in ("attack_eff", "defense_eff", "possession_base", "chance_quality",
                  "press_fatigue_mult", "foul_rate_mult", "offside_risk"):
            assert k in prof

    def test_pressing_moves_profile(self, ctx):
        from tactics import derive_team_profile
        team = _player_team()
        base = dict(team.tactics_data or {})
        t1 = {**base, "out_of_possession": {**(base.get("out_of_possession") or {}),
                                            "pressing_intensity": 1}}
        t2 = {**base, "out_of_possession": {**(base.get("out_of_possession") or {}),
                                            "pressing_intensity": 4}}
        team.tactics_data = t1
        low = derive_team_profile(team)
        team.tactics_data = t2
        high = derive_team_profile(team)
        assert high["press_fatigue_mult"] > low["press_fatigue_mult"]

    def test_mentality_moves_attack(self, ctx):
        from tactics import derive_team_profile
        team = _player_team()
        base = dict(team.tactics_data or {})
        team.tactics_data = {**base, "mentality": 1}
        d = derive_team_profile(team)
        team.tactics_data = {**base, "mentality": 5}
        a = derive_team_profile(team)
        assert a["attack_eff"] > d["attack_eff"]


class TestPhilosophyAI:
    def test_coaching_comes_from_philosophy(self, ctx):
        from ai_coaching import coaching_base
        team = _opponent()
        base = coaching_base(team)
        assert "primary" in base and "mentality" in base
        # Different philosophies -> different mentality targets.
        from config import PHILOSOPHY_COACHING
        assert PHILOSOPHY_COACHING["park_the_bus"]["mentality"] < \
            PHILOSOPHY_COACHING["gegenpress"]["mentality"]

    def test_checkpoint_derives_from_philosophy(self, ctx):
        from ai_coaching import decide_checkpoint
        team = _opponent()
        on = [p for p in team.players if p.is_starting][:11]
        bench = [p for p in team.players if not p.is_starting][:5]
        d = decide_checkpoint(None, "away_xp", 60, -1, team, on, bench, {})
        assert "reason" in d and isinstance(d["reason"], str) and d["reason"]

    def test_weekly_planner_touches_only_ai(self, ctx):
        from ai_coaching import weekly_planner_report
        weekly_planner_report()
        db.session.commit()
        assert _player_team().is_player_team is True

    def test_transfer_valuation_bounded(self, ctx):
        from ai_coaching import transfer_valuation
        team = _opponent()
        p = team.players[0]
        v = transfer_valuation(p, team)
        assert v >= 0


class TestSimLevels:
    def test_routing(self, ctx):
        from sim_levels import get_sim_level
        m = Match.query.filter_by(played=False).first()
        assert get_sim_level(m) == 1  # involves player team

    def test_routing_levels_by_tier(self, ctx):
        """L1 = manager's league, L2 = same tier abroad, L3 = other tiers."""
        from models import League
        from sim_levels import get_sim_level
        state = GameState.query.first()
        my_league = League.query.filter_by(tier=1).first()
        same_tier = League(name="Abroad Tier 1", tier=1, reputation=70,
                           country="Germany")
        lower = League(name="Abroad Tier 3", tier=3, reputation=40,
                       country="Germany")
        db.session.add_all([same_tier, lower])
        db.session.flush()

        m_mine = _tier_fixture(my_league, week=30, tag="M")
        m_same = _tier_fixture(same_tier, week=30, tag="S")
        m_low = _tier_fixture(lower, week=30, tag="L")

        assert get_sim_level(m_mine, state.user_league_id, 1) == 1
        assert get_sim_level(m_same, state.user_league_id, 1) == 2
        assert get_sim_level(m_low, state.user_league_id, 1) == 3

    def test_match_director_mode_by_tier(self, ctx, monkeypatch):
        """Deep for my league, medium for my tier, quick for the rest."""
        import simulation
        from models import League
        from simulation import match_director
        state = GameState.query.first()
        my_league = League.query.filter_by(tier=1).first()
        same_tier = League(name="Abroad Tier 1", tier=1, reputation=70,
                           country="Germany")
        lower = League(name="Abroad Tier 3", tier=3, reputation=40,
                       country="Germany")
        db.session.add_all([same_tier, lower])
        db.session.flush()

        m_mine = _tier_fixture(my_league, week=31, tag="M")
        m_same = _tier_fixture(same_tier, week=31, tag="S")
        m_low = _tier_fixture(lower, week=31, tag="L")

        calls = {}

        def _fake(match, game_state=None, mode="quick"):
            calls[match.id] = mode
            match.played = True
            return True

        monkeypatch.setattr(simulation, "simulate_match", _fake)
        for m in (m_mine, m_same, m_low):
            match_director(m, state)

        assert calls[m_mine.id] == "deep"
        assert calls[m_same.id] == "medium"
        assert calls[m_low.id] == "quick"
        assert m_mine.engine_level == 1
        assert m_same.engine_level == 2
        assert m_low.engine_level == 3

    def test_finish_week_remaining_uses_all_levels(self, ctx):
        """Post-match completion routes L1/L2/L3 instead of running all-deep."""
        from models import League
        from sim_levels import finish_week_remaining
        state = GameState.query.first()
        my_league = League.query.filter_by(tier=1).first()
        same_tier = League(name="Abroad Tier 1", tier=1, reputation=70,
                           country="Germany")
        lower = League(name="Abroad Tier 3", tier=3, reputation=40,
                       country="Germany")
        db.session.add_all([same_tier, lower])
        db.session.flush()

        m_mine = _tier_fixture(my_league, week=32, tag="M")
        m_same = _tier_fixture(same_tier, week=32, tag="S")
        m_low = _tier_fixture(lower, week=32, tag="L")

        played = finish_week_remaining(state, week=32)
        assert played == 3
        for m in (m_mine, m_same, m_low):
            assert m.played is True
        assert m_mine.engine_level == 1
        assert m_same.engine_level == 2
        assert m_low.engine_level == 3

    def test_l2_l3_deterministic(self, ctx):
        from sim_levels import engine_l2, engine_l3
        import random
        m = Match.query.filter_by(played=False).first()
        hp = {"attack_eff": 70, "defense_eff": 65, "possession_base": 55, "chance_quality": 1.0}
        ap = {"attack_eff": 60, "defense_eff": 62, "possession_base": 45, "chance_quality": 1.0}
        r1 = engine_l2(m, hp, ap, random.Random(42))
        r2 = engine_l2(m, hp, ap, random.Random(42))
        assert r1["home_score"] == r2["home_score"]
        r3 = engine_l3(m, hp, ap, random.Random(42))
        assert r3["level"] == 3

    def test_l3_respects_strength_gap(self, ctx):
        from sim_levels import engine_l3
        import random
        m = Match.query.filter_by(played=False).first()
        giant = {"attack_eff": 95, "defense_eff": 90}
        minnow = {"attack_eff": 45, "defense_eff": 42}
        hs = sum(engine_l3(m, giant, minnow, random.Random(i))["home_score"]
                 for i in range(30))
        aws = sum(engine_l3(m, giant, minnow, random.Random(i))["away_score"]
                  for i in range(30))
        assert hs > aws

    def test_world_sim_rejects_when_player_fixture_unplayed(self, ctx):
        from sim_levels import simulate_world_week
        state = GameState.query.first()
        pending = Match.query.filter_by(week=state.current_week,
                                        played=False).all()
        assert any((m.home_team and m.home_team.is_player_team)
                   or (m.away_team and m.away_team.is_player_team)
                   for m in pending)
        res = simulate_world_week(state)
        assert res.get("skipped") is True
        assert state.current_week == 1

    def test_world_sim_advances_calendar_only_when_resolved(self, ctx):
        import random
        from sim_levels import simulate_world_week
        from simulation import simulate_match
        state = GameState.query.first()
        m = Match.query.filter_by(week=state.current_week,
                                  played=False).first()
        simulate_match(m, mode="quick")
        db.session.commit()
        date0, week0 = state.current_date, state.current_week
        random.seed(7)
        res = simulate_world_week(state)
        assert not res.get("skipped")
        assert res.get("played", 0) >= 0
        assert "top_scorers_this_week" in res and "upsets" in res
        assert "ai_transfers" in res and "managers_sacked" in res
        assert "continental_movers" in res
        from datetime import datetime, timedelta
        base = datetime.strptime(date0, "%Y-%m-%d").date()
        assert state.current_date == (base + timedelta(days=7)).isoformat()
        assert state.current_week == week0 + 1
        assert int(state.world_sim_last_week or 0) == state.current_week
        # Cooldown: same week again is rejected.
        res2 = simulate_world_week(state)
        assert res2.get("skipped") is True

    def test_world_sim_never_runs_during_live(self, ctx):
        from sim_levels import simulate_world_week
        from simulation import simulate_match
        state = GameState.query.first()
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="quick")
        db.session.commit()
        live = Match(home_team_id=m.home_team_id, away_team_id=m.away_team_id,
                     week=99, played=False, live_active=True)
        db.session.add(live)
        db.session.commit()
        state.current_week = 99
        state.world_sim_last_week = 98
        db.session.commit()
        res = simulate_world_week(state)
        assert res.get("skipped") is True
        db.session.delete(live)
        db.session.commit()

    def test_world_sim_inbox_and_board(self, ctx):
        import random
        from models import Message
        from sim_levels import simulate_world_week
        from simulation import simulate_match
        state = GameState.query.first()
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="quick")
        db.session.commit()
        # Force a drift scenario: player bottom of the table.
        pt = _player_team()
        pt.points, pt.wins = 0, 0
        opp = _opponent()
        opp.points, opp.wins = 9, 3
        conf0 = int(state.board_confidence or 60)
        n0 = Message.query.filter_by(type="world").count()
        random.seed(11)
        res = simulate_world_week(state)
        assert not res.get("skipped")
        assert Message.query.filter_by(type="world").count() > n0
        assert Message.query.filter_by(type="world").count() - n0 <= 3
        assert 0 <= int(state.board_confidence or 0) <= 100
        assert abs(int(state.board_confidence or 0) - conf0) <= 3

    def test_world_sim_ai_transfer_frequency(self, ctx):
        import random
        from models import League, Player
        from sim_levels import simulate_world_week
        from simulation import simulate_match
        state = GameState.query.first()
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="quick")
        db.session.commit()
        league = League.query.first()
        for name in ("WAI2", "WAI3"):
            t = Team(name=name, morale=70, budget=5000000, reputation=55,
                     is_player_team=False, league_id=league.id)
            db.session.add(t)
            db.session.flush()
            for i in range(15):
                db.session.add(Player(
                    name="%s P%d" % (name, i), position="MID",
                    detailed_position="CM", attack=60, defense=60,
                    overall=60, potential=70, age=24, height=180, weight=75,
                    market_value=100000, team_id=t.id, is_starting=(i < 11)))
        db.session.commit()
        total = 0
        for w in range(4, 12):
            state.current_week = w
            state.world_sim_last_week = w - 1
            for mm in Match.query.filter_by(week=w, played=False).all():
                mm.played = True
            db.session.commit()
            rr = simulate_world_week(state, rng=random.Random(2000 + w))
            assert not rr.get("skipped")
            total += len(rr.get("ai_transfers") or [])
        assert total >= 2  # >=1 per 4 weeks on average

    def test_auto_pick_endpoint(self, client):
        r = client.post("/formation/auto-pick")
        assert r.status_code in (200, 400)
