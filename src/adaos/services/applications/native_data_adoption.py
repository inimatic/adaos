"""Core-only evidence scope for data already adopted by a fenced cutover.

Never exposed as SDK arguments. A runtime bucket upgrade must not copy legacy
data over a target that the Application transaction has already migrated.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
from pathlib import Path

_ADOPTED = ContextVar('adaos_native_data_adoption', default=())


@contextmanager
def adopted_native_data(components):
    values = tuple((str(item.target_root.resolve()), copy.deepcopy(item.target_manifest.get('data_lifecycle')))
                   for item in components)
    marker = _ADOPTED.set(values)
    try:
        yield
    finally:
        _ADOPTED.reset(marker)


def is_adopted_native_data(root: Path, manifest) -> bool:
    declaration = manifest.get('data_lifecycle')
    return bool(declaration and (str(Path(root).resolve()), declaration) in _ADOPTED.get())
