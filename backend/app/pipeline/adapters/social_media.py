"""Social media export adapter (PRD 7 / 12.3).

Platform exports are JSON blobs of wildly different shapes.  CrimeLink infers
the platform from structural signatures (``friends``, ``followers``, ``reels``…)
and reduces the export to just two things: who exists, and who is linked to
whom.

**The PII redaction pre-filter runs first.**  This is the DPDP data-minimisation
requirement applied at ingestion time rather than after the fact: e-mail
addresses, phone numbers, home addresses, dates of birth, IP addresses and free-
text biographies are stripped *before* anything is written to storage, and the
document is marked ``UNVERIFIED``.  Redacting later would mean the data had
already been copied somewhere it did not need to be.
"""

from __future__ import annotations

import json
from typing import Any

from app.domain.enums import DocumentType
from app.domain.models import Block, NormalizedDocument, OriginRef
from app.errors import PipelineError
from app.logging import get_logger
from app.pipeline.adapters.protocol import DocumentMeta, detect_language
from app.pipeline.extraction.gazetteers import SOCIAL_PLATFORM_SIGNATURES

log = get_logger("crimelink.adapter.social")

# Fields that must never reach storage under DPDP data minimisation.
_REDACTED_FIELDS: frozenset[str] = frozenset(
    {
        "email", "email_address", "mail", "phone", "phone_number", "mobile", "contact",
        "address", "home_address", "street", "city", "pincode", "zip", "dob",
        "date_of_birth", "birthdate", "ip", "ip_address", "last_login_ip", "gender",
        "religion", "caste", "political_view", "sexual_orientation", "bio", "about",
        "relationship_status", "work", "education", "school", "employer", "location",
        "current_city", "hometown", "birthday",
    }
)

_REAL_NAME_FIELDS: tuple[str, ...] = (
    "name", "display_name", "displayname", "full_name", "profile_name", "title",
)
_HANDLE_FIELDS: tuple[str, ...] = ("username", "handle", "user_name", "screen_name")
_LINK_FIELDS: tuple[str, ...] = (
    "friends", "friend_list", "friendlist", "connections", "followers", "following",
    "contacts", "mutual_friends", "chats", "messages", "participants", "members",
    "pages_followed", "pages", "liked_pages", "subscriptions", "groups",
)


class SocialMediaAdapter:
    """Platform JSON export → normalised low-confidence social links."""

    document_type = DocumentType.SOCIAL_MEDIA

    def parse(self, raw: bytes, doc_meta: DocumentMeta) -> NormalizedDocument:
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise PipelineError(
                f"This social media export is not valid JSON ({exc}). "
                "Upload the platform's original JSON export without editing it."
            ) from exc

        platform = self._detect_platform(payload)
        redacted = self._redact(payload)
        profiles, links = self._extract(redacted)

        if not profiles and not links:
            raise PipelineError(
                "No people or connections could be recognised in this export "
                f"(detected platform: {platform}). The schema may be unsupported."
            )

        # An uploaded export is ingested verbatim, so the file is its own
        # origin.  Recording it per block means a social link resolves back to
        # the exact export rather than only to the derived document -- the same
        # guarantee the CSV-backed adapters already provide.
        origin_file = (doc_meta.extra or {}).get("relative_path") or doc_meta.filename

        blocks: list[Block] = []
        rendered: list[str] = []
        cursor = 0
        for index, (person_a, person_b, meta) in enumerate(links):
            text = (
                f"Social connection: {person_a} {meta.get('relation', 'linked_to')} "
                f"{person_b} on {platform}"
            )
            blocks.append(
                Block(
                    kind="record",
                    text=text,
                    offset=cursor,
                    data={
                        "kind": "social_link",
                        "person_a": person_a,
                        "person_b": person_b,
                        "platform": platform,
                        "ts": meta.get("timestamp"),
                    },
                    origin=OriginRef(
                        file=origin_file,
                        record_id=meta.get("id") or f"link[{index}]",
                        fields=["person_a", "person_b", "relation", "timestamp"],
                        values={
                            "person_a": person_a,
                            "person_b": person_b,
                            "relation": meta.get("relation", "linked_to"),
                            "timestamp": meta.get("timestamp"),
                            "platform": platform,
                        },
                    ),
                )
            )
            rendered.append(text)
            cursor += len(text) + 1

        warnings = [
            f"PII pre-filter applied: {len(_REDACTED_FIELDS)} sensitive field types are "
            "stripped at ingestion (DPDP data minimisation).",
            "Social-media data is UNVERIFIED: links render dashed and muted and are "
            "down-weighted in every analytic.",
        ]
        return NormalizedDocument(
            doc_id=doc_meta.doc_id,
            case_id=doc_meta.case_id,
            doc_type=DocumentType.SOCIAL_MEDIA.value,
            language=detect_language("\n".join(rendered), doc_meta.language_hint),
            source_confidence=doc_meta.source_confidence,
            blocks=blocks,
            parse_warnings=warnings,
            metadata={
                "filename": doc_meta.filename,
                "platform": platform,
                "profiles": len(profiles),
                "links": len(links),
                "redacted_fields": sorted(_REDACTED_FIELDS),
            },
        )

    # -------------------------------------------------------------- internals
    @staticmethod
    def _detect_platform(payload: Any) -> str:
        blob = json.dumps(payload, ensure_ascii=False)[:20000].lower()
        best, best_hits = "UNKNOWN", 0
        for platform, signatures in SOCIAL_PLATFORM_SIGNATURES.items():
            hits = sum(1 for signature in signatures if signature in blob)
            if hits > best_hits:
                best, best_hits = platform, hits
        return best

    @classmethod
    def _redact(cls, node: Any, _key: str | None = None) -> Any:
        """Recursively strip DPDP-sensitive fields before anything is persisted."""
        if isinstance(node, dict):
            return {
                key: cls._redact(value, key)
                for key, value in node.items()
                if key.lower() not in _REDACTED_FIELDS
            }
        if isinstance(node, list):
            return [cls._redact(item) for item in node]
        return node

    @classmethod
    def _extract(cls, node: Any) -> tuple[list[str], list[tuple[str, str, dict]]]:
        profiles: list[str] = []
        links: list[tuple[str, str, dict]] = []

        def walk(current: Any, owner: str | None) -> None:
            if isinstance(current, list):
                for item in current:
                    walk(item, owner)
                return
            if not isinstance(current, dict):
                # Only *connection* fields create links.  A bare string in any
                # other position is an attribute (a category, a timestamp, a
                # platform name) and must never be mistaken for a person.
                return

            name = cls._name_of(current)
            if name and name not in profiles:
                profiles.append(name)
            for key, value in current.items():
                lowered = key.lower()
                if lowered in _LINK_FIELDS:
                    walk_connections(value, name or owner)
                elif lowered in ("timestamp", "date", "created_at"):
                    continue
                else:
                    walk(value, name or owner)

        def walk_connections(value: Any, owner: str | None) -> None:
            """A connection list — every named entry is linked to the owner."""
            if isinstance(value, dict):
                entries: list[Any] = [value]
            elif isinstance(value, list):
                entries = list(value)
            else:
                entries = []
            for entry in entries:
                if isinstance(entry, str):
                    if owner and _looks_like_name(entry):
                        links.append((owner, entry.strip(), {"relation": "linked_to"}))
                    continue
                if not isinstance(entry, dict):
                    continue
                entry_name = cls._name_of(entry)
                if entry_name:
                    if entry_name not in profiles:
                        profiles.append(entry_name)
                    if owner and owner != entry_name:
                        links.append((owner, entry_name, {"relation": "linked_to"}))
                # Recurse so nested groups (members of a group) keep their own
                # owner context instead of inheriting the top-level account.
                walk(entry, entry_name or owner)

        # The export's own account is the implicit owner of every connection
        # list, including page follows that sit beside the account object.
        walk(node, cls._account_name(node))

        # De-duplicate undirected links.
        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str, dict]] = []
        for person_a, person_b, meta in links:
            if person_a == person_b:
                continue
            key = tuple(sorted((person_a, person_b)))
            if key in seen:
                continue
            seen.add(key)
            unique.append((key[0], key[1], meta))
        return profiles, unique

    @classmethod
    def _account_name(cls, payload: Any) -> str | None:
        """Find the exporting account's display name (facebook/JSON exports)."""
        if not isinstance(payload, dict):
            return None
        for key in ("account", "profile", "user", "owner", "accounts"):
            value = payload.get(key)
            if isinstance(value, dict):
                name = cls._name_of(value)
                if name:
                    return name
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        name = cls._name_of(item)
                        if name:
                            return name
        return None

    @staticmethod
    def _name_of(record: dict) -> str | None:
        # Human-name fields first: a username is only used when the record has
        # no display name, so "suresh.mehta.jaipur" does not shadow the person.
        for field in _REAL_NAME_FIELDS:
            value = record.get(field)
            if isinstance(value, str) and _looks_like_name(value):
                return value.strip()
        for field in _HANDLE_FIELDS:
            value = record.get(field)
            if isinstance(value, str) and _looks_like_name(value):
                return value.strip()
        return None


# Values that appear in exports as *attributes* of a page or profile rather
# than as an identity: a "category": "business" field is not a person.
_GENERIC_VALUES: frozenset[str] = frozenset(
    """
    business community page group public private personal company organisation
    organization education school college government ngo non profit nonprofit
    shopping retail service services product brand entertainment media news
    sports health fitness travel food restaurant hotel cafe event interest
    cause nonprofit organisation charity religious place landmark region city
    friend follower following member admin moderator owner unknown other
    male female everyone friends followers following members
    facebook instagram twitter whatsapp telegram youtube linkedin snapchat
    sharechat moj josh koo x
    """.split()
)


def _looks_like_name(value: str) -> bool:
    text = value.strip()
    if not (2 <= len(text) <= 64):
        return False
    if "@" in text or text.startswith("http") or text.isdigit():
        return False
    words = text.split()
    if not 1 <= len(words) <= 5:
        return False
    if text.lower() in _GENERIC_VALUES:
        return False
    return True
