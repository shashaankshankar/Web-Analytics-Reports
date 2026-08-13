from __future__ import annotations

from datetime import date

from .external_sources import SourceConnector
from .storage import canonical_hash


class ExternalSyncEngine:
    def __init__(self, database, connection_id: str, connector: SourceConnector):
        self.database=database; self.connection_id=connection_id; self.connector=connector

    def run(self, start_date: date, end_date: date) -> dict:
        if end_date<start_date or (end_date-start_date).days>366: raise ValueError("invalid_external_sync_period")
        request_hash=canonical_hash({"source":self.connector.source_type,"connectionId":self.connection_id,"startDate":start_date,"endDate":end_date})
        execution=self.database.begin_external_sync(self.connection_id,self.connector.source_type,start_date,end_date,request_hash)
        if execution.get("idempotentReplay"): return execution
        try:
            validation=self.connector.validate_access()
            rows=self.connector.sync(start_date,end_date)
            response_hash=canonical_hash(rows)
            reconciliation=self._reconciliation(rows,validation)
            return self.database.complete_external_sync(execution,self.connection_id,self.connector.source_type,rows,response_hash,reconciliation)
        except Exception as error:
            self.database.fail_external_sync(execution["executionId"],type(error).__name__)
            raise

    def _reconciliation(self, rows: list[dict], validation: dict) -> dict:
        result={"sourceValidation":validation,"returnedRows":len(rows),"complete":True}
        if self.connector.source_type=="google_ads":
            result.update({"costMicros":sum(row["costMicros"] for row in rows),"clicks":sum(row["clicks"] for row in rows),"impressions":sum(row["impressions"] for row in rows)})
        elif self.connector.source_type=="search_console":
            result.update({"clicks":sum(row["clicks"] for row in rows),"impressions":sum(row["impressions"] for row in rows),"queryTextRetained":any(row["queryText"] is not None for row in rows)})
        else:
            result["outcomes"]={name:sum(1 for row in rows if row["outcomeType"]==name) for name in sorted({row["outcomeType"] for row in rows})}
        return result
