fmt:
    uv run ruff check --fix-only tg_assist
    uv run isort tg_assist
    uv run ruff format tg_assist

update-deps:
    uv pip compile --no-header --upgrade pyproject.toml -o requirements.txt
