FROM node:20-bookworm-slim AS remotion-deps
WORKDIR /opt/remotion
COPY remotion/package.json ./package.json
RUN npm install --omit=dev

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git curl ca-certificates build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN pip install "scenedetect[opencv]" auto-editor whisperx
COPY . .
COPY --from=remotion-deps /opt/remotion/node_modules /app/remotion/node_modules
EXPOSE 8000
CMD ["sh","-c","uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
