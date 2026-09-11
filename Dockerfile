# Python 3.12 matches the tested shared deployment runtime.
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build
COPY requirements.txt requirements.yml ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && /opt/venv/bin/ansible-galaxy collection install \
       -r requirements.yml -p /usr/share/ansible/collections

FROM python:3.12-slim-bookworm AS runtime

ARG APP_UID=1000
ARG APP_GID=1000
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g "${APP_GID}" range42 \
    && useradd -u "${APP_UID}" -g "${APP_GID}" -M \
       -d /var/lib/range42/home --no-log-init range42 \
    && install -d -o range42 -g range42 -m 0700 \
       /var/lib/range42 /var/lib/range42/home /var/lib/range42/workspaces

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /usr/share/ansible/collections /usr/share/ansible/collections
COPY app/ app/
COPY playbooks/ playbooks/
COPY alembic/ alembic/
COPY alembic.ini container_entrypoint.py ./

ENV PATH="/opt/venv/bin:$PATH" \
    HOME=/var/lib/range42/home \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PROJECT_ROOT_DIR=/app \
    RANGE42_WORKSPACE_ROOT=/var/lib/range42/workspaces \
    RANGE42_MAINTENANCE_LOCK_FILE=/var/lib/range42/maintenance.lock \
    RANGE42_AUTH_MODE=required \
    ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections

USER range42
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:8000/v1/health', timeout=3).raise_for_status()"
ENTRYPOINT ["python", "/app/container_entrypoint.py"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
