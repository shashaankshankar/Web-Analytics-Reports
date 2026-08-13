import base64
import json
from datetime import date

import pytest

from app.source_runtime import (
    ADS_SCOPE,
    SEARCH_CONSOLE_SCOPE,
    BufferedOutcomeConnector,
    SecretJsonResolver,
    SourceConnectorFactory,
    external_sync_window,
)


REFERENCE="projects/test-project/secrets/source-credentials/versions/7"


class Resolver:
    def __init__(self,value): self.value=value
    def resolve(self,reference):
        assert reference==REFERENCE
        return self.value


def test_version_pinned_secret_json_is_decoded_and_latest_is_rejected():
    payload=base64.b64encode(json.dumps({"credentialMode":"adc"}).encode()).decode()
    resolver=SecretJsonResolver(lambda reference: {"payload":{"data":payload}})
    assert resolver.resolve(REFERENCE)=={"credentialMode":"adc"}
    with pytest.raises(ValueError,match="version_pinned"): resolver.resolve("projects/test-project/secrets/source-credentials/versions/latest")


def test_connector_factory_refreshes_scoped_tokens_without_storing_access_tokens():
    scopes=[]
    oauth=lambda secret,scope: scopes.append(scope) or "short-lived-token"
    ads=SourceConnectorFactory(Resolver({"developerToken":"developer","refreshToken":"r","clientId":"c","clientSecret":"s"}),oauth_provider=oauth).create({
      "source_type":"google_ads","credential_secret_reference":REFERENCE,"external_account_id":"123-456-7890","configuration_json":{},
    })
    assert ads.customer_id=="1234567890" and scopes==[ADS_SCOPE]
    search=SourceConnectorFactory(Resolver({"credentialMode":"adc"}),adc_provider=lambda scope: scopes.append(scope) or "adc-token").create({
      "source_type":"search_console","credential_secret_reference":REFERENCE,"external_account_id":None,
      "configuration_json":{"siteUrl":"https://example.com","privacyApprovedQueries":False},
    })
    assert search.site_url=="https://example.com" and scopes[-1]==SEARCH_CONSOLE_SCOPE


def test_first_party_push_batch_is_privacy_validated_and_date_bounded():
    factory=SourceConnectorFactory(Resolver({"outcomeHashSecret":"s"*32}))
    normalizer=factory.create({
      "source_type":"crm_booking","credential_secret_reference":REFERENCE,"external_account_id":None,
      "configuration_json":{"identityPolicyReference":"approved_crm_matching_v1"},
    })
    connector=BufferedOutcomeConnector(normalizer,[{"sourceRecordId":"crm-1","outcomeType":"booked_appointment","outcomeDate":"2026-08-13"}])
    rows=connector.sync(date(2026,8,13),date(2026,8,13))
    assert len(rows)==1 and rows[0]["sourceRecordHash"]!="crm-1"
    with pytest.raises(ValueError,match="prohibited_direct_identifier"):
        BufferedOutcomeConnector(normalizer,[{"sourceRecordId":"crm-2","outcomeType":"customer","outcomeDate":"2026-08-13","email":"patient@example.com"}])


def test_external_windows_use_source_finalization_lag_and_timezone():
    ads=external_sync_window("2026-08-13T05:00:00Z",{"source_type":"google_ads","configuration_json":{"timezone":"America/New_York","lookbackDays":14}})
    search=external_sync_window("2026-08-13T05:00:00Z",{"source_type":"search_console","configuration_json":{"timezone":"America/New_York","lookbackDays":14}})
    assert ads==(date(2026,7,30),date(2026,8,12))
    assert search==(date(2026,7,28),date(2026,8,10))
