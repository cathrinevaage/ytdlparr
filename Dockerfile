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

# The incomplete dir is meant to live in the container's own layer
# unless the deployment mounts something over it, so it is not a
# VOLUME (that would spawn an anonymous volume per recreate) and it is
# writable by whatever uid the container is run as.
RUN mkdir -p /downloads/incomplete /downloads/complete \
 && chmod 0777 /downloads /downloads/incomplete /downloads/complete

ENV YTDLPARR_CONFIG=/config/config.yml
VOLUME ["/config"]
EXPOSE 9120

HEALTHCHECK --interval=60s --timeout=5s \
  CMD wget -qO- http://localhost:9120/health || exit 1

CMD ["python3", "-m", "ytdlparr"]
