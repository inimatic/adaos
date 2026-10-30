"""Persist G9 ownership before authorizing any paid candidate repair."""
from __future__ import annotations

import copy

from .task_spec import seal


def _failure_class(finding):
    code = finding.get('code', '')
    if not isinstance(code, str):
        return 'unclassified'
    if finding.get('gate') in {'G5','G6'} and finding.get('owner') == 'context_compilation':
        return 'context_delivery'
    if code.startswith('context.'):
        return 'context_delivery'
    if code.startswith('capability.') or finding.get('owner') == 'capability_admission':
        return 'capability_gap'
    if code.startswith(('runtime.', 'platform.', 'semantic.compiler_')):
        return 'platform_runtime'
    # The task namespace also contains independent candidate-acceptance
    # findings. Their trusted emitter/gate, not their code prefix, owns them.
    if (finding.get('owner') == 'candidate' and finding.get('gate') in {'G7','G8'}
            and code.startswith(('semantic.','task.','requirement.'))):
        return 'candidate'
    if code.startswith('task.'):
        return 'interpretation'
    if code.startswith('semantic.') or code == 'requirement.coverage_missing':
        return 'candidate'
    return 'unclassified'


def compile_repair_diagnosis(*, candidate_digest, findings, parent, compiler_digest):
    classes = sorted({_failure_class(row) for row in findings} or {'unclassified'})
    actions = {'candidate':'repair_candidate','interpretation':'readmit_task',
        'context_delivery':'recompile_context','capability_gap':'report_capability_gap',
        'platform_runtime':'repair_platform','unclassified':'inspect_diagnostics'}
    allowed = classes == ['candidate']
    return seal({'schema':'adaos.builder.repair_diagnosis.v1',
        'parent_generation_plan_ref':parent['generation_plan_id'],
        'task_spec_ref':parent['task_spec_ref'],'candidate_digest':candidate_digest,
        'compiler_digest':compiler_digest,'findings':copy.deepcopy(findings),
        'failure_classes':classes,'paid_candidate_repair_allowed':allowed,
        'next_actions':[actions[value] for value in classes if value != 'candidate' or allowed]},
        id_key='diagnosis_id',prefix='repair-diagnosis:')
