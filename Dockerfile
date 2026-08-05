FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV APP_HOME=/app
ENV PYTHONPATH=/app/WorkBranch/backend:/app/WorkBranch
ENV GUNICORN_WORKERS=1
ENV LLM_TRACE_LOG_PATH=/app/logs/llm_decision_trace.log
ARG INSTALL_RAG_DEPS=0

WORKDIR ${APP_HOME}

RUN sed -i 's|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g; s|http://deb.debian.org/debian-security|http://mirrors.aliyun.com/debian-security|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
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
        libreoffice-core \
        libreoffice-writer \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-rag.txt ./

RUN pip install --upgrade pip setuptools wheel \
    && pip install --retries 5 --timeout 120 -r requirements.txt \
    && pip install --retries 5 --timeout 120 gunicorn psutil \
    && if [ "$INSTALL_RAG_DEPS" = "1" ]; then pip install --retries 5 --timeout 120 -r requirements-rag.txt; fi

COPY . .

RUN mkdir -p /app/logs /app/workspaces /app/WorkBranch/backend/data/db

WORKDIR /app/WorkBranch/backend

EXPOSE 8000

CMD ["gunicorn", "app:app", "-c", "gunicorn.conf.py"]
