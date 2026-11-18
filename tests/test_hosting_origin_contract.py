"""A delivered Client origin must be explicitly admitted by its Root consumer."""
import json
from pathlib import Path


def test_client_delivery_destinations_match_root_origin_registration():
    integrations = Path(__file__).resolve().parents[1] / 'src/adaos/integrations'
    client = json.loads((integrations / 'adaos-client/hosting-sources.json').read_text(encoding='utf-8'))
    root = json.loads((integrations / 'adaos-backend/backend/web-client-hosting.json').read_text(encoding='utf-8'))
    assert root['schema'] == client['schema'] == 'adaos.client.hosting_sources.v1'
    assert root['project'] == client['project']
    assert root['sources'] == client['sources']
