"""Bind declared settings to the active owned component and selected channel."""

from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path

import yaml

from adaos.services.policy.skill_capabilities import require_skill_capability
from .configuration import ApplicationConfigurationStore, ConfigurationConflict, _digest, _validate
from .runtime_channel import ApplicationRuntimeChannel
from .store import ApplicationStore


class ApplicationRuntimeConfiguration:
    def __init__(self, ctx):
        self.ctx = ctx

    def _binding(self, capability):
        admitted = require_skill_capability(self.ctx, capability)
        current = self.ctx.skill_ctx.get()
        source = Path(current.path).resolve()
        native_root = (Path(self.ctx.paths.workspace_dir()) / "skills/.runtime").resolve()
        manifest = yaml.safe_load(Path(admitted.manifest_path).read_text(encoding="utf-8"))
        declaration = manifest.get("configuration") or {}
        schema, defaults = declaration.get("schema"), declaration.get("defaults")
        if not isinstance(schema, dict) or not isinstance(defaults, dict):
            raise ValueError("Skill must declare configuration schema and defaults")
        _validate(schema, defaults)
        if not source.is_relative_to(native_root):
            getter = getattr(self.ctx.paths, "dev_skills_dir", None)
            dev = Path(getter()).resolve() if getter else None
            if dev is None or not (source.is_relative_to(dev / current.name)
                                   or source.is_relative_to(dev / ".runtime" / current.name)):
                raise ConfigurationConflict("Application configuration requires an admitted runtime; DEV stays isolated")
            # Same SDK, separate synthetic store. No installation lookup or copy
            # of real values/credential bindings is performed in development.
            store = ApplicationConfigurationStore(dev.parent / ".runtime/state", f"development:{current.name}", f"skill:{current.name}")
            return None, schema, defaults, store, nullcontext()
        state = Path(getattr(self.ctx, "authority_state_dir", None) or self.ctx.paths.state_dir())
        catalog = ApplicationStore(state)
        matches = {}
        all_selections = catalog.list_runtime_selections()
        ref = f"skill:{current.name}"
        for selection in all_selections:
            if selection.application_id in matches:
                continue
            release = catalog.get_release(selection.application_id, selection.release_digest).project_release
            composition = release.composition_lock
            if composition and any(member.ref == ref and member.lifecycle == "bound" for member in composition.members):
                matches[selection.application_id] = selection
        if len(matches) != 1:
            raise ConfigurationConflict("Configuration requires exactly one selected owning Application; shared components need an explicit owner binding")
        selected = next(iter(matches.values()))
        actual_root = str(getattr(self.ctx.paths, "runtime_channel_ref", "workspace"))
        if actual_root != selected.runtime_root_ref:
            raise ConfigurationConflict("Application configuration channel is inactive")
        store = ApplicationConfigurationStore(state, selected.application_id, ref)
        lease = ApplicationRuntimeChannel(state, selected.application_id).execution(actual_root, selected.release_digest,
            legacy=tuple(item for item in all_selections if item.application_id == selected.application_id))
        return selected, schema, defaults, store, lease

    @staticmethod
    def _current(selected, schema, defaults, record):
        candidate = selected.runtime_root_ref.removeprefix("trial:") if selected and selected.runtime_root_ref.startswith("trial:") else None
        if candidate:
            config = ApplicationConfigurationStore._beta(record, candidate)
        else:
            if (record.get("beta") or {}).get("active"):
                raise ConfigurationConflict("Configuration adoption has not completed")
            config = record.get("stable")
            if config is None:
                return {"values": deepcopy(defaults), "credentials": {}}, None
        release_digest = selected.release_digest if selected else "development:" + _digest(schema)
        if config["release_digest"] != release_digest or config["schema_digest"] != _digest(schema):
            if selected is not None or not config["release_digest"].startswith("development:"):
                raise ConfigurationConflict("Runtime settings require explicit release/schema migration")
            # DEV has no immutable release cutover. Project compatible defaults
            # without deleting overrides or mutating storage on a read; the next
            # explicit CAS write records the new schema. Installed channels never
            # enter this branch, and incompatible DEV changes still fail closed.
            config = {**config, "values": {**deepcopy(defaults), **deepcopy(config["values"])}}
        _validate(schema, config["values"])
        return config, candidate

    def read(self):
        selected, schema, defaults, store, lease = self._binding("configuration.read")
        with lease:
            record = store.read()
            config, _candidate = self._current(selected, schema, defaults, record)
            return {"revision": record["revision"], "values": deepcopy(config["values"])}

    def write(self, values, *, expected_revision: int):
        selected, schema, defaults, store, lease = self._binding("configuration.write")
        with lease:
            record = store.read()
            config, candidate = self._current(selected, schema, defaults, record)
            if candidate:
                saved = store.update_beta(candidate_id=candidate, schema=schema, values=values,
                    credentials=config["credentials"], expected_revision=expected_revision)
                result = saved["beta"]
            else:
                saved = store.set_stable(release_digest=selected.release_digest if selected else "development:" + _digest(schema), schema=schema, values=values,
                    credentials=config["credentials"], expected_revision=expected_revision)
                result = saved["stable"]
            return {"revision": saved["revision"], "values": deepcopy(result["values"])}
