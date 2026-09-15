"""Enumerations shared by the database, the graph and the API.

These are the single source of truth for every controlled vocabulary in the
system.  They are used as SQLAlchemy CHECK-constrained VARCHAR columns, as
Pydantic models and as graph property values, so a typo anywhere becomes a
validation error rather than a silent data-quality bug.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """Operational roles used by the server-side RBAC/ABAC policy.

    ``ADMIN`` remains a backwards-compatible alias for deployments created
    before the role model was expanded.  Routes should still state the exact
    roles they permit; the frontend never grants access.
    """

    VIEWER = "VIEWER"
    INVESTIGATOR = "INVESTIGATOR"
    SUPERVISOR = "SUPERVISOR"
    FORENSIC_ANALYST = "FORENSIC_ANALYST"
    FINANCIAL_ANALYST = "FINANCIAL_ANALYST"
    INTELLIGENCE_ANALYST = "INTELLIGENCE_ANALYST"
    AUDITOR = "AUDITOR"
    STATION_ADMIN = "STATION_ADMIN"
    DISTRICT_ADMIN = "DISTRICT_ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"


class CaseStatus(str, Enum):
    """Case lifecycle states; transitions are enforced by the domain service."""

    DRAFT = "DRAFT"
    OPEN = "OPEN"  # legacy name retained for existing clients
    ACTIVE_INVESTIGATION = "ACTIVE_INVESTIGATION"
    UNDER_REVIEW = "UNDER_REVIEW"
    SUBMITTED = "SUBMITTED"
    CLOSED = "CLOSED"
    SEALED = "SEALED"


class InformationClassification(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    SECRET = "SECRET"
    HIGHLY_RESTRICTED = "HIGHLY_RESTRICTED"


class CustodyEventType(str, Enum):
    COLLECTED = "COLLECTED"
    IMPORTED = "IMPORTED"
    HASH_VERIFIED = "HASH_VERIFIED"
    STORED = "STORED"
    ACCESSED = "ACCESSED"
    DOWNLOADED = "DOWNLOADED"
    DERIVED = "DERIVED"
    SHARED = "SHARED"
    EXPORTED = "EXPORTED"
    SEALED = "SEALED"


class TaskStatus(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    PENDING_REVIEW = "PENDING_REVIEW"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TaskPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class HypothesisStatus(str, Enum):
    OPEN = "OPEN"
    SUPPORTED = "SUPPORTED"
    WEAKENED = "WEAKENED"
    REJECTED = "REJECTED"
    UNVERIFIED = "UNVERIFIED"


class UncertaintyState(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    MISSING = "MISSING"
    CONTRADICTORY = "CONTRADICTORY"
    UNVERIFIED = "UNVERIFIED"


class ApprovalType(str, Enum):
    EVIDENCE_SEAL = "EVIDENCE_SEAL"
    ENTITY_MERGE = "ENTITY_MERGE"
    FINDING = "FINDING"
    REPORT = "REPORT"
    CASE_CLOSURE = "CASE_CLOSURE"
    EVIDENCE_EXPORT = "EVIDENCE_EXPORT"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DocumentType(str, Enum):
    FIR = "FIR"
    CHARGE_SHEET = "CHARGE_SHEET"
    CDR = "CDR"
    CCTV = "CCTV"
    FINANCIAL = "FINANCIAL"
    FINANCIAL_TRANSACTION = "FINANCIAL_TRANSACTION"
    SURVEILLANCE = "SURVEILLANCE"
    SURVEILLANCE_REPORT = "SURVEILLANCE_REPORT"
    WITNESS_STATEMENT = "WITNESS_STATEMENT"
    SCENE_REPORT = "SCENE_REPORT"
    INTELLIGENCE_REPORT = "INTELLIGENCE_REPORT"
    CRIMINAL_RECORD = "CRIMINAL_RECORD"
    CRIMINAL_HISTORY = "CRIMINAL_HISTORY"
    ARREST_RECORD = "ARREST_RECORD"
    BAIL_RECORD = "BAIL_RECORD"
    SOCIAL_MEDIA = "SOCIAL_MEDIA"
    SOCIAL_MEDIA_INTELLIGENCE = "SOCIAL_MEDIA_INTELLIGENCE"
    ANPR = "ANPR"
    PATROL_REPORT = "PATROL_REPORT"
    LEGAL_RECORD = "LEGAL_RECORD"
    CASE_DIARY = "CASE_DIARY"
    SEIZURE = "SEIZURE"
    FORENSIC = "FORENSIC"
    GEO_EVENT = "GEO_EVENT"
    REVIEW = "REVIEW"
    DATASET_RESOURCE = "DATASET_RESOURCE"
    README = "README"
    DATA_DICTIONARY = "DATA_DICTIONARY"
    QUALITY_REPORT = "QUALITY_REPORT"
    MASTER_INDEX = "MASTER_INDEX"
    INTEL = "INTEL"
    EVIDENCE = "EVIDENCE"
    RELATIONSHIP = "RELATIONSHIP"
    OTHER = "OTHER"


class SourceConfidence(str, Enum):
    """Evidentiary weight of a document; propagates to every derived fact."""

    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    ANONYMOUS_TIP = "ANONYMOUS_TIP"
    SYNTHETIC = "SYNTHETIC"   # development/synthetic corpus — never operational evidence


class IngestionStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


class EntityType(str, Enum):
    PERSON = "Person"
    PHONE = "Phone"
    VEHICLE = "Vehicle"
    LOCATION = "Location"
    ORGANIZATION = "Organization"
    BANK_ACCOUNT = "BankAccount"
    EVENT = "Event"
    CASE = "Case"
    FIR = "FIR"
    DOCUMENT = "Document"
    TRANSACTION = "Transaction"
    SOCIAL_ACCOUNT = "SocialAccount"
    EVIDENCE = "Evidence"
    BANK = "Bank"
    ACCOUNT = "Account"


class MatchBasis(str, Enum):
    NAME_FUZZY = "NAME_FUZZY"
    PHONE_PARTIAL = "PHONE_PARTIAL"
    PHOTO_SIMILARITY = "PHOTO_SIMILARITY"
    ALIAS_CO_MENTION = "ALIAS_CO_MENTION"


class ResolutionStatus(str, Enum):
    PENDING = "PENDING"
    MERGED = "MERGED"
    REJECTED = "REJECTED"


class PatternType(str, Enum):
    STRUCTURING = "STRUCTURING"
    BURNER_PHONE = "BURNER_PHONE"
    RAPID_MOVEMENT = "RAPID_MOVEMENT"
    NETWORK_BRIDGE = "NETWORK_BRIDGE"


class PatternStatus(str, Enum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    DISMISSED = "DISMISSED"
    ESCALATED = "ESCALATED"


class AuditAction(str, Enum):
    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    SEARCH = "SEARCH"
    GRAPH_EXPAND = "GRAPH_EXPAND"
    DOC_VIEW = "DOC_VIEW"
    DOC_UPLOAD = "DOC_UPLOAD"
    MERGE = "MERGE"
    MERGE_REJECT = "MERGE_REJECT"
    PATTERN_REVIEW = "PATTERN_REVIEW"
    EXPORT = "EXPORT"
    ACCESS_REQUEST = "ACCESS_REQUEST"
    ACCESS_APPROVAL = "ACCESS_APPROVAL"
    QUARANTINE_RELEASE = "QUARANTINE_RELEASE"
    CONFIG_CHANGE = "CONFIG_CHANGE"
    AI_QUERY = "AI_QUERY"
    INVESTIGATE = "INVESTIGATE"
    GLOBAL_SEARCH = "GLOBAL_SEARCH"
    TIMELINE_ANALYZE = "TIMELINE_ANALYZE"


class AIModelRole(str, Enum):
    """Distinct AI capabilities that can be routed to separate models."""
    EXTRACTION = "EXTRACTION"
    REASONING = "REASONING"
    EXPLANATION = "EXPLANATION"
    CLASSIFICATION = "CLASSIFICATION"
    EMBEDDING = "EMBEDDING"


class AIFindingType(str, Enum):
    CROSS_CASE_LINK = "CROSS_CASE_LINK"
    BRIDGE_ENTITY = "BRIDGE_ENTITY"
    HIDDEN_CONNECTION = "HIDDEN_CONNECTION"
    TEMPORAL_PATTERN = "TEMPORAL_PATTERN"
    MULE_PATTERN = "MULE_PATTERN"
    BURNER_PATTERN = "BURNER_PATTERN"
    COMMUNICATION_CLUSTER = "COMMUNICATION_CLUSTER"
    IDENTITY_AMBIGUITY = "IDENTITY_AMBIGUITY"
    GENERAL = "GENERAL"


class AIEvidenceLevel(str, Enum):
    """Fact vs inference vs hypothesis (§26)."""
    FACT = "FACT"
    INFERENCE = "INFERENCE"
    HYPOTHESIS = "HYPOTHESIS"
    UNKNOWN = "UNKNOWN"


class AccessRequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


class Language(str, Enum):
    EN = "en"
    HI = "hi"
    MR = "mr"
    TA = "ta"
    TE = "te"
    BN = "bn"
    UNKNOWN = "unknown"


class LegalStatus(str, Enum):
    """Source-derived legal/procedural status — never inferred from graph metrics."""

    VICTIM = "VICTIM"
    WITNESS = "WITNESS"
    COMPLAINANT = "COMPLAINANT"
    PERSON_OF_INTEREST = "PERSON_OF_INTEREST"
    SUSPECT = "SUSPECT"
    ACCUSED = "ACCUSED"
    ARRESTED = "ARRESTED"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    CHARGED = "CHARGED"
    CONVICTED = "CONVICTED"
    ACQUITTED = "ACQUITTED"
    RELEASED_ON_BAIL = "RELEASED_ON_BAIL"
    DISCHARGED = "DISCHARGED"
    UNKNOWN = "UNKNOWN"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class NetworkRole(str, Enum):
    """Analytical network role — structural, not legal."""

    HUB = "HUB"
    CONNECTOR = "CONNECTOR"
    BRIDGE = "BRIDGE"
    COMMUNITY_BRIDGE = "COMMUNITY_BRIDGE"
    COMMUNICATION_INTERMEDIARY_CANDIDATE = "COMMUNICATION_INTERMEDIARY_CANDIDATE"
    FINANCIAL_INTERMEDIARY_CANDIDATE = "FINANCIAL_INTERMEDIARY_CANDIDATE"
    CROSS_CASE_BRIDGE = "CROSS_CASE_BRIDGE"
    PERIPHERAL = "PERIPHERAL"
    INFORMATION_FLOW_INTERMEDIARY_CANDIDATE = "INFORMATION_FLOW_INTERMEDIARY_CANDIDATE"
    POTENTIAL_NETWORK_INTERMEDIARY = "POTENTIAL_NETWORK_INTERMEDIARY"
    UNKNOWN = "UNKNOWN"


class InvestigativeRelevance(str, Enum):
    """Why an investigator may want to inspect — never probability of guilt."""

    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL_REVIEW = "CRITICAL_REVIEW"


class EvidenceStrength(str, Enum):
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    INSUFFICIENT = "INSUFFICIENT"


class EvidenceConvergence(str, Enum):
    SINGLE_SOURCE = "SINGLE_SOURCE"
    MULTI_RECORD = "MULTI_RECORD"
    MULTI_SOURCE = "MULTI_SOURCE"
    INDEPENDENT_SOURCE_CONVERGENCE = "INDEPENDENT_SOURCE_CONVERGENCE"
    NONE = "NONE"


# ---------------------------------------------------------------------------
# Graph vocabulary
# ---------------------------------------------------------------------------

NODE_LABELS: frozenset[str] = frozenset(e.value for e in EntityType) | {"Case"}

# Canonical, wire-format labels.  EntityType values are title-case ("Person",
# "BankAccount") because they double as Neo4j labels; the API and the console
# speak SCREAMING_CASE ("PERSON", "BANK_ACCOUNT").  One map, used at the API
# boundary, so every surface agrees and colour/size styling can key on it.
CANONICAL_LABELS: dict[str, str] = {
    "Person": "PERSON",
    "Phone": "PHONE",
    "Vehicle": "VEHICLE",
    "Location": "LOCATION",
    "Organization": "ORGANIZATION",
    "BankAccount": "BANK_ACCOUNT",
    "Event": "EVENT",
    "Case": "CASE",
    "FIR": "FIR",
    "Document": "DOCUMENT",
    "Transaction": "TRANSACTION",
    "SocialAccount": "SOCIAL_ACCOUNT",
    "Evidence": "EVIDENCE",
    "Bank": "BANK",
    "Account": "ACCOUNT",
}


def canonical_label(label: str) -> str:
    """The SCREAMING_CASE wire label for a stored node label."""
    return CANONICAL_LABELS.get(label, label.upper())

DOCUMENT_ARTIFACT_LABELS: frozenset[str] = frozenset({"DOCUMENT", "EVIDENCE"})


def is_document_artifact_node(node: object) -> bool:
    """Centralized defense against document/evidence contamination.

    ``entity_type`` is checked as well as the rendered label because legacy
    projections used ``Event`` for DOCUMENT and old snapshots can still carry
    that shape until they are rebuilt.
    """
    label = canonical_label(str(getattr(node, "label", "") or ""))
    props = getattr(node, "properties", {}) or {}
    entity_type = str(props.get("entity_type") or "").upper()
    return (
        label in DOCUMENT_ARTIFACT_LABELS
        or entity_type in DOCUMENT_ARTIFACT_LABELS
        or bool(props.get("is_document_artifact"))
    )

REL_TYPES: frozenset[str] = frozenset(
    {
        "PARTICIPATED_IN",
        "OWNS_VEHICLE",
        "USES_PHONE",
        "OWNS_ACCOUNT",
        "CALLED",
        "MEMBER_OF",
        # Person-to-person relations inferred by the probabilistic stage.  They
        # are modelled as first-class, separately-weighted types rather than a
        # generic "associated with", so a statement like "seen with" never
        # carries the same weight as "named accomplice of".
        "ASSOCIATE_OF",
        "RELATIVE_OF",
        "ARRESTED_WITH",
        "NAMED_ACCOMPLICE_OF",
        "LINKED_ON_SOCIAL",
        "TRANSFER_TO",
        "CONTROLS_ACCOUNT",
        "ACCUSED_IN",
        "SHARED_PHONE",
        "SHARED_ACCOUNT",
        "SHARED_VEHICLE",
        "SHARED_LOCATION",
        "SHARED_IDENTIFIER",
        "LOCATED_AT",
        "MENTIONED_IN",
        "POTENTIAL_ALIAS",
        "SIMILARITY_REJECTED",
        "MERGED_INTO",
    }
)

# Meta-edges that describe the *state of the investigation* rather than a fact
# about the world (an alias proposal, a rejected match, a reversible merge).
# These are the only relationships permitted to exist without a source
# document, because "investigator X rejected this match" is itself the record.
UNEVIDENCED_META_REL_TYPES: frozenset[str] = frozenset(
    {"POTENTIAL_ALIAS", "SIMILARITY_REJECTED", "MERGED_INTO"}
)

# Edges that are *aggregated* rather than created per record (PRD 6.2 #2).
# A 500-call CDR must not produce 500 parallel edges: every call between a phone
# pair collapses into one CALLED edge carrying call_count / first_ts / last_ts.
#
# TRANSFER_TO is deliberately NOT aggregated.  Structuring detection needs each
# individual transfer's amount and timestamp, and each transfer is a discrete,
# separately-evidenced financial fact.  Rendering aggregates those edges for
# display; the graph keeps them distinct.
AGGREGATING_REL_TYPES: frozenset[str] = frozenset({"CALLED"})

# Edge types that may only exist as low-confidence, visually muted links.
LOW_CONFIDENCE_REL_TYPES: frozenset[str] = frozenset({"LINKED_ON_SOCIAL"})

# Hard identifiers: an exact match on any of these proves identity (PRD 9.1).
HARD_IDENTIFIER_KEYS: tuple[str, ...] = ("number", "plate", "ifsc", "account_number")

# Roles permitted to perform each mutating operation.
ROLE_ORDER: dict[Role, int] = {Role.VIEWER: 0, Role.INVESTIGATOR: 1, Role.ADMIN: 2}
