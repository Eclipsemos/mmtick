# Repository Guidelines

## Project Structure & Module Organization

Python application code lives in `src/mastermind_tick/`. Strategy and replay logic are in
`strategy.py` and `backtest.py`; paper execution is in `engine.py`; Binance live execution is
isolated in `live_futures.py`, `live_spot.py`, and `live_store.py`. Runtime settings belong in
`config/settings.toml`. Python tests mirror these modules under `tests/test_*.py`.

The React/TypeScript dashboard is in `frontend/src/`, with Playwright tests in
`frontend/tests/`. Generated research belongs in `reports/`; operational history is summarized
in `changes.md`. Do not commit files from `data/`, `.env`, `.venv`, or `frontend/dist`.

## Build, Test, and Development Commands

- `.venv/bin/pytest`: run the full Python test suite.
- `.venv/bin/pytest tests/test_strategy.py -q`: run one focused test module.
- `.venv/bin/ruff check src tests`: lint Python for errors, imports, upgrades, and bug patterns.
- `.venv/bin/ruff format src tests`: format Python using the configured 100-character line limit.
- `cd frontend && npm run build`: type-check and produce the Vite production bundle.
- `cd frontend && npm run dev`: start the dashboard development server with `/api` proxying.
- `./scripts/run.sh --foreground --host 127.0.0.1 --port 8100`: run the API and production dashboard locally.
- `cd frontend && npm run test:e2e`: run Playwright tests against a running service.

## Coding Style & Naming Conventions

Use Python 3.11+ with four-space indentation, type hints, `snake_case` functions and variables,
and `PascalCase` classes. Keep financial arithmetic in `Decimal`; do not introduce float math
into order sizing or PnL paths. TypeScript components use `PascalCase`, while helpers and hooks
use `camelCase`. Prefer small, explicit functions and preserve idempotency around orders,
fills, and database writes.

## Testing Guidelines

Pytest is the primary framework. Name tests `test_<behavior>` and cover long/short symmetry,
restart persistence, partial fills, and rejected or ambiguous exchange responses when changing
execution code. Add Playwright coverage for material dashboard interactions. Run Python tests,
Ruff, and the frontend build before submitting.

## Commit & Pull Request Guidelines

History follows Conventional Commit-style subjects such as `feat: enable live continuation
reentry` and `fix: calculate live win rate by round trip`. Keep commits focused and imperative.
Pull requests should describe behavior, risk, test evidence, configuration changes, and relevant
backtest limitations. Include screenshots for visible UI changes and link issues or reports.

## Security & Live Trading

Never expose API keys, operator tokens, cookies, or database contents. Treat live operations as
state-changing: confirm account, symbol, position, and pending orders before acting. Tests must
use fakes or paper execution unless the account owner explicitly authorizes a real operation.
