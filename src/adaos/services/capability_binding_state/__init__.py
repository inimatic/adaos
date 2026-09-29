"""Capability, binding, state, resolution, and activation services."""

from .catalog import (
    PortableContractCatalog,
    PortableContractConflict,
    portable_record_identity,
)
from .activation import (
    CBSActivationCoordinator,
    CBSActivationResult,
    FencedLocalCrudWriter,
    ResolutionPlanCache,
    ResolutionPlanError,
    ResolutionPlanExpired,
    ResolutionPlanner,
    StaleWriterError,
    resolution_plan_diff,
    resolution_plan_replanning_status,
)
from .local_state import (
    LegacyCrudProjection,
    LegacyCrudProjector,
    LocalIdentityConflict,
    LocalIdentityStore,
    PrototypeCrudProjector,
    StateAttachmentError,
    StagedAuthorityTransition,
    calculate_effective_guarantees,
    redacted_graph_record,
    stage_authority_transition,
    validate_state_attachment,
)
from .resolver import (
    ExactPackageResolver,
    ResolutionFailure,
    ResolutionRejection,
    SemanticCandidate,
    SemanticResolver,
)
from .registry_query import (
    SEMANTIC_REGISTRY_QUERY_RESULT_SCHEMA,
    SEMANTIC_REGISTRY_QUERY_SCHEMA,
    SemanticRegistryQueryError,
    execute_semantic_registry_query,
)
from .telemetry import (
    CBSBenchmarkTelemetryConflict,
    CBSBenchmarkTelemetryStore,
    CBSCrudProofStore,
)
from .evidence import (
    EvidenceAssessmentResult,
    EvidenceAssessmentService,
    EvidenceFreshnessError,
    ExternalChangeMonitorStore,
    build_reverification_claim,
    dependency_evidence_impact,
    explain_evidence_assessment,
)
from .booking import (
    BookingCommand,
    BookingConflict,
    BookingError,
    BookingInProgress,
    BookingReservationService,
    BookingUnavailable,
    IdempotencyConflict,
    InMemoryAuditState,
    InMemoryAvailabilityState,
    InMemoryBookingState,
    InvalidBookingInterval,
    ProductionReservationProvider,
    ProviderTimeout,
    ReconciliationRequired,
    ScriptedReservationProvider,
    SimulationReservationProvider,
    StaleAvailability,
)
from .skill_inventory import (
    discover_dev_skills_root,
    inventory_installed_skills,
    render_skill_inventory_markdown,
)
from .graphs import (
    DerivedGraphError,
    DerivedGraphStore,
    build_evolver_observations,
    build_impact_projection,
    build_semantic_graph,
    build_viability_projection,
    create_evolver_proposal,
    promote_evolver_proposal,
)
from .benchmark import (
    CBSBenchmarkError,
    build_benchmark_report,
    create_benchmark_observation,
    freeze_benchmark_case,
    load_cbs_telemetry,
    observation_from_cbs_telemetry,
)
from .tooling import (
    PORTABLE_BUNDLE_PREDICATE,
    TerminologyIssue,
    admit_portable_bundle,
    build_identity_map,
    contract_diff,
    contract_reference,
    explicit_compatibility_edge,
    inspect_state_identity,
    lint_persistent_terminology,
    portable_bundle_digest,
)

_LAZY_REGISTRY_DISTRIBUTION_EXPORTS = frozenset(
    {
        "ReleaseProvenanceAdmission",
        "THIN_DISTRIBUTION_RECEIPT_SCHEMA",
        "ThinDistributionError",
        "ThinDistributionRemote",
        "ThinSemanticDistributionResolver",
    }
)


def __getattr__(name: str):
    # registry_distribution depends on the application CBS projection.  Eager
    # re-exporting it makes importing applications.cbs recurse through this
    # package and back into the partially initialized application module.
    if name in _LAZY_REGISTRY_DISTRIBUTION_EXPORTS:
        from . import registry_distribution

        value = getattr(registry_distribution, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "CBSActivationCoordinator",
    "CBSActivationResult",
    "CBSBenchmarkTelemetryConflict",
    "CBSBenchmarkTelemetryStore",
    "CBSCrudProofStore",
    "BookingCommand",
    "BookingConflict",
    "BookingError",
    "BookingInProgress",
    "BookingReservationService",
    "BookingUnavailable",
    "EvidenceAssessmentResult",
    "EvidenceAssessmentService",
    "EvidenceFreshnessError",
    "ExternalChangeMonitorStore",
    "IdempotencyConflict",
    "InMemoryAuditState",
    "InMemoryAvailabilityState",
    "InMemoryBookingState",
    "InvalidBookingInterval",
    "FencedLocalCrudWriter",
    "LegacyCrudProjection",
    "LegacyCrudProjector",
    "LocalIdentityConflict",
    "LocalIdentityStore",
    "ExactPackageResolver",
    "PortableContractCatalog",
    "PortableContractConflict",
    "portable_record_identity",
    "ProductionReservationProvider",
    "ProviderTimeout",
    "ReconciliationRequired",
    "PrototypeCrudProjector",
    "ResolutionFailure",
    "ResolutionPlanError",
    "ResolutionPlanExpired",
    "ResolutionPlanCache",
    "ResolutionPlanner",
    "ResolutionRejection",
    "SEMANTIC_REGISTRY_QUERY_RESULT_SCHEMA",
    "SEMANTIC_REGISTRY_QUERY_SCHEMA",
    "SemanticCandidate",
    "SemanticRegistryQueryError",
    "SemanticResolver",
    "ReleaseProvenanceAdmission",
    "THIN_DISTRIBUTION_RECEIPT_SCHEMA",
    "ThinDistributionError",
    "ThinDistributionRemote",
    "ThinSemanticDistributionResolver",
    "ScriptedReservationProvider",
    "SimulationReservationProvider",
    "StateAttachmentError",
    "StagedAuthorityTransition",
    "StaleWriterError",
    "StaleAvailability",
    "calculate_effective_guarantees",
    "build_reverification_claim",
    "dependency_evidence_impact",
    "explain_evidence_assessment",
    "execute_semantic_registry_query",
    "redacted_graph_record",
    "stage_authority_transition",
    "validate_state_attachment",
    "resolution_plan_diff",
    "resolution_plan_replanning_status",
    "discover_dev_skills_root",
    "inventory_installed_skills",
    "render_skill_inventory_markdown",
    "DerivedGraphError",
    "DerivedGraphStore",
    "build_evolver_observations",
    "build_impact_projection",
    "build_semantic_graph",
    "build_viability_projection",
    "create_evolver_proposal",
    "promote_evolver_proposal",
    "CBSBenchmarkError",
    "build_benchmark_report",
    "create_benchmark_observation",
    "freeze_benchmark_case",
    "load_cbs_telemetry",
    "observation_from_cbs_telemetry",
    "PORTABLE_BUNDLE_PREDICATE",
    "TerminologyIssue",
    "admit_portable_bundle",
    "build_identity_map",
    "contract_diff",
    "contract_reference",
    "explicit_compatibility_edge",
    "inspect_state_identity",
    "lint_persistent_terminology",
    "portable_bundle_digest",
]
