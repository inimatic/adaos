"""One operation vocabulary for interpretation, generation and query admission."""
from __future__ import annotations

import copy

_OPERATIONS = {
    'list': 'Browse a collection of records. A search result/reset example is not a second list feature.',
    'inspect': 'Read a record or its fields, not change them.',
    'search': 'A free-text collection query, including narrowing by the entered text and reset. Its result examples are query verification, not a separate filter control.',
    'filter': 'A separately requested structured/equality collection control on a typed field. Do not infer it merely because a text search filters the displayed rows.',
    'sort': 'A separately requested ordering control; do not invent one from an example record order.',
    'create': 'An end user creates application data, not the authoring instruction to build the Prototype.',
    'update': 'An end user changes existing application data. Preserve any source-owned precondition/effect.',
    'assign': 'An end user changes a declared relationship or ownership assignment.',
    'transition': 'An end user executes a source-owned from/to state change, including its stored-state eligibility and preservation obligations.',
    'delete': 'An end user deletes application data.',
    'archive': 'An end user archives application data, not an unspecified destructive operation.',
    'other': 'A distinct source-requested product operation not covered above; never a bucket for acceptance examples, idempotence or unchanged values.',
}
_QUERY_KINDS = {'search': 'search', 'filter': 'filter'}


def query_kind_for_operation(operation):
    return _QUERY_KINDS.get(operation)


def operation_contract():
    return {'schema':'adaos.builder.product_operations.v1',
        'operations':copy.deepcopy(_OPERATIONS), 'query_control_kinds':dict(_QUERY_KINDS),
        'verification_boundary':'Expected results, reset outcomes, idempotence and unchanged values describe verification of the requested operation. Do not multiply product operations from those examples.',
        'query_example':{'source':'Add name search. Searching X shows only matching records; reset restores the full collection.',
            'product_operations':['search'],'verification_family':'query','not_product_operations':['filter','list','other']}}
