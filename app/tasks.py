from __future__ import annotations

import hashlib
import json

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2

from .config import Settings

PERIODS = ("7d","28d","this_month","last_month","90d")


class TaskQueue:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.client = client or tasks_v2.CloudTasksClient()

    def enqueue_periods(self, scheduled_for: str) -> list[dict]:
        if not self.settings.queue_enabled:
            raise RuntimeError("cloud_tasks_not_configured")
        parent = self.client.queue_path(self.settings.google_cloud_project,self.settings.google_cloud_region,self.settings.tasks_queue)
        results=[]
        for period in PERIODS:
            digest=hashlib.sha256(f"{scheduled_for}:{period}".encode()).hexdigest()[:32]
            task_name=f"{parent}/tasks/sync-{digest}"
            body=json.dumps({"period":period,"scheduledFor":scheduled_for}).encode()
            task={"name":task_name,"http_request":{"http_method":tasks_v2.HttpMethod.POST,"url":f"{self.settings.service_url}/internal/sync","headers":{"Content-Type":"application/json","X-Internal-Trigger-Token":self.settings.internal_trigger_token},"body":body,"oidc_token":{"service_account_email":self.settings.task_service_account,"audience":self.settings.service_url}}}
            try:
                response=self.client.create_task(parent=parent,task=task)
                results.append({"period":period,"state":"queued","task":response.name})
            except AlreadyExists:
                results.append({"period":period,"state":"already_queued","task":task_name})
        return results
