"""Public evidence-bearing errors at the local pre-provider admission boundary."""


class SubmissionNotAdmitted(ValueError):
    """The SDK proves this invocation never entered its provider transport.

    Only the SDK's local admission owner may raise this type. Timeouts, provider
    rejections and other exceptions do not establish the same fact.
    """

    def __init__(self, reason: str, *, request_id: str):
        super().__init__(reason)
        self.request_id = request_id
        self.admission = {"schema": "adaos.llm.local_admission.v1", "status": "not_admitted",
                          "provider_transport_started": False, "request_id": request_id, "reason": reason}
