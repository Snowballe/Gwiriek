# AGENTS.md

Guide for agents and contributors working on Gwiriek.

## Commands

```bash
uv sync                                   # install
uv run pytest                             # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run pydoclint gwiriek
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run gwiriek --help                     # CLI
uv run gwiriek manage load_demo_data      # demo catalogue and items
```

Translations, from `gwiriek/`:

```bash
uv run django-admin makemessages -l fr --no-location --settings=gwiriek.settings
uv run django-admin compilemessages --settings=gwiriek.settings
```

## Architecture

| Layer | Location | Role |
|---|---|---|
| Models | `gwiriek/equipment/models.py` | structure and single-field rules (`clean()`) |
| Services | `gwiriek/equipment/services/` | **all business logic**, typed, validated, transactional |
| Admin | `gwiriek/equipment/admin.py` | French web UI; forms call service checks, `save_model` calls services |
| CLI | `gwiriek/cli.py` | `gwiriek` command: parses arguments, calls services, formats output |
| MCP | `gwiriek/mcp_server.py` | MCP tools wrapping services (official `mcp` SDK v2, stdio) |

Service modules: `end_of_life` (pure computation), `items`, `assignments`, `catalog`,
`people`, `due`, `views` (dataclasses returned to callers), `errors`.

## Rules

- **Business logic lives in the services.** Admin, CLI and MCP never duplicate a rule nor
  write items, statuses or assignments through the ORM directly.
- Every item write goes through `services.items.save_item`, which records status changes in
  the append-only `StatusChange` log.
- Services raise `ServiceError` subclasses for expected errors; the CLI maps them to exit
  code 1, the MCP server to tool errors.
- New service output is a dataclass in `services/views.py`: it is the JSON contract of both
  the CLI and MCP.
- No deletion tool in the MCP server.
- End-of-life results are indicative: keep the disclaimer wherever they are shown.
- Code, identifiers, comments, docstrings and commit messages in English; user-facing strings
  in English wrapped in `gettext`, with the French translation in
  `gwiriek/locale/fr/LC_MESSAGES/django.po` (compile the `.mo`).
- Docstrings follow the Google style; public service functions document `Args`, `Returns`
  and `Raises`.
- Every behaviour change comes with tests (`pytest`, `pytest-django`); MCP tests use the
  SDK's in-memory `Client` and one real stdio session.
- Record structural decisions in `docs/decisions.md`.
