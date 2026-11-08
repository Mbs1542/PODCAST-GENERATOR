# M.B.S Studio in a container: the browser app, the script engine, voices, video and the
# Hebrew transcriber, on Linux. Runs anywhere Docker runs (Windows with Docker Desktop, a
# Linux server, a NAS); open it from any browser, including an iPhone over a VPN.
#
#   docker compose up -d --build                      # CPU transcription
#   docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build   # NVIDIA GPU
#
# Everything the app saves (settings and keys, projects, the 1.6 GB transcription model)
# lives in /data, a volume, so rebuilding or updating the image loses nothing.

FROM python:3.12-slim-bookworm

# GPU=1 adds the CUDA runtime libraries (about 1 GB) for transcription on an NVIDIA card
ARG GPU=0

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MBS_HOST=0.0.0.0 \
    MBS_PORT=8765 \
    MBS_ENV_FILE=/data/.env \
    MBS_MODELS_DIR=/data/models \
    OUTPUT_BASE_DIR=/data/outputs \
    HF_HOME=/data/models/hf \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib

# ffmpeg: audio and video; libasound2/libssl3: Azure Speech SDK; DejaVu: a Hebrew-capable fallback font
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core libasound2 libssl3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Heebo (SIL Open Font License) as the built-in Hebrew title font for videos
ADD https://raw.githubusercontent.com/google/fonts/main/ofl/heebo/Heebo%5Bwght%5D.ttf /usr/share/fonts/truetype/mbs/Heebo.ttf

WORKDIR /app

# Dependencies first, so code changes rebuild in seconds
COPY requirements.txt requirements-transcribe.txt requirements-gpu.txt installer/constraints.txt ./
RUN pip install -r requirements.txt -r requirements-transcribe.txt -c constraints.txt \
 && if [ "$GPU" = "1" ]; then pip install -r requirements-gpu.txt -c constraints.txt; fi

COPY . .

RUN useradd --create-home --uid 1000 mbs \
 && mkdir -p /data \
 && chown -R mbs /data /app /usr/share/fonts/truetype/mbs
USER mbs

VOLUME /data
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import sys, urllib.request; sys.exit(0 if b'mbs-studio' in urllib.request.urlopen('http://127.0.0.1:8765/api/hello', timeout=3).read() else 1)"

CMD ["python", "-m", "src.web", "--no-browser", "--stay"]
