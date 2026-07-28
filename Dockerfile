FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV APP_HOME=/app
ENV PYTHONPATH=/app/WorkBranch/backend:/app/WorkBranch
ENV GUNICORN_WORKERS=1
ENV LLM_TRACE_LOG_PATH=/app/logs/llm_decision_trace.log

WORKDIR ${APP_HOME}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libmagic1 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt

RUN pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt \
    && pip install gunicorn psutil

COPY . .

RUN mkdir -p /app/logs /app/workspaces /app/WorkBranch/backend/data/db

WORKDIR /app/WorkBranch/backend

EXPOSE 8000

CMD ["gunicorn", "app:app", "-c", "gunicorn.conf.py"]
