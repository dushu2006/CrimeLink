"""Server-side information-classification policy.

Classification is deliberately ordinal only for access checks.  It is not an
assessment of evidence strength and must never be displayed as such.
"""

from app.domain.enums import InformationClassification, Role
from app.errors import PermissionDeniedError

_RANK = {
    InformationClassification.PUBLIC: 0,
    InformationClassification.INTERNAL: 1,
    InformationClassification.CONFIDENTIAL: 2,
    InformationClassification.RESTRICTED: 3,
    InformationClassification.SECRET: 4,
    InformationClassification.HIGHLY_RESTRICTED: 5,
}

_PRIVILEGED = {
    Role.SUPER_ADMIN,
    Role.ADMIN,
    Role.DISTRICT_ADMIN,
    Role.STATION_ADMIN,
}


def rank(value: InformationClassification | str) -> int:
    try:
        return _RANK[InformationClassification(value)]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Unknown information classification: {value!r}") from exc


def can_access(principal, classification: InformationClassification | str) -> bool:
    """Return whether a principal's clearance and jurisdiction policy permit access."""
    level = InformationClassification(classification)
    if principal.role in _PRIVILEGED:
        return True
    clearance = getattr(principal, "max_classification", InformationClassification.CONFIDENTIAL)
    return rank(level) <= rank(clearance)


def require_classification(principal, classification: InformationClassification | str) -> None:
    if not can_access(principal, classification):
        raise PermissionDeniedError("This record's information classification exceeds your clearance.")


def classification_values() -> list[str]:
    return [item.value for item in InformationClassification]
