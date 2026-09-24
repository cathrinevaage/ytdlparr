"""The SABnzbd shapes Sonarr reads. Everything Sonarr acts on is
real; sizes and times are derived from yt-dlp's estimate; the rest is
fields Sonarr checks exist and moves on from."""

from datetime import datetime

from . import jobs

VERSION = "4.2.0"

# What Sonarr sees in the queue for each internal state.
QUEUE_STATUS = {
    jobs.QUEUED: "Queued",
    jobs.DOWNLOADING: "Downloading",
    jobs.POSTPROCESSING: "Extracting",
    jobs.WAITING_TO_MOVE: "Queued",
    jobs.MOVING: "Moving",
}

HISTORY_STATUS = {
    jobs.COMPLETED: "Completed",
    jobs.FAILED: "Failed",
}

PRIORITY_LABEL = {
    "low": "Low", "normal": "Normal", "high": "High", "force": "Force",
}

# Sonarr sends SAB's numeric priorities on addurl/addfile.
PRIORITY_FROM_SAB = {"-1": "low", "0": "normal", "1": "high", "2": "force"}


def megabytes(byte_count):
    return f"{byte_count / (1 << 20):.2f}"


def human_size(byte_count):
    if byte_count < 1024:
        return f"{int(byte_count)} B"

    for unit in ("KB", "MB", "GB", "TB"):
        byte_count /= 1024

        if byte_count < 1024 or unit == "TB":
            return f"{byte_count:.1f} {unit}"


def timeleft(seconds):
    hours, remainder = divmod(max(int(seconds), 0), 3600)
    minutes, secs = divmod(remainder, 60)

    return f"{hours}:{minutes:02d}:{secs:02d}"


def percentage(job):
    if not job.bytes_total:
        return "0"

    return str(min(int(job.bytes_done * 100 / job.bytes_total), 100))


def queue_slot(job, index):
    remaining = max(job.bytes_total - job.bytes_done, 0)

    return {
        "status": QUEUE_STATUS.get(job.state, "Queued"),
        "index": index,
        "nzo_id": job.id,
        "filename": job.name,
        "cat": job.category,
        "priority": PRIORITY_LABEL.get(job.priority, "Normal"),
        "percentage": percentage(job),
        "mb": megabytes(job.bytes_total),
        "mbleft": megabytes(remaining),
        "size": human_size(job.bytes_total),
        "sizeleft": human_size(remaining),
        "timeleft": timeleft(job.eta),
        "eta": "unknown",
        "avg_age": "0d",
        "script": "None",
        "unpackopts": "3",
    }


def queue(active_jobs, paused=False):
    slots = [queue_slot(job, index) for index, job in enumerate(active_jobs)]
    speed = sum(job.speed for job in active_jobs)

    return {
        "queue": {
            "version": VERSION,
            "paused": paused,
            "pause_int": "0",
            "noofslots": len(slots),
            "noofslots_total": len(slots),
            "speed": f"{human_size(speed)}/s",
            "kbpersec": f"{speed / 1024:.2f}",
            "mb": megabytes(sum(job.bytes_total for job in active_jobs)),
            "mbleft": megabytes(sum(
                max(job.bytes_total - job.bytes_done, 0)
                for job in active_jobs
            )),
            "timeleft": timeleft(max(
                (job.eta for job in active_jobs), default=0
            )),
            "status": "Downloading" if active_jobs else "Idle",
            "slots": slots,
        },
    }


def history_slot(job):
    return {
        "status": HISTORY_STATUS.get(job.state, "Failed"),
        "nzo_id": job.id,
        "name": job.name,
        "nzb_name": f"{job.name}.nzb",
        "category": job.category,
        "storage": job.storage,
        "path": job.storage,
        "bytes": job.bytes_total,
        "fail_message": job.fail_message,
        "completed": int(job.finished),
        "download_time": int(max(job.finished - job.started, 0)),
        "postproc_time": 0,
        "downloaded": job.bytes_done,
        "script": "None",
        "script_line": "",
        "action_line": "",
        "stage_log": [],
        "retry": 0,
        "url_info": "",
        "loaded": False,
        "pp": "3",
        "report": "",
        "url": "",
        "size": human_size(job.bytes_total),
        "completeness": 0,
        "meta": None,
        "has_rating": False,
        "duplicate_key": "",
        "series": "",
        "md5sum": "",
        "password": "",
    }


def history(finished_jobs):
    return {
        "history": {
            "version": VERSION,
            "noofslots": len(finished_jobs),
            "day_size": "0 B",
            "week_size": "0 B",
            "month_size": "0 B",
            "total_size": human_size(sum(
                job.bytes_total for job in finished_jobs
            )),
            "slots": [history_slot(job) for job in finished_jobs],
        },
    }


def config_response(complete_dir, category_names, categories):
    """mode=get_config: Sonarr reads complete_dir and each category's
    dir to work out where imports come from, and checks the sorting
    flags are off."""
    return {
        "config": {
            "misc": {
                "complete_dir": complete_dir,
                "enable_tv_sorting": 0,
                "enable_movie_sorting": 0,
                "enable_date_sorting": 0,
                "pre_check": 0,
                "history_retention": "",
            },
            "categories": [
                {
                    "name": "*",
                    "order": 0,
                    "pp": "3",
                    "script": "None",
                    "dir": "",
                    "newzbin": "",
                    "priority": 0,
                },
                *[
                    {
                        "name": name,
                        "order": index + 1,
                        "pp": "3",
                        "script": "None",
                        "dir": categories.get(name, {}).get("dir", ""),
                        "newzbin": "",
                        "priority": 0,
                    }
                    for index, name in enumerate(category_names)
                ],
            ],
            "sorters": [],
            "servers": [],
        },
    }


def added(job_ids):
    return {"status": True, "nzo_ids": job_ids}


def error(message):
    return {"status": False, "error": message}


def priority_from_request(value):
    """Sonarr sends -100 for "default", which is just normal."""
    return PRIORITY_FROM_SAB.get(str(value), "normal")
