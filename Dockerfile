# whatsapp-task-pipeline — one image, two long-lived services (see
# docker-compose.yml): the listener (wtp-listen) and the reminder loop
# (wtp-remind on a 30-minute cycle). Home Assistant and the AI server stay
# OUTSIDE the container by design (INC-001 D6).

FROM python:3.12-slim

# Run as a non-root user: the container needs no privileges at all.
RUN useradd --create-home --shell /usr/sbin/nologin wtp
WORKDIR /app

COPY pyproject.toml LICENSE README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

USER wtp
# Logs and reminder-timing state live under the user's home inside the
# container; override TASK_LOG_PATH / TASK_STATE_PATH to persist them.
ENV TASK_LOG_PATH=/home/wtp/task_pipeline.log \
    TASK_STATE_PATH=/home/wtp/task_pipeline_state.json \
    TASK_PENDING_PATH=/home/wtp/task_pipeline_pending.json

CMD ["wtp-listen"]
