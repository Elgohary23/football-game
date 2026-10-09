"""Backend tests: models, simulation engine, services, HTTP routes."""
import json

from models import (
    db,
    GameState,
    League,
    Match,
    Message,
    Player,
    PlayerRating,
    Staff,
    Team,
    TransferOffer,
)


def _player_team():
    return Team.query.filter_by(is_player_team=True).first()


def _opponent():
    return Team.query.filter_by(is_player_team=False).first()


def _state():
    return GameState.query.first()


# ---------------------------------------------------------------- models
class TestTeamStrength:
    def test_full_xi_positive(self, ctx):
        team = _player_team()
        att, dfn = team.get_strength()
        assert att > 0 and dfn > 0

    def test_empty_squad_returns_zero(self, ctx):
        team = Team(name="Empty FC", morale=70)
        db.session.add(team)
        db.session.flush()
        assert team.get_strength() == (0.0, 0.0)

    def test_no_starters_returns_minimum(self, ctx):
        from config import MIN_LINEUP_RATING

        team = _player_team()
        for p in team.players:
            p.is_starting = False
        att, dfn = team.get_strength()
        assert att == MIN_LINEUP_RATING and dfn == MIN_LINEUP_RATING

    def test_short_squad_penalty(self, ctx):
        team = _player_team()
        full_att, _ = team.get_strength()
        for p in team.players[5:]:
            p.is_starting = False
        short_att, _ = team.get_strength()
        assert short_att < full_att

    def test_injured_excluded(self, ctx):
        team = _player_team()
        full_att, _ = team.get_strength()
        starter = next(p for p in team.players if p.is_starting)
        starter.injury_days = 10
        injured_att, _ = team.get_strength()
        assert injured_att < full_att

    def test_leader_morale_bonus(self, ctx):
        from config import LEADER_MORALE_BONUS

        team = _player_team()
        base_att, _ = team.get_strength()
        starter = next(p for p in team.players if p.is_starting)
        starter.personality = "Leader"
        bonus_att, _ = team.get_strength()
        assert bonus_att >= base_att
        assert LEADER_MORALE_BONUS > 0

    def test_staff_bonuses_log_dampened(self, ctx):
        team = _player_team()
        db.session.add(
            Staff(name="Coach", role="fitness", level=2, salary=10000,
                  attack_bonus=1.0, defense_bonus=1.0, team_id=team.id)
        )
        db.session.flush()
        bonuses = team.get_staff_bonuses()
        import math

        assert bonuses["attack"] == pytest_approx(math.log1p(2.0))

    def test_get_lineup_strength_none(self, ctx):
        from simulation import get_lineup_strength

        assert get_lineup_strength(None) == (0.0, 0.0)

    def test_player_rating_helpers(self, ctx):
        p = Player.query.filter_by(position="FWD").first()
        assert p.primary_ability("FWD") == "attack"
        assert p.primary_ability("GK") == "defense"
        assert p.rating_for() in (p.attack, p.defense)
        assert "$" in p.value_label()


def pytest_approx(x):
    class _A:
        def __eq__(self, other):
            return abs(other - x) < 1e-6

    return _A()


# ------------------------------------------------------- league table
class TestLeagueTable:
    def test_home_win(self, ctx):
        from simulation import update_league_table

        home, away = _player_team(), _opponent()
        hp, ap = home.points, away.points
        update_league_table(home, away, 2, 0)
        assert home.points == hp + 3
        assert home.wins == 1 and away.losses == 1
        assert home.goals_for >= 2

    def test_away_win(self, ctx):
        from simulation import update_league_table

        home, away = _player_team(), _opponent()
        update_league_table(home, away, 0, 3)
        assert away.points == 3 and away.wins == 1 and home.losses == 1

    def test_draw(self, ctx):
        from simulation import update_league_table

        home, away = _player_team(), _opponent()
        update_league_table(home, away, 1, 1)
        assert home.points == 1 and away.points == 1
        assert home.draws == 1 and away.draws == 1

    def test_table_sorted(self, ctx):
        from services import get_league_table, invalidate_league_cache

        home, away = _player_team(), _opponent()
        home.points = 6
        away.points = 0
        db.session.commit()
        invalidate_league_cache()
        rows = get_league_table(home.league_id)
        assert rows[0].id == home.id


# ------------------------------------------------------- match sim
class TestSimulateMatch:
    def test_quick_mode(self, ctx):
        from simulation import simulate_match

        m = Match.query.filter_by(played=False).first()
        assert simulate_match(m, mode="quick") is True
        assert m.played is True
        assert m.result and "-" in m.result

    def test_idempotency(self, ctx):
        from simulation import simulate_match

        m = Match.query.filter_by(played=False).first()
        assert simulate_match(m, mode="quick") is True
        pts = m.home_team.points
        assert simulate_match(m, mode="quick") is False
        assert m.home_team.points == pts

    def test_medium_and_deep(self, ctx):
        from simulation import simulate_match

        teams = Team.query.all()
        for mode in ("medium", "deep"):
            m = Match(home_team_id=teams[0].id, away_team_id=teams[1].id,
                      week=99, played=False)
            db.session.add(m)
            db.session.flush()
            assert simulate_match(m, mode=mode) is True
            assert m.played is True

    def test_match_director_updates_manager_record(self, ctx):
        from simulation import match_director

        state = _state()
        m = Match.query.filter_by(played=False).first()
        w, l, d = state.manager_wins, state.manager_losses, state.manager_draws
        match_director(m, state)
        assert (state.manager_wins + state.manager_losses + state.manager_draws) == w + l + d + 1

    def test_match_report_and_ratings(self, ctx):
        from simulation import get_match_report, simulate_match

        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        report = get_match_report(m.id)
        assert report is not None
        assert report["match"].id == m.id
        assert get_match_report(999999) is None

    def test_top_scorers_assists_queries(self, ctx):
        from simulation import get_top_scorers, get_top_assists, simulate_match

        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        league_id = _player_team().league_id
        assert isinstance(get_top_scorers(league_id), list)
        assert isinstance(get_top_assists(league_id), list)


# ------------------------------------------------------- progression
class TestProgression:
    def test_simulate_day_increments(self, ctx):
        from simulation import simulate_day

        state = _state()
        day = state.current_day
        simulate_day(state)
        assert state.current_day == day + 1

    def test_is_match_day(self, ctx):
        from config import DAYS_PER_WEEK
        from simulation import is_match_day

        state = _state()
        state.current_day = DAYS_PER_WEEK
        assert is_match_day(state) is True
        state.current_day = 1
        assert is_match_day(state) is False

    def test_get_current_match(self, ctx):
        from simulation import get_current_match

        match = get_current_match(_state())
        assert match is not None

    def test_update_market_values_bounds(self, ctx):
        from config import MARKET_VALUE_MAX, MARKET_VALUE_MIN
        from simulation import update_market_values

        p = Player.query.first()
        p.goals = 10
        update_market_values()
        assert MARKET_VALUE_MIN <= p.market_value <= MARKET_VALUE_MAX

    def test_cleanup_old_data(self, ctx):
        from simulation import cleanup_old_data

        db.session.add(Message(title="t", content="c", type="info", read=True, created_day=1))
        db.session.commit()
        cleanup_old_data()
        db.session.commit()
        assert Message.query.filter_by(read=True).count() == 0

    def test_train_team_monthly(self, ctx):
        from simulation import train_team_monthly

        team = _player_team()
        team.training_focus = "attack"
        train_team_monthly(team)

    def test_generate_youth_and_retire(self, ctx):
        from simulation import generate_youth_players, process_season_end

        generate_youth_players()
        state = _state()
        old = Player.query.first()
        old.age = 40
        process_season_end(state)
        db.session.commit()

    def test_promotion_relegation_runs(self, ctx):
        from simulation import process_promotion_relegation

        l2 = League(name="Second Tier", tier=2, reputation=50)
        db.session.add(l2)
        db.session.flush()
        t = Team(name="Lower FC", morale=70, budget=500000, is_player_team=False, league_id=l2.id)
        db.session.add(t)
        db.session.commit()
        process_promotion_relegation(_state())
        db.session.commit()

    def test_relegation_drops_single_division(self, ctx):
        """Relegated clubs must land in the adjacent league, never skip one.

        Regression test: the old pair-by-pair loop let SQLAlchemy autoflush
        expose just-moved (zero-point) clubs to the next pair's query, so
        Premier League bottom sides cascaded straight into League Two.
        """
        from config import PROMOTION_RELEGATION_SPOTS
        from simulation import process_promotion_relegation

        leagues = []
        for name, tier in (("L1x", 1), ("L2x", 2), ("L3x", 3)):
            l = League(name=name, tier=tier, reputation=50)
            db.session.add(l)
            db.session.flush()
            leagues.append(l)
        size = 6
        for league in leagues:
            for j in range(size):
                db.session.add(Team(
                    name=f"{league.name}-T{j}", morale=70, budget=500000,
                    is_player_team=False, league_id=league.id,
                    points=100 - j * 10, goals_for=50 - j * 5, goals_against=10,
                ))
        db.session.commit()
        pl_bottom_ids = {
            t.id for t in Team.query.filter_by(league_id=leagues[0].id)
            .order_by(Team.points.asc()).limit(PROMOTION_RELEGATION_SPOTS).all()
        }
        process_promotion_relegation(_state())
        db.session.commit()
        for team_id in pl_bottom_ids:
            assert db.session.get(Team, team_id).league_id == leagues[1].id
        for league in leagues:
            assert Team.query.filter_by(league_id=league.id).count() == size


# ------------------------------------------------------- services
class TestServices:
    def test_budget_for_tier(self, ctx):
        from config import DEFAULT_BUDGET, TIER_BUDGETS
        from services import get_budget_for_tier

        assert get_budget_for_tier(1) == TIER_BUDGETS[1]
        assert get_budget_for_tier(999) == DEFAULT_BUDGET
        assert get_budget_for_tier(None) == DEFAULT_BUDGET

    def test_validate_formation(self, ctx):
        from services import validate_formation

        team = _player_team()
        ok, _ = validate_formation(team, {"1": {}, "2": {}})
        assert ok is True
        ok, _ = validate_formation(team, {})
        assert ok is False
        ok, _ = validate_formation(team, {str(i): {} for i in range(12)})
        assert ok is False

    def test_validate_formation_gk_rule(self, ctx):
        from services import validate_formation

        team = _player_team()
        ids = [p.id for p in team.players[:11]]
        gk = next(p for p in team.players if p.position == "GK")
        non_gk = [p for p in team.players if p.position != "GK"][:11]
        ok, msg = validate_formation(team, {str(p.id): {} for p in non_gk})
        assert ok is False and "حارس" in msg

    def test_apply_formation(self, ctx):
        from services import apply_formation

        team = _player_team()
        ids = [p.id for p in team.players[:5]]
        apply_formation(team, {str(i): {} for i in ids})
        assert sum(1 for p in team.players if p.is_starting) == 5

    def test_purchase_player(self, ctx):
        from services import purchase_player

        buyer, seller = _player_team(), _opponent()
        buyer.budget = 10_000_000
        target = next(p for p in seller.players)
        target.market_value = 100000
        ok, _ = purchase_player(buyer, target, day=1)
        assert ok is True
        assert target.team_id == buyer.id

    def test_purchase_insufficient_budget(self, ctx):
        from services import purchase_player

        buyer, seller = _player_team(), _opponent()
        buyer.budget = 1
        target = next(p for p in seller.players)
        target.market_value = 5_000_000
        ok, _ = purchase_player(buyer, target, day=1)
        assert ok is False

    def test_staff_hire_fire(self, ctx):
        from services import fire_staff_member, hire_staff_member

        team = _player_team()
        team.budget = 10_000_000
        s = Staff(name="S", role="fitness", level=1, salary=10000, team_id=None)
        db.session.add(s)
        db.session.flush()
        ok, _ = hire_staff_member(team, s)
        assert ok is True and s.team_id == team.id
        ok, _ = fire_staff_member(team, s)
        assert ok is True and s.team_id is None

    def test_offer_accept_reject(self, ctx):
        from services import process_offer_accept, process_offer_reject

        buyer, seller = _player_team(), _opponent()
        buyer.budget = 10_000_000
        target = next(p for p in seller.players)
        offer = TransferOffer(player_id=target.id, from_team_id=seller.id,
                             to_team_id=buyer.id, amount=100000,
                             status="pending", created_day=1, deadline_day=5)
        db.session.add(offer)
        db.session.flush()
        ok, _ = process_offer_accept(offer.id)
        assert ok is True
        offer2 = TransferOffer(player_id=target.id, from_team_id=seller.id,
                              to_team_id=buyer.id, amount=100000,
                              status="pending", created_day=1, deadline_day=5)
        db.session.add(offer2)
        db.session.flush()
        ok, _ = process_offer_reject(offer2.id)
        assert ok is True and offer2.status == "rejected"

    def test_calculate_team_tier(self, ctx):
        from services import calculate_team_tier

        data = calculate_team_tier(_player_team().id)
        assert "tier" in data and "rating" in data
        missing = calculate_team_tier(999999)
        assert missing["tier"] == 4

    def test_scout_display_rating(self, ctx):
        from services import get_display_rating

        team = _player_team()
        opp_player = _opponent().players[0]
        label = get_display_rating(opp_player, team)
        assert isinstance(label, str) and len(label) > 0


# ------------------------------------------------------- HTTP routes
class TestRoutes:
    def test_index_redirects_to_dashboard(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code in (301, 302)
        assert "/dashboard" in r.headers["Location"]

    def test_dashboard_ok(self, client):
        assert client.get("/dashboard").status_code == 200

    def test_squad_ok(self, client):
        r = client.get("/squad")
        assert r.status_code == 200
        assert "Player FC" in r.get_data(as_text=True)

    def test_formation_ok(self, client):
        assert client.get("/formation").status_code == 200

    def test_standings_ok(self, client):
        assert client.get("/standings").status_code == 200

    def test_calendar_ok(self, client):
        assert client.get("/calendar").status_code == 200

    def test_match_ok(self, client):
        assert client.get("/match").status_code == 200

    def test_stats_pages_ok(self, client):
        for url in ("/stats", "/stats/top-scorers", "/stats/top-assists"):
            assert client.get(url).status_code == 200, url

    def test_messages_and_inbox(self, client):
        assert client.get("/messages").status_code == 200
        assert client.get("/inbox_unread").status_code == 200

    def test_toggle_starter_success(self, client):
        with client.application.app_context():
            team = _player_team()
            bench = next((p for p in team.players if not p.is_starting and p.injury_days == 0), None)
            # make room: unset one starter
            if bench is None:
                starter = next(p for p in team.players if p.is_starting)
                starter.is_starting = False
                db.session.commit()
                bench = starter
            else:
                starter = next(p for p in team.players if p.is_starting)
                starter.is_starting = False
                db.session.commit()
            pid = bench.id
        r = client.post(f"/toggle_starter/{pid}")
        assert r.status_code == 200
        assert r.get_json()["success"] is True

    def test_toggle_starter_injured(self, client):
        with client.application.app_context():
            team = _player_team()
            p = team.players[0]
            p.injury_days = 5
            p.is_starting = False
            db.session.commit()
            pid = p.id
        r = client.post(f"/toggle_starter/{pid}")
        assert r.status_code == 400

    def test_toggle_starter_full_xi(self, client):
        with client.application.app_context():
            team = _player_team()
            for i, p in enumerate(team.players):
                p.is_starting = i < 11
                p.injury_days = 0
            db.session.commit()
            bench = team.players[11]
            pid = bench.id
        r = client.post(f"/toggle_starter/{pid}")
        assert r.status_code == 400

    def test_toggle_starter_get_rejected(self, client):
        # Mutations are POST-only: a GET must not toggle and must not
        # return a JSON success body (405, JSON for XHR callers).
        with client.application.app_context():
            team = _player_team()
            pid = team.players[0].id
        r = client.get(
            f"/toggle_starter/{pid}", headers={"X-Requested-With": "XMLHttpRequest"}
        )
        assert r.status_code == 405
        assert r.get_json()["error"] == "method not allowed"

    def test_save_formation_valid(self, client):
        with client.application.app_context():
            team = _player_team()
            gk = next(p for p in team.players if p.position == "GK")
            others = [p for p in team.players if p.position != "GK"][:10]
            ids = [gk.id] + [p.id for p in others]
        r = client.post("/formation/save", json={"positions": {str(i): {} for i in ids}})
        assert r.status_code == 200
        assert r.get_json()["success"] is True

    def test_save_formation_invalid(self, client):
        r = client.post("/formation/save", json={})
        assert r.status_code == 400

    def test_live_payload_exposes_sot(self, ctx):
        # Live UI renders shots as "total (on target)"; the payload must
        # carry the on-target counts the engine already tracks.
        from live_match import live_state_payload

        match = Match.query.filter_by(played=False).first()
        payload = live_state_payload(match)
        assert payload["home_sot"] == 0
        assert payload["away_sot"] == 0

    def test_advance_day(self, client):
        with client.application.app_context():
            day = _state().current_day
        r = client.post("/advance_day", follow_redirects=False)
        assert r.status_code in (200, 302)
        with client.application.app_context():
            assert _state().current_day >= day

    def test_match_report_missing_redirects(self, client):
        r = client.get("/match_report/999999", follow_redirects=False)
        assert r.status_code in (200, 302)

    def test_404_json_for_api(self, client):
        r = client.get("/api/no-such-thing", headers={"Accept": "application/json"})
        assert r.status_code == 404
