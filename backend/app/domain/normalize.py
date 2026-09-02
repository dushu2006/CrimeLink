"""Normalisation helpers shared by every extraction and matching stage.

Normalisation is what makes two mentions of the same real-world thing
comparable: ``+91 98110 12345``, ``09811012345`` and ``9811012345`` are one
phone number, and ``रमेश कुमार``/``Ramesh Kumar`` must at least be *comparable*
even though ``pg_trgm`` cannot handle Devanagari directly (PRD 9.2).
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Phone numbers
# --------------------------------------------------------------------------

_PHONE_STRIP = re.compile(r"[^\d+]")
_PHONE_RE = re.compile(r"(\+?91[\s\-]?)?([6-9]\d{9})")
_PHONE_ANY_RE = re.compile(r"(?:\+?91[\s\-]?)?([6-9]\d{9})")


def normalize_phone(raw: str) -> str | None:
    """Return E.164 ``+91XXXXXXXXXX`` for a valid Indian mobile number."""
    if not raw:
        return None
    text = unicodedata.normalize("NFKC", raw)
    text = _PHONE_STRIP.sub("", text)
    if text.startswith("0091"):
        text = text[4:]
    elif text.startswith("+91"):
        text = text[3:]
    elif text.startswith("91") and len(text) == 12:
        text = text[2:]
    elif text.startswith("0") and len(text) == 11:
        text = text[1:]
    if len(text) == 10 and text[0] in "6789" and text.isdigit():
        return "+91" + text
    return None


def iter_phones(text: str) -> list[tuple[int, int, str]]:
    """Yield ``(start, end, e164)`` for every Indian mobile number in *text*."""
    out: list[tuple[int, int, str]] = []
    for match in _PHONE_RE.finditer(text):
        e164 = normalize_phone(match.group(0))
        if e164:
            out.append((match.start(), match.end(), e164))
    return out


# --------------------------------------------------------------------------
# Vehicle registration plates
# --------------------------------------------------------------------------

_PLATE_RE = re.compile(
    r"\b([A-Z]{2})[\s\-]?(\d{1,2})[\s\-]?([A-Z]{0,3})[\s\-]?(\d{1,4})\b"
)
_PLATE_CANON = re.compile(r"[^A-Z0-9]")


def normalize_plate(raw: str) -> str | None:
    """Return the canonical plate form ``MH12AB3456``."""
    if not raw:
        return None
    text = unicodedata.normalize("NFKC", raw).upper()
    canon = _PLATE_CANON.sub("", text)
    if not (8 <= len(canon) <= 10):
        return None
    if not canon[:2].isalpha() or not canon[2:4].isdigit():
        return None
    return canon


def iter_plates(text: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for match in _PLATE_RE.finditer(unicodedata.normalize("NFKC", text).upper()):
        canon = normalize_plate(match.group(0))
        if canon:
            out.append((match.start(), match.end(), canon))
    return out


# --------------------------------------------------------------------------
# Bank accounts / IFSC
# --------------------------------------------------------------------------

_IFSC_RE = re.compile(r"\b([A-Z]{4})0([A-Z0-9]{6})\b")
_ACCOUNT_RE = re.compile(r"\b(\d{9,18})\b")


def normalize_ifsc(raw: str) -> str | None:
    if not raw:
        return None
    text = unicodedata.normalize("NFKC", raw).upper().replace(" ", "")
    if _IFSC_RE.fullmatch(text):
        return text
    return None


def normalize_account(raw: str) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", unicodedata.normalize("NFKC", raw))
    if 9 <= len(digits) <= 18:
        return digits
    return None


def mask_account(number: str) -> str:
    """DPDP-safe display form: last four digits only (PRD 12.3)."""
    return "X" * max(0, len(number) - 4) + number[-4:] if number else ""


# --------------------------------------------------------------------------
# Aadhaar (stored masked — last four digits only)
# --------------------------------------------------------------------------

_AADHAAR_RE = re.compile(r"\b(\d{4})[\s\-]?(\d{4})[\s\-]?(\d{4})\b")


def normalize_aadhaar(raw: str) -> str | None:
    """Return the DPDP-compliant masked form ``XXXXXXXX1234``."""
    match = _AADHAAR_RE.search(unicodedata.normalize("NFKC", raw or ""))
    if not match:
        return None
    return "XXXXXXXX" + match.group(3)


# --------------------------------------------------------------------------
# Amounts (Indian numbering: ₹, lakh / crore words, digit grouping)
# --------------------------------------------------------------------------

_AMOUNT_RE = re.compile(
    r"(?:rs\.?|inr|₹)\s*([0-9][0-9,]*(?:\.\d+)?)|([0-9][0-9,]*(?:\.\d+)?)\s*(?:rs\.?|inr|₹)",
    re.IGNORECASE,
)
_AMOUNT_UNITS = {"lakh": 100_000, "lac": 100_000, "crore": 1_00_00_000, "cr": 1_00_00_000, "thousand": 1_000}


def parse_amount(raw: str | float | int | None) -> float | None:
    """Parse ``₹1,20,000`` / ``2.5 lakh`` / ``45000`` into a float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = unicodedata.normalize("NFKC", str(raw)).strip()
    if not text:
        return None
    multiplier = 1.0
    lowered = text.lower()
    for word, factor in _AMOUNT_UNITS.items():
        if re.search(rf"\b{re.escape(word)}s?\b", lowered):
            multiplier = factor
            break
    match = _AMOUNT_RE.search(text)
    if match is not None:
        # Currency-anchored form: the alternative groups capture "₹ 1,000" and
        # "1,000 ₹" respectively.
        digits = (match.group(1) or match.group(2) or "").strip()
    else:
        # Bare numeric form (e.g. a CSV "amount" column).  This pattern has no
        # capture group, so use group(0).
        fallback = re.search(r"[0-9][0-9,]*(?:\.\d+)?", text)
        if fallback is None:
            return None
        digits = fallback.group(0)
    if not digits:
        return None
    try:
        return round(float(digits.replace(",", "")) * multiplier, 2)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------

_HONORIFICS = {
    "shri", "shrimati", "smt", "sri", "mr", "mrs", "ms", "dr", "sri.", "kumari",
    "late", "s/o", "d/o", "w/o", "son", "daughter", "wife",
    "shrimati.", "maj", "capt", "col", "inspector", "si", "asi", "constable",
    "hc", "sh.", "sushri",
}
_NAME_PUNCT = re.compile(r"[^\w\s\u0900-\u097F.\-']", re.UNICODE)


def normalize_name(raw: str) -> str:
    """Lower-cased, punctuation-free, honorific-free comparison form of a name."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw).strip()
    text = text.replace("'", "").replace(".", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    tokens = [t for t in text.split(" ") if t and t.lower().strip(":") not in _HONORIFICS]
    return " ".join(tokens).strip().lower()


def display_name(raw: str) -> str:
    """Human-readable, whitespace-normalised form (case preserved)."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", raw or "").strip())


def is_probable_person_name(raw: str) -> bool:
    """Cheap structural gate to keep obvious non-names out of the graph."""
    text = display_name(raw)
    if not (2 <= len(text) <= 64):
        return False
    words = text.split()
    if not (1 <= len(words) <= 5):
        return False
    if not all(re.match(r"^[A-Za-z\u0900-\u097F][A-Za-z\u0900-\u097F.\-']*$", w) for w in words):
        return False
    if text.lower() in _HONORIFICS:
        return False
    return True


def normalize_organization(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw or "").strip().lower()
    text = re.sub(r"\b(pvt|private|limited|ltd|llp|inc|co|company|group|society|trust)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_location(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw or "").strip().lower()
    text = re.sub(r"\b(police station|ps|than|thana|district|dist|near|at|in|the)\b", " ", text)
    return re.sub(r"[\s,]+", " ", text).strip()


# --------------------------------------------------------------------------
# Devanagari transliteration (ISO-15919) — see PRD 9.2
# --------------------------------------------------------------------------

# Vowels (independent forms) and vowel signs (matras)
_DEV_VOWELS = {
    "अ": "a", "आ": "ā", "इ": "i", "ई": "ī", "उ": "u", "ऊ": "ū",
    "ऋ": "r̥", "ॠ": "r̥̄", "ऌ": "l̥", "ए": "ē", "ऐ": "ai",
    "ओ": "ō", "औ": "au",
}
_DEV_MATRAS = {
    "ा": "ā", "ि": "i", "ी": "ī", "ु": "u", "ू": "ū",
    "ृ": "r̥", "ॄ": "r̥̄", "ॢ": "l̥", "े": "ē", "ै": "ai",
    "ो": "ō", "ौ": "au", "ँ": "ṁ", "ं": "ṁ", "ः": "ḥ", "ॅ": "e", "ॉ": "o",
}
_DEV_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ṅ",
    "च": "c", "छ": "ch", "ज": "j", "झ": "jh", "ञ": "ñ",
    "ट": "ṭ", "ठ": "ṭh", "ड": "ḍ", "ढ": "ḍh", "ण": "ṇ",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "ś",
    "ष": "ṣ", "स": "s", "ह": "h", "ळ": "ḷ", "क्ष": "kṣ",
    "ज्ञ": "jñ", "श्र": "śr",
}
_DEV_DIGITS = {
    "०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
    "५": "5", "६": "6", "७": "7", "८": "8", "९": "9",
}
_VIRAMA = "्"
_NUKTA = "़"
_DANDA = "।"
# Anusvara, visarga and candrabindu follow a syllable; unlike a matra they
# do not replace the consonant's inherent vowel (चंद = caṁda, not cṁda).
_DEV_MODIFIERS = frozenset({"ं", "ः", "ँ"})


# The inherent vowel.  It is emitted as a marker rather than as a literal "a"
# so that Hindi schwa deletion can remove it where it is not pronounced
# ("राम" is "rām", not "rāma"), while explicit vowels (अ, आ, इ …) survive.
_SCHWA = "\u0001"
_DEV_NUKTA_CONSONANTS = {
    "क": "q", "ख": "kh", "ग": "ġ", "ज": "z", "ड": "ṛ", "ढ": "ṛh", "फ": "f",
}


def transliterate_devanagari(text: str, *, schwa_delete: bool = True) -> str:
    """Romanise Devanagari to ISO-15919 so trigram similarity works (PRD 9.2).

    ``pg_trgm`` (and Python trigram similarity) operate on characters and behave
    poorly on Devanagari because the script encodes vowels as combining matras
    and drops the inherent ``a`` through the virama.  Romanising first makes
    ``रमेश`` and ``Ramesh`` comparable, which is exactly the comparison an
    investigator-facing fuzzy matcher has to get right.

    Two details make the romanisation usable for matching:

    * a matra *replaces* the consonant's inherent vowel instead of following it,
      so ``मे`` is ``mē`` and not ``maē``;
    * the inherent schwa is written as an internal marker and deleted where
      Hindi does not pronounce it — word-finally.  Explicit long vowels are
      never touched, so ``सुनीता`` stays ``sunītā``.
    """
    if not text:
        return ""
    out: list[str] = []
    chars = unicodedata.normalize("NFC", text)
    i = 0
    n = len(chars)
    while i < n:
        ch = chars[i]
        nxt = chars[i + 1] if i + 1 < n else ""
        if ch in _DEV_DIGITS:
            out.append(_DEV_DIGITS[ch])
        elif ch in _DEV_VOWELS:
            out.append(_DEV_VOWELS[ch])
        elif ch in _DEV_MATRAS:
            out.append(_DEV_MATRAS[ch])
        elif ch in _DEV_CONSONANTS:
            roman = _DEV_CONSONANTS[ch]
            if nxt == _NUKTA:
                roman = _DEV_NUKTA_CONSONANTS.get(ch, roman)
                i += 1
                nxt = chars[i + 1] if i + 1 < n else ""
            if nxt == _VIRAMA:
                out.append(roman)          # suppress the inherent vowel
                i += 2
                continue
            # A following matra supplies the vowel; otherwise the inherent
            # schwa is implied.
            takes_vowel = nxt in _DEV_MATRAS and nxt not in _DEV_MODIFIERS
            out.append(roman + ("" if takes_vowel else _SCHWA))
        elif ch == _VIRAMA or ch == _NUKTA:
            out.append("")
        elif ch == _DANDA:
            out.append(" ")
        elif ch.isspace():
            out.append(" ")
        elif ord(ch) < 128:
            out.append(ch.lower())
        else:
            out.append(" ")
        i += 1

    roman = re.sub(r"\s+", " ", "".join(out)).strip()
    if schwa_delete:
        # Hindi does not pronounce a word-final inherent vowel: राम = rām.
        roman = re.sub(_SCHWA + r"(?=\s|$)", "", roman)
        # Nor one that sits before a syllable with a full vowel, as long as it
        # is not the word's first syllable: मेहता = mēhtā, but रमेश = ramēś.
        roman = re.sub(
            r"([aāiīuūēōeo][kgṅcjñṭḍṇtdnpbmyrlvśṣshqzḍhṭh]{1,2})"
            + _SCHWA
            + r"(?=[kgṅcjñṭḍṇtdnpbmyrlvśṣshqzḍhṭh]{1,2}[āīūēō])",
            r"\1",
            roman,
        )
    return roman.replace(_SCHWA, "a")


# ISO-15919 → ASCII folding for matching only.  Investigators type names the
# way they hear them ("Rathore", "Singh"), never with diacritics, so the
# comparison key folds diacritics away instead of discarding them.
_FOLD_MAP: tuple[tuple[str, str], ...] = (
    ("r̥̄", "ri"), ("r̥", "ri"), ("l̥", "li"),
    ("ṛh", "rh"), ("ṭh", "th"), ("ḍh", "dh"),
    ("ā", "a"), ("ī", "i"), ("ū", "u"), ("ē", "e"), ("ō", "o"),
    ("ai", "ai"), ("au", "au"), ("ṁ", "n"), ("ḥ", "h"),
    ("ṅ", "n"), ("ñ", "n"), ("ṇ", "n"), ("ṛ", "r"),
    ("ṭ", "t"), ("ḍ", "d"), ("ś", "sh"), ("ṣ", "sh"),
    ("c", "ch"), ("q", "k"), ("ġ", "g"), ("z", "z"), ("f", "f"),
)
# An anusvara before ह is what English spells "ngh" (सिंह → Singh).
_FOLD_PRE: tuple[tuple[str, str], ...] = (("ṁh", "ngh"),)


def fold_to_ascii(text: str) -> str:
    """Fold a romanised (ISO-15919) string down to investigator-typed ASCII."""
    out = text
    for src, dst in _FOLD_PRE:
        out = out.replace(src, dst)
    for src, dst in _FOLD_MAP:
        out = out.replace(src, dst)
    return out


def fuzzy_key(raw: str) -> str:
    """Comparison key that is script-agnostic: romanised, folded, alnum-only."""
    if any("ऀ" <= c <= "ॿ" for c in raw):
        roman = fold_to_ascii(transliterate_devanagari(raw))
    else:
        roman = raw
    return re.sub(r"[^a-z0-9 ]+", " ", roman.lower()).strip()


def trigrams(text: str) -> set[str]:
    padded = f"  {text} "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def jaccard_similarity(a: str, b: str) -> float:
    """Trigram Jaccard similarity used by the fuzzy entity matcher (PRD 9.2)."""
    ta, tb = trigrams(fuzzy_key(a)), trigrams(fuzzy_key(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def token_sort_ratio(a: str, b: str) -> float:
    """Order-insensitive similarity — 'Ramesh Yadav' vs 'Yadav Ramesh'."""
    ka, kb = fuzzy_key(a), fuzzy_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    sa, sb = sorted(ka.split()), sorted(kb.split())
    if sa == sb:
        return 0.99
    ja, jb = jaccard_similarity(ka, kb), jaccard_similarity(" ".join(sa), " ".join(sb))
    return max(ja, jb)


def consonant_skeleton(key: str) -> str:
    """Vowel-free skeleton of a comparison key ('rathore' -> 'rthr').

    Indian names are spelled into Latin script by ear, so the consonants are
    stable while the vowels wander (Rathore / Rathaud / Rathod).  Comparing
    skeletons recovers the matches a strict trigram comparison misses.
    """
    return re.sub(r"[aeiouāīūēōr̥l̥\s]", "", key)


def _subset_score(a: str, b: str) -> float:
    """One name contained in the other ('रमेश यादव' inside 'Ramesh Kumar Yadav')."""
    ta, tb = fuzzy_key(a).split(), fuzzy_key(b).split()
    if not ta or not tb:
        return 0.0
    if set(ta) <= set(tb) or set(tb) <= set(ta):
        return 0.9
    return 0.0


def _skeleton_score(a: str, b: str) -> float:
    import difflib

    sa, sb = consonant_skeleton(fuzzy_key(a)), consonant_skeleton(fuzzy_key(b))
    if not sa or not sb:
        return 0.0
    if sa == sb:
        return 0.9
    return round(difflib.SequenceMatcher(None, sa, sb).ratio() * 0.85, 4)


def combined_similarity(a: str, b: str) -> float:
    """Blend of whole-string, token-sorted, containment and skeleton similarity.

    Cross-script matching is the point: an English CDR subscriber name and the
    Devanagari name in an FIR describe the same person, and an investigator who
    has to find that by hand will miss it.  The containment and skeleton signals
    are deliberately capped below 1.0, so they can propose a review but never
    masquerade as an exact match — every proposal still reaches a human (G2).
    """
    whole = jaccard_similarity(a, b)
    tokened = token_sort_ratio(a, b)
    contained = _subset_score(a, b)
    skeletal = _skeleton_score(a, b)
    return round(max(whole, tokened, contained, skeletal), 4)
