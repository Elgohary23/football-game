# Agent Guidelines for Football Manager Project

## Project Overview

A football management simulation game built with Flask and SQLite, featuring:
- Team management with budget system
- Match simulation engine
- League table with points, goal difference
- Player roster with positions (GK/DEF/MID/FWD), fitness, injuries
- Starting XI selection mechanic
- Arabic RTL interface

## Running the Application

```bash
# Install dependencies (if needed)
pip install flask flask-sqlalchemy

# Run the application
python app.py
```

The app runs at http://127.0.0.1:5000

## Linting and Type Checking

This project uses basic Python without formal linting. For code quality:

```bash
# Optional: Install ruff for linting
pip install ruff

# Run linter
ruff check .

# Check code style
ruff format --check .
```

For type checking (optional):
```bash
pip install mypy
mypy .
```

## Testing

This project does NOT have a formal test suite. To manually test:
1. Run `python app.py`
2. Navigate to http://127.0.0.1:5000
3. Test all routes manually:
   - `/` - Dashboard
   - `/calendar` - Calendar
   - `/match` - Match view
   - `/advance_day` - Advance one day
   - `/advance_week` - Advance to next match day
   - `/toggle_starter/<id>` (POST) - Toggle player in starting XI

Key manual test scenarios:
- Starting with exactly 11 players works
- Starting with <11 players gives penalty
- Match simulation updates league table correctly (points, goals, wins/losses/draws)
- Injuries reduce fitness and prevent starting
- Toggle starter respects 11-player limit

## Code Style

### Python (Flask/SQLAlchemy)

**Imports**
```python
# Standard library first
import os
import random
from datetime import datetime

# Third-party
from flask import Flask, render_template, redirect, flash

# Local application
from models import db, Team, Player, GameState, Match
from simulation import simulate_match, get_current_match
```

**Formatting**
- 4 spaces for indentation (no tabs)
- Max line length: 100 characters
- One blank line between top-level definitions
- No trailing whitespace

**Types**
- Use type hints where helpful, especially for public functions:
```python
def get_lineup_strength(team: Team) -> tuple[float, float]:
```

**Naming Conventions**
- `snake_case` for variables, functions, methods
- `PascalCase` for classes
- `UPPER_SNAKE_CASE` for constants
- Descriptive names: `player_team`, `current_match`, `is_match_day`
- Avoid: `tmp`, `x`, `data` (too generic)

**Error Handling**
- Use `try/except` with specific exceptions
- Always `db.session.commit()` after modifications
- Flash messages for user feedback:
```python
flash("هذا اللاعب مصاب ولا يمكنه البدء!")
return redirect('/')
```

### HTML/Jinja2 Templates

**Structure**
- Extends `base.html` for consistent layout
- Use semantic HTML: `<nav>`, `<section>`, `<table>`
- Arabic content with RTL direction

**Naming**
- kebab-case for CSS classes: `.squad-management`, `.progress-bar`
- Descriptive IDs: `#player-list`, `#match-center`

**Best Practices**
- Escape user content: `{{ variable }}` (Jinja2 auto-escapes)
- Use loops: `{% for player in players %}`
- Use conditionals: `{% if player.is_starting %}`
- Avoid inline styles; use CSS classes

### CSS

**Organization**
- One declaration per line
- Alphabetize properties within declaration
- Use CSS variables for colors:
```css
:root {
    --primary: #e94560;
    --bg-dark: #16213e;
}
```

**Responsive Design**
- Use flexbox and grid
- Media queries for mobile:
```css
@media (max-width: 768px) {
    .stats-grid { grid-template-columns: repeat(2, 1fr); }
}
```

### JavaScript

**When adding JS:**
- Use Vanilla JS (no frameworks)
- DOMContentLoaded for page load:
```javascript
document.addEventListener('DOMContentLoaded', () => {
    // code here
});
```

## File Structure

```
project/
├── app.py              # Flask application & routes
├── models.py          # SQLAlchemy models
├── simulation.py      # Game logic (match engine)
├── templates/        # Jinja2 HTML templates
│   ├── base.html
│   ├── dashboard.html
│   ├── match.html
│   └── calendar.html
├── static/
│   ├── style.css      # Styles
│   └── app.js       # Client-side JS (if needed)
├── instance/
│   └── db.sqlite3   # SQLite database
└── AGENTS.md        # This file
```

## Key Patterns

### Database Changes

Always commit after modifications:
```python
db.session.commit()

# Or rollback on error:
try:
    db.session.commit()
except Exception:
    db.session.rollback()
    raise
```

### Route Safety

- Use `@app.route('/path')` for GET
- Use `@app.route('/path', methods=['POST']` for mutations
- Redirect after POST (Post/Redirect/Get pattern)

### Query Patterns

```python
# Get single item
team = Team.query.get(id)

# Filter
player_team = Team.query.filter_by(is_player_team=True).first()

# Order and limit
sorted_teams = Team.query.order_by(Team.points.desc()).all()
```

### Template Inheritance

```html
{% extends "base.html" %}

{% block title %}Page Title{% endblock %}

{% block content %}
<!-- content here -->
{% endblock %}
```

## Common Tasks

### Adding a new route

1. Add function in `app.py` with `@app.route()` decorator
2. Return `render_template()` with context
3. Add corresponding template or modify existing

### Adding a model field

1. Add column to model in `models.py`
2. Delete `instance/db.sqlite3` to rebuild
3. Update `init_db()` in `app.py` if needed
4. Update templates that use this field

### Adding a new team

Team must have:
- `name` (unique)
- `morale` (default 70)
- `budget` (default 1000000)
- 15 players with positions

### Modifying match simulation

Key functions in `simulation.py`:
- `get_lineup_strength(team)` - calculates team strength
- `simulate_match(match)` - runs match and updates stats
- `assign_scorers(team, goals)` - distributes goals to players
- `update_league_table()` - updates points/goals

## Non-Functional Requirements

See project documentation (NFRs) for:
- Usability: Arabic RTL interface
- Performance: <2s page load
- Scalability: Support for more teams/seasons
- Reliability: State persistence in SQLite
- Security: POST-only for mutations
- Maintainability: Clear separation of concerns

## Database Reset

To reset the game state:
```bash
rm instance/db.sqlite3
python app.py
```

This creates a fresh database with default teams and players.