FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY docs ./docs

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

COPY agents ./agents
COPY skills ./skills

RUN test -f agents/main-flow-agent.md \
    && test -f agents/test-analysis-design-agent.md \
    && test -f agents/testcase-review-agent.md \
    && test -f skills/requirement-review/SKILL.md \
    && test -f skills/requirement-analysis/SKILL.md \
    && test -f skills/testcase-design/SKILL.md \
    && test -f skills/testcase-evaluation/SKILL.md \
    && test -f skills/test-report/SKILL.md

EXPOSE 8000

CMD ["butterfly-qa", "web", "--host", "0.0.0.0", "--port", "8000"]
