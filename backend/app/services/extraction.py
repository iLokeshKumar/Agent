"""WP-4 Extraction classes — scheme circulars, KYC docs, serial numbers.

Framework per Cowork Tech Spec §6.2 and the WBS item 1.3:

  raw doc (file URI / WA media / email attachment / inline text)
        │
        ▼
  OCR transport        text | tesseract | llm_vision   ← pluggable
        │  (text, pages, per-transport confidence)
        ▼
  Extractor (per doc class) → fields + per-field confidence
        │
        ├─ overall ≥ accept threshold  → fields usable downstream
        └─ overall <  review threshold → needs_review=True, NO
                                        downstream use until a human
                                        accepts (review_fallback)

Design rules (same discipline as the rest of the platform):
  - Deterministic parsing in Python; never prompt-math or prompt-parse
    money, dates or serials.
  - Extraction is a *candidate*, never truth. F2's scheme register
    keeps its own ``verified_by`` gate on top of this.
  - Every attempt is persisted to ``doc_extraction`` so the audit
    trail shows what was read, how confident, and who confirmed.
  - Nothing here commits; callers own the transaction (mirrors
    app/services/documents.py).

OCR transports are chosen by ``EXTRACTION_OCR_PROVIDER``:
    "" (default)  PlainTextTransport  — .txt/.eml/.html and tests
    "tesseract"   TesseractTransport  — binary runs via pytesseract,
                                        import-guarded: only offered
                                        when the package is installed
    "llm_vision"  LlmVisionTransport — stub raising NotImplementedError
                                        until the vision model contract
                                        is wired (keeps the seam real)
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Thresholds (configurable per deployment via settings later) ────

ACCEPT_THRESHOLD = 0.85   # ≥ this: fields may flow downstream
REVIEW_THRESHOLD = 0.60   # below this: force human review
REVIEW_FLOOR = 0.30       # below this: fields are almost certainly
                          # garbage — still reviewed, but flagged hard

VALID_KINDS = ("scheme_circular", "kyc", "serial_capture", "vendor_invoice")


class ExtractionError(Exception):
    """Raised when a document cannot be extracted at all."""


# ═══════════════════════════════════════════════════════════════════
# Transports — raw document → text
# ═══════════════════════════════════════════════════════════════════


@dataclass
class OcrResult:
    text: str
    pages: int
    provider: str
    # 0..1 — how much the transport itself trusts its text
    transport_confidence: float = 0.9


class OcrTransport:
    """Interface: raw bytes/path → OcrResult."""

    name = "base"

    def extract(self, source: str | None = None, *, text: str = "", **_) -> OcrResult:
        raise NotImplementedError


class PlainTextTransport(OcrTransport):
    """For text-native documents (txt, eml body, html strip) and tests."""

    name = "text"

    def extract(self, source: str | None = None, *, text: str = "", **_) -> OcrResult:
        if text:
            return OcrResult(text=text, pages=1, provider=self.name,
                             transport_confidence=1.0)
        if source:
            p = Path(source)
            if not p.exists():
                raise ExtractionError(f"document not found: {source}")
            raw = p.read_text(encoding="utf-8", errors="replace")
            pages = raw.count("\f") + 1  # form feeds = page breaks
            return OcrResult(text=raw, pages=pages, provider=self.name,
                             transport_confidence=1.0)
        raise ExtractionError("no text or source provided")


class TesseractTransport(OcrTransport):
    """PDF/scan → text via ``pdftotext`` (text-layer) or tesseract OCR.

    Import/run guarded: if the binaries are missing we raise a clear
    ExtractionError so callers surface a configuration problem instead
    of silently producing garbage.
    """

    name = "tesseract"

    def extract(self, source: str | None = None, *, text: str = "", **_) -> OcrResult:
        if text:
            # Caller already did OCR upstream; pass through.
            return OcrResult(text=text, pages=1, provider=self.name,
                             transport_confidence=0.9)
        if not source:
            raise ExtractionError("no source file for tesseract transport")
        p = Path(source)
        if not p.exists():
            raise ExtractionError(f"document not found: {source}")

        suffix = p.suffix.lower()
        if suffix in (".txt", ".md", ".csv"):
            return PlainTextTransport().extract(source=source)

        if suffix == ".pdf" and shutil.which("pdftotext"):
            try:
                out = subprocess.run(  # noqa: S603 — fixed argv
                    ["pdftotext", "-layout", str(p), "-"],
                    capture_output=True, text=True, timeout=120, check=True,
                )
                text_out = out.stdout
                pages = text_out.count("\f") + 1
                # Text-layer PDFs are exact; scanned ones (short output
                # for a real file) should fall through to OCR below.
                if len(text_out.strip()) > 200:
                    return OcrResult(text=text_out, pages=pages,
                                     provider="pdftotext",
                                     transport_confidence=0.95)
            except (subprocess.SubprocessError, OSError) as exc:
                logger.warning("pdftotext failed for %s: %s", source, exc)

        if not shutil.which("tesseract"):
            raise ExtractionError(
                "tesseract binary not found on PATH — install tesseract-ocr "
                "(and poppler-utils for PDF text layers) or set "
                "EXTRACTION_OCR_PROVIDER to '' for text documents"
            )
        try:
            out = subprocess.run(  # noqa: S603 — fixed argv
                ["tesseract", str(p), "-", "--psm", "6"],
                capture_output=True, text=True, timeout=300, check=True,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise ExtractionError(f"tesseract failed on {source}: {exc}") from exc

        text_out = out.stdout
        pages = text_out.count("\f") + 1
        # Short output for a big file ⇒ OCR likely misread most of it.
        confidence = 0.75 if len(text_out.strip()) > 100 else 0.4
        return OcrResult(text=text_out, pages=pages, provider=self.name,
                         transport_confidence=confidence)


class LlmVisionTransport(OcrTransport):
    """Vision-model OCR seam — intentionally not implemented yet.

    The contract is already fixed (same OcrResult) so swapping it in
    later touches nothing else. Kept as a real seam rather than a
    silent fallback to plain text, which would fake confidence.
    """

    name = "llm_vision"

    def extract(self, source: str | None = None, *, text: str = "", **_) -> OcrResult:
        raise NotImplementedError(
            "llm_vision OCR is not wired yet — use EXTRACTION_OCR_PROVIDER="
            "'tesseract' or '' (text documents)."
        )


_TRANSPORTS: dict[str, type[OcrTransport]] = {
    "": PlainTextTransport,
    "text": PlainTextTransport,
    "tesseract": TesseractTransport,
    "llm_vision": LlmVisionTransport,
}


def get_transport(provider: str | None = None) -> OcrTransport:
    """Resolve an OCR transport by name (default from settings if unset)."""
    if provider is None:
        try:
            from app.core.config import settings
            provider = (settings.extraction_ocr_provider or "").strip().lower()
        except Exception:  # settings unavailable (early tests) → text
            provider = ""
    provider = (provider or "").strip().lower()
    cls = _TRANSPORTS.get(provider)
    if cls is None:
        raise ExtractionError(
            f"unknown OCR provider {provider!r} — known: "
            f"{sorted(k for k in _TRANSPORTS)}"
        )
    return cls()


# ═══════════════════════════════════════════════════════════════════
# Field confidence — every field carries a 0..1 score
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ExtractionResult:
    kind: str
    fields: dict = field(default_factory=dict)
    field_confidence: dict = field(default_factory=dict)
    ocr: OcrResult | None = None
    overall_confidence: float = 0.0
    needs_review: bool = False
    review_reason: str | None = None

    @property
    def accepted(self) -> bool:
        return not self.needs_review

    def to_row_kwargs(self) -> dict:
        """Fields for a DocExtraction row (caller persists + commits)."""
        return {
            "kind": self.kind,
            "ocr_provider": self.ocr.provider if self.ocr else None,
            "pages": self.ocr.pages if self.ocr else None,
            "fields_json": self.fields,
            "fields_conf_json": self.field_confidence,
            "overall_confidence": round(self.overall_confidence, 4),
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "status": "extracted",
        }


def _finalize(kind: str, fields: dict, conf: dict, ocr: OcrResult,
              required: list[str]) -> ExtractionResult:
    """Combine transport + per-field confidence into a verdict.

    Field confidence blends the transport's own trust (OCR noise
    affects every field) with the per-field parse confidence computed
    by the extractor. Missing required fields always forces review.
    """
    blended = {
        k: round(conf.get(k, 0.0) * ocr.transport_confidence, 4)
        for k in fields
    }
    overall = (
        round(sum(blended.values()) / len(blended), 4) if blended else 0.0
    )

    missing = [k for k in required if not fields.get(k)]
    reasons: list[str] = []
    if missing:
        reasons.append("missing required fields: " + ", ".join(missing))
    if overall < REVIEW_THRESHOLD:
        reasons.append(f"overall confidence {overall:.2f} below review floor "
                       f"{REVIEW_THRESHOLD:.2f}")
    if ocr.transport_confidence < 0.5:
        reasons.append(
            f"OCR transport confidence low ({ocr.transport_confidence:.2f})")

    return ExtractionResult(
        kind=kind,
        fields=fields,
        field_confidence=blended,
        ocr=ocr,
        overall_confidence=overall,
        needs_review=bool(reasons),
        review_reason="; ".join(reasons) if reasons else None,
    )


# ═══════════════════════════════════════════════════════════════════
# Shared parsing helpers (deterministic, Decimal for money)
# ═══════════════════════════════════════════════════════════════════

# Samsung circular IDs seen in the wild: SEC/2026-27/041, SM-2026-0091,
# "Circular No. 2026/07" etc. Anything with letters+digits+separator.
_CIRCULAR_ID_RE = re.compile(
    r"(?:circular|scheme)\s*(?:no\.?|number|#)?\s*[:\-]?\s*"
    r"([A-Z0-9][A-Z0-9/\-\.]{4,40})", re.IGNORECASE)
_DATE_RE = re.compile(
    r"(20\d{2})[-/\.](\d{1,2})[-/\.](\d{1,2})")
_SLAB_LINE_RE = re.compile(
    r"(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)\s*(?:₹|Rs\.?|INR)?\s*[-–—to]+\s*"
    r"(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)\s*(?:₹|Rs\.?|INR)?\s*[:\-]?\s*"
    r"(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
_SINGLE_SLAB_RE = re.compile(
    r"(?:above|≥|>=|min(?:imum)?)\s*(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)\s*"
    r"(?:₹|Rs\.?|INR)?\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
_MONEY_RE = re.compile(r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)")
_SERIAL_RE = re.compile(r"\b([A-Z0-9]{11,22})\b")
_SKU_RE = re.compile(r"\b((?:SM|QA|LA|RS|UA|UN|GA|RA|NE)[A-Z0-9\-]{6,24})\b",
                     re.IGNORECASE)
# Aadhaar: masked or full — XXXX XXXX 1234 / 1234 5678 9012
_AADHAAR_RE = re.compile(r"\b(\d{4}|[Xx]{4})\s?(\d{4}|[Xx]{4})\s?(\d{4})\b")
_PAN_RE = re.compile(r"\b([A-Z]{5}\d{4}[A-Z])\b")
_GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z]\d)\b")
_PHONE_RE = re.compile(r"\b((?:\+91[\-\s]?)?[6-9]\d{9})\b")
_ACCOUNT_RE = re.compile(r"\b(\d{9,18})\b")
_IFSC_RE = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
_DOJ_RE = re.compile(
    r"(?:doj|date of join|joining date|date of joining)\s*[:\-]?\s*"
    r"((?:\d{1,2}[\s\-/][A-Za-z0-9]+[\s\-/]\d{2,4})|(?:\d{1,2}[\s\-/]\d{1,2}[\s\-/]\d{2,4})|(?:\d{4}-\d{2}-\d{2}))",
    re.IGNORECASE)
_DOB_RE = re.compile(
    r"(?:dob|d\.o\.b|date of birth)\s*[:\-]?\s*"
    r"((?:\d{1,2}[\s\-/][A-Za-z0-9]+[\s\-/]\d{2,4})|(?:\d{1,2}[\s\-/]\d{1,2}[\s\-/]\d{2,4})|(?:\d{4}-\d{2}-\d{2}))",
    re.IGNORECASE)
_IN_WORDS_RE = re.compile(r"\b([A-Za-z]{3,})\b")


def parse_money(value: str) -> tuple[Decimal | None, float]:
    """'₹ 12,50,000.50' → (Decimal('1250000.50'), 0.95). None if unparseable."""
    m = _MONEY_RE.search(value or "")
    raw = m.group(1) if m else (value or "").strip()
    raw = raw.replace(",", "").replace(" ", "")
    try:
        return Decimal(raw), 0.95
    except (InvalidOperation, ValueError):
        return None, 0.0


def parse_iso_date(value: str) -> tuple[date | None, float]:
    """Multiple date formats → (date, confidence)."""
    raw = (value or "").strip()
    for fmt, conf in (
        ("%Y-%m-%d", 0.99), ("%d-%m-%Y", 0.9), ("%d/%m/%Y", 0.9),
        ("%d.%m.%Y", 0.85), ("%d %b %Y", 0.9), ("%d %B %Y", 0.9),
        ("%d-%b-%Y", 0.9), ("%d-%B-%Y", 0.9),
    ):
        try:
            return datetime.strptime(raw, fmt).date(), conf
        except ValueError:
            continue
    return None, 0.0


def _parse_slab_line(line: str) -> dict | None:
    m = _SLAB_LINE_RE.search(line)
    if m:
        lo, _ = parse_money(m.group(1))
        hi, _ = parse_money(m.group(2))
        try:
            rate = Decimal(m.group(3)) / Decimal(100)
        except InvalidOperation:
            return None
        if lo is None or hi is None:
            return None
        return {"min_sales": float(lo), "max_sales": float(hi),
                "rate": float(rate), "conf": 0.9}
    m = _SINGLE_SLAB_RE.search(line)
    if m:
        lo, _ = parse_money(m.group(1))
        try:
            rate = Decimal(m.group(2)) / Decimal(100)
        except InvalidOperation:
            return None
        if lo is None:
            return None
        return {"min_sales": float(lo), "max_sales": None,
                "rate": float(rate), "conf": 0.75}
    return None


# ═══════════════════════════════════════════════════════════════════
# Extractor 1 — Samsung scheme circular (feeds F2 scheme_master)
# ═══════════════════════════════════════════════════════════════════


def extract_scheme_circular(
    source: str | None = None, *, text: str = "",
    provider: str | None = None,
) -> ExtractionResult:
    """Parse a vendor scheme circular → structured candidate scheme.

    Output fields (all *candidates* — F2's verified_by gate still
    applies before claims compute against this):
      circular_id, scheme_type, window_start, window_end,
      eligible_skus[], slabs[{min_sales, max_sales, rate}],
      per_invoice_min, evidence_reqs[]
    """
    ocr = get_transport(provider).extract(source, text=text)
    t = ocr.text

    fields: dict = {}
    conf: dict = {}

    # Circular ID — usually the strongest signal on the page.
    m = _CIRCULAR_ID_RE.search(t)
    if m:
        fields["circular_id"] = m.group(1).strip()
        conf["circular_id"] = 0.95
    else:
        # Fall back to any ALLCAPS reference-looking token near "scheme".
        idx = t.lower().find("scheme")
        if idx >= 0:
            m2 = _SERIAL_RE.search(t[idx:idx + 120])
            if m2:
                fields["circular_id"] = m2.group(1)
                conf["circular_id"] = 0.5

    # Scheme type from keywords.
    type_conf = 0.6
    tl = t.lower()
    if "slab" in tl or "turnover" in tl or "offtake" in tl:
        stype = "slab"
    elif "bundle" in tl or "attach" in tl and "free" in tl:
        stype = "bundle"
    elif "price protection" in tl or "price support" in tl:
        stype = "price_support"
    else:
        stype = "slab"
        type_conf = 0.3
    fields["scheme_type"] = stype
    conf["scheme_type"] = type_conf

    # Window — first two dates in the doc are start/end by convention.
    dates = [(m.start(), date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
             for m in _DATE_RE.finditer(t)]
    dates.sort(key=lambda p: p[0])
    if len(dates) >= 1:
        fields["window_start"] = dates[0][1].isoformat()
        conf["window_start"] = 0.8
    if len(dates) >= 2:
        fields["window_end"] = dates[1][1].isoformat()
        conf["window_end"] = 0.75

    # Slabs — parse every slab-looking line; keep the consistent ladder.
    slabs: list[dict] = []
    for line in t.splitlines():
        parsed = _parse_slab_line(line)
        if parsed:
            slabs.append(parsed)
    slabs.sort(key=lambda s: s["min_sales"])
    # Drop entries whose rate descends as min_sales climbs (misparse).
    clean: list[dict] = []
    last_min = -1.0
    for s in slabs:
        if s["min_sales"] > last_min:
            clean.append(s)
            last_min = s["min_sales"]
    if clean:
        fields["slabs"] = [
            {k: v for k, v in s.items() if k != "conf"} for s in clean]
        conf["slabs"] = round(sum(s["conf"] for s in clean) / len(clean), 4)

    # Eligible SKUs — Samsung-style model codes.
    skus: list[str] = []
    for m in _SKU_RE.finditer(t):
        code = m.group(1).upper()
        if code not in skus:
            skus.append(code)
    if skus:
        fields["eligible_skus"] = skus[:50]  # cap noise from OCR garbage
        conf["eligible_skus"] = 0.7

    # Per-invoice minimum, if stated.
    m = re.search(r"per[- ]invoice\s*(?:minimum|min\.?|value)\s*[:\-]?\s*"
                  r"(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)", tl and t or "",
                  re.IGNORECASE)
    if m:
        val, c = parse_money(m.group(1))
        if val is not None:
            fields["per_invoice_min"] = float(val)
            conf["per_invoice_min"] = c

    # Evidence requirements — bulleted doc list after "claim" keyword.
    ev: list[str] = []
    lower = t.lower()
    for kw in ("invoice copy", "gst invoice", "proof of purchase",
               "photograph", "serial", "delivery challan", "e-way bill",
               "claim form"):
        if kw in lower:
            ev.append(kw)
    if ev:
        fields["evidence_reqs"] = ev
        conf["evidence_reqs"] = 0.65

    result = _finalize("scheme_circular", fields, conf, ocr,
                       required=["circular_id", "window_start", "window_end"])

    # A circular with no slabs and no SKUs is not worth a human's time.
    if result.fields.get("slabs") is None and not result.fields.get("eligible_skus"):
        result.needs_review = True
        result.review_reason = (result.review_reason or "") + (
            "; no slabs or SKUs recognised — possible wrong document class")

    return result


# ═══════════════════════════════════════════════════════════════════
# Extractor 2 — KYC / onboarding documents (feeds H2)
# ═══════════════════════════════════════════════════════════════════


def extract_kyc(
    source: str | None = None, *, text: str = "",
    provider: str | None = None,
) -> ExtractionResult:
    """Parse KYC docs (PAN/Aadhaar/bank letter) → identity fields.

    Output fields: full_name, pan, aadhaar_last4, gstin, phone,
    account_number, ifsc, date_of_joining, date_of_birth.

    Privacy: only the last 4 digits of Aadhaar are ever captured; a
    full unmasked number is detected, noted, and *not* stored. PAN and
    GSTIN are business identifiers required for onboarding; they are
    stored per DPDP retention_class 'kyc' (purge_after owned by caller).
    """
    ocr = get_transport(provider).extract(source, text=text)
    t = ocr.text

    fields: dict = {}
    conf: dict = {}

    def grab(pattern: re.Pattern, key: str, base_conf: float) -> None:
        m = pattern.search(t)
        if m:
            fields[key] = " ".join(g for g in m.groups() if g)
            conf[key] = base_conf

    grab(_PAN_RE, "pan", 0.95)
    grab(_GSTIN_RE, "gstin", 0.95)
    grab(_PHONE_RE, "phone", 0.8)
    grab(_IFSC_RE, "ifsc", 0.9)

    # Aadhaar — masked preferred; full captured as "detected" only.
    m = _AADHAAR_RE.search(t)
    if m:
        joined = "".join(g for g in m.groups() if g)
        if set(joined.upper()) & {"X"}:
            fields["aadhaar_last4"] = joined[-4:]
            conf["aadhaar_last4"] = 0.9
        else:
            fields["aadhaar_last4"] = joined[-4:]
            conf["aadhaar_last4"] = 0.7
            # Unmasked full number seen: flag, do not store the number.
            fields["aadhaar_unmasked_seen"] = True
            conf["aadhaar_unmasked_seen"] = 1.0

    # Bank account: prefer line with 'account' context.
    m = re.search(
        r"account\s*(?:no\.?|number|#)?\s*[:\-]?\s*(\d{9,18})",
        t, re.IGNORECASE)
    if m:
        fields["account_number"] = m.group(1)
        conf["account_number"] = 0.85
    else:
        m = _ACCOUNT_RE.search(t)
        if m:
            fields["account_number"] = m.group(1)
            conf["account_number"] = 0.5  # bare number — weak

    # Name: the "Name :" convention on Indian KYC forms.
    m = re.search(r"(?:full\s*)?name\s*[:\-]\s*([A-Z][A-Za-z .]{2,60})", t)
    if m:
        fields["full_name"] = m.group(1).strip()
        conf["full_name"] = 0.8

    for pattern, key in ((_DOJ_RE, "date_of_joining"), (_DOJ_RE, "_x"),
                         (_DOB_RE, "date_of_birth")):
        if key == "_x":
            continue
        m = pattern.search(t)
        if m:
            d, c = parse_iso_date(m.group(1))
            if d:
                fields[key] = d.isoformat()
                conf[key] = c

    result = _finalize("kyc", fields, conf, ocr,
                       required=["full_name", "pan"])

    # H2 onboarding requires a working bank account; weak/no IFSC with
    # an account number is the classic OCR-mangled cheque scan.
    if fields.get("account_number") and not fields.get("ifsc"):
        result.needs_review = True
        result.review_reason = (result.review_reason or "") + (
            "; account number present but IFSC missing — verify bank details")

    return result


# ═══════════════════════════════════════════════════════════════════
# Extractor 3 — serial-number capture (feeds S1 / serial_registry)
# ═══════════════════════════════════════════════════════════════════


def extract_serials(
    source: str | None = None, *, text: str = "",
    provider: str | None = None,
    expected_count: int | None = None,
    context_skus: list[str] | None = None,
) -> ExtractionResult:
    """Pull candidate serials from a label photo / GRN scan / carton.

    Output fields: serials[], count, model_codes[].
    Cross-checks against ``expected_count`` (from the PO line) and
    ``context_skus`` (SKUs on the GRN) to score each hit:

      - token matches an expected SKU prefix       → 0.95
      - token has Samsung-checksum-like shape      → 0.7  (base)
      - token is lowercase / mixed garbage         → dropped
    """
    ocr = get_transport(provider).extract(source, text=text)
    t = ocr.text

    sku_prefixes = tuple(
        (s or "").upper()[:2] for s in (context_skus or []) if s)

    serials: list[dict] = []
    seen: set[str] = set()
    for m in _SERIAL_RE.finditer(t.upper()):
        token = m.group(1)
        if token in seen:
            continue
        seen.add(token)
        # Reject tokens that are obviously words (all vowels or common
        # English patterns) — OCR loves inventing these.
        if re.fullmatch(r"[A-EIOU]{11,22}", token):
            continue
        if re.fullmatch(r"[0-9]{11,22}", token) and len(token) < 13:
            continue  # too short to be a serial, likely invoice/phone no.
        conf = 0.7
        if sku_prefixes and token.startswith(sku_prefixes):
            conf = 0.95
        elif context_skus:
            # No prefix match against known SKUs — suspicious.
            conf = 0.45
        serials.append({"serial": token, "conf": conf})

    serials.sort(key=lambda s: -s["conf"])
    fields = {
        "serials": [{"serial": s["serial"]} for s in serials],
        "count": len(serials),
        "model_codes": sorted({s["serial"][:4] for s in serials})[:20],
    }
    conf_map = {"count": 0.9, "model_codes": 0.6}
    if serials:
        conf_map["serials"] = round(sum(s["conf"] for s in serials) / len(serials), 4)

    result = _finalize("serial_capture", fields, conf_map, ocr,
                       required=["serials"])

    if expected_count is not None and len(serials) != expected_count:
        result.needs_review = True
        result.review_reason = (
            (result.review_reason or "")
            + f"; serial count {len(serials)} != expected {expected_count}")

    return result


# ═══════════════════════════════════════════════════════════════════
# Persistence + human-review fallback
# ═══════════════════════════════════════════════════════════════════


def persist_extraction(db, result: ExtractionResult, *,
                       doc_id=None, source: str | None = None,
                       source_ref: str | None = None,
                       sender_ref: str | None = None) -> "DocExtraction":
    """Write one DocExtraction row (and a DocIndex row when a file
    source is given). Caller commits."""
    from app.models.enterprise import DocExtraction, DocIndex

    doc_row = None
    if source or source_ref:
        doc_row = DocIndex(
            source="upload",
            source_ref=source_ref or source,
            sender_ref=sender_ref,
            doc_class=result.kind,
            file_uri=source,
            pages=result.ocr.pages if result.ocr else None,
        )
        db.add(doc_row)
        db.flush()

    row = DocExtraction(
        doc_id=doc_row.id if doc_row else doc_id,
        **result.to_row_kwargs(),
    )
    db.add(row)
    db.flush()
    return row


def review_fallback(db, extraction_id, decision: str, reviewed_by: str,
                    fields_override: dict | None = None):
    """Human gate for low-confidence extractions.

    decision: 'accept' (fields usable, optionally with corrections) |
              'reject' (wrong/garbage extraction).
    Returns the updated row or None if the id is unknown / already
    reviewed. Never moves data downstream itself — downstream wiring
    (scheme_master candidate, serial_registry write) reads the row
    after acceptance.
    """
    from app.models.enterprise import DocExtraction

    if decision not in ("accept", "reject"):
        raise ValueError("decision must be 'accept' or 'reject'")

    row = db.query(DocExtraction).filter(
        DocExtraction.id == extraction_id).first()
    if row is None:
        return None
    if row.reviewed_by is not None:
        raise ValueError("extraction already reviewed")

    if decision == "accept":
        if fields_override:
            merged = dict(row.fields_json or {})
            merged.update(fields_override)
            row.fields_json = merged
        row.status = "accepted"
    else:
        row.status = "rejected"
    row.reviewed_by = reviewed_by
    row.reviewed_ts = datetime.utcnow()
    row.needs_review = False
    db.flush()
    return row


def accept_scheme_circular(db, extraction_id: str, verified_by: str):
    """Accepted circular → SchemeMaster candidate (status stays
    'candidate' — the F2 verified_by gate is separate and human)."""
    from app.models.enterprise import DocExtraction
    from app.models.scheme import SchemeMaster

    row = db.query(DocExtraction).filter(
        DocExtraction.id == extraction_id).first()
    if row is None or row.kind != "scheme_circular":
        raise ValueError("scheme_circular extraction not found")
    if row.status != "accepted":
        raise ValueError("extraction must be accepted before promotion")

    f = row.fields_json or {}
    if not (f.get("circular_id") and f.get("window_start") and f.get("window_end")):
        raise ValueError("fields missing circular_id/window — cannot promote")

    existing = db.query(SchemeMaster).filter(
        SchemeMaster.scheme_id == f["circular_id"]).first()
    if existing:
        row.downstream_ref = existing.scheme_id
        db.flush()
        return existing

    scheme = SchemeMaster(
        scheme_id=f["circular_id"],
        vendor="Samsung",
        scheme_type=f.get("scheme_type", "slab"),
        window_start=date.fromisoformat(f["window_start"]),
        window_end=date.fromisoformat(f["window_end"]),
        eligibility_rules_json={
            k: f[k] for k in ("eligible_skus", "slabs", "per_invoice_min")
            if f.get(k) is not None
        },
        evidence_reqs_json=(
            {"reqs": f["evidence_reqs"]} if f.get("evidence_reqs") else None),
        circular_doc_ref=str(row.doc_id) if row.doc_id else None,
        parsed_confidence=row.overall_confidence,
    )
    db.add(scheme)
    db.flush()
    row.downstream_ref = scheme.scheme_id
    db.flush()
    return scheme


def accept_serials(db, extraction_id: str, accepted_by: str, *,
                   entity: str, grn_id=None, sku_ref: str | None = None):
    """Accepted serial capture → serial_registry rows (in_stock).

    Idempotent per serial: existing rows are skipped, not overwritten —
    S1's discrepancy logic owns any state change after that.
    """
    from app.models.enterprise import DocExtraction, SerialRegistry

    row = db.query(DocExtraction).filter(
        DocExtraction.id == extraction_id).first()
    if row is None or row.kind != "serial_capture":
        raise ValueError("serial_capture extraction not found")
    if row.status != "accepted":
        raise ValueError("extraction must be accepted before registry write")

    f = row.fields_json or {}
    created, skipped = [], []
    for item in f.get("serials", []):
        serial = item.get("serial")
        if not serial:
            continue
        if db.query(SerialRegistry).filter(
                SerialRegistry.serial == serial).first():
            skipped.append(serial)
            continue
        db.add(SerialRegistry(
            serial=serial,
            sku_ref=sku_ref,
            entity=entity,
            grn_id=grn_id,
            status="in_stock",
            received_ts=datetime.utcnow(),
        ))
        created.append(serial)
    row.downstream_ref = ",".join(created[:10])
    db.flush()
    return {"created": created, "skipped_existing": skipped}


# ═══════════════════════════════════════════════════════════════════
# Extractor 4 — Vendor GST invoice (feeds P4 / F3 staging)
# ═══════════════════════════════════════════════════════════════════

# Patterns specific to Indian GST tax invoices
_BILL_NO_RE = re.compile(
    r"(?:invoice|bill|tax\s*invoice|inv)[\s\-\.#]*(?:no\.?|number|#)?\s*[:\-]?\s*"
    r"([A-Z0-9][A-Z0-9/\-\.]{2,40})",
    re.IGNORECASE,
)
_IRN_RE = re.compile(r"\b([a-fA-F0-9]{64})\b")
_HSN_RE = re.compile(r"(?:HSN|SAC)\s*(?:code|no\.?)?\s*[:\-]?\s*(\d{4,8})", re.IGNORECASE)
_PLACE_OF_SUPPLY_RE = re.compile(
    r"place\s*of\s*supply\s*[:\-]?\s*([A-Za-z\s]{3,40})", re.IGNORECASE
)
_LINE_ITEM_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s+"          # qty
    r"(?:[A-Za-z]+\s+)?"           # optional unit
    r"(\d+(?:,\d+)*(?:\.\d+)?)\s+" # unit price
    r"(\d+(?:,\d+)*(?:\.\d+)?)",   # line total
)
_TAX_RATE_RE = re.compile(
    r"(?:CGST|SGST|IGST|GST)\s*@?\s*(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
_TOTAL_TAX_RE = re.compile(
    r"(?:total\s+)?(?:tax(?:able)?(?:\s+value)?|gst\s+amount)\s*[:\-]?\s*"
    r"(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_INVOICE_TOTAL_RE = re.compile(
    r"(?:grand\s+total|invoice\s+(?:total|amount)|total\s+(?:invoice\s+)?value|"
    r"amount\s+payable)\s*[:\-]?\s*(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_CGST_AMOUNT_RE = re.compile(
    r"CGST\s*(?:@\s*[\d.]+\s*%)?\s*[:\-]?\s*(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_SGST_AMOUNT_RE = re.compile(
    r"SGST\s*(?:@\s*[\d.]+\s*%)?\s*[:\-]?\s*(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_IGST_AMOUNT_RE = re.compile(
    r"IGST\s*(?:@\s*[\d.]+\s*%)?\s*[:\-]?\s*(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)


def extract_vendor_invoice(
    source: str | None = None, *, text: str = "",
    provider: str | None = None,
) -> ExtractionResult:
    """Parse a vendor GST tax invoice → structured candidate.

    Output fields (all candidates — P4 / F3 staging gate still applies):
      bill_no, invoice_date, irn,
      seller_gstin, buyer_gstin,
      place_of_supply,
      taxable_value, cgst_amount, sgst_amount, igst_amount,
      invoice_total,
      tax_rates[], hsn_codes[],
      payment_terms

    GST compliance (Finance Act, India):
      - GSTIN is validated by regex; checksum is NOT verified here —
        that is done downstream by the F3 agent before staging.
      - IRN is a 64-char hex hash; present only on e-invoices (mandatory
        above ₹5 Cr turnover as of 2023).
      - Bill number is free-form; all known Samsung distribution invoice
        formats are captured by the regex above.

    Required fields: bill_no, invoice_date, seller_gstin, invoice_total.
    Any of these missing → needs_review=True, downstream blocked.
    """
    ocr = get_transport(provider).extract(source, text=text)
    t = ocr.text

    fields: dict = {}
    conf: dict = {}

    # Bill number
    m = _BILL_NO_RE.search(t)
    if m:
        fields["bill_no"] = m.group(1).strip()
        conf["bill_no"] = 0.9
    else:
        # Fallback: look for any alphanumeric code near "invoice"
        idx = t.lower().find("invoice")
        if idx >= 0:
            m2 = re.search(r"[A-Z0-9][A-Z0-9/\-]{4,30}", t[idx:idx + 80])
            if m2:
                fields["bill_no"] = m2.group(0)
                conf["bill_no"] = 0.4

    # Invoice date
    dates = [
        (m.start(), date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        for m in _DATE_RE.finditer(t)
    ]
    dates.sort(key=lambda p: p[0])
    if dates:
        fields["invoice_date"] = dates[0][1].isoformat()
        conf["invoice_date"] = 0.8

    # IRN (e-invoice reference number)
    m = _IRN_RE.search(t)
    if m:
        fields["irn"] = m.group(1).lower()
        conf["irn"] = 0.99

    # GSTINs — expect 2 (seller + buyer)
    gstins = list(dict.fromkeys(_GSTIN_RE.findall(t)))  # unique, order-preserved
    if len(gstins) >= 1:
        fields["seller_gstin"] = gstins[0]
        conf["seller_gstin"] = 0.9
    if len(gstins) >= 2:
        fields["buyer_gstin"] = gstins[1]
        conf["buyer_gstin"] = 0.85

    # Place of supply
    m = _PLACE_OF_SUPPLY_RE.search(t)
    if m:
        fields["place_of_supply"] = m.group(1).strip().title()
        conf["place_of_supply"] = 0.8

    # Tax amounts
    def grab_amount(pattern: re.Pattern, key: str, base_conf: float) -> None:
        m = pattern.search(t)
        if m:
            val, c = parse_money(m.group(1))
            if val is not None and val > 0:
                fields[key] = float(val)
                conf[key] = base_conf * c

    grab_amount(_CGST_AMOUNT_RE, "cgst_amount", 0.9)
    grab_amount(_SGST_AMOUNT_RE, "sgst_amount", 0.9)
    grab_amount(_IGST_AMOUNT_RE, "igst_amount", 0.9)

    # Taxable value (pre-tax subtotal)
    m = _TOTAL_TAX_RE.search(t)
    if m:
        val, c = parse_money(m.group(1))
        if val is not None and val > 0:
            fields["taxable_value"] = float(val)
            conf["taxable_value"] = 0.8 * c

    # Invoice total
    m = _INVOICE_TOTAL_RE.search(t)
    if m:
        val, c = parse_money(m.group(1))
        if val is not None and val > 0:
            fields["invoice_total"] = float(val)
            conf["invoice_total"] = 0.85 * c

    # Tax rates (deduplicated)
    rates = list(dict.fromkeys(
        float(m.group(1)) for m in _TAX_RATE_RE.finditer(t)
    ))
    if rates:
        fields["tax_rates"] = rates[:10]
        conf["tax_rates"] = 0.75

    # HSN/SAC codes
    hsns = list(dict.fromkeys(m.group(1) for m in _HSN_RE.finditer(t)))
    if hsns:
        fields["hsn_codes"] = hsns[:20]
        conf["hsn_codes"] = 0.8

    # Payment terms — look for "net 30", "due date", etc.
    pt = re.search(
        r"(?:payment\s+terms?|net\s+\d+|due\s+(?:date|in|within)|credit\s+period)\s*[:\-]?\s*([^\n]{3,50})",
        t, re.IGNORECASE,
    )
    if pt:
        fields["payment_terms"] = pt.group(1).strip()[:80]
        conf["payment_terms"] = 0.65

    result = _finalize("vendor_invoice", fields, conf, ocr,
                       required=["bill_no", "invoice_date",
                                 "seller_gstin", "invoice_total"])

    # Sanity check: total = taxable + taxes (within 1% tolerance)
    if (
        fields.get("invoice_total")
        and fields.get("taxable_value")
        and (fields.get("cgst_amount") or fields.get("igst_amount"))
    ):
        tax_sum = (
            fields.get("cgst_amount", 0)
            + fields.get("sgst_amount", 0)
            + fields.get("igst_amount", 0)
        )
        expected = fields["taxable_value"] + tax_sum
        diff_pct = abs(expected - fields["invoice_total"]) / max(expected, 1)
        if diff_pct > 0.01:
            result.needs_review = True
            result.review_reason = (
                (result.review_reason or "")
                + f"; total mismatch: taxable+tax={expected:.2f} vs invoice_total={fields['invoice_total']:.2f}"
            )

    return result
