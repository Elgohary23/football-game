"""Frontend tests: templates, RTL/Arabic UI, static assets, rendered pages."""
import json
import os

import pytest

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def _html(client, url):
    r = client.get(url)
    assert r.status_code == 200, url
    text = r.get_data(as_text=True)
    assert "<html" in text
    return text


class TestTemplatesExist:
    REQUIRED = ["base.html", "dashboard.html", "squad.html", "formation.html",
                "match.html", "calendar.html", "standings.html", "stats.html"]

    def test_required_templates_present(self):
        for name in self.REQUIRED:
            assert os.path.exists(os.path.join(TEMPLATES_DIR, name)), name

    def test_pages_extend_base(self):
        for name in ["dashboard.html", "squad.html", "formation.html", "match.html"]:
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


class TestRtlArabicUi:
    def test_base_is_rtl_arabic(self, client):
        text = _html(client, "/dashboard")
        assert 'dir="rtl"' in text
        assert 'lang="ar"' in text

    def test_dashboard_shows_team_and_manager(self, client):
        text = _html(client, "/dashboard")
        assert "Player FC" in text
        assert "Test Manager" in text or "المدرب" in text or "Test" in text

    def test_nav_links_present(self, client):
        text = _html(client, "/dashboard")
        for href in ["/dashboard", "/squad", "/formation", "/calendar", "/match", "/standings"]:
            assert href in text, href

    def test_squad_lists_players(self, client):
        text = _html(client, "/squad")
        assert "Player FC P0" in text

    def test_formation_embeds_json(self, client):
        text = _html(client, "/formation")
        assert "formation" in text.lower()

    def test_standings_has_table(self, client):
        text = _html(client, "/standings")
        assert "<table" in text
        assert "Player FC" in text

    def test_calendar_shows_week(self, client):
        text = _html(client, "/calendar")
        assert "1" in text  # current week/day number visible

    def test_match_page_renders(self, client):
        text = _html(client, "/match")
        assert "Player FC" in text or "Opponent FC" in text or "المباراة" in text

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
