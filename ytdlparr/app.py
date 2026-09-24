"""SABnzbd's API, as far as Sonarr and Radarr call it. Exactly the
modes beaconarr implements, which is exactly what Sonarr asks for."""

import logging
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request

from . import jobs, sab
from .categories import advertised
from .nzb import NotAJobSpec, extract_spec

log = logging.getLogger(__name__)


def create_app(config, store, pipeline, worker):
    app = Flask(__name__)
    categories = config["categories"]

    def authorised():
        return request.values.get("apikey") == config["server"]["api_key"]

    def enqueue(nzb_bytes):
        """Turn an uploaded faux NZB into a queued job."""
        spec = extract_spec(nzb_bytes)
        name = request.values.get("nzbname") or spec.get("name") or spec["url"]
        job = jobs.Job(
            id=jobs.new_id(),
            name=name,
            category=request.values.get("cat", "") or "",
            spec={**spec, "name": name},
            priority=sab.priority_from_request(request.values.get("priority", "0")),
        )
        store.add(job)
        log.info("queued %s [%s]", job.name, job.category)

        return job

    handlers = {}

    def mode(name):
        def register(function):
            handlers[name] = function
            return function

        return register

    @mode("version")
    def version():
        return {"version": sab.VERSION}

    @mode("get_config")
    def get_config():
        return sab.config_response(
            config["paths"]["complete"], advertised(categories), categories
        )

    @mode("addfile")
    def addfile():
        upload = request.files.get("name") or request.files.get("nzbfile")

        if upload is None:
            return sab.error("no file uploaded")

        try:
            job = enqueue(upload.read())
        except NotAJobSpec as error:
            return sab.error(f"not a job spec: {error}")

        return sab.added([job.id])

    @mode("addurl")
    def addurl():
        url = request.values.get("name", "")

        if not url:
            return sab.error("no url")

        try:
            with urlopen(Request(url), timeout=30) as response:
                job = enqueue(response.read())
        except NotAJobSpec as error:
            return sab.error(f"not a job spec: {error}")
        except OSError as error:
            return sab.error(f"could not fetch {url}: {error}")

        return sab.added([job.id])

    @mode("queue")
    def queue():
        if request.values.get("name") == "delete":
            return remove(from_history=False)

        if request.values.get("name") == "pause":
            worker.pause("paused by request")
            return {"status": True}

        if request.values.get("name") == "resume":
            worker.resume()
            return {"status": True}

        return sab.queue(store.active(), paused=worker.paused)

    @mode("history")
    def history():
        if request.values.get("name") == "delete":
            return remove(from_history=True)

        finished = store.finished()
        category = request.values.get("category")

        if category:
            finished = [job for job in finished if job.category == category]

        start = request.values.get("start", 0, type=int)
        limit = request.values.get("limit", 0, type=int) or len(finished)

        return sab.history(finished[start:start + limit])

    @mode("delete")
    def delete():
        return remove(from_history=None)

    def remove(from_history):
        """Sonarr removes from the queue and from history with the
        same value= parameter; del_files says whether to keep data."""
        ids = [value for value in request.values.get("value", "").split(",") if value]
        delete_files = request.values.get("del_files", "0") == "1"

        for job_id in ids:
            job = store.get(job_id)

            if job is None:
                continue

            if job.is_active():
                pipeline.cancel(job_id)

                if job.state != jobs.DOWNLOADING:
                    pipeline.discard(job, delete_files)
            else:
                store.remove(job_id)

        return {"status": True, "nzo_ids": ids}

    @mode("pause")
    def pause():
        worker.pause("paused by request")
        return {"status": True}

    @mode("resume")
    def resume():
        worker.resume()
        return {"status": True}

    @app.route("/api", methods=["GET", "POST"])
    @app.route("/sabnzbd/api", methods=["GET", "POST"])
    def api():
        if not authorised():
            return jsonify(sab.error("API Key Incorrect")), 403

        handler = handlers.get(request.values.get("mode", ""))

        if handler is None:
            return jsonify(sab.error(f"unknown mode {request.values.get('mode')!r}"))

        return jsonify(handler())

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "active": len(store.active()),
            "paused": worker.paused,
            "pause_reason": worker.pause_reason,
        }

    return app
