# Mock telemetry API / ticket sandbox. Contains NO data, answer keys, tests or secrets:
# data is mounted read-only at runtime, tokens are verified with $EVAL_SECRET.
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN useradd --system --uid 10001 triage && mkdir -p /app/runs /app/data/replay && chown -R triage /app/runs
COPY triage_eval/ triage_eval/
COPY config/eval.json config/eval.json
COPY client/ client/
COPY docs/api.md docs/result_schema.json docs/
USER triage
EXPOSE 8081 8082
CMD ["python", "-m", "triage_eval", "serve-data", "--host", "0.0.0.0", "--port", "8081"]
