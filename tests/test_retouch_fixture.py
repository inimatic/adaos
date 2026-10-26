"""Provider doubles must honor legacy and new strict schemas without inventing visibility."""
from copy import deepcopy

import jsonschema
import pytest

from adaos.e2e.retouch_fixture import RetouchFixture, synthetic_qa_checks


@pytest.mark.parametrize("presence_required", [False, True])
def test_synthetic_qa_obeys_recorded_schema(presence_required):
    check = {"type": "object", "additionalProperties": False, "required": ["verdict", "reason"],
             "properties": {"verdict": {"enum": ["pass", "fail", "uncertain"]}, "reason": {"type": "string"}}}
    if presence_required:
        check["required"].append("subject_presence")
        check["properties"]["subject_presence"] = {"enum": ["visible", "absent", "unknown"]}
    schema = {"properties": {"checks": {"properties": {"pores_moles": deepcopy(check), "identity": deepcopy(check)}}}}
    before = deepcopy(schema)
    result = synthetic_qa_checks(["pores_moles", "identity"], schema)
    for item in result.values():
        jsonschema.validate(item, check)
        assert "SYNTHETIC" in item["reason"]
        assert item.get("subject_presence") == ("unknown" if presence_required else None)
    assert schema == before


def test_double_rejects_missing_content_purpose_before_any_effect():
    fixture = RetouchFixture.__new__(RetouchFixture)
    with pytest.raises(TypeError, match="purpose"):
        fixture.submit("mask_advice", {"request_id": "missing-purpose", "prompt": "example",
                                        "schema": {"type": "object"}})


def test_fixture_attachment_download_preserves_bytes_and_read_authorization():
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from adaos.e2e.retouch_fixture import fixture_app
    raw=b'\x89PNG\r\n\x1a\nfixture';calls=[]
    async def read(tool,args):calls.append((tool,args));return {'ok':True}
    owner=SimpleNamespace(token='fixture-only',source_digest='sha256:'+'a'*64,
        call=read,read_blob=lambda ref,logical:raw)
    client=TestClient(fixture_app(owner))
    url='/api/tools/natural_retouch_skill/read_attachment/attachments/exports/'+'b'*64+'/result.png'
    assert client.get(url).status_code==403
    for query,disposition in [('', 'inline;'),('?download=1','attachment;')]:
        response=client.get(url+query,headers={'X-Retouch-Fixture-Token':'fixture-only'})
        assert response.content==raw and response.headers['content-disposition'].startswith(disposition)
    assert len(calls)==2 and all(tool=='read_attachment' for tool,args in calls)
