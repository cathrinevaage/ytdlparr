"""Notifications through Apprise, filtered to the configured events."""

import logging

log = logging.getLogger(__name__)

JOB_FAILED = "job_failed"
LOW_SPACE = "low_space"
QUEUE_PAUSED = "queue_paused"


class Notifier:
    def __init__(self, urls, events):
        self.events = set(events)
        self.apprise = None

        if urls:
            import apprise

            self.apprise = apprise.Apprise()

            for url in urls:
                self.apprise.add(url)

    def send(self, event, title, body):
        if event not in self.events:
            return

        log.warning("%s: %s - %s", event, title, body)

        if self.apprise is not None:
            self.apprise.notify(title=title, body=body)
