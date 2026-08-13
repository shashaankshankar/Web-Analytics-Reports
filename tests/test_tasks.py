import json
from types import SimpleNamespace

from app.config import Settings
from app.tasks import PERIODS, TaskQueue


class Tasks:
    def __init__(self): self.created=[]
    def queue_path(self,*_): return "projects/p/locations/r/queues/q"
    def create_task(self,parent,task): self.created.append(task); return SimpleNamespace(name=task["name"])


def settings():
    return Settings("demo",False,False,"","","","127.0.0.1",3000,google_cloud_project="p",google_cloud_region="r",tasks_queue="q",service_url="https://service.example",task_service_account="worker@example.com",internal_trigger_token="x"*32)


def test_scheduler_creates_fixed_period_jobs_per_assignment():
    client=Tasks(); queue=TaskQueue(settings(),client)
    targets=[{"assignmentId":"assignment-a"},{"assignmentId":"assignment-b"}]
    result=queue.enqueue_periods("2026-08-13T03:15:00Z",targets)
    assert len(result)==len(PERIODS)*2 and len(client.created)==len(PERIODS)*2
    bodies=[json.loads(task["http_request"]["body"]) for task in client.created]
    assert {body["assignmentId"] for body in bodies}=={"assignment-a","assignment-b"}
    assert {body["period"] for body in bodies}==set(PERIODS)
    assert len({task["name"] for task in client.created})==len(client.created)


def test_external_source_tasks_are_isolated_and_idempotently_named():
    client=Tasks(); queue=TaskQueue(settings(),client)
    targets=[
      {"connectionId":"source-a","sourceType":"google_ads","startDate":"2026-08-01","endDate":"2026-08-12"},
      {"connectionId":"source-b","sourceType":"search_console","startDate":"2026-07-30","endDate":"2026-08-10"},
    ]
    result=queue.enqueue_external_sources("2026-08-13T05:00:00Z",targets)
    assert len(result)==2 and len(client.created)==2
    bodies=[json.loads(task["http_request"]["body"]) for task in client.created]
    assert bodies==targets
    assert all(task["http_request"]["url"].endswith("/internal/external-sources/sync") for task in client.created)
    assert len({task["name"] for task in client.created})==2
