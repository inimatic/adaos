"""One bounded source-authority rule shared by interpretation input/admission.

This does not infer a complete product specification. In particular a field
enumeration or example is data, not a requested display order. Unknown phrasing
must remain unproved rather than silently becoming a hard assertion.
"""
from __future__ import annotations

import copy
import re

from .task_spec import source_clauses

_GENERIC_COLLECTION = re.compile(
    r'(?:(?:the|one|single|existing|current|original)\s+)*(?:table|collection|list)'
    r'|(?:(?:существующая|текущая|исходная|единственная)\s+)*(?:таблица|коллекция|список)', re.I)

_SCOPED_SELECTOR = re.compile(
    r'\b(?P<base>(?:(?:the|one|single|existing|current|original)\s+)*(?:table|collection|list|board|chart|tree|cards|accordion|details|editor)\b)'
    r'(?:\s+in\s+section\s+"(?P<section>[^"]+)")?'
    r'(?:\s+for\s+resource\s+"(?P<resource>[^"]+)")?', re.I)


def resolve_reference(context, *, ref=None, role=None, presentation=None, section=None, resource=None, name=None):
    """Resolve one design-time referent in the frozen inventory, never runtime data.

    Exact captions may scope a referent but never win over ambiguity. IDs are
    canonical after resolution. This function is shared by model and Core rows.
    """
    def named(value, identity, label):
        return value is None or value == identity or value in [text for key, text in (label or {}).items() if key != 'key']
    cards = [*context.get('collections', []), *context.get('supporting_views', [])]
    resources = {row['id']: row for row in context.get('resources', [])}
    matches = []
    for card in cards:
        card_ref = card.get('ref', 'view:' + card.get('id', ''))
        owner = card['resource_ref'].removeprefix('resource:')
        scope = card.get('section') or {}
        if (ref is not None and ref != card_ref
                or not named(name, card.get('id', card_ref.removeprefix('view:')), card.get('title'))
                or role is not None and card.get('role', 'collection') != role
                or presentation is not None and (card.get('role', 'collection') != 'collection'
                    or card.get('presentation') not in (None, presentation))
                or not named(section, scope.get('id'), scope.get('title'))
                or not named(resource, owner, resources.get(owner, {}).get('item_label'))):
            continue
        matches.append({**card, 'ref': card_ref})
    if len(matches) != 1:
        from .task_interpretation import TaskInterpretationRejected
        raise TaskInterpretationRejected([{'code': 'task.display_selector_ambiguous', 'gate': 'G3',
            'owner': 'task_interpretation', 'detail': 'An existing reference needs one exact frozen-base owner; clarify missing or ambiguous scope'}])
    card = matches[0]
    from adaos.services.builder_context import digest
    return {'ref': card['ref'], 'baseline_digest': context['baseline_digest'],
            'role': card.get('role', 'collection'), 'presentation': card.get('presentation'),
            'section_ref': ('section:' + card['section']['id']) if card.get('section') else None,
            'resource_ref': 'resource:' + card['resource_ref'].removeprefix('resource:'),
            'owner_digest': digest(card)}


def resolve_feature_reference(context, *, kind, name=None, owner_view_ref=None):
    """Exact existing operation identity; labels select objects, not equivalence."""
    groups = {'search': 'query_controls', 'filter': 'query_controls', 'query': 'query_controls',
              'command': 'commands', 'state': 'state_proofs'}
    cards = [row for row in context.get(groups[kind], [])
             if (kind not in {'search', 'filter'} or row['kind'] == kind)
             and (owner_view_ref is None or row['owner_view_ref'] == owner_view_ref)
             and (name is None or name in {row['ref'], row['ref'].partition(':')[2],
                 *[value for locale, value in (row.get('label') or {}).items() if locale != 'key']})]
    if len(cards) != 1:
        from .task_interpretation import TaskInterpretationRejected
        raise TaskInterpretationRejected([{'code': 'task.feature_selector_ambiguous', 'gate': 'G3',
            'owner': 'task_interpretation', 'detail': 'An existing feature requires one exact kind/name/owner in the frozen base'}])
    return copy.deepcopy(cards[0])


def reference_context(baseline, previous_brief=None):
    """Project identities, not records or new requirements, from the exact base."""
    from adaos.services.builder_context import digest
    if not baseline:
        return None
    document = baseline.get('document') if baseline.get('schema') == 'adaos.builder.semantic_compiler_view.v1' else baseline
    if baseline.get('schema') == 'adaos.builder.semantic_compiler_view.v1' and digest(document) != baseline.get('view_digest'):
        raise ValueError('Interpretation reference context requires the pinned compiler view')
    bindings = {}
    for row in document.get('requirement_bindings', []):
        for ref in row['semantic_refs']:
            bindings.setdefault(ref, []).append(row['requirement_ref'])
    def evidence(ref, **value):
        return {'ref': ref, **value, 'prior_requirement_refs': sorted(set(bindings.get(ref, [])))}
    views = {view['id']: view for view in document.get('views', [])}
    context = {'schema': 'adaos.builder.interpretation_references.v1', 'baseline_digest': digest(baseline),
            'resources': [{'id': resource['id'], 'item_label': resource.get('item_label', {}),
                           'field_ids': [field['id'] for field in resource.get('fields', [])]}
                          for resource in document.get('resources', [])],
            'collections': [evidence('view:' + view['id'], id=view['id'], resource_ref=view['resource_ref'],
                             role=view['role'], presentation=view.get('presentation'), section=view.get('section'),
                             title=view.get('title', {}), field_refs=view.get('field_refs', []))
                            for view in document.get('views', []) if view.get('role') == 'collection'],
            'query_controls': [evidence('query:' + control['id'], kind=control['kind'],
                owner_view_ref='view:' + view['id'], resource_ref='resource:' + view['resource_ref'],
                field_ref=control['field_ref'], label=control.get('label', {}))
                for view in document.get('views', []) for control in view.get('query_controls', [])],
            'commands': [evidence('command:' + command['id'], kind=command['kind'],
                owner_view_ref='view:' + command['view_ref'], label=command.get('label', {}),
                collection_view_ref=('view:' + command['exposure']['collection_view_ref']
                    if (command.get('exposure') or {}).get('collection_view_ref') else None))
                for command in document.get('commands', [])],
            'state_proofs': [evidence('state:' + state['id'], owner_view_ref='view:' + state['view_ref'],
                proof_kind=state['proof']['kind'], label=state.get('label', {}))
                for state in document.get('representative_states', [])],
            'supporting_views': [evidence('view:' + view['id'], role=view['role'],
                id=view['id'], presentation=view.get('presentation'), section=view.get('section'),
                resource_ref='resource:' + view['resource_ref'], title=view.get('title', {}),
                field_refs=view.get('field_refs', [])) for view in views.values() if view.get('role') != 'collection'],
            'policy': 'Read-only existing target identities, not extra user requirements or permission to redesign. '
                'Resolve existing references by exact role/presentation, optional section and resource scope, then use '
                'view:<exact id>. A table is not a list/board/chart; do not count unrelated presentation peers as tables. '
                'A unique match is required; otherwise ask a material clarification. New named collections are not existing ones. '
                'Runtime selected record is a runtime selector, never one snapshotted record ID. '
                'Query/command/state cards project existing canonical semantic owners and prior proof refs; they do not '
                'create new operations, fixture states or output requirements. Preserve unchanged behavior through '
                'the admitted history instead of re-extracting it as a new operation. These read refs grant no write authority.'}
    if previous_brief:
        from .semantic_reaffirmation import candidates
        context['reaffirmation_candidates'] = candidates(baseline, previous_brief)
    return copy.deepcopy(context)


def normalize_generic_selectors(task, proposal):
    """Resolve a source-explicit generic selector only against a unique base.

    Raw proposals remain immutable. No arbitrary noun/name matching or first-
    collection fallback is permitted, and ambiguity stops at interpretation.
    """
    import copy
    context = task.get('reference_context')
    if not context:
        return proposal, []
    clauses = {row['id']: row['text'] for row in task['clauses']}
    result = copy.deepcopy(proposal)
    receipts = []
    def source_selector(cited):
        from .source_relations import _unquoted
        matches = [match for text in cited for match in _SCOPED_SELECTOR.finditer(text)
                   if _unquoted(text)[match.start()]
                   and re.search(r'\b(?:existing|current|original)\b', match['base'], re.I)]
        scopes = {(m['base'].split()[-1].casefold(), m['section'], m['resource']) for m in matches}
        return matches[0] if len(scopes) == 1 else None
    for index, row in enumerate(result.get('constraints', [])):
        subject = row.get('subject', '').strip()
        if row.get('kind') not in {'view_title', 'field_order'}:
            continue
        from .source_relations import _unquoted
        cited = [clauses.get(ref, '') for ref in row.get('clause_ids', [])]
        selector = _SCOPED_SELECTOR.fullmatch(subject)
        typed = subject.startswith('view:')
        if typed:
            selector = source_selector(cited)
            if selector is None:
                continue
        if subject:
            if not typed and not selector and not _GENERIC_COLLECTION.fullmatch(subject):
                continue
            pattern = r'(?<!\w)' + re.escape(subject) + r'(?!\w)'
            if not typed and not any(_unquoted(text)[match.start()] for text in cited for match in re.finditer(pattern, text, re.I)):
                continue
        else:
            # The same Core lexical target constructor also emits an implicit
            # generic owner. Resolve it through the SAME inventory, not a second
            # late candidate-wide lookup after new peers have been introduced.
            from .semantic_ui_constraints import source_targets
            if not any(item['kind']==row['kind'] and item['subject']=='' and item['values']==row['values']
                       and set(item['clause_ids'])<=set(row.get('clause_ids',[]))
                       for item in source_targets(task['source']['statement'] if task.get('source') else ''.join(cited))):
                continue
            # Recover an explicit existing-kind/scope from the cited SOURCE,
            # never from newly generated peers. Multiple different selectors
            # remain ambiguous; do not select the first textual mention.
            selector = source_selector(cited)
        target = (selector['base'].split()[-1].casefold() if selector else
                  'table' if subject.casefold().endswith('таблица') else
                  'list' if subject.casefold().endswith('список') else None)
        resolution = resolve_reference(context,
            role=target if target in {'details', 'editor'} else 'collection',
            presentation=target if target not in {None, 'collection', 'details', 'editor'} else None,
            section=selector['section'] if selector else None,
            resource=selector['resource'] if selector else None)
        if typed and subject != resolution['ref']:
            from .task_interpretation import TaskInterpretationRejected
            raise TaskInterpretationRejected([{'code': 'task.display_selector_conflict', 'gate': 'G3',
                'owner': 'task_interpretation', 'detail': 'A proposed typed reference conflicts with the source-scoped existing target'}])
        row['subject'] = resolution['ref']
        receipts.append({'kind': 'unique_existing_collection_selector', 'path': f'$.constraints[{index}].subject',
                         'from': subject, 'to': row['subject'], 'owner_ref': row['subject'],
                         'baseline_digest': context['baseline_digest'],
                         'resolution': {**resolution, 'source_clause_ids': copy.deepcopy(row['clause_ids'])}})
    return result, receipts

_ORDER = re.compile(
    r'\b(?:in\s+(?:(?:this|that|the|their|its|following|specified|same|existing|relative)\s+)*order'
    r'|(?:column|field)s?\s+order|order\s+(?:the\s+)?(?:columns|fields)'
    r'|поряд(?:ок|ке|ка)\s+(?:столбцов|колонок|полей)'
    r'|в\s+(?:(?:этом|таком|следующем|указанном|прежнем)\s+)?порядке)\b', re.I)


def contract():
    return {
        'schema': 'adaos.builder.interpretation_boundary.v1',
        'mandatory': 'Only source-requested outcomes and exact admitted prior obligations are mandatory. '
            'Keep their owner, polarity, preconditions and evidence. A fixture value is a data fact, not a presentation decision.',
        'design_freedom': 'Unspecified reversible presentation choices remain design_freedom. '
            'Do not turn your preferred order, layout, control label or examples into a new user constraint. '
            'No material question is needed merely to choose an unspecified column order.',
        'field_order': 'Emit a field_order constraint ONLY when the cited source explicitly requires ordering. '
            'A list of source fields, record tuple order, Show all source fields in a table, or a collection introduction '
            'does not require that order. Omit that constraint and choose a valid design instead. '
            'For an explicit new ordering cite the clause with the ordering instruction AND all ordered field literals; '
            'do not cite an introduction or another collection. Retain prior accepted orders without re-inventing them.',
        'exact_display_labels': 'A mentioned concept such as Teams directory requires that concept, NOT the exact '
            'heading Teams directory. Emit view_title/field_label only for an explicit naming/caption directive, '
            'using source_display_directives as bounded lexical evidence. Otherwise keep the concept in its UI/collection '
            'requirement and choose its caption as a reversible design. A request to keep a named existing heading '
            'may repeat an EXACT already-active prior constraint with the same kind, subject and value; it cannot '
            'create a new caption obligation from a conceptual mention. Normally retain that prior assertion without '
            're-emitting it. Do not ask for a heading merely because it is unspecified.',
        'scope': 'This bounded admission rule qualifies explicit EN/RU ordering/naming directives and exact prior-label preservation; '
            'it is not general natural-language completeness or permission to discard other requirements.',
    }


def field_order_evidence(statement):
    from .source_relations import _unquoted
    from .source_operators import record_head_operator
    from .source_literals import _parts
    rows = []
    for clause in source_clauses(statement):
        text = clause['text']
        outside = _unquoted(text)
        for match in _ORDER.finditer(text):
            begin, end = next((a, b) for a, b in _parts(text, '.!?\n') if a <= match.start() < b)
            prefix = text[begin:match.start()]
            if not outside[match.start()] or record_head_operator(text[begin:end], source_start=clause['start'] + begin):
                continue
            if re.search(r'\b(?:example|e\.g\.|for\s+instance|if|when|пример|например|если|когда)\b', prefix, re.I):
                continue
            if re.search(r'\b(?:any|arbitrary|unspecified|no\s+required|любой|произвольн\w*)\s*$', prefix, re.I):
                continue
            if re.search(r'\b(?:arbitrary|optional|unspecified|not\s+(?:required|specified)|произвольн\w*|не\s+задан\w*)\b',
                         text[match.end():end], re.I):
                continue
            rows.append({'clause_id': clause['id'], 'operator': match.group(),
                         'start': clause['start'] + match.start(), 'end': clause['start'] + match.end()})
    return rows


def display_directive_evidence(statement):
    from .semantic_ui_constraints import source_targets
    from .source_relations import _unquoted, _label
    from .source_operators import record_head_operator
    from .source_literals import _parts
    rows = source_targets(statement)
    pattern = re.compile(
        r'\b(?:table|collection|view|list|directory|таблиц\w*|коллекци\w*|представлени\w*|спис\w*|справочник\w*)\s+'
        r'(?:titled|named|called|с\s+(?:названием|заголовком)|под\s+названием)\s+'
        r'(?P<literal>"(?:\\.|[^"\\])*"|«[^»]+»|“[^”]+”|`[^`]+`|[\w.-]+)', re.I)
    for clause in source_clauses(statement):
        text = clause['text']; outside = _unquoted(text)
        for match in pattern.finditer(text):
            begin, end = next((a, b) for a, b in _parts(text, '.!?\n') if a <= match.start() < b)
            if not outside[match.start()] or record_head_operator(text[begin:end], source_start=clause['start'] + begin):
                continue
            if re.search(r'\b(?:example|e\.g\.|if|when|например|если|когда)\b', text[begin:match.start()], re.I):
                continue
            rows.append({'kind': 'view_title', 'subject': '', 'values': [_label(match['literal'])],
                         'clause_ids': [clause['id']]})
    return rows


def _preserves_prior_label(row, clauses, retained_constraints):
    """Preservation can retain exact authority, never invent another owner/value."""
    from .source_relations import _unquoted
    from .source_operators import record_head_operator
    from .source_literals import _parts
    if not any(all(prior.get(key) == row.get(key) for key in ('kind', 'subject', 'values'))
               for prior in retained_constraints):
        return False
    preserve = re.compile(r'\b(?:keep|preserve|retain|сохрани\w*|остав\w*)\b', re.I)
    for ref in row.get('clause_ids', []):
        text = clauses.get(ref, {}).get('text', '')
        outside = _unquoted(text)
        for match in preserve.finditer(text):
            begin, end = next((a, b) for a, b in _parts(text, '.!?\n') if a <= match.start() < b)
            prefix, suffix = text[begin:match.start()], text[match.end():end]
            if (not outside[match.start()] or record_head_operator(text[begin:end], source_start=begin)
                    or re.search(r'\b(?:not|never|if|when|example|не|если|когда|например)\b', prefix, re.I)):
                continue
            if all(str(value) in suffix for value in row.get('values', [])):
                return True
    return False


def findings(statement, proposal, *, retained_constraints=()):
    source = {row['id']: row for row in source_clauses(statement)}
    evidence = {row['clause_id'] for row in field_order_evidence(statement)}
    display = display_directive_evidence(statement)
    result = []
    for index, row in enumerate(proposal.get('constraints', [])):
        if row.get('kind') in {'view_title', 'field_label'}:
            if not any(item['kind'] == row['kind'] and item['values'] == row['values']
                       and set(item['clause_ids']) <= set(row.get('clause_ids', [])) for item in display
                       ) and not _preserves_prior_label(row, source, retained_constraints):
                result.append({'code': 'task.constraint_authority_unproved', 'gate': 'G3',
                    'owner': 'task_interpretation', 'path': f'$.constraints[{index}]',
                    'source_clause_ids': row.get('clause_ids', []),
                    'detail': row['kind'] + ' needs an explicit source naming/caption directive; '
                        'mentioning a concept is not a mandatory exact display label. Keep unspecified captions as design freedom.'})
            continue
        if row.get('kind') != 'field_order':
            continue
        cited = [source[identity]['text'] for identity in row.get('clause_ids', [])
                 if identity in evidence and identity in source]
        # Field literals must occur together in a clause actually requesting
        # ordering; joining an introduction to unrelated fixture clauses is not proof.
        if not any(all(re.search(r'(?<![\w.-])' + re.escape(str(value)) + r'(?![\w-]|\.\w)', text, re.I)
                           for value in row.get('values', [])) for text in cited):
            result.append({'code': 'task.constraint_authority_unproved', 'gate': 'G3',
                'owner': 'task_interpretation', 'path': f'$.constraints[{index}]',
                'source_clause_ids': row.get('clause_ids', []),
                'detail': 'field_order needs an explicit source ordering directive citing its field literals; '
                    'a field enumeration or record example is not mandatory display order. '
                    'Keep unspecified presentation as design_freedom, without dropping requested fields.'})
    return result
