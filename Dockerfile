FROM python:3.12-slim

# Install FFmpeg and libraries needed by Azure Speech SDK
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libssl-dev \
    libasound2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy everything then install
COPY . .

RUN pip install --no-cache-dir .

# Create storage directory
RUN mkdir -p storage

EXPOSE 8000

CMD uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips="*"
