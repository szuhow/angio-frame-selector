"""
Metadata extraction and display configuration for sequences.

Supports two sources:
- `pydicom.Dataset` for `.dcm` sequences.
- DICOM-JSON sidecar files (`{"00181510": {"vr": "DS", "Value": [-24.0]}}`)
  for PNG sequences.

Also provides a small, admin-configurable whitelist of tags to display in
the UI, with special formatting for the pair of positioner angles.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Optional

import pydicom

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tag normalization
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"^[0-9A-Fa-f]{8}$")


def normalize_tag(value: str) -> str:
    """
    Normalize a DICOM tag identifier to 8-character uppercase hex
    (group + element with no separators).

    Accepts forms like:
        "00181510"
        "0018,1510"
        "(0018,1510)"
        " 0018 1510 "

    Raises ValueError on invalid input.
    """
    if not isinstance(value, str):
        raise ValueError(f"Tag must be a string, got {type(value).__name__}")
    cleaned = re.sub(r"[\s(),]", "", value).upper()
    if not _TAG_RE.match(cleaned):
        raise ValueError(f"Invalid DICOM tag: {value!r}")
    return cleaned


# ---------------------------------------------------------------------------
# Keyword dictionary and default configuration
# ---------------------------------------------------------------------------

TAG_KEYWORDS: dict[str, str] = {
    "00080020": "StudyDate",
    "00080030": "StudyTime",
    "00080060": "Modality",
    "00080070": "Manufacturer",
    "00080080": "InstitutionName",
    "00081090": "ManufacturerModelName",
    "00180060": "KVP",
    "00181063": "FrameTime",
    "00181110": "DistanceSourceToDetector",
    "00181111": "DistanceSourceToPatient",
    "00181150": "ExposureTime",
    "00181151": "XRayTubeCurrent",
    "00181152": "Exposure",
    "00181500": "PositionerMotion",
    "00181508": "PositionerType",
    "00181510": "PositionerPrimaryAngle",
    "00181511": "PositionerSecondaryAngle",
    "00181520": "PositionerPrimaryAngleIncrement",
    "00181521": "PositionerSecondaryAngleIncrement",
    "00181600": "ShutterShape",
    "00280010": "Rows",
    "00280011": "Columns",
}

# Default labels shown when the admin config has no override.
_DEFAULT_LABELS: dict[str, str] = {
    "00181510": "Primary angle",
    "00181511": "Secondary angle",
    "00180060": "kVp",
    "00181151": "Tube current",
    "00181063": "Frame time",
    "00080020": "Study date",
}

DEFAULT_METADATA_FIELDS: list[dict[str, str]] = [
    {"tag": "00181510", "label": "Primary angle"},
    {"tag": "00181511", "label": "Secondary angle"},
    {"tag": "00180060", "label": "kVp"},
    {"tag": "00181151", "label": "Tube current"},
    {"tag": "00181063", "label": "Frame time"},
    {"tag": "00080020", "label": "Study date"},
]


def default_config() -> list[dict[str, str]]:
    """Return a fresh copy of the default field configuration."""
    return [dict(f) for f in DEFAULT_METADATA_FIELDS]


def normalize_config(
    fields: Iterable[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """
    Validate and normalize a user-supplied field list.
    Invalid entries are dropped and logged.
    """
    if not fields:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in fields:
        if not isinstance(entry, dict):
            log.warning("Dropping non-dict metadata field entry: %r", entry)
            continue
        raw_tag = entry.get("tag")
        if not isinstance(raw_tag, str):
            log.warning("Dropping metadata field without string tag: %r", entry)
            continue
        try:
            tag = normalize_tag(raw_tag)
        except ValueError as e:
            log.warning("Dropping invalid metadata tag %r: %s", raw_tag, e)
            continue
        if tag in seen:
            continue
        seen.add(tag)
        label = entry.get("label")
        item: dict[str, str] = {"tag": tag}
        if isinstance(label, str) and label.strip():
            item["label"] = label.strip()
        out.append(item)
    return out


def effective_label(tag: str, configured_label: Optional[str]) -> str:
    """Label to display: configured > built-in default > keyword > tag."""
    if configured_label:
        return configured_label
    if tag in _DEFAULT_LABELS:
        return _DEFAULT_LABELS[tag]
    if tag in TAG_KEYWORDS:
        return TAG_KEYWORDS[tag]
    return tag


# ---------------------------------------------------------------------------
# Extraction from pydicom.Dataset
# ---------------------------------------------------------------------------

def _tag_to_pair(tag: str) -> tuple[int, int]:
    return int(tag[:4], 16), int(tag[4:], 16)


def _unwrap_pydicom_value(raw: Any) -> Any:
    """Convert pydicom value to a JSON-serializable Python value."""
    try:
        import pydicom.multival as _mv
        from pydicom.valuerep import PersonName
    except Exception:  # pragma: no cover
        _mv = None
        PersonName = ()

    if raw is None:
        return None
    if _mv is not None and isinstance(raw, _mv.MultiValue):
        items = [_unwrap_pydicom_value(x) for x in raw]
        return items[0] if len(items) == 1 else items
    if isinstance(raw, (bytes, bytearray)):
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return None
    if isinstance(raw, PersonName):
        return str(raw)
    if isinstance(raw, (int, float, str, bool)):
        return raw
    # Numeric-like (e.g. pydicom DS/IS)
    try:
        return float(raw)
    except (TypeError, ValueError):
        try:
            return str(raw)
        except Exception:
            return None


def extract_from_dicom(
    ds: pydicom.Dataset, tags: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """
    Return `{tag: {"value": ..., "vr": "..."}}` for tags present in `ds`.
    Skips missing and unreadable tags silently.
    """
    out: dict[str, dict[str, Any]] = {}
    for tag in tags:
        try:
            g, e = _tag_to_pair(tag)
        except ValueError:
            continue
        try:
            if (g, e) not in ds:
                continue
            elem = ds[(g, e)]
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("Failed to access tag %s: %s", tag, exc)
            continue
        try:
            value = _unwrap_pydicom_value(elem.value)
            vr = getattr(elem, "VR", "") or ""
        except Exception as exc:  # pragma: no cover
            log.debug("Failed to unwrap tag %s: %s", tag, exc)
            continue
        if value is None:
            continue
        out[tag] = {"value": value, "vr": vr}
    return out


# ---------------------------------------------------------------------------
# Extraction from DICOM JSON sidecar
# ---------------------------------------------------------------------------

def _unwrap_json_value(vr: str, raw: Any) -> Any:
    """Interpret a DICOM JSON `Value` list."""
    if raw is None:
        return None
    if isinstance(raw, list):
        if not raw:
            return None
        items = [_unwrap_json_value(vr, x) for x in raw]
        items = [x for x in items if x is not None]
        if not items:
            return None
        return items[0] if len(items) == 1 else items
    # PN is an object like {"Alphabetic": "Doe^John"}
    if isinstance(raw, dict):
        for key in ("Alphabetic", "Ideographic", "Phonetic"):
            if key in raw:
                return raw[key]
        return None
    return raw


def _read_sidecar_json(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception as exc:
        log.warning("Failed to read sidecar JSON %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def extract_from_sidecar_json(
    path: Path, tags: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """
    Return `{tag: {"value": ..., "vr": "..."}}` from a DICOM-JSON sidecar.
    """
    data = _read_sidecar_json(path)
    if data is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for tag in tags:
        try:
            key = normalize_tag(tag)
        except ValueError:
            continue
        entry = data.get(key) or data.get(key.lower())
        if not isinstance(entry, dict):
            continue
        vr = str(entry.get("vr") or "")
        try:
            value = _unwrap_json_value(vr, entry.get("Value"))
        except Exception as exc:  # pragma: no cover
            log.debug("Failed to unwrap sidecar tag %s: %s", key, exc)
            continue
        if value is None:
            continue
        out[key] = {"value": value, "vr": vr}
    return out


def find_sidecar(png_dir: Path) -> Path | None:
    """
    Locate a DICOM-JSON sidecar for a PNG sequence directory.

    Search order:
    1. `<png_dir>/<png_dir.name>.json`  (current repo layout: folder `foo.dcm`
        contains `foo.dcm.json`).
    2. `<png_dir.parent>/<png_dir.name>.json`.
    3. `<png_dir>/metadata.json`.
    4. Any `<stem>.json` sibling (same base name, no extension swap ambiguity).
    """
    candidates: list[Path] = []
    if png_dir.is_dir():
        candidates.append(png_dir / f"{png_dir.name}.json")
        candidates.append(png_dir / "metadata.json")
    parent = png_dir.parent
    candidates.append(parent / f"{png_dir.name}.json")
    stem = png_dir.name.rsplit(".", 1)[0]
    if stem and stem != png_dir.name:
        candidates.append(parent / f"{stem}.json")
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------

_MAX_ARRAY_ITEMS = 5


def _fmt_number(v: float) -> str:
    if float(v).is_integer():
        return f"{int(v)}"
    return f"{v:g}"


def format_field(tag: str, value: Any, vr: str = "") -> str:
    """Return a human-readable string for a tag value."""
    # Angiographic angles
    if tag == "00181510" and isinstance(value, (int, float)):
        v = float(value)
        side = "RAO" if v < 0 else "LAO"
        return f"{side} {abs(v):.0f}°"
    if tag == "00181511" and isinstance(value, (int, float)):
        v = float(value)
        side = "CAU" if v < 0 else "CRA"
        return f"{side} {abs(v):.0f}°"

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _fmt_number(float(value))
    if isinstance(value, list):
        items = value[:_MAX_ARRAY_ITEMS]
        rendered = [
            _fmt_number(float(x)) if isinstance(x, (int, float)) and not isinstance(x, bool)
            else str(x)
            for x in items
        ]
        suffix = "…" if len(value) > _MAX_ARRAY_ITEMS else ""
        return ", ".join(rendered) + suffix
    return str(value)


# ---------------------------------------------------------------------------
# Building the public field list
# ---------------------------------------------------------------------------

def build_metadata_fields(
    raw: dict[str, dict[str, Any]],
    config: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """
    Assemble the ordered, UI-ready field list.

    - `raw` is the output of `extract_from_dicom` or `extract_from_sidecar_json`
      (keyed by normalized tag, values are `{"value", "vr"}`).
    - `config` is the ordered display configuration (already normalized).

    Tags that are not in `raw` are silently dropped.
    """
    out: list[dict[str, Any]] = []
    for entry in config:
        tag = entry["tag"]
        data = raw.get(tag)
        if data is None:
            continue
        value = data.get("value")
        vr = data.get("vr", "") or ""
        out.append(
            {
                "tag": tag,
                "vr": vr,
                "keyword": TAG_KEYWORDS.get(tag, ""),
                "label": effective_label(tag, entry.get("label")),
                "value": value,
                "display": format_field(tag, value, vr),
            }
        )
    return out
