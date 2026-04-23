"""
Outbound replication of finalized `export_versions` to `crai-collector`.

This module treats `crai-collector` strictly as an external HTTP contract:
- `POST /api/key-frame-selector/`  ← each annotation row
- `POST /api/artifact-classifier/` ← each skip whose reason matches the artifact set
- `POST /api/dicom-parameters/`    ← once per unique sequence_id in the export

It reads the immutable exported JSON snapshot from disk (not the live DB) so
re-syncs replay the captured state of the version. Per-row results are recorded
in the `crai_sync_log` SQLite table. Sync is gated by env vars and completely
disabled by default.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

DEFAULT_ARTIFACT_REASONS: list[str] = ["Artefakt", "Artifact", "Brak kontrastu"]

# Exposed so tests can speed up retry timing via monkeypatch.
RETRY_BACKOFFS_S: tuple[float, ...] = (0.5, 1.0, 2.0)

# Tests can inject an `httpx.MockTransport` here; production leaves it None.
_TRANSPORT_OVERRIDE: Optional[httpx.AsyncBaseTransport] = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class CraiSyncConfig:
    base_url: str
    username: str
    password: str
    timeout_s: float
    max_concurrency: int
    artifact_reasons: frozenset[str]


def crai_sync_config() -> tuple[Optional[CraiSyncConfig], Optional[str]]:
    """Return `(config, None)` when active, or `(None, reason)` when disabled
    or misconfigured. Never raises."""
    if not _env_bool("CRAI_COLLECTOR_SYNC_ENABLED", False):
        return None, "CRAI_COLLECTOR_SYNC_ENABLED is not true"
    base = os.environ.get("CRAI_COLLECTOR_BASE_URL", "").strip()
    if not base:
        return None, "CRAI_COLLECTOR_BASE_URL is not set"
    user = os.environ.get("CRAI_COLLECTOR_USERNAME", "").strip()
    if not user:
        return None, "CRAI_COLLECTOR_USERNAME is not set"
    pw = os.environ.get("CRAI_COLLECTOR_PASSWORD", "")
    if not pw:
        return None, "CRAI_COLLECTOR_PASSWORD is not set"
    try:
        timeout_s = float(os.environ.get("CRAI_COLLECTOR_TIMEOUT_S", "10"))
    except ValueError:
        timeout_s = 10.0
    try:
        max_concurrency = max(1, int(os.environ.get("CRAI_COLLECTOR_MAX_CONCURRENCY", "4")))
    except ValueError:
        max_concurrency = 4
    reasons_env = os.environ.get("CRAI_COLLECTOR_ARTIFACT_REASONS", "").strip()
    reasons: set[str]
    if reasons_env:
        try:
            raw = json.loads(reasons_env)
            reasons = {str(r).strip() for r in raw if str(r).strip()}
        except Exception:
            reasons = set(DEFAULT_ARTIFACT_REASONS)
    else:
        reasons = set(DEFAULT_ARTIFACT_REASONS)
    return (
        CraiSyncConfig(
            base_url=base.rstrip("/"),
            username=user,
            password=pw,
            timeout_s=timeout_s,
            max_concurrency=max_concurrency,
            artifact_reasons=frozenset(reasons),
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _normalize_experiment_name(sequence_id: Any) -> Optional[str]:
    """Trim and validate. Returns None when empty after stripping."""
    if sequence_id is None:
        return None
    s = str(sequence_id).strip()
    if not s:
        return None
    return s


def _classify_skip_reason(reason: Any, artifact_reasons: frozenset[str]) -> bool:
    """True when the skip reason maps to `artifact: true`; False otherwise."""
    if reason is None:
        return False
    r = str(reason).strip()
    if not r:
        return False
    # Case-insensitive match.
    r_low = r.lower()
    return any(r_low == x.strip().lower() for x in artifact_reasons)


# ---------------------------------------------------------------------------
# SyncSummary data structures
# ---------------------------------------------------------------------------


@dataclass
class SyncSummary:
    version_id: int
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    by_endpoint: dict[str, dict[str, int]] = field(default_factory=dict)
    dry_run: bool = False
    planned: list[dict[str, Any]] = field(default_factory=list)
    sync_error: Optional[str] = None

    def record(self, endpoint: str, outcome: str) -> None:
        if outcome == "ok":
            self.ok += 1
        elif outcome == "failed":
            self.failed += 1
        else:
            self.skipped += 1
        by = self.by_endpoint.setdefault(
            endpoint, {"ok": 0, "failed": 0, "skipped": 0}
        )
        by[outcome] = by.get(outcome, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "dry_run": self.dry_run,
            "summary": {
                "ok": self.ok,
                "failed": self.failed,
                "skipped": self.skipped,
                "by_endpoint": self.by_endpoint,
            },
            "planned": self.planned if self.dry_run else [],
            "sync_error": self.sync_error,
        }


# ---------------------------------------------------------------------------
# Snapshot loading
# ---------------------------------------------------------------------------


@dataclass
class _VersionPayload:
    version_id: int
    dataset_id: int
    annotations: list[dict]      # {patient_id, sequence_id, frame_index}
    skips: list[dict]            # {patient_id, sequence_id, reason}
    sequences: list[dict]        # {patient_id, sequence_id, frame_count}


def _load_version_payload(version_id: int) -> Optional[_VersionPayload]:
    """Read the exported JSON file for `version_id` and flatten it."""
    import main  # lazy to avoid cycles

    with main._db() as cur:
        cur.execute("SELECT * FROM export_versions WHERE id=?", (version_id,))
        row = cur.fetchone()
    if row is None:
        return None
    file_path = Path(row["file_path"])
    if not file_path.exists():
        return None
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    anns: list[dict] = []
    skips: list[dict] = []
    sequences: list[dict] = []

    if row["format"] == "annotations-json":
        for p in data.get("patients", []):
            pid = p.get("patient_id", "")
            for seq in p.get("sequences", []):
                sid = seq.get("sequence_id", "")
                fc = seq.get("frame_count")
                sequences.append({"patient_id": pid, "sequence_id": sid, "frame_count": fc})
                for a in seq.get("annotations", []) or []:
                    anns.append({
                        "patient_id": pid,
                        "sequence_id": sid,
                        "frame_index": a.get("informative_frame"),
                    })
                for s in seq.get("skips", []) or []:
                    skips.append({
                        "patient_id": pid,
                        "sequence_id": sid,
                        "reason": s.get("reason", ""),
                    })
    elif row["format"] == "coco":
        img_by_id = {i.get("id"): i for i in data.get("images", [])}
        for img in data.get("images", []):
            sequences.append({
                "patient_id": img.get("patient_id", ""),
                "sequence_id": img.get("sequence_id", ""),
                "frame_count": img.get("frame_count"),
            })
        for a in data.get("annotations", []):
            img = img_by_id.get(a.get("image_id"))
            if img is None:
                continue
            attrs = a.get("attributes") or {}
            if a.get("category_id") == 1:
                anns.append({
                    "patient_id": img.get("patient_id", ""),
                    "sequence_id": img.get("sequence_id", ""),
                    "frame_index": attrs.get("frame_index"),
                })
            elif a.get("category_id") == 2:
                skips.append({
                    "patient_id": img.get("patient_id", ""),
                    "sequence_id": img.get("sequence_id", ""),
                    "reason": attrs.get("reason", ""),
                })

    # Deduplicate sequences (each unique (patient, sequence))
    seen: set[tuple[str, str]] = set()
    unique_sequences: list[dict] = []
    for s in sequences:
        key = (s["patient_id"], s["sequence_id"])
        if key in seen:
            continue
        seen.add(key)
        unique_sequences.append(s)

    return _VersionPayload(
        version_id=version_id,
        dataset_id=row["dataset_id"],
        annotations=anns,
        skips=skips,
        sequences=unique_sequences,
    )


def _extract_dicom_params(
    dataset_id: int, patient_id: str, sequence_id: str
) -> tuple[Optional[int], Optional[float]]:
    """Resolve the sequence path via `main._PATH_INDEX` and read DICOM tags.
    Returns (number_of_frames, projection_angle). Either may be None."""
    import main  # lazy

    ds_row = main._get_dataset(dataset_id)
    if ds_row is None:
        return None, None
    key = (dataset_id, patient_id, sequence_id)
    if key not in main._PATH_INDEX:
        try:
            main.scan_dataset(ds_row)
        except Exception:
            return None, None
    path = main._PATH_INDEX.get(key)
    if path is None:
        return None, None
    try:
        if path.is_file() and path.suffix.lower() == ".dcm":
            ds = main._get_dicom(path)
            nf_raw = getattr(ds, "NumberOfFrames", None)
            nf = int(nf_raw) if nf_raw is not None else None
            angle_raw = getattr(ds, "PositionerPrimaryAngle", None)
            try:
                angle = float(angle_raw) if angle_raw is not None else None
            except (TypeError, ValueError):
                angle = None
            return nf, angle
    except Exception:
        return None, None
    return None, None


# ---------------------------------------------------------------------------
# Log persistence
# ---------------------------------------------------------------------------


def _excerpt(text: Optional[str], limit: int = 500) -> str:
    if text is None:
        return ""
    s = str(text)
    return s[:limit]


def _write_log(
    *,
    version_id: int,
    target_endpoint: str,
    experiment_name: str,
    source_row_ref: str,
    http_status: Optional[int],
    response_excerpt: str,
    attempt_count: int,
    duration_ms: int,
    outcome: str,
) -> None:
    import main  # lazy

    now = datetime.now(timezone.utc).isoformat()
    with main._db() as cur:
        cur.execute(
            """
            INSERT INTO crai_sync_log
                (version_id, target_endpoint, experiment_name, source_row_ref,
                 http_status, response_excerpt, attempt_count, duration_ms,
                 outcome, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                target_endpoint,
                experiment_name,
                source_row_ref,
                http_status,
                response_excerpt,
                attempt_count,
                duration_ms,
                outcome,
                now,
            ),
        )


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


def _build_client(config: CraiSyncConfig) -> httpx.AsyncClient:
    kwargs: dict[str, Any] = {
        "base_url": config.base_url,
        "auth": (config.username, config.password),
        "timeout": config.timeout_s,
        "headers": {"Content-Type": "application/json"},
    }
    if _TRANSPORT_OVERRIDE is not None:
        kwargs["transport"] = _TRANSPORT_OVERRIDE
    return httpx.AsyncClient(**kwargs)


async def _post_with_retry(
    client: httpx.AsyncClient, path: str, payload: dict
) -> tuple[Optional[int], str, int, int]:
    """Return (http_status, response_excerpt, attempt_count, duration_ms).

    - Retries up to 3 attempts on network errors and HTTP 5xx.
    - No retry on 4xx; returns immediately.
    - Backoff between attempts per `RETRY_BACKOFFS_S[attempt-1]`.
    """
    start = time.monotonic()
    last_status: Optional[int] = None
    last_excerpt = ""
    attempt = 0
    max_attempts = 3
    while attempt < max_attempts:
        attempt += 1
        try:
            resp = await client.post(path, json=payload)
            last_status = resp.status_code
            last_excerpt = _excerpt(resp.text)
            if 200 <= resp.status_code < 300:
                break
            if 400 <= resp.status_code < 500:
                break  # no retry on 4xx
            # 5xx → retry
        except httpx.HTTPError as exc:
            last_status = None
            last_excerpt = _excerpt(f"{type(exc).__name__}: {exc}")
        if attempt < max_attempts:
            backoff = RETRY_BACKOFFS_S[attempt - 1] if attempt - 1 < len(RETRY_BACKOFFS_S) else RETRY_BACKOFFS_S[-1]
            await asyncio.sleep(backoff)
    duration_ms = int((time.monotonic() - start) * 1000)
    return last_status, last_excerpt, attempt, duration_ms


def _outcome_for(status_code: Optional[int]) -> str:
    if status_code is None:
        return "failed"
    if 200 <= status_code < 300:
        return "ok"
    return "failed"


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------


async def _sync_key_frame_selector(
    *,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    summary: SyncSummary,
    version_id: int,
    annotation: dict,
    row_index: int,
    dry_run: bool,
) -> None:
    endpoint = "key-frame-selector"
    exp = _normalize_experiment_name(annotation.get("sequence_id"))
    source_row_ref = f"annotations:{row_index}"
    if exp is None:
        if not dry_run:
            _write_log(
                version_id=version_id,
                target_endpoint=endpoint,
                experiment_name="",
                source_row_ref=source_row_ref,
                http_status=None,
                response_excerpt="invalid experiment_name",
                attempt_count=0,
                duration_ms=0,
                outcome="skipped",
            )
        summary.record(endpoint, "skipped")
        return
    frame_index = annotation.get("frame_index")
    if frame_index is None or not isinstance(frame_index, int) or frame_index < 0:
        if not dry_run:
            _write_log(
                version_id=version_id,
                target_endpoint=endpoint,
                experiment_name=exp,
                source_row_ref=source_row_ref,
                http_status=None,
                response_excerpt="invalid frame_index",
                attempt_count=0,
                duration_ms=0,
                outcome="skipped",
            )
        summary.record(endpoint, "skipped")
        return
    # GUI displays frames 1-based ("Klatka 1" = first frame) while the DB stores
    # 0-based `frame_index`. crai-collector's `frame_number` is aligned with the
    # label shown to the annotator, so we convert to 1-based on the wire.
    payload = {"experiment_name": exp, "frame_number": frame_index + 1}
    if dry_run:
        summary.planned.append(
            {"target_endpoint": endpoint, "experiment_name": exp, "payload": payload}
        )
        summary.record(endpoint, "skipped")
        return
    async with sem:
        status_code, excerpt, attempts, duration_ms = await _post_with_retry(
            client, "/api/key-frame-selector/", payload
        )
    outcome = _outcome_for(status_code)
    _write_log(
        version_id=version_id,
        target_endpoint=endpoint,
        experiment_name=exp,
        source_row_ref=source_row_ref,
        http_status=status_code,
        response_excerpt=excerpt,
        attempt_count=attempts,
        duration_ms=duration_ms,
        outcome=outcome,
    )
    summary.record(endpoint, outcome)


async def _sync_artifact_classifier(
    *,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    summary: SyncSummary,
    version_id: int,
    skip: dict,
    row_index: int,
    dry_run: bool,
    artifact_reasons: frozenset[str],
) -> None:
    endpoint = "artifact-classifier"
    exp = _normalize_experiment_name(skip.get("sequence_id"))
    source_row_ref = f"skipped:{row_index}"
    if exp is None:
        if not dry_run:
            _write_log(
                version_id=version_id,
                target_endpoint=endpoint,
                experiment_name="",
                source_row_ref=source_row_ref,
                http_status=None,
                response_excerpt="invalid experiment_name",
                attempt_count=0,
                duration_ms=0,
                outcome="skipped",
            )
        summary.record(endpoint, "skipped")
        return
    is_artifact = _classify_skip_reason(skip.get("reason"), artifact_reasons)
    if not is_artifact:
        # Non-artifact reason → no call; log as skipped for audit.
        if not dry_run:
            _write_log(
                version_id=version_id,
                target_endpoint=endpoint,
                experiment_name=exp,
                source_row_ref=source_row_ref,
                http_status=None,
                response_excerpt=f"reason not in artifact set: {skip.get('reason', '')[:100]}",
                attempt_count=0,
                duration_ms=0,
                outcome="skipped",
            )
        summary.record(endpoint, "skipped")
        return
    payload = {"experiment_name": exp, "artifact": True}
    if dry_run:
        summary.planned.append(
            {"target_endpoint": endpoint, "experiment_name": exp, "payload": payload}
        )
        summary.record(endpoint, "skipped")
        return
    async with sem:
        status_code, excerpt, attempts, duration_ms = await _post_with_retry(
            client, "/api/artifact-classifier/", payload
        )
    outcome = _outcome_for(status_code)
    _write_log(
        version_id=version_id,
        target_endpoint=endpoint,
        experiment_name=exp,
        source_row_ref=source_row_ref,
        http_status=status_code,
        response_excerpt=excerpt,
        attempt_count=attempts,
        duration_ms=duration_ms,
        outcome=outcome,
    )
    summary.record(endpoint, outcome)


async def _sync_dicom_parameters(
    *,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    summary: SyncSummary,
    version_id: int,
    dataset_id: int,
    sequence: dict,
    dry_run: bool,
) -> None:
    endpoint = "dicom-parameters"
    exp = _normalize_experiment_name(sequence.get("sequence_id"))
    source_row_ref = f"sequence:{sequence.get('patient_id', '')}/{sequence.get('sequence_id', '')}"
    if exp is None:
        if not dry_run:
            _write_log(
                version_id=version_id,
                target_endpoint=endpoint,
                experiment_name="",
                source_row_ref=source_row_ref,
                http_status=None,
                response_excerpt="invalid experiment_name",
                attempt_count=0,
                duration_ms=0,
                outcome="skipped",
            )
        summary.record(endpoint, "skipped")
        return
    # Prefer DICOM-derived values; fall back to exported frame_count.
    nf, angle = _extract_dicom_params(
        dataset_id, sequence.get("patient_id", ""), exp
    )
    if nf is None:
        fc = sequence.get("frame_count")
        if isinstance(fc, int) and fc > 0:
            nf = fc
    if not isinstance(nf, int) or nf <= 0:
        if not dry_run:
            _write_log(
                version_id=version_id,
                target_endpoint=endpoint,
                experiment_name=exp,
                source_row_ref=source_row_ref,
                http_status=None,
                response_excerpt="missing number_of_frames",
                attempt_count=0,
                duration_ms=0,
                outcome="skipped",
            )
        summary.record(endpoint, "skipped")
        return
    if angle is None:
        if not dry_run:
            _write_log(
                version_id=version_id,
                target_endpoint=endpoint,
                experiment_name=exp,
                source_row_ref=source_row_ref,
                http_status=None,
                response_excerpt="missing projection_angle",
                attempt_count=0,
                duration_ms=0,
                outcome="skipped",
            )
        summary.record(endpoint, "skipped")
        return
    payload = {
        "experiment_name": exp,
        "number_of_frames": nf,
        "projection_angle": angle,
    }
    if dry_run:
        summary.planned.append(
            {"target_endpoint": endpoint, "experiment_name": exp, "payload": payload}
        )
        summary.record(endpoint, "skipped")
        return
    async with sem:
        status_code, excerpt, attempts, duration_ms = await _post_with_retry(
            client, "/api/dicom-parameters/", payload
        )
    outcome = _outcome_for(status_code)
    _write_log(
        version_id=version_id,
        target_endpoint=endpoint,
        experiment_name=exp,
        source_row_ref=source_row_ref,
        http_status=status_code,
        response_excerpt=excerpt,
        attempt_count=attempts,
        duration_ms=duration_ms,
        outcome=outcome,
    )
    summary.record(endpoint, outcome)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def sync_export_version(
    version_id: int, *, dry_run: bool = False
) -> SyncSummary:
    """Replicate a single finalized export version to `crai-collector`.

    When `crai_sync_config()` returns `None` (disabled or misconfigured), a
    summary with `sync_error` set is returned and no HTTP call is made. When
    `dry_run=True`, planned calls are collected and neither HTTP calls nor
    `crai_sync_log` writes happen.
    """
    summary = SyncSummary(version_id=version_id, dry_run=dry_run)
    config, reason = crai_sync_config()
    if config is None:
        summary.sync_error = reason
        return summary

    payload = _load_version_payload(version_id)
    if payload is None:
        summary.sync_error = f"export version {version_id} not found or file missing"
        return summary

    sem = asyncio.Semaphore(config.max_concurrency)
    async with _build_client(config) as client:
        tasks: list[asyncio.Task] = []
        for idx, ann in enumerate(payload.annotations):
            tasks.append(asyncio.create_task(
                _sync_key_frame_selector(
                    client=client, sem=sem, summary=summary,
                    version_id=version_id, annotation=ann,
                    row_index=idx, dry_run=dry_run,
                )
            ))
        for idx, skip in enumerate(payload.skips):
            tasks.append(asyncio.create_task(
                _sync_artifact_classifier(
                    client=client, sem=sem, summary=summary,
                    version_id=version_id, skip=skip, row_index=idx,
                    dry_run=dry_run,
                    artifact_reasons=config.artifact_reasons,
                )
            ))
        for seq in payload.sequences:
            tasks.append(asyncio.create_task(
                _sync_dicom_parameters(
                    client=client, sem=sem, summary=summary,
                    version_id=version_id, dataset_id=payload.dataset_id,
                    sequence=seq, dry_run=dry_run,
                )
            ))
        if tasks:
            await asyncio.gather(*tasks)

    return summary
