from pathlib import Path

from adaos.e2e.component_contract_conformance import load_slice, matched_runtime_episodes
from adaos.services.builder.semantic_prototype import compile_semantic_prototype


def test_matched_schema_binding_authority_availability_and_state_episodes(tmp_path):
    document, _ = load_slice(Path(__file__).resolve().parents[1])
    compiled = compile_semantic_prototype(document, project_ref='project:matched-contract')
    rows = matched_runtime_episodes(compiled, tmp_path)
    assert len(rows) == 7
    assert all(row['ok'] for row in rows), rows
    assert sum(row['observed'] == 'committed' for row in rows) == 3
