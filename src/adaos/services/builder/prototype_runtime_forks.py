"""Bounded local data forks and admitted additive Prototype evolution.

Runtime records never become semantic source or model context. A fork pins the
accepted base's data and refuses silent migration/reseeding. The caller holds
the resource lock while capturing or validating a base.
"""

from __future__ import annotations

import copy

from adaos.services.builder_context import digest
from adaos.services.resources.prototype import PrototypeResourceService
from adaos.services.resources.prototype_publication_guard import assert_current_mutation

from .task_spec import seal, verify_identity


def _reject(message):
    from .prototype_publications import PrototypePublicationRejected

    raise PrototypePublicationRejected(message)


def bindings(preparation):
    """Map compiler-declared logical resources, never parse truncated names."""
    compiled = preparation["compiled"]
    rows = compiled.get("prototype_resources")
    if rows is None:
        specs = preparation["resource_specs"]
        if len(specs) != 1:
            _reject("Runtime fork requires an unambiguous logical resource")
        return {preparation["document"]["resource"]["id"]: specs[0]}
    names = {row["resource_type"]: row["resource_ref"] for row in rows}
    result = {
        names[spec["resource_definition"]["resource_type"]]: spec
        for spec in preparation["resource_specs"]
    }
    if len(result) != len(rows) or len(names) != len(rows):
        _reject("Runtime fork has ambiguous compiler resource bindings")
    return result


def _resource_contract(document):
    resources = document.get("resources")
    if resources is None:
        resources = [document["resource"]]
    return {row["id"]: row["identity_field_refs"] for row in resources}


def storage_relationships(document):
    """Keep storage identity/direction/cardinality separate from UI captions."""
    return {row['id']: {key: copy.deepcopy(value) for key, value in row.items()
                        if key not in {'label_field_refs', 'fulfills'}}
            for row in document.get('relationships', [])}


def capture(store, publication_ref, *, project_ref, base_revision):
    previous = store.ready(publication_ref)["preparation"]
    if (
        previous["seed"]["project_ref"] != project_ref
        or previous["seed"]["revision"] != base_revision
    ):
        _reject("Runtime fork base target or revision mismatch")
    runtime = PrototypeResourceService(state_dir=store.state_dir)
    rows = []
    for logical_ref, spec in sorted(bindings(previous).items()):
        state = runtime.snapshot(spec["resource_definition"]["resource_type"])
        assert_current_mutation(state, store.state_dir)
        records = state["records"]
        if not isinstance(records, list) or len(records) > 1000:
            _reject("Runtime fork records exceed their bounded object array")
        rows.append(
            {
                "resource_ref": logical_ref,
                "resource_type": state["resource_type"],
                "definition_digest": state["definition_digest"],
                "bundle_digest": state["bundle_digest"],
                "generation": state["generation"],
                "records_digest": digest(records),
                "records": copy.deepcopy(records),
            }
        )
    result = seal(
        {
            "schema": "adaos.builder.prototype_runtime_fork.v1",
            "project_ref": project_ref,
            "base_publication_ref": publication_ref,
            "base_preparation_ref": previous["preparation_id"],
            "base_revision": base_revision,
            "resources": rows,
        },
        id_key="fork_id",
        prefix="runtime-fork:",
    )
    store._bounded(result)
    return result


def apply(store, value, fork):
    """Attach initial-data pins without rewriting semantic fixtures/reset data."""
    verify_identity(fork, id_key="fork_id", prefix="runtime-fork:")
    previous = store.load(fork["base_publication_ref"])
    if store._runtime_project_ref(previous["seed"]) != store._runtime_project_ref(
        value["seed"]
    ):
        _reject("Runtime fork requires explicit ownership namespace migration")
    old, new = bindings(previous), bindings(value)
    changed=(_resource_contract(previous['document'])!=_resource_contract(value['document'])
        or storage_relationships(previous['document'])!=storage_relationships(value['document'])
        or set(old)!=set(new) or any(old[key]['data_definition']['record_schema']!=new[key]['data_definition']['record_schema']
            or old[key]['data_definition']['seed']!=new[key]['data_definition']['seed'] for key in set(old)&set(new)))
    if changed:
        from .prototype_additive_evolution import derive
        evolution=derive(previous,value,fork,old,new)
        if value.get('runtime_evolution') not in (None,evolution):_reject('Additive evolution pin changed')
        value['runtime_evolution']=evolution
        for row in evolution['resources']:
            metadata=new[row['resource_ref']]['resource_definition']['metadata']
            metadata['prototype_runtime_fork_ref']=fork['fork_id']
            metadata['prototype_runtime_evolution_ref']=evolution['evolution_id']
            metadata['prototype_initial_records_digest']=row['records_digest']
        return
    if _resource_contract(previous["document"]) != _resource_contract(
        value["document"]
    ) or set(old) != set(new):
        _reject("Runtime fork requires explicit resource/identity migration")
    if storage_relationships(previous['document']) != storage_relationships(value['document']):
        _reject("Runtime fork requires explicit relationship migration")
    rows = {row["resource_ref"]: row for row in fork["resources"]}
    for logical_ref, spec in new.items():
        before, after = old[logical_ref]["data_definition"], spec["data_definition"]
        if before["record_schema"] != after["record_schema"]:
            _reject("Runtime fork requires explicit record schema migration")
        if before["seed"] != after["seed"]:
            _reject("Runtime fork requires explicit fixture change admission")
        row = rows[logical_ref]
        if digest(row["records"]) != row["records_digest"]:
            _reject("Runtime fork records differ from their pin")
        from .prototype_runtime import PrototypeDataRuntime

        # Validates bounded records, IDs and the new schema before resource I/O.
        PrototypeDataRuntime.start({**after, "seed": row["records"]})
        metadata = spec["resource_definition"]["metadata"]
        metadata["prototype_runtime_fork_ref"] = fork["fork_id"]
        metadata["prototype_initial_records_digest"] = row["records_digest"]


def assert_current(store, value):
    """Check the captured data base inside the guarded head-commit interval."""
    fork = value.get("runtime_fork")
    previous_ref = value["seed"]["previous_publication_ref"]
    if previous_ref is None:
        if fork is not None:
            _reject("Initial publication cannot claim a runtime fork")
        return
    if fork is None:
        _reject("Successor publication requires a pinned compatible runtime fork")
    verify_identity(fork, id_key="fork_id", prefix="runtime-fork:")
    if (
        fork["base_publication_ref"] != previous_ref
        or fork["project_ref"] != value["seed"]["project_ref"]
        or fork["base_revision"] != value["seed"]["base_revision"]
        or fork["fork_id"] != value["seed"].get("runtime_fork_ref")
    ):
        _reject("Runtime fork identity differs from publication base")
    current = capture(
        store,
        previous_ref,
        project_ref=fork["project_ref"],
        base_revision=fork["base_revision"],
    )
    if current != fork:
        _reject("Runtime fork base changed; refresh the local fork without inference")
    if value.get('runtime_evolution'):
        check=copy.deepcopy(value)
        apply(store,check,fork)
        if check!=value:_reject('Additive runtime evolution changed after preparation')


def initial_records(value, spec):
    fork = value.get("runtime_fork")
    if fork is None:
        return None
    key = next(
        key
        for key, item in bindings(value).items()
        if item["resource_definition"]["resource_type"]
        == spec["resource_definition"]["resource_type"]
    )
    if value.get('runtime_evolution'):
        from .task_spec import verify_identity
        evolution=value['runtime_evolution']
        verify_identity(evolution,id_key='evolution_id',prefix='prototype-additive-evolution:')
        row=next(row for row in evolution['resources'] if row['resource_ref']==key)
        if digest(row['records'])!=row['records_digest']:_reject('Additive initial-data pin changed')
        return copy.deepcopy(row['records'])
    return copy.deepcopy(
        next(row["records"] for row in fork["resources"] if row["resource_ref"] == key)
    )
