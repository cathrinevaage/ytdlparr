import logging
import os
from pathlib import Path

from waitress import serve

from .app import create_app
from .config import load
from .fetcher import write_ffmpeg_wrappers
from .jobs import JobStore
from .notify import Notifier
from .pipeline import Pipeline
from .worker import Worker


def build(config):
    store = JobStore(config["paths"]["state"], config["history"]["keep_jobs"])
    ffmpeg_dir = write_ffmpeg_wrappers(
        Path(config["paths"]["state"]).parent / "ffmpeg-wrappers",
        config["limits"]["nice"],
        config["limits"]["ionice"],
    )
    pipeline = Pipeline(config, store, ffmpeg_dir)
    notifier = Notifier(
        config["notifications"]["apprise_urls"],
        config["notifications"]["on"],
    )
    worker = Worker(config, store, pipeline, notifier)

    return create_app(config, store, pipeline, worker), worker


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load(os.environ.get("YTDLPARR_CONFIG", "config.yml"))
    app, worker = build(config)
    worker.start()

    try:
        serve(
            app,
            host=config["server"]["host"],
            port=config["server"]["port"],
            threads=8,
        )
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
