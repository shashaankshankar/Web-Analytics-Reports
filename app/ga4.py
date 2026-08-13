from __future__ import annotations
from datetime import date, timedelta
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest
from .config import Site

PERIODS = {"7d", "28d", "90d", "this_month", "last_month"}
EVENT_FIELDS = (("generated_leads", "generate_lead"), ("appointment_requests", "appointment_request"), ("form_intent", "form_start"), ("technical_submissions", "form_submit"), ("phone_intent", "phone_click"), ("email_intent", "email_click"), ("cta_engagement", "cta_click"))
def period_dates(period, today=None):
    if period not in PERIODS: raise ValueError("unsupported_period")
    end = (today or date.today()) - timedelta(days=1)
    if period == "this_month": return end.replace(day=1).isoformat(), end.isoformat()
    if period == "last_month":
        end = end.replace(day=1) - timedelta(days=1); return end.replace(day=1).isoformat(), end.isoformat()
    return (end - timedelta(days=int(period[:-1])-1)).isoformat(), end.isoformat()
class GA4Reporter:
    def __init__(self, site: Site, client=None): self.site, self.client = site, client or BetaAnalyticsDataClient()
    def report(self, period, dimensions, metrics):
        start, end = period_dates(period)
        response = self.client.run_report(request=RunReportRequest(property=f"properties/{self.site.property_id}", date_ranges=[DateRange(start_date=start, end_date=end)], dimensions=[Dimension(name=x) for x in dimensions], metrics=[Metric(name=x) for x in metrics], return_property_quota=True))
        return {"rows":[{"dimensions":[x.value for x in row.dimension_values], "metrics":[x.value for x in row.metric_values]} for row in response.rows], "row_count":response.row_count, "property_timezone":response.metadata.time_zone or self.site.property_timezone, "data_loss_from_other_row":bool(response.metadata.data_loss_from_other_row)}
    @staticmethod
    def num(value): return int(value or "0")
    def overview(self, period):
        summary, events = self.report(period, [], ["activeUsers", "sessions", "eventCount"]), self.report(period, ["eventName"], ["eventCount"])
        counts = {r["dimensions"][0]:self.num(r["metrics"][0]) for r in events["rows"]}; values = summary["rows"][0]["metrics"] if summary["rows"] else ["0", "0", "0"]; sessions = self.num(values[1])
        metrics = [{"metric":field,"value":counts.get(event,0),"previousValue":None,"source":"ga4_reporting_api","warnings":["previous_period_not_loaded"]} for field,event in EVENT_FIELDS]
        metrics += [{"metric":"active_users","value":self.num(values[0]),"previousValue":None,"source":"ga4_reporting_api","warnings":["previous_period_not_loaded"]},{"metric":"sessions","value":sessions,"previousValue":None,"source":"ga4_reporting_api","warnings":["previous_period_not_loaded"]},{"metric":"appointment_request_rate","value":counts.get("appointment_request",0)/sessions if sessions else None,"previousValue":None,"source":"ga4_reporting_api","warnings":["previous_period_not_loaded"]}]
        return {"websiteId":self.site.site_id,"period":period,"property":f"properties/{self.site.property_id}","stream":f"properties/{self.site.property_id}/dataStreams/{self.site.stream_id}","measurementId":self.site.measurement_id,"propertyTimezone":summary["property_timezone"],"dataStatus":"live","metrics":metrics,"api":{"summaryRows":len(summary["rows"]),"eventRows":len(events["rows"]),"rowCount":summary["row_count"]+events["row_count"],"dataLossFromOtherRow":summary["data_loss_from_other_row"] or events["data_loss_from_other_row"]}}
    def acquisition(self, period):
        report = self.report(period,["sessionDefaultChannelGroup"],["sessions","activeUsers"])
        return {"websiteId":self.site.site_id,"period":period,"dataStatus":"live","propertyTimezone":report["property_timezone"],"rows":[{"channel":r["dimensions"][0] or "(not set)","sessions":self.num(r["metrics"][0]),"activeUsers":self.num(r["metrics"][1])} for r in report["rows"]],"warnings":["previous_period_not_loaded"],"api":{"rowCount":report["row_count"],"dataLossFromOtherRow":report["data_loss_from_other_row"]}}
