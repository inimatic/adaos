from pathlib import Path

from adaos.e2e.semantic_proposer_comparison import changes, expected_after, fixtures
from adaos.services.builder.semantic_prototype import compile_semantic_prototype


def test_frozen_change_chain_preserves_records_relationships_commands_and_other_locales():
    repo = Path(__file__).resolve().parents[1]
    for _name, source in fixtures(repo):
        document = compile_semantic_prototype(source, project_ref='project:test')['semantic_document']
        before = document
        for change in changes():
            document = expected_after(document, change)
            compile_semantic_prototype(document, project_ref='project:test')
        assert document['resources'][0]['records'] == before['resources'][0]['records']
        assert document['commands'] == before['commands']
        assert document['relationships'] == before['relationships']
        assert document['views'][0]['title']['ru'] == before['views'][0]['title']['ru']
        assert document['views'][1:] == before['views'][1:]
