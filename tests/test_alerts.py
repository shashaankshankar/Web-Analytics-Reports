from datetime import datetime, timedelta, timezone

from app.storage import contract_is_outdated, data_stale_alert
from app.sync import access_revocation_error, bundle_alert_states


def bundle(period: str, sessions: tuple[int, int] = (0, 0), leads: tuple[int, int] = (0, 0)) -> dict:
    return {
        "period": period,
        "views": {
            "overview": {
                "metrics": [
                    {"metric": "sessions", "value": sessions[0], "previousValue": sessions[1]},
                    {"metric": "generated_leads", "value": leads[0], "previousValue": leads[1]},
                ]
            }
        },
    }


def test_tracking_stopped_requires_a_nontrivial_prior_week():
    stopped = bundle_alert_states(bundle("7d", sessions=(0, 8)))
    assert stopped["tracking_stopped"]["active"] is True
    assert stopped["tracking_stopped"]["severity"] == "critical"
    assert bundle_alert_states(bundle("7d", sessions=(0, 2)))["tracking_stopped"]["active"] is False
    assert bundle_alert_states(bundle("7d", sessions=(1, 8)))["tracking_stopped"]["active"] is False


def test_lead_drop_uses_a_guardrailed_period_comparison():
    assert bundle_alert_states(bundle("28d", leads=(0, 3)))["lead_count_unexpected_drop"]["active"] is True
    assert bundle_alert_states(bundle("28d", leads=(4, 10)))["lead_count_unexpected_drop"]["active"] is True
    assert bundle_alert_states(bundle("28d", leads=(2, 3)))["lead_count_unexpected_drop"]["active"] is False
    assert "lead_count_unexpected_drop" not in bundle_alert_states(bundle("90d", leads=(0, 20)))


def test_access_revocation_classification_does_not_treat_every_sync_error_as_auth_failure():
    class PermissionDenied(Exception):
        pass

    assert access_revocation_error(PermissionDenied("denied")) is True
    assert access_revocation_error(RuntimeError("invalid_grant")) is True
    assert access_revocation_error(RuntimeError("temporary backend failure")) is False


def test_data_staleness_is_time_based_and_target_free():
    now = datetime(2026, 8, 13, 20, 0, tzinfo=timezone.utc)
    assert data_stale_alert(now - timedelta(hours=35), now) is None
    alert = data_stale_alert(now - timedelta(hours=37), now)
    assert alert["key"] == "data_stale"
    assert alert["detail"]["ageHours"] == 37.0
    assert data_stale_alert(None, now)["detail"]["lastSuccessfulSync"] is None


def test_contract_outdated_covers_missing_unapproved_and_superseded_assignments():
    assert contract_is_outdated(None, None, None, None) is True
    assert contract_is_outdated("local_service_v1@1", "pending_approval", 1, 1) is True
    assert contract_is_outdated("local_service_v1@1", "approved", 1, 2) is True
    assert contract_is_outdated("local_service_v1@2", "approved", 2, 2) is False
