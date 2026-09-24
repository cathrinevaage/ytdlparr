FROM alpine:3.20

# yt-dlp's extractors break and get fixed upstream constantly, so the
# version is a build argument: the rebuild workflow passes the latest
# release that passed its smoke test, and the tag records it.
ARG YTDLP_VERSION=2026.8.19

RUN apk add --no-cache ffmpeg python3 py3-pip \
 && pip install --break-system-packages --no-cache-dir \
      "flask>=3.0" "pyyaml>=6.0" "apprise>=1.9" "yt-dlp==${YTDLP_VERSION}"

WORKDIR /app
COPY ytdlparr ./ytdlparr

ENV YTDLPARR_CONFIG=/config/config.yml
VOLUME ["/config", "/downloads"]
EXPOSE 9120

HEALTHCHECK --interval=60s --timeout=5s \
  CMD wget -qO- http://localhost:9120/health || exit 1

CMD ["python3", "-m", "ytdlparr"]
