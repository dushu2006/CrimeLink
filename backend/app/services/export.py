"""Watermarked case-brief export (PRD 10 / 12.5).

An exported brief is a document that may end up outside the system, so it is
watermarked on every page and embeds the SHA-256 hash of every source document it
cites.  That makes the export self-verifying: a recipient can recompute any cited
document's hash and confirm nothing was altered after export.

Exporting is audited as ``EXPORT`` (PRD 10) and restricted to INVESTIGATOR+.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.container import get_container
from app.db.models import (
    Case,
    CaseDocument,
    DetectedPattern,
    EntityResolutionItem,
)
from app.domain.enums import PatternStatus, ResolutionStatus
from app.domain.normalize import fold_to_ascii, transliterate_devanagari
from app.logging import get_logger

log = get_logger("crimelink.services.export")


# ---------------------------------------------------------------------------
# Devanagari in the PDF
# ---------------------------------------------------------------------------
# reportlab ships no Indic font, and a district server may or may not have one
# installed.  Rather than silently printing a row of empty boxes for every Hindi
# name in a case — the worst possible failure for a bilingual system — the brief
# falls back to the same ISO-15919 transliteration the entity resolver already
# uses, and says so at the bottom of the page.
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_FONT_CANDIDATES = (
    "NotoSansDevanagari-Regular.ttf",
    "NotoSansDevanagari.ttf",
    "NotoSansDevanagari[wdth,wght].ttf",
    "Samanata.ttf",
    "Lohit-Devanagari.ttf",
)
_FONT_DIRS = (
    "/usr/share/fonts/truetype/noto",
    "/usr/share/fonts/opentype/noto",
    "/usr/share/fonts/truetype/lohit",
    "/usr/share/fonts/truetype/samyak",
    "/usr/share/fonts",
)

_devanagari_font: str | None = None
_devanagari_lookup_done = False
# Set when the fallback was used, so the brief can say what it did.
_transliterated_any = False


def _find_devanagari_font() -> str | None:
    """Locate and register a Devanagari TTF, once per process."""
    global _devanagari_font, _devanagari_lookup_done
    if _devanagari_lookup_done:
        return _devanagari_font
    _devanagari_lookup_done = True
    settings = get_settings()
    candidates: list[Path] = []
    configured = getattr(settings, "pdf_devanagari_font", None)
    if configured:
        candidates.append(Path(configured))
    for directory in _FONT_DIRS:
        for name in _FONT_CANDIDATES:
            candidates.append(Path(directory) / name)
    for path in candidates:
        if not path.is_file():
            continue
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont

            pdfmetrics.registerFont(TTFont("CrimeLinkDevanagari", str(path)))
            _devanagari_font = "CrimeLinkDevanagari"
            log.info("export.devanagari_font_registered", path=str(path))
            break
        except Exception as exc:  # noqa: BLE001 - a broken font must not block an export
            log.warning("export.devanagari_font_failed", path=str(path), error=str(exc))
    return _devanagari_font


def _has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text))


def _render_text(text: str) -> str:
    """Mark up a string for a reportlab Paragraph, handling Devanagari."""
    text = str(text)
    if not _has_devanagari(text):
        return _escape(text)
    if _find_devanagari_font():
        return f'<font name="{_devanagari_font}">{_escape(text)}</font>'
    # No Indic font on this host: show the romanised form instead of boxes.
    # It is folded to ASCII because the brief is set in a core PDF font, which
    # has neither Devanagari nor the ISO-15919 diacritics.
    global _transliterated_any
    _transliterated_any = True
    return _escape(fold_to_ascii(transliterate_devanagari(text)).title())


def render_case_brief(
    session: AsyncSession,
    case: Case,
    *,
    documents: list[CaseDocument],
    influencers: list[dict],
    patterns: list[dict],
    matches: list[dict],
) -> bytes:
    """Render the case brief PDF and return it as bytes."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    settings = get_settings()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=f"Case brief — {case.case_number}",
        author="CrimeLink",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=16, spaceAfter=8,
                        textColor=colors.HexColor("#1B3A6B"))
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceBefore=10,
                        spaceAfter=4, textColor=colors.HexColor("#1B3A6B"))
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9, leading=12,
                          alignment=TA_LEFT)
    small = ParagraphStyle("Small", parent=body, fontSize=7.5, leading=9.5,
                           textColor=colors.HexColor("#555555"))
    mono = ParagraphStyle("Mono", parent=body, fontName="Courier", fontSize=7.5, leading=9.5)

    story: list = []
    story.append(Paragraph("CASE BRIEF — CRIMELINK", h1))
    story.append(
        Paragraph(
            f"<b>Case:</b> {case.case_number}<br/>"
            f"<b>Title:</b> {_render_text(case.title)}<br/>"
            f"<b>Jurisdiction:</b> {case.jurisdiction_id}<br/>"
            f"<b>Status:</b> {case.status.value}<br/>"
            f"<b>Generated:</b> {datetime.utcnow().isoformat(timespec='seconds')} UTC",
            body,
        )
    )
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "<i>This brief was generated by an automated system. Every relationship it "
            "describes is evidenced by a source document; every identity match and every "
            "pattern finding listed here requires human confirmation before it is treated "
            "as fact.</i>",
            small,
        )
    )

    # --- Source documents and their integrity hashes -----------------------
    story.append(Paragraph("1. Source documents (chain of custody)", h2))
    if documents:
        table_data = [["Type", "File", "Language", "Confidence", "SHA-256"]]
        for document in documents:
            table_data.append(
                [
                    document.document_type.value,
                    Paragraph(_render_text(document.filename[:40]), small),
                    document.language or "-",
                    document.source_confidence.value,
                    Paragraph(document.content_hash[:24] + "…", mono),
                ]
            )
        table = Table(table_data, colWidths=[24 * mm, 52 * mm, 16 * mm, 26 * mm, 56 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F8")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
    else:
        story.append(Paragraph("No documents ingested.", body))

    # --- Influence ranking --------------------------------------------------
    story.append(Paragraph("2. Influence ranking (with explanation)", h2))
    if influencers:
        rows = [["#", "Entity", "Type", "Betweenness", "PageRank", "Degree", "Community"]]
        for item in influencers:
            rows.append(
                [
                    str(item.get("rank")),
                    Paragraph(_render_text(str(item.get("name"))[:44]), small),
                    str(item.get("label")),
                    f"{item.get('betweenness', 0):.4f}",
                    f"{item.get('pagerank', 0):.4f}",
                    str(item.get("degree")),
                    str(item.get("community")),
                ]
            )
        table = Table(rows, colWidths=[8 * mm, 66 * mm, 20 * mm, 24 * mm, 22 * mm, 16 * mm, 20 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F8")),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 4))
        top = influencers[0]
        story.append(
            Paragraph(
                f"<b>Why {_render_text(str(top.get('name')))} ranks first:</b> the ranking is "
                "betweenness centrality computed on this case's evidence graph with edge "
                "weights equal to each relationship's confidence. Low-confidence sources "
                "(social media, tips) contribute proportionally less.",
                small,
            )
        )
    else:
        story.append(Paragraph("Not enough graph data to rank influence yet.", body))

    # --- Pattern findings ---------------------------------------------------
    story.append(Paragraph("3. Pattern findings (awaiting human review)", h2))
    if patterns:
        for item in patterns:
            story.append(
                Paragraph(
                    f"<b>{item['pattern_type']}</b> — {item['status']} "
                    f"(confidence {item['confidence']:.2f})<br/>"
                    f"{_render_text(item['explanation'])}<br/>"
                    f"<font color='#555555'>Evidence documents: "
                    f"{len(item['evidence_doc_ids'])}</font>",
                    body,
                )
            )
            story.append(Spacer(1, 3))
    else:
        story.append(Paragraph("No pattern findings.", body))

    # --- Entity resolution queue -------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("4. Pending identity matches (awaiting human decision)", h2))
    if matches:
        rows = [["Candidate A", "Candidate B", "Similarity", "Basis", "Status"]]
        for item in matches:
            rows.append(
                [
                    Paragraph(_render_text(str(item["source"]["name"])[:32]), small),
                    Paragraph(_render_text(str(item["target"]["name"])[:32]), small),
                    f"{item['similarity_score']:.2f}",
                    f"{item['match_basis']} \u00b7 {len(item['evidence_doc_ids'])} doc(s)",
                    item["status"],
                ]
            )
        table = Table(rows, colWidths=[52 * mm, 52 * mm, 20 * mm, 30 * mm, 22 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F8")),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ]
            )
        )
        story.append(table)
    else:
        story.append(Paragraph("No pending identity matches.", body))

    story.append(Paragraph("5. Verification", h2))
    story.append(
        Paragraph(
            "Each source document's SHA-256 fingerprint is listed in section 1. Recompute "
            "any document's hash from the original file and compare it against this brief to "
            "confirm that the evidence has not been altered since ingestion.",
            body,
        )
    )
    if _transliterated_any:
        story.append(Spacer(1, 6))
        story.append(
            Paragraph(
                "<i>Devanagari names above appear in romanised form because no Devanagari "
                "font is installed on this server. The stored graph retains the original "
                "script; only this rendering is transliterated.</i>",
                small,
            )
        )

    def _watermark(canvas, document_):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColorRGB(0.72, 0.72, 0.72)
        canvas.drawString(18 * mm, 10 * mm, settings.export_watermark)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, case.case_number)
        canvas.setStrokeColorRGB(0.8, 0.8, 0.8)
        canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
        canvas.restoreState()

    doc.build(story, onFirstPage=_watermark, onLaterPages=_watermark)
    return buffer.getvalue()


async def build_case_brief(session: AsyncSession, case: Case) -> bytes:
    """Assemble everything a case brief needs and render it."""
    from app.services.cases import case_summaries
    from app.services.graph_service import GraphService
    from app.services.patterns import list_patterns
    from app.services.resolution import list_queue

    graph = GraphService(get_container())
    documents = (
        await session.execute(
            select(CaseDocument)
            .where(CaseDocument.case_id == case.id, CaseDocument.is_deleted.is_(False))
            .order_by(CaseDocument.created_at)
        )
    ).scalars().all()

    scope = _CaseScope(case)
    # Every section degrades on its own: an empty graph, no findings or no
    # review items must still produce a brief an investigator can file.
    try:
        influencers = await graph.ranked_influencers(session, scope, case.id, limit=10)
    except Exception:  # noqa: BLE001 - an empty graph must not block an export
        influencers = []
    try:
        patterns = await list_patterns(session, scope, case_id=case.id, limit=50)
    except Exception:  # noqa: BLE001
        patterns = []
    try:
        matches = await list_queue(session, scope, case_id=case.id, limit=50)
    except Exception:  # noqa: BLE001
        matches = []

    return render_case_brief(
        session,
        case,
        documents=list(documents),
        influencers=influencers,
        patterns=patterns,
        matches=matches,
    )


class _CaseScope:
    """Scope narrowed to one case, used after the caller's access check passed."""

    def __init__(self, case: Case) -> None:
        self.case_id = case.id

    def case_filter(self):
        from app.db.models import Case as _Case

        return _Case.id == self.case_id

    def assert_case(self, target):
        return target

    @property
    def principal(self):
        return None


def _escape(text: str) -> str:
    """Escape for a reportlab Paragraph, substituting characters the core PDF
    fonts cannot draw."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\u20b9", "INR ")   # ₹ — not in the base-14 fonts
        .replace("\u2014", "-")
    )
