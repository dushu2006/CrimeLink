"""Enumerations shared by the database, the graph and the API.

These are the single source of truth for every controlled vocabulary in the
system.  They are used as SQLAlchemy CHECK-constrained VARCHAR columns, as
Pydantic models and as graph property values, so a typo anywhere becomes a
validation error rather than a silent data-quality bug.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    VIEWER = "VIEWER"
    INVESTIGATOR = "INVESTIGATOR"
    ADMIN = "ADMIN"


class CaseStatus(str, Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    CLOSED = "CLOSED"


class DocumentType(str, Enum):
    FIR = "FIR"
    CDR = "CDR"
    FINANCIAL = "FINANCIAL"
    SURVEILLANCE = "SURVEILLANCE"
    SOCIAL_MEDIA = "SOCIAL_MEDIA"
    CRIMINAL_HISTORY = "CRIMINAL_HISTORY"
    INTEL = "INTEL"


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
