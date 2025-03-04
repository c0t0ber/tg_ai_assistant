FROM python:3.12-slim-bullseye AS common-deps

ENV APP_DIR=/opt/project

WORKDIR ${APP_DIR}

COPY --from=ghcr.io/astral-sh/uv:0.6.3 /uv /uvx /bin/

COPY ./requirements.txt ${APP_DIR}/requirements.txt
RUN uv pip install --system --no-cache -r ${APP_DIR}/requirements.txt;

# -------------------- development dependencies and sources --------------
FROM common-deps AS dev

COPY ./pyproject.toml ./pyproject.toml
COPY ./tg_assist ./tg_assist

# -------------------- unit tests and linters --------------------
FROM dev AS dev-unittested

RUN isort --check tg_assist
RUN ruff format --check tg_assist
RUN ruff check tg_assist
RUN mypy tg_assist

ENTRYPOINT ["python", "-m", "tg_assist"]