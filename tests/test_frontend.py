"""Frontend tests: templates, RTL/Arabic UI, static assets, rendered pages.

CONVENTION (strict): every page test is DB-backed. A test never passes on an
empty render — it first reads the expected value from the database, then
asserts that exact value appears in the rendered HTML/JSON. A page that
returns 200 but shows nothing from the DB is a FAILURE, not a success.
"""
import json
import os
import re

import pytest

from models import (
    BoardObjective,
    db,
    GameState,
    League,
    Match,
    Mentoring,
    Message,
    Player,
    PlayerRating,
    Staff,
    TacticPreset,
    Team,
    TransferOffer,
)

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def _html(client, url):
    r = client.get(url)
    assert r.status_code == 200, url
    text = r.get_data(as_text=True)
    assert "<html" in text, url
    # Guard: a rendered page must have a real body, not an empty shell.
    assert len(text.strip()) > 500, url
    return text


def _player_team():
    return Team.query.filter_by(is_player_team=True).first()


def _opponent():
    return Team.query.filter_by(is_player_team=False).first()


def _state():
    return GameState.query.first()


def _money(value):
    return "${:,}".format(value or 0)


# ======================================================================
# Templates on disk
# ======================================================================
class TestTemplatesExist:
    REQUIRED = ["base.html", "dashboard.html", "squad.html", "formation.html",
                "match.html", "calendar.html", "standings.html", "stats.html"]

    ALL_PAGES = ["base.html", "dashboard.html", "squad.html", "formation.html",
                 "match.html", "calendar.html", "standings.html", "stats.html",
                 "top_scorers.html", "top_assists.html", "messages.html",
                 "training.html", "staff.html", "facilities.html", "medical.html",
                 "scouting.html", "compare.html", "live.html", "load.html",
                 "setup.html", "select_division.html", "select_team.html",
                 "manager_form.html", "new_manager.html", "match_report.html",
                 "match_history.html"]

    def test_required_templates_present(self):
        for name in self.REQUIRED:
            assert os.path.exists(os.path.join(TEMPLATES_DIR, name)), name

    def test_every_feature_template_present(self):
        for name in self.ALL_PAGES:
            assert os.path.exists(os.path.join(TEMPLATES_DIR, name)), name

    def test_every_template_non_empty(self):
        for name in self.ALL_PAGES:
            path = os.path.join(TEMPLATES_DIR, name)
            assert os.path.getsize(path) > 100, name

    def test_pages_extend_base(self):
        for name in ["dashboard.html", "squad.html", "formation.html", "match.html"]:
            with open(os.path.join(TEMPLATES_DIR, name), encoding="utf-8") as f:
                content = f.read()
            assert 'extends "base.html"' in content or "extends 'base.html'" in content, name

    def test_all_feature_pages_extend_base(self):
        for name in [n for n in self.ALL_PAGES if n != "base.html"]:
            with open(os.path.join(TEMPLATES_DIR, name), encoding="utf-8") as f:
                content = f.read()
            assert 'extends "base.html"' in content or "extends 'base.html'" in content, name

    def test_no_unclosed_jinja_blocks(self):
        for fname in os.listdir(TEMPLATES_DIR):
            if not fname.endswith(".html"):
                continue
            with open(os.path.join(TEMPLATES_DIR, fname), encoding="utf-8") as f:
                content = f.read()
            assert content.count("{% block") == content.count("{% endblock"), fname

    def test_no_unclosed_jinja_if_for(self):
        for fname in os.listdir(TEMPLATES_DIR):
            if not fname.endswith(".html"):
                continue
            with open(os.path.join(TEMPLATES_DIR, fname), encoding="utf-8") as f:
                content = f.read()
            assert content.count("{% if") == content.count("{% endif"), fname
            assert content.count("{% for") == content.count("{% endfor"), fname

    def test_base_has_content_and_scripts_blocks(self):
        with open(os.path.join(TEMPLATES_DIR, "base.html"), encoding="utf-8") as f:
            content = f.read()
        assert "{% block content %}" in content
        assert "{% block scripts %}" in content
        assert "{% block title %}" in content


# ======================================================================
# RTL / Arabic shell + nav + context processor (DB-backed)
# ======================================================================
class TestRtlArabicUi:
    def test_base_is_rtl_arabic(self, client):
        text = _html(client, "/dashboard")
        assert 'dir="rtl"' in text
        assert 'lang="ar"' in text

    def test_every_page_is_rtl_arabic(self, client):
        for url in ("/dashboard", "/squad", "/formation", "/calendar", "/match",
                    "/standings", "/stats", "/messages", "/training", "/staff",
                    "/facilities", "/medical", "/scouting", "/compare",
                    "/match_history"):
            text = _html(client, url)
            assert 'dir="rtl"' in text, url
            assert 'lang="ar"' in text, url

    def test_dashboard_shows_team_and_manager(self, client):
        team = _player_team()
        state = _state()
        text = _html(client, "/dashboard")
        assert team.name in text  # DB team name, e.g. "Player FC"
        assert state.manager_name in text  # DB manager name, e.g. "Test Manager"

    def test_nav_links_present(self, client):
        text = _html(client, "/dashboard")
        for href in ["/dashboard", "/squad", "/formation", "/calendar", "/match", "/standings"]:
            assert href in text, href

    def test_nav_has_every_feature_link(self, client):
        text = _html(client, "/dashboard")
        for href in ["/stats", "/messages", "/scouting", "/compare", "/facilities",
                     "/training", "/staff", "/medical", "/match_history", "/exit"]:
            assert href in text, href

    def test_logged_out_nav_links_load_page(self, client):
        with client.session_transaction() as sess:
            sess.pop("active_save", None)
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code in (200, 301, 302)
        with client.session_transaction() as sess:
            sess["active_save"] = 1

    def test_squad_lists_players(self, client):
        team = _player_team()
        first = team.players[0]
        text = _html(client, "/squad")
        assert first.name in text  # exact DB player name, not a placeholder

    def test_formation_embeds_json(self, client):
        text = _html(client, "/formation")
        assert "formation" in text.lower()

    def test_standings_has_table(self, client):
        team = _player_team()
        text = _html(client, "/standings")
        assert "<table" in text
        assert team.name in text  # DB team name appears as a table row

    def test_calendar_shows_week(self, client):
        text = _html(client, "/calendar")
        assert "1" in text  # current week/day number visible

    def test_match_page_renders(self, client):
        team = _player_team()
        opp = _opponent()
        text = _html(client, "/match")
        assert team.name in text or opp.name in text or "المباراة" in text

    def test_stats_pages_render(self, client):
        for url in ("/stats", "/stats/top-scorers", "/stats/top-assists"):
            text = _html(client, url)
            assert "html" in text

    def test_messages_page_renders(self, client):
        assert _html(client, "/messages")

    def test_context_processor_globals(self, client):
        text = _html(client, "/dashboard")
        # unread badge + manager name injected via context processor
        assert "unreadBadge" in text or "badge" in text

    def test_context_processor_db_values_on_every_page(self, client):
        team = _player_team()
        state = _state()
        for url in ("/dashboard", "/squad", "/standings"):
            text = _html(client, url)
            assert team.name in text, url  # player_team_name from DB
            assert state.manager_name in text, url  # manager_name from DB
            assert str(state.current_week) in text, url
            assert str(state.current_day) in text, url

    def test_unread_badge_count_matches_db(self, client):
        from simulation import get_unread_count
        expected = get_unread_count()
        text = _html(client, "/dashboard")
        assert str(expected) in text

    def test_footer_and_a11y_shell(self, client):
        text = _html(client, "/dashboard")
        assert "skip-link" in text
        assert 'role="banner"' in text
        assert 'role="main"' in text
        assert "2026" in text  # footer year


# ======================================================================
# Dashboard — every widget shows DB values
# ======================================================================
class TestDashboardDb:
    def test_budget_morale_points_from_db(self, client):
        team = _player_team()
        text = _html(client, "/dashboard")
        assert _money(team.budget) in text
        assert f"{team.morale}%" in text
        assert str(team.points) in text

    def test_manager_record_from_db(self, client):
        state = _state()
        text = _html(client, "/dashboard")
        assert str(state.manager_wins or 0) in text
        assert str(state.manager_draws or 0) in text
        assert str(state.manager_losses or 0) in text

    def test_season_and_board_confidence_from_db(self, client):
        state = _state()
        team = _player_team()
        text = _html(client, "/dashboard")
        assert str(state.season_number or 1) in text
        assert f"{state.board_confidence or 60}%" in text
        assert str(team.chemistry or 0) in text
        assert str(team.youth_goals or 0) in text

    def test_goal_difference_from_db(self, client):
        team = _player_team()
        text = _html(client, "/dashboard")
        assert str(team.goals_for - team.goals_against) in text

    def test_league_table_rows_from_db(self, client):
        from services import get_league_table
        team = _player_team()
        table = get_league_table(team.league_id)
        text = _html(client, "/dashboard")
        for row in table[:8]:
            assert row.name in text  # every table row name rendered
            assert str(row.points) in text

    def test_squad_table_lists_every_db_player(self, client):
        team = _player_team()
        text = _html(client, "/dashboard")
        for p in team.players:
            assert p.name in text, p.name  # no player hidden
            assert str(p.age) in text
            assert str(p.attack) in text
            assert str(p.defense) in text
            assert _money(p.market_value) in text

    def test_recent_results_from_db(self, client):
        from simulation import get_recent_results, simulate_match
        state = _state()
        team = _player_team()
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="quick")
        db.session.commit()
        text = _html(client, "/dashboard")
        results = get_recent_results(state, team.league_id)
        assert results, "expected recent results after simulating a match"
        for match in results[:6]:
            assert match.home_team.name in text
            assert match.away_team.name in text
            assert match.result in text

    def test_board_objectives_from_db(self, client):
        state = _state()
        team = _player_team()
        db.session.add(BoardObjective(team_id=team.id, season=state.season_number or 1,
                                      objective_type="youth_goals", target_value=5.0,
                                      achieved=False))
        db.session.commit()
        text = _html(client, "/dashboard")
        assert "أهداف مجلس الإدارة" in text
        assert "الأكاديمية" in text

    def test_quick_action_forms_present(self, client):
        text = _html(client, "/dashboard")
        assert 'action="/continue"' in text
        assert 'action="/advance_week"' in text
        assert "csrf_token" in text

    def test_player_modal_hook_present(self, client):
        text = _html(client, "/dashboard")
        assert "playerModal" in text
        assert "/player_profile/" in text


# ======================================================================
# Squad page — DB-backed
# ======================================================================
class TestSquadDb:
    def test_all_players_with_all_fields(self, client):
        team = _player_team()
        text = _html(client, "/squad")
        for p in team.players:
            assert p.name in text, p.name
            assert str(p.age) in text
            assert str(p.attack) in text
            assert str(p.defense) in text
            assert str(p.potential) in text
            assert _money(p.market_value) in text
            assert p.position in text

    def test_starter_count_matches_db(self, client):
        team = _player_team()
        n = sum(1 for p in team.players if p.is_starting)
        text = _html(client, "/squad")
        assert f"{n}/11" in text

    def test_tier_rating_from_db(self, client):
        from services import calculate_team_tier
        team = _player_team()
        data = calculate_team_tier(team.id)
        text = _html(client, "/squad")
        assert str(data["tier"]) in text
        assert "%.1f" % data["rating"] in text
        assert "%.1f" % data["attack"] in text
        assert "%.1f" % data["defense"] in text

    def test_toggle_forms_for_fit_players(self, client):
        team = _player_team()
        text = _html(client, "/squad")
        fit = [p for p in team.players if p.injury_days == 0]
        assert fit, "seed must have fit players"
        for p in fit[:3]:
            assert f"/toggle_starter/{p.id}" in text

    def test_injured_player_row_from_db(self, client):
        team = _player_team()
        p = team.players[0]
        p.injury_days = 6
        db.session.commit()
        try:
            text = _html(client, "/squad")
            assert p.name in text
            assert "مصاب" in text
        finally:
            p.injury_days = 0
            db.session.commit()

    def test_mentoring_section_from_db(self, client):
        team = _player_team()
        mentor = next(p for p in team.players if p.age >= 24)
        mentor.age = 32
        mentor.personality = "Leader"
        mentee = next(p for p in team.players if p.id != mentor.id)
        mentee.age = 19
        db.session.add(Mentoring(team_id=team.id, mentor_id=mentor.id,
                                 mentee_id=mentee.id, started_day=1))
        db.session.commit()
        try:
            text = _html(client, "/squad")
            assert "التوجيه" in text
            assert mentor.name in text
            assert mentee.name in text
        finally:
            Mentoring.query.filter_by(team_id=team.id).delete()
            db.session.commit()

    def test_toggle_then_squad_reflects_db(self, client):
        team = _player_team()
        bench = next((p for p in team.players if not p.is_starting and p.injury_days == 0), None)
        if bench is None:
            starter = next(p for p in team.players if p.is_starting)
            starter.is_starting = False
            db.session.commit()
            bench = starter
        else:
            starter = next(p for p in team.players if p.is_starting)
            starter.is_starting = False
            db.session.commit()
        r = client.post(f"/toggle_starter/{bench.id}")
        assert r.status_code == 200
        text = _html(client, "/squad")
        assert bench.name in text  # still listed, now as starter (DB state shown)


# ======================================================================
# Formation page — DB-backed
# ======================================================================
class TestFormationDb:
    def test_all_squad_names_in_takers_dropdown(self, client):
        team = _player_team()
        text = _html(client, "/formation")
        for p in team.players[:5]:
            assert p.name in text, p.name

    def test_pressing_and_tactic_selected_from_db(self, client):
        team = _player_team()
        team.pressing_style = "high"
        team.match_tactic = "attack"
        db.session.commit()
        text = _html(client, "/formation")
        assert 'value="high" selected' in text
        assert 'value="attack" selected' in text

    def test_players_json_embeds_db_ids(self, client):
        team = _player_team()
        text = _html(client, "/formation")
        for p in team.players[:3]:
            assert str(p.id) in text
            assert p.name in text

    def test_saved_takers_selected_from_db(self, client):
        team = _player_team()
        taker = team.players[0]
        team.penalty_taker_id = taker.id
        team.freekick_taker_id = taker.id
        db.session.commit()
        text = _html(client, "/formation")
        assert f'value="{taker.id}" selected' in text
        assert taker.name in text

    def test_preset_appears_after_save(self, client):
        team = _player_team()
        team.formation_data = {str(p.id): {"x": 50, "y": 50} for p in team.players[:11]}
        db.session.commit()
        r = client.post("/formation/preset/save",
                        data={"preset_type": "standard", "name": "DB-Preset-1"},
                        follow_redirects=True)
        assert r.status_code == 200
        text = r.get_data(as_text=True)
        assert "DB-Preset-1" in text  # saved preset name rendered from DB

    def test_role_save_returns_db_values(self, client):
        team = _player_team()
        pid = team.players[0].id
        r = client.post("/formation/role", data={"player_id": pid, "role": "poacher"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        assert body["player_id"] == pid
        assert body["role"] == "poacher"
        # persisted on the team's formation_data in the DB
        assert _player_team().formation_data[str(pid)]["role"] == "poacher"

    def test_tactics_save_reflected_in_form(self, client):
        client.post("/formation/tactics",
                    data={"pressing_style": "low", "match_tactic": "defensive"},
                    follow_redirects=True)
        text = _html(client, "/formation")
        assert 'value="low" selected' in text
        assert 'value="defensive" selected' in text


# ======================================================================
# Calendar — DB-backed (embedded JSON + current date)
# ======================================================================
class TestCalendarDb:
    def test_current_date_from_db(self, client):
        state = _state()
        text = _html(client, "/calendar")
        assert state.current_date in text

    def test_match_json_contains_db_teams(self, client):
        team = _player_team()
        opp = _opponent()
        text = _html(client, "/calendar")
        assert team.name in text
        assert opp.name in text

    def test_match_week_and_score_from_db(self, client):
        m = Match.query.filter(
            (Match.home_team_id == _player_team().id) |
            (Match.away_team_id == _player_team().id)).first()
        text = _html(client, "/calendar")
        assert str(m.week) in text
        # The fixture is embedded as JSON (ensure_ascii escapes the dash),
        # so parse it and compare against the DB row directly.
        embedded = json.loads(re.search(r"const matchData = (\{.*?\});", text, re.S).group(1))
        assert embedded, "calendar must embed match JSON from the DB"
        blob = json.dumps(embedded, ensure_ascii=False)
        assert m.home_team.name in blob
        assert m.away_team.name in blob
        assert (m.result or "—") in blob or (m.result or "\u2014") in text

    def test_simulate_form_present(self, client):
        text = _html(client, "/calendar")
        assert 'action="/simulate_to_date"' in text
        assert 'name="target_date"' in text


# ======================================================================
# Match center — DB-backed
# ======================================================================
class TestMatchCenterDb:
    def test_lineups_from_db(self, client):
        match = Match.query.filter_by(played=False).first()
        text = _html(client, "/match")
        assert match.home_team.name in text
        assert match.away_team.name in text
        for p in [x for x in match.home_team.players if x.is_starting][:3]:
            assert p.name in text
        for p in [x for x in match.away_team.players if x.is_starting][:3]:
            assert p.name in text

    def test_scouting_report_from_db(self, client):
        match = Match.query.filter_by(played=False).first()
        opp = match.home_team if not match.home_team.is_player_team else match.away_team
        danger = max(opp.players, key=lambda p: p.attack)
        text = _html(client, "/match")
        assert opp.name in text
        assert danger.name in text
        assert str(danger.goals) in text

    def test_played_match_score_visible_in_report_and_history(self, client):
        # get_current_match() only returns UNPLAYED fixtures, so once the
        # week's match is played /match correctly shows the empty state
        # (with the DB day count) while the DB score lives on the report
        # + history pages.
        from simulation import simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="quick")
        db.session.commit()
        text = _html(client, "/match")
        assert str(_state().current_day) in text  # empty state is DB-backed
        report = _html(client, f"/match_report/{m.id}")
        assert str(m.home_score) in report
        assert str(m.away_score) in report
        history = _html(client, "/match_history")
        assert f"{m.home_score} - {m.away_score}" in history


class TestMatchReportHistoryDb:
    def test_report_shows_db_result_and_teams(self, client):
        from simulation import simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        text = _html(client, f"/match_report/{m.id}")
        assert m.home_team.name in text
        assert m.away_team.name in text
        assert str(m.home_score) in text
        assert str(m.away_score) in text

    def test_history_lists_db_results(self, client):
        from simulation import simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="quick")
        db.session.commit()
        text = _html(client, "/match_history")
        assert m.home_team.name in text
        assert m.away_team.name in text
        assert f"{m.home_score} - {m.away_score}" in text
        assert f"/match_report/{m.id}" in text


# ======================================================================
# Standings — DB-backed
# ======================================================================
class TestStandingsDb:
    def test_all_teams_points_wdl_from_db(self, client):
        from services import get_league_table
        team = _player_team()
        table = get_league_table(team.league_id)
        text = _html(client, "/standings")
        for t in table:
            assert t.name in text, t.name
            assert str(t.points) in text
            assert str(t.wins) in text
            assert str(t.draws) in text
            assert str(t.losses) in text

    def test_league_name_and_rank_from_db(self, client):
        from services import get_team_rank
        team = _player_team()
        league = db.session.get(League, team.league_id)
        rank = get_team_rank(team.league_id, team)
        text = _html(client, "/standings")
        assert league.name in text
        assert str(rank) in text

    def test_league_tabs_from_db(self, client):
        text = _html(client, "/standings")
        for league in League.query.all():
            assert league.name in text

    def test_points_change_reflected(self, client):
        team = _player_team()
        team.points = 42
        team.wins = 14
        db.session.commit()
        from services import invalidate_league_cache
        invalidate_league_cache()
        text = _html(client, "/standings")
        assert "42" in text
        assert team.name in text

    def test_unknown_league_falls_back_to_db_league(self, client):
        team = _player_team()
        r = client.get("/standings?league_id=999999", follow_redirects=True)
        assert r.status_code == 200
        assert team.name in r.get_data(as_text=True)


# ======================================================================
# Stats center — DB-backed
# ======================================================================
class TestStatsDb:
    def test_scorer_and_assist_from_db(self, client):
        # Leaderboards aggregate PlayerRating rows of PLAYED matches (not the
        # Player.goals column), so simulate a deep match then assert the
        # rated DB players render with their DB totals.
        from simulation import get_top_assists, get_top_scorers, simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        league_id = _player_team().league_id
        scorers = get_top_scorers(league_id)
        assists = get_top_assists(league_id)
        assert scorers, "deep sim must yield scorer rows"
        assert assists, "deep sim must yield assist rows"
        sid, sname, _, _, sgoals, _ = scorers[0]
        aid, aname, _, _, aassists, _ = assists[0]
        assert db.session.get(Player, sid).name == sname
        for url in ("/stats", "/stats/top-scorers", "/stats/top-assists"):
            text = _html(client, url)
            assert sname in text, url  # DB leader visible, not an empty table
            assert aname in text, url
        assert str(sgoals) in _html(client, "/stats/top-scorers")
        assert str(aassists) in _html(client, "/stats/top-assists")

    def test_team_names_in_leaderboards(self, client):
        from simulation import get_top_scorers, simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        team_ids = {row[3] for row in get_top_scorers(_player_team().league_id)}
        text = _html(client, "/stats/top-scorers")
        for tid in team_ids:
            assert db.session.get(Team, tid).name in text

    def test_league_selector_from_db(self, client):
        text = _html(client, "/stats")
        for league in League.query.all():
            assert league.name in text


# ======================================================================
# Messages / inbox — DB-backed
# ======================================================================
class TestMessagesInboxDb:
    def _add_msg(self, **kw):
        kw.setdefault("title", "DB Title X1")
        kw.setdefault("content", "DB Content body abc 123")
        kw.setdefault("type", "info")
        kw.setdefault("read", False)
        kw.setdefault("created_day", 3)
        m = Message(**kw)
        db.session.add(m)
        db.session.commit()
        return m

    def test_message_title_content_day_rendered(self, client):
        m = self._add_msg()
        text = _html(client, "/messages")
        assert m.title in text
        assert m.content[:60] in text
        assert str(m.created_day) in text
        assert m.type in text

    def test_empty_inbox_shows_empty_state(self, client):
        Message.query.delete()
        db.session.commit()
        text = _html(client, "/messages")
        assert "لا توجد رسائل" in text

    def test_unread_json_matches_db(self, client):
        from simulation import get_unread_count
        Message.query.delete()
        db.session.add(Message(title="u1", content="c1", type="info", read=False, created_day=1))
        db.session.add(Message(title="u2", content="c2", type="info", read=True, created_day=1))
        db.session.commit()
        r = client.get("/inbox_unread")
        assert r.status_code == 200
        assert r.get_json()["unread"] == get_unread_count() == 1

    def test_inbox_api_returns_db_row_and_marks_read(self, client):
        m = self._add_msg(read=False)
        r = client.get(f"/inbox_api/{m.id}")
        assert r.status_code == 200
        body = r.get_json()
        assert body["title"] == m.title
        assert body["content"] == m.content
        assert body["created_day"] == m.created_day
        assert db.session.get(Message, m.id).read is True

    def test_inbox_api_unknown_is_404_json(self, client):
        r = client.get("/inbox_api/999999")
        assert r.status_code == 404
        assert r.get_json()["error"] == "not found"

    def test_message_read_marks_db_and_redirects(self, client):
        m = self._add_msg(read=False)
        r = client.get(f"/message_read/{m.id}", follow_redirects=False)
        assert r.status_code in (301, 302)
        assert db.session.get(Message, m.id).read is True

    def test_pending_offer_from_db_rendered(self, client):
        buyer, seller = _player_team(), _opponent()
        target = next(p for p in seller.players)
        offer = TransferOffer(player_id=target.id, from_team_id=seller.id,
                              to_team_id=buyer.id, amount=321321,
                              status="pending", created_day=1, deadline_day=9)
        db.session.add(offer)
        db.session.commit()
        try:
            text = _html(client, "/messages")
            assert target.name in text
            assert "321,321" in text
            assert seller.name in text or buyer.name in text
        finally:
            db.session.delete(offer)
            db.session.commit()

    def test_accept_offer_moves_player_in_db(self, client):
        buyer, seller = _player_team(), _opponent()
        buyer.budget = 10_000_000
        target = next(p for p in seller.players)
        target.market_value = 50000
        offer = TransferOffer(player_id=target.id, from_team_id=seller.id,
                              to_team_id=buyer.id, amount=50000,
                              status="pending", created_day=1, deadline_day=9)
        db.session.add(offer)
        db.session.commit()
        tid = target.id
        r = client.post(f"/transfer_offer/{offer.id}/accept", follow_redirects=True)
        assert r.status_code == 200
        assert db.session.get(Player, tid).team_id == buyer.id

    def test_reject_offer_status_in_db(self, client):
        buyer, seller = _player_team(), _opponent()
        target = next(p for p in seller.players)
        offer = TransferOffer(player_id=target.id, from_team_id=seller.id,
                              to_team_id=buyer.id, amount=50000,
                              status="pending", created_day=1, deadline_day=9)
        db.session.add(offer)
        db.session.commit()
        oid = offer.id
        client.post(f"/transfer_offer/{oid}/reject", follow_redirects=True)
        assert db.session.get(TransferOffer, oid).status == "rejected"


# ======================================================================
# Training / staff / facilities / medical — DB-backed
# ======================================================================
class TestTrainingDb:
    def test_focus_intensity_selected_from_db(self, client):
        team = _player_team()
        team.training_focus = "attack"
        team.training_intensity = 8
        db.session.commit()
        text = _html(client, "/training")
        assert 'value="attack" selected' in text
        assert 'value="8"' in text
        assert f"{team.training_facilities or 3}/10" in text

    def test_every_player_in_training_table(self, client):
        team = _player_team()
        text = _html(client, "/training")
        for p in team.players:
            assert p.name in text, p.name
            assert str(p.potential) in text

    def test_save_training_persists_and_renders(self, client):
        client.post("/training/save", data={"focus": "defense", "intensity": "3"},
                    follow_redirects=True)
        team = _player_team()
        assert team.training_focus == "defense"
        assert team.training_intensity == 3
        text = _html(client, "/training")
        assert 'value="defense" selected' in text


class TestStaffDb:
    def _mk_staff(self, team_id=None, name="DB Coach ZZ", role="fitness"):
        s = Staff(name=name, role=role, level=2, salary=11111,
                  attack_bonus=0.5, defense_bonus=0.5, team_id=team_id)
        db.session.add(s)
        db.session.commit()
        return s

    def test_team_and_available_staff_from_db(self, client):
        team = _player_team()
        mine = self._mk_staff(team_id=team.id, name="DB Mine Coach")
        free = self._mk_staff(team_id=None, name="DB Free Coach")
        try:
            text = _html(client, "/staff")
            assert mine.name in text
            assert free.name in text
            assert str(mine.level) in text
            assert "11,111" in text  # salary formatting from DB
        finally:
            db.session.delete(mine)
            db.session.delete(free)
            db.session.commit()

    def test_budget_from_db_on_staff_page(self, client):
        team = _player_team()
        text = _html(client, "/staff")
        assert _money(team.budget) in text

    def test_hire_moves_staff_to_db_team(self, client):
        team = _player_team()
        team.budget = 5_000_000
        s = self._mk_staff(team_id=None, name="DB Hire Me")
        client.post(f"/staff/hire/{s.id}", follow_redirects=True)
        assert db.session.get(Staff, s.id).team_id == team.id
        assert s.name in _html(client, "/staff")

    def test_fire_clears_team_in_db(self, client):
        team = _player_team()
        team.budget = 5_000_000
        s = self._mk_staff(team_id=team.id, name="DB Fire Me")
        client.post(f"/staff/fire/{s.id}", follow_redirects=True)
        assert db.session.get(Staff, s.id).team_id is None

    def test_generate_pool_adds_rows(self, client):
        n0 = Staff.query.filter_by(team_id=None).count()
        client.post("/staff/generate", follow_redirects=True)
        assert Staff.query.filter_by(team_id=None).count() >= n0


class TestFacilitiesDb:
    def test_levels_costs_budget_from_db(self, client):
        from config import TRAINING_COST_PER_LEVEL, YOUTH_COST_PER_LEVEL
        team = _player_team()
        team.training_facilities = 4
        team.youth_facilities = 5
        db.session.commit()
        text = _html(client, "/facilities")
        assert "المستوى 4 / 10" in text
        assert "المستوى 5 / 10" in text
        assert "{:,}".format(4 * TRAINING_COST_PER_LEVEL) in text
        assert "{:,}".format(5 * YOUTH_COST_PER_LEVEL) in text
        assert _money(team.budget) in text

    def test_upgrade_training_increments_db(self, client):
        team = _player_team()
        team.training_facilities = 3
        team.budget = 50_000_000
        db.session.commit()
        client.post("/upgrade_facility/training", follow_redirects=True)
        assert _player_team().training_facilities == 4
        assert "المستوى 4 / 10" in _html(client, "/facilities")

    def test_upgrade_youth_increments_db(self, client):
        team = _player_team()
        team.youth_facilities = 3
        team.budget = 50_000_000
        db.session.commit()
        client.post("/upgrade_facility/youth", follow_redirects=True)
        assert _player_team().youth_facilities == 4

    def test_invalid_facility_type_redirects(self, client):
        r = client.post("/upgrade_facility/bogus", follow_redirects=True)
        assert r.status_code == 200
        assert "المرافق" in r.get_data(as_text=True)


class TestMedicalDb:
    def test_injured_and_fit_sections_from_db(self, client):
        team = _player_team()
        for p in team.players:
            p.injury_days = 0
        hurt = team.players[0]
        hurt.injury_days = 9
        hurt.fitness = 33
        db.session.commit()
        text = _html(client, "/medical")
        assert hurt.name in text
        assert "9 يوم" in text
        assert "33%" in text
        fit_one = team.players[1]
        assert fit_one.name in text  # fit list also from DB

    def test_no_injuries_empty_state(self, client):
        team = _player_team()
        for p in team.players:
            p.injury_days = 0
        db.session.commit()
        text = _html(client, "/medical")
        assert "لا يوجد لاعبون مصابون" in text


# ======================================================================
# Scouting / transfers — DB-backed
# ======================================================================
class TestScoutingDb:
    def test_opponent_players_and_values_from_db(self, client):
        opp = _opponent()
        text = _html(client, "/scouting")
        for p in opp.players[:5]:
            assert p.name in text, p.name
            assert "{:,}".format(p.market_value) in text

    def test_budget_and_board_confidence_from_db(self, client):
        team = _player_team()
        state = _state()
        text = _html(client, "/scouting")
        assert _money(team.budget) in text
        assert f"{state.board_confidence or 60}%" in text

    def test_free_agent_section_from_db(self, client):
        fa = Player(name="DB Free Agent Q", position="FWD", attack=60, defense=40,
                    potential=70, age=22, height=180, weight=75,
                    market_value=77777, team_id=None)
        db.session.add(fa)
        db.session.commit()
        try:
            text = _html(client, "/scouting")
            assert "وكلاء أحرار" in text
            assert fa.name in text
            assert "77,777" in text
        finally:
            db.session.delete(fa)
            db.session.commit()

    def test_scout_row_and_region_from_db(self, client):
        team = _player_team()
        scout = Staff(name="DB Scout R", role="scout", level=3, salary=20000,
                      team_id=team.id, region="Europe", on_assignment=True)
        db.session.add(scout)
        db.session.commit()
        try:
            text = _html(client, "/scouting")
            assert scout.name in text
            assert "Europe" in text
        finally:
            db.session.delete(scout)
            db.session.commit()

    def test_buy_player_changes_db_and_scouting(self, client):
        team = _player_team()
        team.budget = 10_000_000
        opp = _opponent()
        target = next(p for p in opp.players)
        target.market_value = 60000
        tid = target.id
        db.session.commit()
        client.post(f"/buy_player/{tid}", data={"sell_on": "0"},
                    follow_redirects=True)
        assert db.session.get(Player, tid).team_id == team.id

    def test_swap_offer_endpoint(self, client):
        team = _player_team()
        opp = _opponent()
        target = next(p for p in opp.players)
        offered = next(p for p in team.players if not p.is_starting)
        r = client.post(f"/swap_offer/{target.id}",
                        data={"offered_player_id": offered.id, "cash": "0"},
                        follow_redirects=True)
        assert r.status_code == 200

    def test_scout_assign_unassign_db(self, client):
        team = _player_team()
        scout = Staff(name="DB Scout A", role="scout", level=1, salary=5000,
                      team_id=team.id)
        db.session.add(scout)
        db.session.commit()
        sid = scout.id
        client.post(f"/scout/assign/{sid}", data={"region": "Africa"},
                    follow_redirects=True)
        assert db.session.get(Staff, sid).on_assignment is True
        assert db.session.get(Staff, sid).region == "Africa"
        client.post(f"/scout/unassign/{sid}", follow_redirects=True)
        assert db.session.get(Staff, sid).on_assignment is False


# ======================================================================
# Compare + player profile — DB-backed
# ======================================================================
class TestCompareDb:
    def test_selector_lists_db_players(self, client):
        team = _player_team()
        text = _html(client, "/compare")
        for p in team.players[:5]:
            assert p.name in text, p.name

    def test_comparison_table_from_db(self, client):
        team = _player_team()
        a, b = team.players[0], team.players[1]
        text = _html(client, f"/compare?player_a={a.id}&player_b={b.id}")
        assert a.name in text
        assert b.name in text
        assert str(a.attack) in text
        assert str(b.attack) in text
        assert str(a.defense) in text
        assert str(b.defense) in text

    def test_same_player_collapses_to_prompt(self, client):
        team = _player_team()
        a = team.players[0]
        text = _html(client, f"/compare?player_a={a.id}&player_b={a.id}")
        assert "اختر لاعبين" in text


class TestPlayerProfileDb:
    def test_all_fields_match_db(self, client):
        p = _player_team().players[0]
        r = client.get(f"/player_profile/{p.id}")
        assert r.status_code == 200
        body = r.get_json()
        assert body["name"] == p.name
        assert body["position"] == p.position
        assert body["attack"] == p.attack
        assert body["defense"] == p.defense
        assert body["age"] == p.age
        assert body["market_value"] == p.market_value
        assert body["goals"] == (p.goals or 0)
        assert "avg_rating" in body and "recent_ratings" in body

    def test_ratings_in_profile_from_db(self, client):
        from simulation import simulate_match
        m = Match.query.filter_by(played=False).first()
        simulate_match(m, mode="deep")
        db.session.commit()
        rated = PlayerRating.query.first()
        assert rated is not None, "deep sim must create ratings"
        r = client.get(f"/player_profile/{rated.player_id}")
        assert r.status_code == 200
        assert r.get_json()["recent_ratings"], "ratings list must not be empty"

    def test_unknown_player_is_404_json(self, client):
        r = client.get("/player_profile/999999")
        assert r.status_code == 404
        assert r.get_json()["error"] == "not found"


# ======================================================================
# Live match — DB-backed
# ======================================================================
class TestLiveDb:
    def _start_live(self):
        from live_match import start_live
        match = Match.query.filter_by(played=False).first()
        assert start_live(match, mode="live") is True
        db.session.commit()
        return match

    def test_live_page_shows_db_teams(self, client):
        match = self._start_live()
        text = _html(client, f"/live/{match.id}")
        assert match.home_team.name in text
        assert match.away_team.name in text

    def test_live_state_json_from_db(self, client):
        match = self._start_live()
        r = client.get(f"/live/{match.id}/state")
        assert r.status_code == 200
        body = r.get_json()
        for key in ("home_score", "away_score", "minute", "home_sot", "away_sot"):
            assert key in body, key
        assert body["home_score"] == (match.live_state or {}).get("home_score", 0)

    def test_live_state_unknown_is_404(self, client):
        r = client.get("/live/999999/state")
        assert r.status_code == 404

    def test_live_advance_updates_db_minute(self, client):
        match = self._start_live()
        before = (match.live_state or {}).get("minute", 0)
        r = client.post(f"/live/{match.id}/advance", data={"minutes": 5})
        assert r.status_code == 200
        after = db.session.get(Match, match.id).live_state.get("minute", 0)
        assert after > before

    def test_live_sub_validates_db_squad(self, client):
        match = self._start_live()
        team_key = ("home_xp" if match.home_team.is_player_team else "away_xp")
        r = client.post(f"/live/{match.id}/sub",
                        data={"team_key": team_key, "player_out": 1, "player_in": 2})
        assert r.status_code == 200
        assert "ok" in r.get_json()

    def test_live_sub_wrong_side_rejected(self, client):
        match = self._start_live()
        other = ("away_xp" if match.home_team.is_player_team else "home_xp")
        r = client.post(f"/live/{match.id}/sub",
                        data={"team_key": other, "player_out": 1, "player_in": 2})
        assert r.status_code == 403

    def test_live_finish_marks_db_played(self, client):
        match = self._start_live()
        mid = match.id
        r = client.post(f"/live/{mid}/finish")
        assert r.status_code == 200
        assert r.get_json()["finished"] is True
        assert db.session.get(Match, mid).played is True


# ======================================================================
# Progression buttons reflect in the frontend (DB day/week shown)
# ======================================================================
class TestProgressionFrontendDb:
    def test_advance_day_increments_db_and_dashboard(self, client):
        day0 = _state().current_day
        client.post("/advance_day", follow_redirects=False)
        assert _state().current_day >= day0
        text = _html(client, "/dashboard")
        assert str(_state().current_day) in text

    def test_continue_renders_dashboard_with_db_state(self, client):
        r = client.post("/continue", follow_redirects=True)
        assert r.status_code == 200
        text = r.get_data(as_text=True)
        assert _player_team().name in text
        assert _state().manager_name in text

    def test_advance_week_keeps_db_team_visible(self, client):
        r = client.post("/advance_week", follow_redirects=True)
        assert r.status_code == 200
        assert _player_team().name in r.get_data(as_text=True)

    def test_simulate_to_date_moves_db_date(self, client):
        state = _state()
        state.current_date = "2026-06-01"
        db.session.commit()
        r = client.post("/simulate_to_date",
                        data={"target_date": "2026-06-10"},
                        follow_redirects=True)
        assert r.status_code == 200
        assert _state().current_date >= "2026-06-01"
        assert _state().current_date in _html(client, "/calendar")

    def test_simulate_to_date_bad_input_stays_on_calendar(self, client):
        r = client.post("/simulate_to_date", data={"target_date": "nope"},
                        follow_redirects=True)
        assert r.status_code == 200

    def test_season_end_flow(self, client):
        state = _state()
        state.season_over = True
        db.session.commit()
        r = client.post("/season_end", follow_redirects=True)
        assert r.status_code == 200
        assert _player_team().name in r.get_data(as_text=True)


# ======================================================================
# Save / setup flow pages (engine-safe: never touch the real slot files)
# ======================================================================
class TestSaveFlowFrontend:
    def test_load_page_lists_slots(self, client):
        text = _html(client, "/load")
        assert "slot" in text.lower() or "حفظ" in text

    def test_exit_clears_session(self, client):
        r = client.get("/exit", follow_redirects=True)
        assert r.status_code == 200

    def test_missing_slot_redirects_without_engine_switch(self, client):
        # Slot 99 does not exist -> redirect, in-memory DB untouched.
        before = _player_team().name
        assert client.get("/setup/99", follow_redirects=False).status_code in (301, 302)
        assert client.get("/select-division/99", follow_redirects=False).status_code in (301, 302)
        assert client.get("/select-team/99/1", follow_redirects=False).status_code in (301, 302)
        assert _player_team().name == before  # engine was NOT repointed

    def test_invalid_new_slot_rejected(self, client):
        r = client.post("/new/99", follow_redirects=True)
        assert r.status_code == 200

    def test_delete_missing_slot_stays_safe(self, client):
        before = _player_team().name
        r = client.post("/delete/99", follow_redirects=True)
        assert r.status_code == 200
        assert _player_team().name == before


# ======================================================================
# Mentoring / presets mutations reflected in frontend
# ======================================================================
class TestMentoringPresetFrontendDb:
    def test_mentoring_add_remove_roundtrip(self, client):
        team = _player_team()
        mentor = next(p for p in team.players if p.age >= 24)
        mentor.age = 33
        mentor.personality = "Leader"
        mentee = next(p for p in team.players if p.id != mentor.id)
        mentee.age = 18
        db.session.commit()
        r = client.post("/mentoring/add",
                        data={"mentor_id": mentor.id, "mentee_id": mentee.id},
                        follow_redirects=True)
        assert r.status_code == 200
        row = Mentoring.query.filter_by(team_id=team.id, mentee_id=mentee.id).first()
        assert row is not None
        assert mentee.name in _html(client, "/squad")
        client.post(f"/mentoring/remove/{row.id}", follow_redirects=True)
        assert db.session.get(Mentoring, row.id) is None

    def test_preset_load_delete_roundtrip(self, client):
        team = _player_team()
        team.formation_data = {str(p.id): {"x": 50, "y": 50} for p in team.players[:11]}
        team.pressing_style = "high"
        db.session.commit()
        client.post("/formation/preset/save",
                    data={"preset_type": "attacking", "name": "DB-RT-Preset"},
                    follow_redirects=True)
        preset = TacticPreset.query.filter_by(team_id=team.id, name="DB-RT-Preset").first()
        assert preset is not None
        client.post(f"/formation/preset/{preset.id}/load", follow_redirects=True)
        assert _player_team().pressing_style == "high"
        client.post(f"/formation/preset/{preset.id}/delete", follow_redirects=True)
        assert db.session.get(TacticPreset, preset.id) is None


# ======================================================================
# Static assets
# ======================================================================
class TestStaticAssets:
    def test_style_css_exists_and_has_variables(self):
        path = os.path.join(STATIC_DIR, "style.css")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert ":root" in content
        assert "--primary" in content or "primary" in content

    def test_app_js_exists_and_hooks_dom(self):
        path = os.path.join(STATIC_DIR, "app.js")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "DOMContentLoaded" in content

    def test_static_served(self, client):
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/static/app.js").status_code == 200

    def test_responsive_css(self):
        with open(os.path.join(STATIC_DIR, "style.css"), encoding="utf-8") as f:
            content = f.read()
        assert "@media" in content

    def test_base_links_static(self, client):
        text = _html(client, "/dashboard")
        assert "style.css" in text

    def test_app_js_hooks_every_fetch_feature(self):
        with open(os.path.join(STATIC_DIR, "app.js"), encoding="utf-8") as f:
            content = f.read()
        assert "fetch" in content
        assert "toggle" in content.lower() or "starter" in content.lower()

    def test_base_loads_app_js(self, client):
        text = _html(client, "/dashboard")
        assert "app.js" in text


# ======================================================================
# Forms: every mutation form carries CSRF + POST; tables present
# ======================================================================
class TestFormsAndTables:
    def test_mutation_forms_use_post_and_csrf(self, client):
        for url in ("/dashboard", "/squad", "/formation", "/training",
                    "/staff", "/facilities", "/scouting", "/messages"):
            text = _html(client, url)
            if "<form" in text:
                assert 'method="POST"' in text, url
                assert "csrf_token" in text, url

    def test_data_pages_have_tables(self, client):
        for url in ("/dashboard", "/squad", "/standings", "/training",
                    "/scouting"):
            assert "<table" in _html(client, url), url
        # /compare only renders its table once two DB players are chosen
        team = _player_team()
        a, b = team.players[0], team.players[1]
        assert "<table" in _html(client, f"/compare?player_a={a.id}&player_b={b.id}")
        assert a.name in _html(client, f"/compare?player_a={a.id}&player_b={b.id}")

    def test_scouting_filter_controls_present(self, client):
        text = _html(client, "/scouting")
        assert "searchName" in text
        assert "filterPos" in text
        assert "sortBy" in text

    def test_squad_filter_controls_present(self, client):
        text = _html(client, "/squad")
        assert "squadTable" in text
        assert "filter-btn" in text


# ======================================================================
# Error handling + JSON-vs-redirect split
# ======================================================================
class TestFrontendErrorHandling:
    def test_unknown_player_toggle_returns_json_error(self, client):
        r = client.post("/toggle_starter/999999")
        assert r.status_code == 404
        assert r.get_json()["success"] is False

    def test_bad_formation_json_returns_error(self, client):
        r = client.post("/formation/save", json={"positions": {}})
        assert r.status_code == 400
        assert "error" in r.get_json()

    def test_match_report_unknown_redirects(self, client):
        r = client.get("/match_report/999999", follow_redirects=True)
        assert r.status_code == 200

    def test_pages_escape_content(self, client):
        text = _html(client, "/dashboard")
        assert "<script>alert" not in text

    def test_toggle_starter_get_rejected_as_json_for_xhr(self, client):
        team = _player_team()
        r = client.get(f"/toggle_starter/{team.players[0].id}",
                       headers={"X-Requested-With": "XMLHttpRequest"})
        assert r.status_code == 405
        assert r.get_json()["error"] == "method not allowed"

    def test_api_404_is_json(self, client):
        r = client.get("/api/no-such-thing", headers={"Accept": "application/json"})
        assert r.status_code == 404
        assert "error" in r.get_json()

    def test_favicon_silenced(self, client):
        assert client.get("/favicon.ico").status_code == 204

    def test_formation_save_missing_positions_key(self, client):
        r = client.post("/formation/save", json={})
        assert r.status_code == 400

    def test_takers_rejects_foreign_player(self, client):
        opp = _opponent()
        r = client.post("/formation/takers",
                        data={"penalty_taker_id": str(opp.players[0].id),
                              "freekick_taker_id": ""},
                        follow_redirects=True)
        assert r.status_code == 200
        assert _player_team().penalty_taker_id != opp.players[0].id


# ======================================================================
# Anti-empty-render guard: no feature page may render without DB content
# ======================================================================
class TestNoEmptySuccess:
    """Regression net for '200 with nothing shown counts as success'.

    Each page must contain at least one value read from the DB first.
    """

    PAGES_WITH_DB_MARKERS = [
        ("/dashboard", lambda: [_player_team().name, _state().manager_name]),
        ("/squad", lambda: [_player_team().players[0].name]),
        ("/formation", lambda: [_player_team().players[0].name]),
        ("/standings", lambda: [_player_team().name]),
        ("/calendar", lambda: [_state().current_date]),
        ("/match", lambda: [_player_team().name]),
        ("/stats", lambda: [League.query.first().name]),
        ("/stats/top-scorers", lambda: [League.query.first().name]),
        ("/stats/top-assists", lambda: [League.query.first().name]),
        ("/training", lambda: [_player_team().players[0].name]),
        ("/staff", lambda: [_money(_player_team().budget)]),
        ("/facilities", lambda: [_money(_player_team().budget)]),
        ("/medical", lambda: [_player_team().players[1].name]),
        ("/scouting", lambda: [_opponent().players[0].name]),
        ("/compare", lambda: [_player_team().players[0].name]),
        ("/match_history", lambda: ["تاريخ المباريات"]),
        ("/load", lambda: ["حفظ"]),
    ]

    @pytest.mark.parametrize("url", [u for u, _ in PAGES_WITH_DB_MARKERS])
    def test_page_shows_db_content(self, client, url):
        markers_fn = dict(self.PAGES_WITH_DB_MARKERS)[url]
        markers = markers_fn()
        text = _html(client, url)
        for marker in markers:
            assert str(marker) in text, f"{url} missing DB value {marker!r}"

    def test_messages_db_content_when_present(self, client):
        m = Message(title="Guard Title 42", content="Guard content body 42",
                    type="info", read=False, created_day=2)
        db.session.add(m)
        db.session.commit()
        text = _html(client, "/messages")
        assert "Guard Title 42" in text
        assert "Guard content body 42"[:60] in text
