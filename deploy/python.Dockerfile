# Images built from this repository's uv.lock. Build context: the repository root.
#   target adapter     the timeline adapter (production and demo)
#   target demo-tools  ett-demo-bootstrap / ett-demo-seed (demo stack only)
#   target tiled       a Tiled server with PostgreSQL support (demo stack only)

FROM ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv

FROM docker.io/library/python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS base
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin app
WORKDIR /src

FROM base AS adapter
COPY pyproject.toml uv.lock README.md LICENSE.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable && rm -rf /root/.cache
USER app
EXPOSE 8000
CMD ["ett-adapter", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS demo-tools
COPY pyproject.toml uv.lock README.md LICENSE.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable --extra testing && rm -rf /root/.cache
USER app

FROM base AS tiled
COPY pyproject.toml uv.lock README.md LICENSE.md ./
RUN uv sync --locked --no-dev --no-install-project --group tiled-server && rm -rf /root/.cache
COPY deploy/tiled/config.template.yml deploy/tiled/entrypoint.py deploy/tiled/healthcheck.py /etc/tiled/
RUN mkdir -p /data/files /data/processed && chown -R app /data
USER app
EXPOSE 8000
ENTRYPOINT ["python", "/etc/tiled/entrypoint.py"]
