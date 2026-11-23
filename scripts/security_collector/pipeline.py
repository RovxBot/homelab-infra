"""Explicitly gated coordinator; no GitHub writer or production scheduler."""
from .issues import render
from .model import require


class Pipeline:
    def __init__(self, intake, runtime, assessments, *, enabled=False, publications=None):
        self.intake, self.runtime = intake, runtime
        self.assessments = tuple(assessments)  # (reader, enrolled Workload)
        require(len(self.assessments) <= 100, 'reader_capacity')
        self.enabled = enabled
        self.publications = publications or {}

    def tick(self, now):
        if not self.enabled:
            return {'state': 'disabled', 'previews': []}
        self.intake.health(now)
        if self.runtime is not None:
            self.runtime.poll(now)
        for reader, workload in self.assessments:
            reader.poll(workload, now)
        health = self.intake.health(now)
        return dict(state='observed', health=health, previews=[render(incident, self.publications[incident['workload_key']])
                    for incident in self.intake.c.all() if incident['workload_key'] in self.publications])
