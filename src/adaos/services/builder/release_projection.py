"""Keep editable Automation lineage distinct from installed release authority."""
from copy import deepcopy


def project_release_lineage(nodes, *, lifecycle, workflow):
    rows = deepcopy(nodes)
    if lifecycle.get('available') is False:
        return rows  # An unregistered authoring source has no runtime authority.
    runtime = lifecycle.get('runtime') or {}
    candidate = lifecycle.get('candidate') or {}
    current_candidate = (workflow.get('delivery') or {}).get('candidate_id')
    allowed = {item.get('command') for item in (workflow.get('workflow_description') or {}).get('allowed_commands', [])}
    can_publish = bool(allowed & {'accept_verification', 'start_trial', 'accept_trial', 'begin_publication'})
    selected = runtime.get('release_digest')
    stage = 'Stable' if runtime.get('admission_kind') == 'stable' else 'Beta'
    automation = workflow.get('automation') or {}
    retained = bool(automation.get('snapshot_path') or automation.get('snapshot_task_id'))

    def visit(row, dev_version):
        if row.get('kind') == 'automation_result' and row.get('lineageInferred'):
            row.update(status='Source not linked', canPreview=False, canOpenAutomation=False,
                       summary='This release has no retained link to an editable Automation result. Its version does not identify the current DEV head.')
        elif (row.get('kind') == 'automation_result' and not retained and dev_version and row.get('version')
              and str(row['version']) != str(dev_version)):
            row.update(status='Source snapshot unavailable', canPreview=False, canOpenAutomation=False,
                       summary='The current DEV version differs from this Automation result, which has no retained source snapshot.')
        if row.get('kind') == 'publication_release':
            digest = row.get('release_digest') or row.get('release')
            exact_selected = bool(selected and digest == selected)
            row['canOpenPublication'] = bool(can_publish and not row.get('lineageInferred')
                and (not exact_selected or candidate.get('candidate_id') == current_candidate))
            if exact_selected:
                row.update(status=stage.lower(), selected=True, release_digest=selected,
                           title=f"{stage} {runtime.get('version')}")
        for child in row.get('children') or []:
            visit(child, dev_version)
    for row in rows:
        visit(row, row.get('version'))
        if selected:
            row['subtitle'] = f"Installed: {stage} {runtime.get('version')}"
    return rows
