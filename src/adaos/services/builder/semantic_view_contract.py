"""Provider-side structural closure of the supported presentation combinations.

Cross-element references and data-dependent predicates remain compiler checks.
Keep the authoring schema backward compatible; this constrains fresh output.
"""
from __future__ import annotations

import copy


def selection_variants(policy):
    disabled = copy.deepcopy(policy)
    disabled['properties']['mode'] = {'type': 'string', 'enum': ['none']}
    disabled['properties']['row_activation'] = {'type': 'string', 'enum': ['none']}
    disabled['properties']['indicator'] = {'type': 'string', 'enum': ['none', 'row_accent']}
    single = copy.deepcopy(policy)
    single['properties']['mode'] = {'type': 'string', 'enum': ['single']}
    single['properties']['row_activation'] = {'type': 'string', 'enum': ['select', 'open_details']}
    return disabled, single


def collection_variants(collection, definitions):
    variants = []
    for presentation in ('list', 'table', 'cards', 'board', 'tree', 'chart', 'accordion'):
        view = copy.deepcopy(collection)
        props = view['properties']
        props['presentation'] = {'type': 'string', 'enum': [presentation]}
        if presentation != 'table':
            props['compact_presentation'] = {'type': 'null'}
        if presentation not in {'list', 'table', 'cards', 'accordion'}:
            props['field_display'] = {'type': 'array', 'items': {'type': 'null'}, 'maxItems': 0}
        if presentation in {'tree', 'chart'}:
            props['media'] = {'type': 'null'}
        if presentation != 'table':
            link = copy.deepcopy(definitions['selectionFilter'])
            link['properties']['effect'] = {'type': 'string', 'enum': ['filter']}
            props['selection_filter'] = {'anyOf': [link, {'type': 'null'}]}
        if presentation == 'chart':
            props['selection'] = {'anyOf': [copy.deepcopy(definitions['selectionPolicy']['anyOf'][0]), {'type': 'null'}]}
            props['initial_selection_id'] = {'type': 'null'}
        if presentation in {'table', 'cards'}:
            props['presentation_options'] = {'type': 'null'}
        else:
            unranked = copy.deepcopy(view)
            options = copy.deepcopy(definitions['presentationOptions'])
            active = {'list': {'order_field_ref'}, 'board': {'group_field_ref'},
                      'tree': {'parent_field_ref'}, 'chart': {'group_field_ref', 'value_field_ref'},
                      'accordion': {'group_field_ref'}}[presentation]
            for name in ('group_field_ref', 'parent_field_ref', 'value_field_ref', 'order_field_ref'):
                options['properties'][name] = ({'$ref': '#/$defs/id'} if name in active else {'type': 'null'})
            if presentation not in {'list', 'board'}:
                options['properties']['draggable'] = {'type': 'boolean', 'enum': [False]}
            elif presentation == 'list':
                options['properties']['draggable'] = {'type': 'boolean', 'enum': [True]}
                # A rank move cannot be defined over a hidden/filtered subset.
                for name in ('query_controls', 'scope_filters'):
                    props[name] = {'type': 'array', 'items': {'type': 'null'}, 'maxItems': 0}
                for name in ('filter', 'selection_filter'):
                    props[name] = {'type': 'null'}
            if presentation == 'list':
                # Ordinary lists do support queries. Only their reorder variant
                # disallows hidden records; keep both, never prune the feature.
                plain = unranked
                plain['properties']['presentation'] = {'type': 'string', 'enum': ['list']}
                plain['properties']['compact_presentation'] = {'type': 'null'}
                plain['properties']['presentation_options'] = {'type': 'null'}
                variants.append(plain)
            props['presentation_options'] = options
        variants.append(view)
    return variants
