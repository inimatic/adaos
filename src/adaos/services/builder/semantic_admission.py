"""Core-issued, immutable admission for bounded proposals, independent of the model."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from .semantic_graph import SemanticGraphService, _read, _label, _view, digest, reduce_graph, verify_baseline
from .semantic_inspector import semantic_refs, dependency_refs
from .semantic_ui import _operation_root
from .workflow import BuilderWorkflowError, _LOCK

ADMISSION_SCHEMA = 'adaos.builder.semantic_admission.v1'
PROPOSAL_SCHEMA = 'adaos.builder.semantic_op_proposal.v1'
SUPPORTED = ('label.set', 'view.fields.reorder')


def compiler_digest():
    source = Path(__file__).resolve().parent
    abi = source.parents[1] / 'abi'
    noncompiler = {'semantic_admission.py', 'semantic_proposer.py', 'semantic_inspector.py',
                   'semantic_edit_requests.py', 'semantic_label_requests.py'}
    paths = sorted(path for path in source.glob('semantic_*.py') if path.name not in noncompiler) + [abi / name for name in (
        'webui.semantic.v2.schema.json', 'builder.semantic_graph_change.v1.schema.json',
        'builder.semantic_op_proposal.v1.schema.json')]
    return digest({str(path.relative_to(source.parents[1])): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})


def proposal_contract() -> dict[str, Any]:
    return json.loads((Path(__file__).resolve().parents[2] / 'abi/builder.semantic_op_proposal.v1.schema.json').read_text(encoding='utf-8'))


@dataclass(slots=True)
class SemanticAdmissionService:
    graph: SemanticGraphService

    @classmethod
    def from_context(cls):
        return cls(SemanticGraphService.from_context())

    def _location(self, project_ref: str, admission_id: str | None = None):
        from .semantic_request_scope import current_target
        if current_target() is not None and current_target() != project_ref:
            raise BuilderWorkflowError('semantic admission target differs from the caller-selected project')
        if not re.fullmatch(r'scenario:[A-Za-z0-9_.-]{1,160}', project_ref):
            raise BuilderWorkflowError('semantic admission requires an exact scenario ref')
        root = self.graph.workflow.project_root('scenario', project_ref.partition(':')[2])
        private = _operation_root(self.graph.workflow, project_ref) / 'admissions'
        if admission_id is None:
            return root, private
        if not re.fullmatch(r'admission-[0-9a-f]{48}', admission_id):
            raise BuilderWorkflowError('invalid semantic admission identity')
        return root, private / (admission_id + '.json')

    def _base(self, project_ref: str):
        root, _private = self._location(project_ref)
        object_id = project_ref.partition(':')[2]
        gate = self.graph.workflow.prototype_edit_context('scenario', object_id)
        if not (gate.get('change') or {}).get('change_id') or not gate['capabilities']['can_edit_prototype']:
            raise BuilderWorkflowError('semantic admission requires an active editable Change')
        document = _read(self.graph._file(root, 'semantic.webui.json'))
        runtime = _read(self.graph._file(root, 'webui.json'))
        baseline = verify_baseline(document, runtime, project_ref='project:' + object_id)
        locales = self.graph.verify_locale_assets(root, baseline)
        return root, gate, document, runtime, locales

    def begin(self, *, project_ref: str, instruction: str, source_message_id: str,
              write_refs: Sequence[str] | None = None, write_properties: Mapping[str, Sequence[str]] | None = None,
              operation_kinds: Sequence[str] = SUPPORTED,
              max_ops: int = 1) -> dict[str, Any]:
        if (not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 1000
                or not isinstance(source_message_id, str) or not source_message_id.strip() or len(source_message_id) > 240):
            raise BuilderWorkflowError('admission requires bounded verbatim user evidence')
        if isinstance(max_ops, bool) or not isinstance(max_ops, int) or not 1 <= max_ops <= 16:
            raise BuilderWorkflowError('invalid admitted operation capacity')
        kinds = sorted(set(operation_kinds))
        if not kinds or any(kind not in SUPPORTED for kind in kinds):
            raise BuilderWorkflowError('unsupported admission operation family')
        origin = {'instruction': instruction.strip(), 'source_message_id': source_message_id}
        admission_id = 'admission-' + digest([project_ref, source_message_id])[7:55]
        root, path = self._location(project_ref, admission_id)
        with _LOCK, mutation_lock(path.parent / '.mutation.lock'):
            root, gate, document, runtime, locales = self._base(project_ref)
            requested_refs = sorted(set(write_refs)) if write_refs is not None else None
            requested_properties = ({ref: sorted(set(props)) for ref, props in write_properties.items()}
                                    if write_properties is not None else None)
            if requested_properties is not None and set(requested_properties) != set(requested_refs or []):
                raise BuilderWorkflowError('property scope requires exactly its explicit semantic refs')
            if path.is_file():
                retained = _read(path)
                if (retained['source_request'] != origin or retained['requested_write_refs'] != requested_refs
                        or retained.get('requested_write_properties') != requested_properties
                        or retained['operation_kinds'] != kinds or retained['max_ops'] != max_ops):
                    raise BuilderWorkflowError('source message identity conflicts with retained semantic admission')
                if retained['change_id'] != gate['change']['change_id']:
                    raise BuilderWorkflowError('semantic admission belongs to another active Change')
                return self._packet(retained)
            nodes = semantic_refs(document)
            masks = []
            descriptors = []
            for node in nodes:
                if requested_refs is not None and node['ref'] not in requested_refs:
                    continue
                properties = [value for value in node['supported_writes']
                              if (value == 'field_refs' and 'view.fields.reorder' in kinds)
                              or (value.startswith(('title.', 'label.')) and 'label.set' in kinds)]
                if not properties:
                    continue
                if requested_properties is not None:
                    selected = requested_properties[node['ref']]
                    if not selected or not set(selected) <= set(properties):
                        raise BuilderWorkflowError('requested semantic properties are unsupported')
                    properties = selected
                values = {}
                for prop in properties:
                    values[prop] = (copy.deepcopy(_view(document, node['ref'])['field_refs']) if prop == 'field_refs'
                                    else _label(document, node['ref'])[prop.partition('.')[2]])
                masks.append({'ref': node['ref'], 'properties': properties})
                descriptors.append({**node, 'values': values})
            if not masks or (requested_refs is not None and {row['ref'] for row in masks} != set(requested_refs)):
                raise BuilderWorkflowError('requested write scope includes unsupported or unknown semantic refs')
            refs = [row['ref'] for row in masks]
            dependency_ids = dependency_refs(nodes, refs)
            record = {'schema': ADMISSION_SCHEMA, 'admission_id': admission_id, 'project_ref': project_ref,
                'change_id': gate['change']['change_id'], 'source_revision': self.graph.workflow.current_prototype_revision('scenario', gate['object_id']),
                'expected_generation': gate['generation'], 'base_hash': digest(document), 'runtime_hash': digest(runtime),
                'locale_hashes': {locale: digest(value) for locale, value in locales.items()},
                'source_request': origin, 'requested_write_refs': requested_refs,
                'requested_write_properties': requested_properties, 'operation_kinds': kinds,
                'max_ops': max_ops, 'write_masks': masks, 'status': 'admitted', 'compiler_digest': compiler_digest(),
                'context': {'schema': 'adaos.builder.semantic_op_context.v1', 'title': document['title'],
                    'operation_kinds': kinds, 'max_ops': max_ops, 'targets': descriptors,
                    'dependency_refs': dependency_ids,
                    'dependencies': [{key: node[key] for key in ('ref', 'kind', 'label_en', 'label_ru', 'dependencies')}
                                     for node in nodes if node['ref'] in dependency_ids and node['ref'] not in refs],
                    'read_scope': 'descriptors_for_existing_labels_and_table_field_order',
                    'preservation': 'Core validates the complete source, runtime, locales and protected semantics.'}}
            record['admission_digest'] = digest(record)
            atomic_write_json(path, record)
            return self._packet(record)

    @staticmethod
    def _packet(record):
        return copy.deepcopy({key: record[key] for key in ('schema', 'admission_id', 'admission_digest', 'project_ref',
            'change_id', 'source_revision', 'expected_generation', 'base_hash', 'operation_kinds', 'max_ops',
            'write_masks', 'status', 'context', 'compiler_digest')})

    def prepare(self, *, project_ref: str, admission_id: str, proposal: Mapping[str, Any]) -> dict[str, Any]:
        Draft202012Validator(proposal_contract()).validate(proposal)
        root, path = self._location(project_ref, admission_id)
        with _LOCK, mutation_lock(path.parent / '.mutation.lock'):
            retained = _read(path)
            immutable = {key: value for key, value in retained.items() if key not in ('admission_digest', 'prepared_request', 'proposal', 'prepared_digest')}
            if digest(immutable) != retained['admission_digest']:
                raise BuilderWorkflowError('semantic admission integrity mismatch')
            gate = self.graph.workflow.prototype_edit_context('scenario', project_ref.partition(':')[2])
            if retained['change_id'] != gate['change']['change_id'] or not gate['capabilities']['can_edit_prototype']:
                raise BuilderWorkflowError('semantic proposal requires its active editable Change')
            if 'proposal' in retained:
                if retained['proposal'] != proposal:
                    raise BuilderWorkflowError('conflicting proposal under the same semantic admission')
                return {'status': 'planned', 'request': copy.deepcopy(retained['prepared_request']), 'replayed': True}
            root, gate, document, runtime, locales = self._base(project_ref)
            if (gate['change']['change_id'] != retained['change_id'] or gate['generation'] != retained['expected_generation']
                    or self.graph.workflow.current_prototype_revision('scenario', gate['object_id']) != retained['source_revision']
                    or digest(document) != retained['base_hash'] or digest(runtime) != retained['runtime_hash']
                    or {locale: digest(value) for locale, value in locales.items()} != retained['locale_hashes']
                    or compiler_digest() != retained['compiler_digest']):
                raise BuilderWorkflowError('stale immutable semantic admission; explicit new scope is required')
            if proposal['status'] != 'proposed':
                if proposal['ops']:
                    raise BuilderWorkflowError('clarification or unsupported proposal cannot carry operations')
                return {'status': proposal['status'], 'message': proposal['message'], 'model_calls': 0}
            if not proposal['ops']:
                return {'status': 'noop', 'message': proposal['message'] or 'The proposal changes no admitted property.'}
            if len(proposal['ops']) > retained['max_ops']:
                raise BuilderWorkflowError('proposal exceeds admitted operation capacity')
            masks = {row['ref']: set(row['properties']) for row in retained['write_masks']}
            ops = []
            working = copy.deepcopy(document)
            for seq, proposed in enumerate(proposal['ops'], 1):
                kind, ref = proposed['kind'], proposed['ref']
                prop = ('title' if ref.startswith('view:') else 'label') + '.' + proposed.get('locale', '') if kind == 'label.set' else 'field_refs'
                if kind not in retained['operation_kinds'] or prop not in masks.get(ref, set()):
                    raise BuilderWorkflowError('proposal is outside the Core-admitted property write set')
                op = {'seq': seq, 'kind': kind, 'ref': ref}
                if kind == 'label.set':
                    op.update(locale=proposed['locale'], expected=_label(working, ref)[proposed['locale']], text=proposed['text'])
                else:
                    op.update(expected=copy.deepcopy(_view(working, ref)['field_refs']), field_refs=copy.deepcopy(proposed['field_refs']))
                working = reduce_graph(working, [dict(op, seq=1)], write_refs=[ref])['semantic_document']
                ops.append(op)
            request = {'schema': 'adaos.builder.semantic_graph_change.v1', 'project_ref': project_ref,
                'change_id': retained['change_id'], 'transaction_id': admission_id, 'source_revision': retained['source_revision'],
                'base_hash': retained['base_hash'], 'expected_generation': retained['expected_generation'],
                'write_refs': sorted({op['ref'] for op in ops}), 'ops': ops, 'source_request': retained['source_request'],
                'admission': {'id': admission_id, 'digest': retained['admission_digest']}}
            if digest(working) == retained['base_hash']:
                return {'status': 'noop', 'message': 'The proposal changes no admitted semantic property.'}
            atomic_write_json(path, {**retained, 'proposal': dict(proposal), 'prepared_request': request, 'prepared_digest': digest(request)})
            return {'status': 'planned', 'request': request, 'replayed': False}

    def prepare_document(self, *, project_ref: str, admission_id: str, candidate: Mapping[str, Any]) -> dict[str, Any]:
        """Controlled full-document reference with the same authority and commit gate."""
        if set(candidate) != {'status', 'message', 'semantic_document'}:
            raise BuilderWorkflowError('invalid full-document proposal envelope')
        if candidate['status'] not in ('proposed', 'clarification', 'unsupported'):
            raise BuilderWorkflowError('invalid full-document proposal status')
        if candidate['status'] != 'proposed':
            if candidate['semantic_document'] is not None:
                raise BuilderWorkflowError('nonproposal must not carry a candidate document')
            return self.prepare(project_ref=project_ref, admission_id=admission_id, proposal={
                'schema': PROPOSAL_SCHEMA, 'status': candidate['status'], 'message': candidate['message'], 'ops': []})
        _root, path = self._location(project_ref, admission_id)
        record = _read(path)
        _root, _gate, before, _runtime, _locales = self._base(project_ref)
        after = candidate['semantic_document']
        if not isinstance(after, Mapping):
            raise BuilderWorkflowError('full-document proposal requires a semantic object')
        expected = copy.deepcopy(before)
        ops = []
        for mask in record['write_masks']:
            ref = mask['ref']
            for prop in mask['properties']:
                if prop == 'field_refs':
                    original, updated = _view(before, ref)['field_refs'], _view(after, ref)['field_refs']
                    if original != updated:
                        _view(expected, ref)['field_refs'] = copy.deepcopy(updated)
                        ops.append({'kind': 'view.fields.reorder', 'ref': ref, 'field_refs': copy.deepcopy(updated)})
                else:
                    locale = prop.partition('.')[2]
                    original, updated = _label(before, ref)[locale], _label(after, ref)[locale]
                    if original != updated:
                        _label(expected, ref)[locale] = updated
                        ops.append({'kind': 'label.set', 'ref': ref, 'locale': locale, 'text': updated})
        if after != expected:
            raise BuilderWorkflowError('full-document candidate changed semantics outside its admitted property write set')
        return self.prepare(project_ref=project_ref, admission_id=admission_id, proposal={
            'schema': PROPOSAL_SCHEMA, 'status': 'proposed', 'message': candidate['message'], 'ops': ops})

    def verify_request(self, request: Mapping[str, Any]) -> None:
        permit = request['admission']
        _root, path = self._location(request['project_ref'], permit['id'])
        retained = _read(path)
        immutable = {key: value for key, value in retained.items() if key not in ('admission_digest', 'prepared_request', 'proposal', 'prepared_digest')}
        if (digest(immutable) != permit['digest'] or permit['digest'] != retained.get('admission_digest')
                or retained.get('prepared_request') != request or retained.get('prepared_digest') != digest(request)):
            raise BuilderWorkflowError('semantic request does not match its Core-issued admission')
        if compiler_digest() != retained['compiler_digest']:
            raise BuilderWorkflowError('compiler contracts changed since semantic admission; explicit new scope is required')
