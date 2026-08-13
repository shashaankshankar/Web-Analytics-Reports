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
