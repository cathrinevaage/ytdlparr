FROM alpine:3.20

# yt-dlp's extractors break and get fixed upstream constantly, so the
# version is a build argument: the rebuild workflow passes the latest
# release that passed its smoke test, and the tag records it.
ARG YTDLP_VERSION=2026.8.19

# tzdata: TZ names nothing without the zone database, and the schedule
# windows resolve it through zoneinfo.
RUN apk add --no-cache ffmpeg python3 py3-pip tzdata \
 && pip install --break-system-packages --no-cache-dir \
      "flask>=3.0" "pyyaml>=6.0" "apprise>=1.9" "waitress>=3.0" \
      "yt-dlp==${YTDLP_VERSION}"

WORKDIR /app
COPY ytdlparr ./ytdlparr

# /downloads is not a VOLUME: that would spawn an anonymous volume per
# recreate. Incomplete files live under /config by default (SAB's
# convention), so nothing needs mounting for them.
RUN mkdir -p /downloads/complete && chmod 0777 /downloads /downloads/complete

ENV YTDLPARR_CONFIG=/config/config.yml
VOLUME ["/config"]
EXPOSE 9120

HEALTHCHECK --interval=60s --timeout=5s \
  CMD wget -qO- http://127.0.0.1:9120/health || exit 1

CMD ["python3", "-m", "ytdlparr"]
