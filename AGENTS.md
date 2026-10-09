# AGENTS.md — Football Manager (Flask + SQLite, Arabic RTL)

## Mandatory test loop (applies to every change)

For any prompt that touches code: run the affected unit tests and keep
fixing until they are all green. Do not declare done with red tests.
Do not stop after one run — fix, re-run, repeat until pass.

```bash
# Fast suite — the default gate (288 tests: backend + frontend, verified)
python -m pytest tests/test_backend.py tests/test_frontend.py -q
```

- Run from repo root. Never bare `pytest`: `scratch/test_db.py` has a stale
  `from app import ... create_new_game` import that breaks collection.
- E2E is separate and slow (real Chromium + live server on port 5001,
  marked `e2e` in `pytest.ini`). Only run it when touching rendered
  pages/flows: `python -m pytest tests/test_e2e_playwright.py -q`
  (requires `playwright install chromium`).

## Finish every prompt: stage, commit, push

For any prompt: once tests are green, run `git add .`, commit, and push to
`origin` (`main`, SSH). Never leave work uncommitted in the working tree.
Message style follows history: `feat: ...` / `docs: ...`, imperative subject.

- Before committing, inspect `git status --porcelain` and
  `git diff --cached --stat` — `instance/`, `*.db`, `*.sqlite3`, `*.log`
  are gitignored and must never be staged.
- Git on this machine hits a dubious-ownership guard; prefix every git
  command with `-c safe.directory="D:/software projects/football-game"`
  instead of changing git config.

## Test fixtures (`tests/conftest.py`)

- In-memory SQLite, `WTF_CSRF_ENABLED=False`, session `active_save=1`
  pre-set. Tests never touch `instance/db.sqlite3` or `saves/`.
- `client` = HTTP tests, `ctx` = plain app-context for
  service/simulation unit tests. `seed_world()` gives 1 league,
  2×15 players (first 11 starting), 1 `GameState`, 1 unplayed `Match`.

## Where code lives

- `app.py` — wiring only: `create_app()`, blueprint registration,
  context processor, 404/405/500 handlers. No business logic.
- `routes/` — HTTP glue only: `game_routes`, `team_routes`, `inbox_routes`,
  `stats_routes`, `save_routes` (registered in `routes/__init__.py`).
  Handlers stay thin; rules live in `services.py` / `simulation.py`.
- `simulation.py` — match engine (`simulate_match`, lineup strength,
  scorers, league table). `services.py` — progression, saves, market,
  youth, board. `config.py` — every tuning constant.
- `models.py` — SQLAlchemy models. `live_match.py`, `procedural.py`,
  `save_manager.py` — satellite modules, touch only when needed.

## Repo-specific rules (verified, non-obvious)

- Tuning numbers live in `config.py` (single source of truth). Never
  hard-code budgets, fitness, morale, or match constants — import from
  `config` (e.g. budgets via `services.get_budget_for_tier()`, not multipliers).
- Template-DB flow: `saves/template.db` is built by `services.init_template()`
  and copied to the instance DB. Schema freshness is guarded by
  `TEMPLATE_SCHEMA_VERSION` in `config.py` — bump it to force a rebuild.
  Never delete `saves/save*.sqlite3` on rebuild (previously wiped progress;
  see `_needs_rebuild` comment in `app.py`).
- Mutations are POST-only with redirect-after-POST; CSRF is enforced via
  Flask-WTF (disabled only in tests). The 404 handler returns JSON for
  `/static/ /live/ /inbox /api/` + XHR/JSON callers, flash+redirect otherwise —
  keep that split or `fetch().json()` callers break.
- `app.run(..., use_reloader=False)` is required: the reloader re-executes
  the startup block and rebuilds the template DB concurrently (looks frozen).
- Always `db.session.commit()` after mutations; rollback on error.
- Frontend: every page extends `base.html`, root must keep `dir="rtl"`
  + `lang="ar"`. `test_frontend.py` asserts template presence, base
  inheritance, balanced `{% block %}/{% endblock %}`, and nav hrefs
  (`/dashboard /squad /formation /calendar /match /standings`) — keep them.
- `instance/`, `*.db`, `*.sqlite3`, `*.log` are gitignored. Never commit a
  database. Run the app with `python app.py` (port 5000).
