"""Retain proven local admission refusal at the existing draft owner boundary."""
from contextlib import contextmanager

from adaos.sdk.llm.errors import SubmissionNotAdmitted
from adaos.services.artifact_pipeline.storage import atomic_write_json


@contextmanager
def retain_local_admission(path, record, request_id):
    try:
        yield
    except SubmissionNotAdmitted as exc:
        # Root IDs are implementation detail; public callers own the draft ID.
        mapped = SubmissionNotAdmitted(str(exc), request_id=request_id,
            root_request_id=exc.admission.get('root_request_id') or exc.request_id)
        record.update(status='not_admitted', last_admission_refusal=mapped.admission,
                      admission_refusals=int(record.get('admission_refusals') or 0) + 1)
        atomic_write_json(path, record)
        raise mapped from exc
