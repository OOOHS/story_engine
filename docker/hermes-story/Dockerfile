ARG HERMES_BASE_IMAGE=python:3.12-slim
FROM ${HERMES_BASE_IMAGE}

COPY hermes-agent /opt/hermes-agent
RUN python3 -m venv /opt/story-venv \
    && /opt/story-venv/bin/pip install --no-cache-dir -e /opt/hermes-agent

COPY config.yaml /root/.hermes/config.yaml
COPY entrypoint.py /opt/story-entrypoint.py

ENTRYPOINT ["/opt/story-venv/bin/python", "/opt/story-entrypoint.py"]
