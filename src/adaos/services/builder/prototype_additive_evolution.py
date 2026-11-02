"""Source-admitted additive evolution of disposable Prototype data.

Not a production migration engine: old fields, identities, fixtures and live
values cannot be replaced. New cells require exact admitted source facts.
"""
from __future__ import annotations

import copy
from jsonschema import Draft202012Validator
from adaos.services.builder_context import digest
from .task_spec import seal


def _reject(detail):
    from .prototype_publications import PrototypePublicationRejected
    raise PrototypePublicationRejected('Additive Prototype evolution: '+detail)


def derive(previous, value, fork, old, new):
    contract=(value.get('brief') or {}).get('task_contract')
    if not contract:_reject('requires an admitted task contract; explicit migration is otherwise required')
    from .source_field_bindings import resolve_fields
    targets,findings=resolve_fields(value['document'],contract)
    if findings:_reject('source field admission is unresolved')
    cells={(target['resource']['id'],str(target['record']['id']),target['field']['id']):target['fact']['value'] for target in targets}
    def admitted(resource,record,field,expected):
        key=(resource,str(record),field)
        if key not in cells or digest(cells[key])!=digest(expected):_reject('new fixture cells need exact admitted source facts')
    if not set(old)<=set(new):_reject('cannot remove or rename an existing resource')
    old_documents={row['id']:row for row in previous['document']['resources']}
    new_documents={row['id']:row for row in value['document']['resources']}
    for name in old:
        if old_documents[name]['identity_field_refs']!=new_documents[name]['identity_field_refs']:
            _reject('cannot change an existing identity')
    rows={row['resource_ref']:row for row in fork['resources']}
    if set(rows)!=set(old):_reject('fork resources do not match the admitted base')
    merged={};added_fields={}
    for name,spec in new.items():
        after=spec['data_definition'];new_seed={str(row['id']):row for row in after['seed']}
        if len(new_seed)!=len(after['seed']):_reject('duplicate new fixture identity')
        if name not in old:
            added_fields[name]=set(after['record_schema']['properties'])-{'id','revision'}
            for record in new_seed.values():
                for field,expected in record.items():
                    if field not in {'id','revision'}:admitted(name,record['id'],field,expected)
            merged[name]=copy.deepcopy(after['seed'])
        else:
            before=old[name]['data_definition'];a,b=before['record_schema'],after['record_schema']
            if ({key:val for key,val in a.items() if key not in {'properties','required'}}
                !={key:val for key,val in b.items() if key not in {'properties','required'}}
                or not set(a['properties'])<=set(b['properties'])
                or any(b['properties'][key]!=schema for key,schema in a['properties'].items())
                or not set(a.get('required',[]))<=set(b.get('required',[]))
                or set(b.get('required',[]))&set(a['properties'])!=set(a.get('required',[]))):
                _reject('old record schemas must remain unchanged')
            old_seed={str(row['id']):row for row in before['seed']}
            if not set(old_seed)<=set(new_seed):_reject('cannot delete or rename a source fixture')
            for identity,record in old_seed.items():
                if any(field not in new_seed[identity] or digest(expected)!=digest(new_seed[identity][field])
                       for field,expected in record.items()):_reject('cannot replace old fixture values')
            added_fields[name]=set(b['properties'])-set(a['properties'])
            for identity,record in new_seed.items():
                for field,expected in record.items():
                    if field in added_fields[name] or identity not in old_seed:
                        if field not in {'id','revision'}:admitted(name,identity,field,expected)
            snapshot=rows[name]
            if digest(snapshot['records'])!=snapshot['records_digest']:_reject('fork record pin changed')
            live=copy.deepcopy(snapshot['records']);live_ids={str(row['id']) for row in live}
            if len(live_ids)!=len(live):_reject('duplicate live identity')
            for record in live:
                for field in added_fields[name]:
                    if field in record:_reject('new field collides with live data')
                    if str(record['id']) in old_seed and field in new_seed[str(record['id'])]:
                        record[field]=copy.deepcopy(new_seed[str(record['id'])][field])
                    elif Draft202012Validator(b['properties'][field]).is_valid(None):record[field]=None
                    else:_reject('live-only records need explicit required-field migration')
            for identity in sorted(set(new_seed)-set(old_seed)):
                if identity in live_ids:_reject('new fixture identity collides with a live-created record')
                live.append(copy.deepcopy(new_seed[identity]))
            merged[name]=live
        if len(merged[name])>1000:_reject('bounded record capacity exceeded')
        from .prototype_runtime import PrototypeDataRuntime
        PrototypeDataRuntime.start({**after,'seed':merged[name]})
    prior_links={row['id']:row for row in previous['document'].get('relationships',[])}
    links={row['id']:row for row in value['document'].get('relationships',[])}
    if not set(prior_links)<=set(links) or any(links[key]!=row for key,row in prior_links.items()):
        _reject('cannot remove or reinterpret an existing relationship')
    for key,link in links.items():
        if key in prior_links:continue
        source,target=link['from_resource_ref'],link['to_resource_ref']
        if source in old and link['from_field_ref'] not in added_fields[source]:
            _reject('a new relationship may not reinterpret an existing live field')
        def token(record,field):return digest(record.get(field))
        target_values=[token(row,link['to_field_ref']) for row in merged[target] if row.get(link['to_field_ref']) is not None]
        source_values=[token(row,link['from_field_ref']) for row in merged[source] if row.get(link['from_field_ref']) is not None]
        if link['cardinality'] not in {'many_to_one','one_to_one'}:_reject('inverse relationships need an explicit migration')
        if len(target_values)!=len(set(target_values)) or not set(source_values)<=set(target_values):
            _reject('relationship would have missing or ambiguous live targets')
        if link['cardinality']=='one_to_one' and len(source_values)!=len(set(source_values)):
            _reject('one-to-one relationship has duplicate live source values')
    return seal({'schema':'adaos.builder.prototype_additive_evolution.v1',
        'base_fork_ref':fork['fork_id'],'source_digest':value['seed']['source_digest'],
        'resources':[{'resource_ref':name,'records':records,'records_digest':digest(records)} for name,records in sorted(merged.items())],
        'policy':'Preserve live old values and deletions; admit only source-backed additions. No destructive, retyping or source-value migration.'},
        id_key='evolution_id',prefix='prototype-additive-evolution:')
