from jsonschema import Draft202012Validator
import pytest
from adaos.services.root_mcp.applications_plane import contracts


@pytest.mark.parametrize('tool', ['publish_to_registry', 'publish_stable_source', 'publish_prerelease', 'promote_stable',
    'get_operation', 'list_operations', 'reconcile_operation'])
def test_publication_observers_use_the_existing_journal_and_exact_intent(tool):
    contract = next(c for c in contracts() if c.id == 'applications.development.' + tool)
    schema = contract.output_schema['properties']['result']
    record = {'schema':'adaos.application.development_operation.v1', 'operation_id':'appdevop.fixture',
        'application_id':'fixture', 'action':'publish_to_registry', 'status':'unknown',
        'idempotency_key':'retained-before-transport', 'intent':{'release_digest':'sha256:'+'a'*64, 'release_notes':'draft'},
        'result':{}, 'recovery_reason':'transport_unknown', 'revision':3}
    result = {'operations':[record]} if tool=='list_operations' else {'operation':record} if tool in {'get_operation','reconcile_operation'} else record
    Draft202012Validator(schema).validate(result)
    if tool not in {'get_operation','list_operations','reconcile_operation'}:
        Draft202012Validator(schema).validate({'would_'+tool:True,'request':{}})
