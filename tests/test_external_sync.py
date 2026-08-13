from datetime import date

import pytest

from app.external_sync import ExternalSyncEngine


class Connector:
    source_type="google_ads"
    def validate_access(self): return {"status":"ok"}
    def sync(self,start,end): return [{"costMicros":1000000,"clicks":2,"impressions":20}]
    def disable(self): pass


class Database:
    def __init__(self,replay=False): self.replay=replay; self.failed=None; self.completed=None
    def begin_external_sync(self,connection_id,source_type,start,end,request_hash): return {"executionId":"execution-1","websiteId":"site-1","idempotentReplay":self.replay,"rowCount":1}
    def complete_external_sync(self,execution,connection_id,source_type,rows,response_hash,reconciliation): self.completed=(rows,reconciliation); return {"status":"succeeded","rowCount":len(rows),"reconciliation":reconciliation}
    def fail_external_sync(self,execution_id,error_code): self.failed=error_code


def test_external_sync_records_reconciliation_and_is_idempotent():
    database=Database(); result=ExternalSyncEngine(database,"connection-1",Connector()).run(date(2026,8,1),date(2026,8,2))
    assert result["status"]=="succeeded" and result["reconciliation"]["costMicros"]==1000000
    replay=Database(replay=True); value=ExternalSyncEngine(replay,"connection-1",Connector()).run(date(2026,8,1),date(2026,8,2))
    assert value["idempotentReplay"] is True and replay.completed is None


def test_external_sync_failure_is_recorded_without_becoming_empty_data():
    class Failed(Connector):
        def sync(self,start,end): raise RuntimeError("provider unavailable")
    database=Database()
    with pytest.raises(RuntimeError): ExternalSyncEngine(database,"connection-1",Failed()).run(date(2026,8,1),date(2026,8,2))
    assert database.failed=="RuntimeError" and database.completed is None
