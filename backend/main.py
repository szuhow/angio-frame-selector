"""
Keyselector – FastAPI backend for coronary angiography frame selection.
Handles DICOM (multi-frame) and PNG folder sequences.

Datasets are now first-class entities: the admin curates them from a
server-side library directory (`LIBRARY_DIR`), assigns each dataset to
one or more users, and every patient/frame/annotation endpoint is
scoped to the caller's assigned dataset(s).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import cv2
import numpy as np
import pydicom
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from auth import (
    create_jwt,
    decode_jwt,
    generate_api_key,
    hash_password,
    verify_password,
)
from metadata import (
    build_metadata_fields,
    default_config as _metadata_default_config,
    extract_from_dicom,
    extract_from_sidecar_json,
    find_sidecar,
    normalize_config as _metadata_normalize_config,
    normalize_tag as _metadata_normalize_tag,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent / "data"))
LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", "/app/library"))
EXPORTS_DIR = Path(os.environ.get("EXPORTS_DIR", "/app/exports"))
DB_PATH = Path(
    os.environ.get(
        "DB_PATH",
        Path(__file__).resolve().parent.parent / "keyselector.db",
    )
)
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024 * 1024)))  # 10 GiB default
JPEG_QUALITY = 85
ALLOWED_DATA_EXTENSIONS = {".dcm", ".png"}  # matched case-insensitively

app = FastAPI(title="Keyselector API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_bearer = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# SQLite database
# ---------------------------------------------------------------------------
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return _local.conn


@contextmanager
def _db():
    """Yield a cursor that auto-commits on success."""
    conn = _get_conn()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _table_columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [r["name"] for r in cur.fetchall()]


def _migrate_annotations_to_dataset_id() -> None:
    """
    Ensure `annotations` and `skipped` tables have a `dataset_id` column
    and UNIQUE(dataset_id, patient_id, sequence_id, user_id).

    Uses the rename-and-copy pattern because SQLite cannot modify
    existing UNIQUE constraints in place.
    """
    with _db() as cur:
        for table, payload_cols in [
            ("annotations", ("frame_index INTEGER NOT NULL", "comment TEXT NOT NULL DEFAULT ''")),
            ("skipped", ("reason TEXT NOT NULL DEFAULT ''",)),
        ]:
            cur.execute(
                f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'"
            )
            row = cur.fetchone()
            if not row:
                continue
            ddl = row["sql"].replace(" ", "")
            has_dataset_id = "dataset_id" in ddl
            has_new_unique = "UNIQUE(dataset_id,patient_id,sequence_id,user_id)" in ddl
            if has_dataset_id and has_new_unique:
                continue

            # Build new DDL
            payload_ddl = ",\n                        ".join(payload_cols)
            cur.execute(f"ALTER TABLE {table} RENAME TO _{table}_old")
            cur.execute(
                f"""
                CREATE TABLE {table} (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset_id  INTEGER REFERENCES datasets(id) ON DELETE CASCADE,
                    patient_id  TEXT NOT NULL,
                    sequence_id TEXT NOT NULL,
                    {payload_ddl},
                    user_id     TEXT NOT NULL DEFAULT 'default',
                    created_at  TEXT NOT NULL,
                    UNIQUE(dataset_id, patient_id, sequence_id, user_id)
                )
                """
            )

            old_cols = _table_columns(cur, f"_{table}_old")
            if "dataset_id" in old_cols:
                # Preserve existing dataset_id values
                common = [c for c in old_cols if c != "id"]
                cols_csv = ", ".join(common)
                cur.execute(
                    f"INSERT INTO {table} ({cols_csv}) SELECT {cols_csv} FROM _{table}_old"
                )
            else:
                # Copy with NULL dataset_id (will be backfilled in _backfill_legacy_dataset)
                common = [c for c in old_cols if c != "id"]
                cols_csv = ", ".join(common)
                insert_cols = "dataset_id, " + cols_csv
                select_cols = "NULL, " + cols_csv
                cur.execute(
                    f"INSERT INTO {table} ({insert_cols}) SELECT {select_cols} FROM _{table}_old"
                )
            cur.execute(f"DROP TABLE _{table}_old")


def _backfill_legacy_dataset() -> None:
    """
    If any rows in `annotations`/`skipped` have NULL dataset_id, create a
    `legacy` dataset (rooted at the old DATA_DIR), assign it to the default
    admin, and backfill the column.
    """
    with _db() as cur:
        cur.execute("SELECT COUNT(*) c FROM annotations WHERE dataset_id IS NULL")
        legacy_ann = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM skipped WHERE dataset_id IS NULL")
        legacy_skip = cur.fetchone()["c"]
        if legacy_ann == 0 and legacy_skip == 0:
            return

        cur.execute("SELECT id FROM datasets WHERE slug='legacy'")
        row = cur.fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if row:
            legacy_id = row["id"]
        else:
            # Find an admin to record as creator (default: first admin or id=1)
            cur.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
            adm = cur.fetchone()
            creator = adm["id"] if adm else None
            cur.execute(
                "INSERT INTO datasets (name, slug, root_path, created_by, created_at) VALUES (?,?,?,?,?)",
                ("Legacy", "legacy", str(DATA_DIR), creator, now),
            )
            legacy_id = cur.lastrowid
            if creator is not None:
                cur.execute(
                    "INSERT OR IGNORE INTO user_datasets (user_id, dataset_id, assigned_at) VALUES (?,?,?)",
                    (creator, legacy_id, now),
                )

        cur.execute("UPDATE annotations SET dataset_id=? WHERE dataset_id IS NULL", (legacy_id,))
        cur.execute("UPDATE skipped SET dataset_id=? WHERE dataset_id IS NULL", (legacy_id,))


def _init_db() -> None:
    """Create tables if they don't exist and migrate legacy JSON data."""
    with _db() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'annotator' CHECK(role IN ('admin','annotator','viewer')),
                api_token     TEXT UNIQUE,
                created_at    TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS datasets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                slug        TEXT NOT NULL UNIQUE,
                root_path   TEXT NOT NULL,
                created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at  TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_datasets (
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                dataset_id   INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
                assigned_at  TEXT NOT NULL,
                PRIMARY KEY (user_id, dataset_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS annotations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id  INTEGER REFERENCES datasets(id) ON DELETE CASCADE,
                patient_id  TEXT NOT NULL,
                sequence_id TEXT NOT NULL,
                frame_index INTEGER NOT NULL,
                comment     TEXT NOT NULL DEFAULT '',
                user_id     TEXT NOT NULL DEFAULT 'default',
                created_at  TEXT NOT NULL,
                UNIQUE(dataset_id, patient_id, sequence_id, user_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS skipped (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id  INTEGER REFERENCES datasets(id) ON DELETE CASCADE,
                patient_id  TEXT NOT NULL,
                sequence_id TEXT NOT NULL,
                reason      TEXT NOT NULL DEFAULT '',
                user_id     TEXT NOT NULL DEFAULT 'default',
                created_at  TEXT NOT NULL,
                UNIQUE(dataset_id, patient_id, sequence_id, user_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS export_versions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id   INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
                version      TEXT NOT NULL,
                format       TEXT NOT NULL CHECK(format IN ('annotations-json','coco')),
                created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at   TEXT NOT NULL,
                file_path    TEXT NOT NULL,
                sha256       TEXT NOT NULL,
                size_bytes   INTEGER NOT NULL,
                counts_json  TEXT NOT NULL DEFAULT '{}',
                notes        TEXT NOT NULL DEFAULT '',
                UNIQUE(dataset_id, version, format)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS crai_sync_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                version_id       INTEGER NOT NULL,
                target_endpoint  TEXT NOT NULL,
                experiment_name  TEXT NOT NULL,
                source_row_ref   TEXT NOT NULL,
                http_status      INTEGER,
                response_excerpt TEXT,
                attempt_count    INTEGER NOT NULL,
                duration_ms      INTEGER NOT NULL,
                outcome          TEXT NOT NULL,
                created_at       TEXT NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ix_crai_sync_log_version "
            "ON crai_sync_log(version_id)"
        )

    # Migrate legacy annotation tables (pre dataset_id) to the new schema
    _migrate_annotations_to_dataset_id()

    # Migrate from legacy annotations.json if present and DB is empty
    legacy_json = Path(__file__).resolve().parent.parent / "annotations.json"
    if legacy_json.exists():
        try:
            data = json.loads(legacy_json.read_text(encoding="utf-8"))
        except Exception:
            data = None
        if data is not None:
            with _db() as cur:
                cur.execute("SELECT COUNT(*) c FROM annotations")
                if cur.fetchone()["c"] == 0:
                    for a in data.get("annotations", []):
                        cur.execute(
                            "INSERT OR IGNORE INTO annotations (dataset_id, patient_id, sequence_id, frame_index, comment, user_id, created_at) VALUES (NULL,?,?,?,?,?,?)",
                            (
                                a["patient_id"],
                                a["sequence_id"],
                                a["frame_index"],
                                a.get("comment", ""),
                                a.get("user_id", "default"),
                                a.get("timestamp", datetime.now(timezone.utc).isoformat()),
                            ),
                        )
                    for s in data.get("skipped", []):
                        cur.execute(
                            "INSERT OR IGNORE INTO skipped (dataset_id, patient_id, sequence_id, reason, user_id, created_at) VALUES (NULL,?,?,?,?,?)",
                            (
                                s["patient_id"],
                                s["sequence_id"],
                                s.get("reason", ""),
                                s.get("user_id", "default"),
                                s.get("timestamp", datetime.now(timezone.utc).isoformat()),
                            ),
                        )

    # Create default admin if no users exist
    with _db() as cur:
        cur.execute("SELECT COUNT(*) c FROM users")
        if cur.fetchone()["c"] == 0:
            now = datetime.now(timezone.utc).isoformat()
            cur.execute(
                "INSERT INTO users (username, password_hash, role, api_token, created_at) VALUES (?,?,?,?,?)",
                ("admin", hash_password("admin"), "admin", generate_api_key(), now),
            )
            print("=" * 60)
            print("  Domyślne konto administratora: admin / admin")
            print("  ZMIEŃ HASŁO PO PIERWSZYM ZALOGOWANIU!")
            print("=" * 60)

    # Backfill legacy dataset_id on annotations/skipped
    _backfill_legacy_dataset()

    # Ensure library / exports roots exist
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


@app.on_event("startup")
def _on_startup():
    _init_db()


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Validate JWT or API key and return user dict."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Brak tokenu autoryzacji",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    if token.startswith("ks_"):
        with _db() as cur:
            cur.execute("SELECT id, username, role FROM users WHERE api_token=?", (token,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Nieprawidłowy klucz API",
                )
            return {"sub": row["id"], "username": row["username"], "role": row["role"]}
    return decode_jwt(token)


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Wymagane uprawnienia administratora",
        )
    return user


async def require_annotator(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] not in ("admin", "annotator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Brak uprawnień do adnotacji",
        )
    return user


# ---------------------------------------------------------------------------
# Dataset helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _make_slug(name: str) -> str:
    base = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return base or "dataset"


def _unique_slug(name: str) -> str:
    slug = _make_slug(name)
    candidate = slug
    with _db() as cur:
        i = 2
        while True:
            cur.execute("SELECT 1 FROM datasets WHERE slug=?", (candidate,))
            if not cur.fetchone():
                return candidate
            candidate = f"{slug}-{i}"
            i += 1


def _visible_dataset_ids(user: dict) -> list[int]:
    """Admins see all datasets; others see only those explicitly assigned."""
    with _db() as cur:
        if user["role"] == "admin":
            cur.execute("SELECT id FROM datasets ORDER BY id")
            return [r["id"] for r in cur.fetchall()]
        cur.execute(
            "SELECT dataset_id FROM user_datasets WHERE user_id=? ORDER BY dataset_id",
            (user["sub"],),
        )
        return [r["dataset_id"] for r in cur.fetchall()]


def _get_dataset(dataset_id: int) -> Optional[sqlite3.Row]:
    with _db() as cur:
        cur.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,))
        return cur.fetchone()


def _resolve_dataset(user: dict, dataset_id: Optional[int]) -> sqlite3.Row:
    """
    Resolve a dataset the caller is allowed to access.

    - Admin: any dataset.
    - Others: must be in user_datasets.
    - Returns 404 if dataset does not exist or caller has no access,
      so existence of other datasets is not leaked.
    """
    if dataset_id is None:
        raise HTTPException(status_code=400, detail="dataset_id jest wymagany")
    ds = _get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail="Dataset nie znaleziony")
    if user["role"] != "admin":
        if dataset_id not in _visible_dataset_ids(user):
            raise HTTPException(status_code=404, detail="Dataset nie znaleziony")
    return ds


def _dataset_contains_data(root: Path) -> bool:
    if not root.exists() or not root.is_dir():
        return False
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in ALLOWED_DATA_EXTENSIONS:
            return True
    return False


def _safe_join_library(source_path: str) -> Path:
    """Return LIBRARY_DIR / source_path, rejecting traversal and absolute paths."""
    if not source_path or source_path.startswith("/") or ".." in Path(source_path).parts:
        raise HTTPException(status_code=400, detail="Nieprawidłowa ścieżka źródłowa")
    target = (LIBRARY_DIR / source_path).resolve()
    library_root = LIBRARY_DIR.resolve()
    try:
        target.relative_to(library_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ścieżka poza katalogiem biblioteki")
    return target


def _extract_zip_safely(zip_path: Path, target_dir: Path) -> None:
    """
    Validate and extract a ZIP archive into target_dir.
    - Rejects zip-slip (entries escaping target).
    - Rejects entries with disallowed extensions (directories allowed).
    - Raises HTTPException(400) on any violation.
    """
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # First pass: validate
            for info in zf.infolist():
                name = info.filename
                if not name or name.startswith("/") or "\x00" in name:
                    raise HTTPException(status_code=400, detail=f"Nieprawidłowa nazwa wpisu: {name!r}")
                resolved = (target_dir / name).resolve()
                try:
                    resolved.relative_to(target_dir)
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Zip-slip wykryty: {name!r}")
                if not info.is_dir():
                    ext = Path(name).suffix.lower()
                    if ext not in ALLOWED_DATA_EXTENSIONS:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Nieobsługiwane rozszerzenie: {name!r}",
                        )
            # Second pass: extract
            zf.extractall(target_dir)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Uszkodzony plik ZIP")


# ---------------------------------------------------------------------------
# DICOM frame extraction
# ---------------------------------------------------------------------------

def _apply_windowing(pixel_array: np.ndarray, ds: pydicom.Dataset) -> np.ndarray:
    """Apply DICOM windowing (level/width) to get a viewable 8-bit image."""
    wc = getattr(ds, "WindowCenter", None)
    ww = getattr(ds, "WindowWidth", None)

    if wc is not None and ww is not None:
        wc = float(wc[0]) if isinstance(wc, pydicom.multival.MultiValue) else float(wc)
        ww = float(ww[0]) if isinstance(ww, pydicom.multival.MultiValue) else float(ww)
    else:
        wc = float(np.mean(pixel_array))
        ww = float(np.std(pixel_array) * 4) or 1.0

    img_min = wc - ww / 2
    img_max = wc + ww / 2
    img = np.clip(pixel_array, img_min, img_max)
    img = ((img - img_min) / (img_max - img_min) * 255).astype(np.uint8)
    return img


def _dicom_frame_to_jpeg(ds: pydicom.Dataset, frame_idx: int) -> bytes:
    pixel_array = ds.pixel_array
    if pixel_array.ndim == 3:
        if frame_idx >= pixel_array.shape[0]:
            raise IndexError(f"Frame {frame_idx} out of range (0-{pixel_array.shape[0]-1})")
        frame = pixel_array[frame_idx]
    elif pixel_array.ndim == 2:
        if frame_idx != 0:
            raise IndexError("Single-frame DICOM – only frame 0 exists")
        frame = pixel_array
    else:
        raise ValueError("Unexpected pixel_array dimensions")

    frame = frame.astype(np.float64)
    img = _apply_windowing(frame, ds)

    pi = getattr(ds, "PhotometricInterpretation", "MONOCHROME2")
    if pi == "MONOCHROME1":
        img = 255 - img

    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return buf.tobytes()


def _dicom_frame_count(ds: pydicom.Dataset) -> int:
    nf = getattr(ds, "NumberOfFrames", None)
    if nf is not None:
        return int(nf)
    if ds.pixel_array.ndim == 3:
        return ds.pixel_array.shape[0]
    return 1


# ---------------------------------------------------------------------------
# Dataset scanning
# ---------------------------------------------------------------------------

_DICOM_CACHE: dict[str, pydicom.Dataset] = {}
# Path index keyed by (dataset_id, patient_id, sequence_id)
_PATH_INDEX: dict[tuple[int, str, str], Path] = {}
# resolved_path_str -> raw extracted metadata {tag: {"value", "vr"}}
_METADATA_CACHE: dict[str, dict[str, dict[str, Any]]] = {}


def _invalidate_metadata_cache() -> None:
    """Drop cached metadata; call when config changes or files move."""
    _METADATA_CACHE.clear()


# ---------------------------------------------------------------------------
# Metadata display configuration (admin-editable whitelist of tags)
# ---------------------------------------------------------------------------

_SETTINGS_KEY_METADATA_FIELDS = "metadata_display_fields"


def _get_metadata_config() -> list[dict[str, str]]:
    """Return the current ordered field list, falling back to defaults."""
    with _db() as cur:
        cur.execute(
            "SELECT value FROM app_settings WHERE key=?",
            (_SETTINGS_KEY_METADATA_FIELDS,),
        )
        row = cur.fetchone()
    if row is None:
        return _metadata_default_config()
    try:
        raw = json.loads(row["value"])
    except Exception:
        return _metadata_default_config()
    return _metadata_normalize_config(raw if isinstance(raw, list) else [])


def _set_metadata_config(fields: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Persist a new field list; returns the normalized stored value."""
    normalized = _metadata_normalize_config(fields)
    with _db() as cur:
        cur.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_SETTINGS_KEY_METADATA_FIELDS, json.dumps(normalized)),
        )
    _invalidate_metadata_cache()
    return normalized


def _extract_sequence_metadata(
    path: Path, tags: list[str]
) -> dict[str, dict[str, Any]]:
    """Return raw `{tag: {"value", "vr"}}` for a resolved sequence path."""
    if not tags:
        return {}
    key = str(path)
    cached = _METADATA_CACHE.get(key)
    if cached is not None:
        return {t: cached[t] for t in tags if t in cached}

    raw: dict[str, dict[str, Any]] = {}
    try:
        if path.is_dir():
            sidecar = find_sidecar(path)
            if sidecar is not None:
                raw = extract_from_sidecar_json(sidecar, tags)
        elif path.is_file() and path.suffix.lower() == ".dcm":
            ds = _get_dicom(path)
            raw = extract_from_dicom(ds, tags)
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Metadata extraction failed for %s: %s", path, exc
        )
        raw = {}
    _METADATA_CACHE[key] = raw
    return raw


def _get_dicom(path: Path) -> pydicom.Dataset:
    key = str(path)
    if key not in _DICOM_CACHE:
        _DICOM_CACHE[key] = pydicom.dcmread(str(path))
    return _DICOM_CACHE[key]


def _natural_sort_key(s: str):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


def _dicom_tag_str(ds: pydicom.Dataset, tag: tuple[int, int]) -> Optional[str]:
    """Return a DICOM tag's value as a stripped string, or None."""
    try:
        raw = ds.get(tag, None)
        if raw is None:
            return None
        val = raw.value if hasattr(raw, "value") else raw
        if val is None:
            return None
        s = str(val).strip()
        return s or None
    except Exception:
        return None


def _dicom_patient_name(ds: pydicom.Dataset) -> Optional[str]:
    """Return the DICOM PatientName (0010,0010) as a display string, or None."""
    return _dicom_tag_str(ds, (0x0010, 0x0010))


def _dicom_patient_id(ds: pydicom.Dataset) -> Optional[str]:
    """Return the DICOM PatientID (0010,0020) used as the grouping key, or None."""
    return _dicom_tag_str(ds, (0x0010, 0x0020))


def _sidecar_tag_str(png_dir: Path, tag_key: str) -> Optional[str]:
    """Return a tag value from the PNG sidecar JSON (tag_key e.g. '00100010')."""
    try:
        sidecar = find_sidecar(png_dir)
        if sidecar is None:
            return None
        tags = extract_from_sidecar_json(sidecar, [tag_key])
        entry = tags.get(tag_key)
        if not entry:
            return None
        val = entry.get("value")
        if val is None:
            return None
        s = str(val).strip()
        return s or None
    except Exception:
        return None


def _sidecar_patient_name(png_dir: Path) -> Optional[str]:
    """Return PatientName from a DICOM-JSON sidecar next to a PNG sequence."""
    return _sidecar_tag_str(png_dir, "00100010")


def _sidecar_patient_id(png_dir: Path) -> Optional[str]:
    """Return PatientID from a DICOM-JSON sidecar next to a PNG sequence."""
    return _sidecar_tag_str(png_dir, "00100020")


def _annotated_keys(dataset_id: int) -> set[tuple[str, str]]:
    with _db() as cur:
        cur.execute(
            "SELECT patient_id, sequence_id FROM annotations WHERE dataset_id=?",
            (dataset_id,),
        )
        return {(r["patient_id"], r["sequence_id"]) for r in cur.fetchall()}


def _skipped_keys(dataset_id: int) -> set[tuple[str, str]]:
    with _db() as cur:
        cur.execute(
            "SELECT patient_id, sequence_id FROM skipped WHERE dataset_id=?",
            (dataset_id,),
        )
        return {(r["patient_id"], r["sequence_id"]) for r in cur.fetchall()}


def scan_dataset(ds_row: sqlite3.Row) -> list[dict]:
    """
    Scan a single dataset's root directory and return its patient list.
    Populates _PATH_INDEX entries keyed by (dataset_id, patient_id, sequence_id).
    """
    dataset_id = ds_row["id"]
    root = Path(ds_row["root_path"])
    if not root.exists():
        return []

    patients: dict[str, dict[str, Any]] = {}
    annotated = _annotated_keys(dataset_id)
    skipped = _skipped_keys(dataset_id)
    config_tags = [f["tag"] for f in _get_metadata_config()]

    # Clear existing entries for this dataset
    for key in list(_PATH_INDEX.keys()):
        if key[0] == dataset_id:
            del _PATH_INDEX[key]

    # DICOM files
    for dcm_path in sorted(root.rglob("*"), key=lambda p: _natural_sort_key(str(p))):
        if dcm_path.name.startswith(".") or not dcm_path.is_file():
            continue
        if dcm_path.suffix.lower() != ".dcm":
            continue

        rel = dcm_path.relative_to(root)
        parts = rel.parts

        # Sequence id = path relative to the top-level folder (without .dcm).
        # Kept folder-relative so that URLs/DB keys remain stable and backward
        # compatible; patient grouping is driven by the DICOM tag below.
        if len(parts) == 1:
            seq_id = dcm_path.stem
        else:
            seq_id = (
                str(Path(*parts[1:])).rsplit(".", 1)[0]
                if len(parts) > 1
                else dcm_path.stem
            )

        # Folder-based fallback patient id (used when DICOM tag is missing).
        folder_pid = parts[0] if len(parts) > 1 else dcm_path.stem

        try:
            d = _get_dicom(dcm_path)
        except Exception:
            continue

        # Group by actual DICOM PatientID (0010,0020) so that all sequences
        # belonging to the same patient are aggregated into a single tree node
        # regardless of on-disk folder layout. Fall back to the folder name
        # when the tag is missing or empty.
        patient_id = _dicom_patient_id(d) or folder_pid

        if patient_id not in patients:
            patients[patient_id] = {
                "patient_id": patient_id,
                "display_name": None,
                "dataset_id": dataset_id,
                "sequences": [],
            }

        try:
            fc = _dicom_frame_count(d)
            seq_status = (
                "done"
                if (patient_id, seq_id) in annotated
                else ("skipped" if (patient_id, seq_id) in skipped else "todo")
            )
            has_meta = False
            if config_tags:
                try:
                    has_meta = bool(extract_from_dicom(d, config_tags))
                except Exception:
                    has_meta = False
            # Capture PatientName from the first DICOM seen for this patient.
            if patients[patient_id]["display_name"] is None:
                patients[patient_id]["display_name"] = _dicom_patient_name(d)
            patients[patient_id]["sequences"].append(
                {
                    "sequence_id": seq_id,
                    "type": "dicom",
                    "frame_count": fc,
                    "status": seq_status,
                    "has_metadata": has_meta,
                }
            )
            _PATH_INDEX[(dataset_id, patient_id, seq_id)] = dcm_path
        except Exception:
            pass

    # PNG folders
    png_dirs: set[Path] = set()
    for png_file in sorted(root.rglob("*"), key=lambda p: _natural_sort_key(str(p))):
        if (
            png_file.is_file()
            and png_file.suffix.lower() == ".png"
            and not png_file.name.startswith(".")
        ):
            png_dirs.add(png_file.parent)

    for png_dir in sorted(png_dirs, key=lambda p: _natural_sort_key(str(p))):
        rel = png_dir.relative_to(root)
        parts = rel.parts
        if len(parts) < 1:
            continue

        folder_pid = parts[0]
        seq_id = png_dir.name if len(parts) == 1 else str(Path(*parts[1:]))

        pngs = sorted(
            [
                f
                for f in png_dir.iterdir()
                if f.suffix.lower() == ".png" and not f.name.startswith(".")
            ],
            key=lambda p: _natural_sort_key(p.name),
        )
        if not pngs:
            continue

        # Group by sidecar PatientID when present; fall back to folder name.
        patient_id = _sidecar_patient_id(png_dir) or folder_pid

        if patient_id not in patients:
            patients[patient_id] = {
                "patient_id": patient_id,
                "display_name": None,
                "dataset_id": dataset_id,
                "sequences": [],
            }

        seq_status = (
            "done"
            if (patient_id, seq_id) in annotated
            else ("skipped" if (patient_id, seq_id) in skipped else "todo")
        )
        has_meta = False
        sidecar = find_sidecar(png_dir) if config_tags else None
        if config_tags and sidecar is not None:
            try:
                has_meta = bool(extract_from_sidecar_json(sidecar, config_tags))
            except Exception:
                has_meta = False
        # Capture PatientName from the first sidecar seen for this patient.
        if patients[patient_id]["display_name"] is None:
            patients[patient_id]["display_name"] = _sidecar_patient_name(png_dir)
        patients[patient_id]["sequences"].append(
            {
                "sequence_id": seq_id,
                "type": "png",
                "frame_count": len(pngs),
                "status": seq_status,
                "has_metadata": has_meta,
            }
        )
        _PATH_INDEX[(dataset_id, patient_id, seq_id)] = png_dir

    # Sort each patient's sequences by natural order of sequence_id so the
    # annotator sees them in a predictable grouping per patient.
    for p in patients.values():
        p["sequences"].sort(key=lambda s: _natural_sort_key(str(s["sequence_id"])))

    # Sort patients by display_name when available, falling back to patient_id.
    return sorted(
        patients.values(),
        key=lambda p: _natural_sort_key(p.get("display_name") or p["patient_id"]),
    )


def _scan_datasets_for_user(user: dict, dataset_id: Optional[int] = None) -> list[dict]:
    """Aggregate patient lists across the datasets visible to the user."""
    visible = _visible_dataset_ids(user)
    if dataset_id is not None:
        if dataset_id not in visible:
            return []
        visible = [dataset_id]
    out: list[dict] = []
    for did in visible:
        ds = _get_dataset(did)
        if ds is None:
            continue
        out.extend(scan_dataset(ds))
    return out


def _lookup_sequence_path(
    user: dict, dataset_id: int, patient_id: str, sequence_id: str
) -> Path:
    ds = _resolve_dataset(user, dataset_id)
    key = (ds["id"], patient_id, sequence_id)
    if key not in _PATH_INDEX:
        scan_dataset(ds)
    path = _PATH_INDEX.get(key)
    if path is None:
        raise HTTPException(status_code=404, detail="Sequence not found")
    return path


# ---------------------------------------------------------------------------
# API – auth
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "annotator"


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/login")
def login(body: LoginRequest):
    with _db() as cur:
        cur.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username=?",
            (body.username,),
        )
        row = cur.fetchone()
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nieprawidłowa nazwa użytkownika lub hasło",
        )
    token = create_jwt(row["id"], row["username"], row["role"])
    return {
        "token": token,
        "user": {"id": row["id"], "username": row["username"], "role": row["role"]},
    }


@app.get("/api/auth/me")
def get_me(user: dict = Depends(get_current_user)):
    return {"id": user["sub"], "username": user["username"], "role": user["role"]}


@app.post("/api/auth/change-password")
def change_password(body: PasswordChange, user: dict = Depends(get_current_user)):
    with _db() as cur:
        cur.execute("SELECT password_hash FROM users WHERE id=?", (user["sub"],))
        row = cur.fetchone()
        if not row or not verify_password(body.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Nieprawidłowe obecne hasło")
        cur.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (hash_password(body.new_password), user["sub"]),
        )
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API – admin user management
# ---------------------------------------------------------------------------

@app.get("/api/admin/users")
def list_users(user: dict = Depends(require_admin)):
    with _db() as cur:
        cur.execute(
            "SELECT id, username, role, api_token, created_at FROM users ORDER BY id"
        )
        return [dict(r) for r in cur.fetchall()]


@app.post("/api/admin/users")
def create_user(body: UserCreate, user: dict = Depends(require_admin)):
    if body.role not in ("admin", "annotator", "viewer"):
        raise HTTPException(
            status_code=400, detail="Nieprawidłowa rola: admin, annotator, viewer"
        )
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _db() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, role, api_token, created_at) VALUES (?,?,?,?,?)",
                (
                    body.username,
                    hash_password(body.password),
                    body.role,
                    generate_api_key(),
                    now,
                ),
            )
            new_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Użytkownik o tej nazwie już istnieje")
    return {"status": "ok", "user_id": new_id}


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, user: dict = Depends(require_admin)):
    if user_id == user["sub"]:
        raise HTTPException(status_code=400, detail="Nie można usunąć własnego konta")
    with _db() as cur:
        cur.execute("DELETE FROM users WHERE id=?", (user_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Użytkownik nie znaleziony")
    return {"status": "ok"}


@app.post("/api/admin/users/{user_id}/regenerate-token")
def regenerate_token(user_id: int, user: dict = Depends(require_admin)):
    new_token = generate_api_key()
    with _db() as cur:
        cur.execute("UPDATE users SET api_token=? WHERE id=?", (new_token, user_id))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Użytkownik nie znaleziony")
    return {"status": "ok", "api_token": new_token}


# ---------------------------------------------------------------------------
# API – admin dataset management
# ---------------------------------------------------------------------------

class DatasetOut(BaseModel):
    id: int
    name: str
    slug: str
    root_path: str
    created_by: Optional[int] = None
    created_at: str


class DatasetRegister(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    source_path: str = Field(min_length=1)


class DatasetAssignment(BaseModel):
    dataset_id: int


def _dataset_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "root_path": row["root_path"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


@app.get("/api/admin/datasets")
def admin_list_datasets(user: dict = Depends(require_admin)):
    with _db() as cur:
        cur.execute("SELECT * FROM datasets ORDER BY id")
        return [_dataset_to_dict(r) for r in cur.fetchall()]


@app.get("/api/admin/datasets/library")
def admin_list_library(user: dict = Depends(require_admin)):
    """List subdirectories of LIBRARY_DIR that are not yet registered."""
    with _db() as cur:
        cur.execute("SELECT root_path FROM datasets")
        registered = {Path(r["root_path"]).resolve() for r in cur.fetchall()}

    entries: list[dict] = []
    if LIBRARY_DIR.exists():
        for child in sorted(LIBRARY_DIR.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or child.name.startswith("."):
                continue
            entries.append(
                {
                    "name": child.name,
                    "source_path": child.name,
                    "has_data": _dataset_contains_data(child),
                    "registered": child.resolve() in registered,
                }
            )
    return {"library_root": str(LIBRARY_DIR), "entries": entries}


def _insert_dataset(name: str, root_path: Path, creator_id: int) -> dict:
    slug = _unique_slug(name)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _db() as cur:
            cur.execute(
                "INSERT INTO datasets (name, slug, root_path, created_by, created_at) VALUES (?,?,?,?,?)",
                (name, slug, str(root_path), creator_id, now),
            )
            new_id = cur.lastrowid
            cur.execute("SELECT * FROM datasets WHERE id=?", (new_id,))
            return _dataset_to_dict(cur.fetchone())
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Dataset o tej nazwie już istnieje")


@app.post("/api/admin/datasets", status_code=201)
async def admin_create_dataset(
    user: dict = Depends(require_admin),
    name: Optional[str] = Form(None),
    source_path: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    """
    Create a dataset either by registering an existing LIBRARY_DIR subdirectory
    (`source_path`) or by uploading a ZIP (`file`). `name` is required.
    """
    if not name:
        raise HTTPException(status_code=400, detail="Pole 'name' jest wymagane")
    if file is None and not source_path:
        raise HTTPException(status_code=400, detail="Wymagane: source_path lub file")
    if file is not None and source_path:
        raise HTTPException(
            status_code=400,
            detail="Podaj tylko jedno: source_path lub file",
        )

    # Register existing directory
    if source_path is not None:
        target = _safe_join_library(source_path)
        if not _dataset_contains_data(target):
            raise HTTPException(
                status_code=400,
                detail="Katalog nie zawiera plików DICOM/PNG",
            )
        return _insert_dataset(name, target, user["sub"])

    # Upload ZIP path
    assert file is not None
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Oczekiwany plik .zip")

    slug = _unique_slug(name)
    target_dir = (LIBRARY_DIR / slug).resolve()
    try:
        target_dir.relative_to(LIBRARY_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Ścieżka docelowa poza biblioteką")
    if target_dir.exists():
        raise HTTPException(status_code=409, detail="Katalog docelowy już istnieje")

    # Stream upload to tempfile (size-limited)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    total = 0
    try:
        try:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Archiwum przekracza limit {MAX_UPLOAD_BYTES} bajtów",
                    )
                tmp.write(chunk)
        finally:
            tmp.close()

        tmp_path = Path(tmp.name)
        # Extract (blocking IO) in threadpool
        try:
            await asyncio.to_thread(_extract_zip_safely, tmp_path, target_dir)
        except HTTPException:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            raise
        except Exception as exc:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"Błąd rozpakowywania: {exc}")

        if not _dataset_contains_data(target_dir):
            shutil.rmtree(target_dir, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail="Archiwum nie zawiera plików DICOM/PNG",
            )

        try:
            return _insert_dataset(name, target_dir, user["sub"])
        except HTTPException:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


@app.delete("/api/admin/datasets/{dataset_id}")
def admin_delete_dataset(dataset_id: int, user: dict = Depends(require_admin)):
    ds = _get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail="Dataset nie znaleziony")

    with _db() as cur:
        cur.execute("DELETE FROM datasets WHERE id=?", (dataset_id,))

    # NOTE: We intentionally do NOT delete the source directory on disk.
    # `LIBRARY_DIR` is frequently a bind-mount of a host path
    # (`HOST_LIBRARY_DIR`) containing user-curated DICOMs — blowing it away
    # would be destructive and surprising. Removing the dataset only
    # unregisters it; the folder remains available for re-registration. An
    # admin who truly wants to delete source files should do so on the host.

    # Remove exports directory for this dataset (app-managed artifacts only)
    export_root = (EXPORTS_DIR / ds["slug"]).resolve()
    try:
        export_root.relative_to(EXPORTS_DIR.resolve())
        if export_root.exists():
            shutil.rmtree(export_root, ignore_errors=True)
    except ValueError:
        pass

    # Drop any cached index entries for this dataset
    for key in list(_PATH_INDEX.keys()):
        if key[0] == dataset_id:
            del _PATH_INDEX[key]
    _invalidate_metadata_cache()

    return {"status": "ok"}


@app.get("/api/admin/datasets/{dataset_id}/users")
def admin_list_dataset_users(dataset_id: int, user: dict = Depends(require_admin)):
    if _get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail="Dataset nie znaleziony")
    with _db() as cur:
        cur.execute(
            """
            SELECT u.id, u.username, u.role, ud.assigned_at
            FROM user_datasets ud JOIN users u ON u.id = ud.user_id
            WHERE ud.dataset_id=?
            ORDER BY u.id
            """,
            (dataset_id,),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# API – admin user↔dataset assignments
# ---------------------------------------------------------------------------

@app.get("/api/admin/users/{user_id}/datasets")
def admin_list_user_datasets(user_id: int, user: dict = Depends(require_admin)):
    with _db() as cur:
        cur.execute("SELECT 1 FROM users WHERE id=?", (user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Użytkownik nie znaleziony")
        cur.execute(
            """
            SELECT d.* FROM user_datasets ud
            JOIN datasets d ON d.id = ud.dataset_id
            WHERE ud.user_id=?
            ORDER BY d.id
            """,
            (user_id,),
        )
        return [_dataset_to_dict(r) for r in cur.fetchall()]


@app.post("/api/admin/users/{user_id}/datasets")
def admin_assign_dataset(
    user_id: int,
    body: DatasetAssignment,
    user: dict = Depends(require_admin),
):
    with _db() as cur:
        cur.execute("SELECT 1 FROM users WHERE id=?", (user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Użytkownik nie znaleziony")
        if _get_dataset(body.dataset_id) is None:
            raise HTTPException(status_code=404, detail="Dataset nie znaleziony")
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(
            "INSERT OR IGNORE INTO user_datasets (user_id, dataset_id, assigned_at) VALUES (?,?,?)",
            (user_id, body.dataset_id, now),
        )
    return {"status": "ok"}


@app.delete("/api/admin/users/{user_id}/datasets/{dataset_id}")
def admin_unassign_dataset(
    user_id: int, dataset_id: int, user: dict = Depends(require_admin)
):
    with _db() as cur:
        cur.execute(
            "DELETE FROM user_datasets WHERE user_id=? AND dataset_id=?",
            (user_id, dataset_id),
        )
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API – datasets (user-facing)
# ---------------------------------------------------------------------------

@app.get("/api/datasets")
def list_datasets(user: dict = Depends(get_current_user)):
    ids = _visible_dataset_ids(user)
    if not ids:
        return []
    with _db() as cur:
        qmarks = ",".join("?" * len(ids))
        cur.execute(
            f"SELECT * FROM datasets WHERE id IN ({qmarks}) ORDER BY id", ids
        )
        return [_dataset_to_dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# API – patients / sequences
# ---------------------------------------------------------------------------

@app.get("/api/patients")
def list_patients(
    user: dict = Depends(get_current_user),
    dataset_id: Optional[int] = Query(None),
):
    if dataset_id is not None:
        # Visibility check
        if user["role"] != "admin" and dataset_id not in _visible_dataset_ids(user):
            return []
        ds = _get_dataset(dataset_id)
        if ds is None:
            return []
        return scan_dataset(ds)
    return _scan_datasets_for_user(user)


@app.get("/api/patients/{patient_id}/sequences/{sequence_id:path}/frames/{frame_idx}")
def get_frame(
    patient_id: str,
    sequence_id: str,
    frame_idx: int,
    dataset_id: int = Query(..., description="Dataset ID"),
    window_center: float | None = Query(None),
    window_width: float | None = Query(None),
    user: dict = Depends(get_current_user),
):
    resolved = _lookup_sequence_path(user, dataset_id, patient_id, sequence_id)

    if resolved.is_dir():
        pngs = sorted(
            [f for f in resolved.iterdir() if f.suffix.lower() == ".png"],
            key=lambda p: _natural_sort_key(p.name),
        )
        if frame_idx < 0 or frame_idx >= len(pngs):
            raise HTTPException(404, "Frame index out of range")
        img = cv2.imread(str(pngs[frame_idx]), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise HTTPException(500, "Failed to read PNG")
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return Response(content=buf.tobytes(), media_type="image/jpeg")

    try:
        ds = _get_dicom(resolved)
        if window_center is not None and window_width is not None:
            ds = ds.copy()
            ds.WindowCenter = window_center
            ds.WindowWidth = window_width
        jpeg_bytes = _dicom_frame_to_jpeg(ds, frame_idx)
        return Response(content=jpeg_bytes, media_type="image/jpeg")
    except IndexError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/patients/{patient_id}/sequences/{sequence_id:path}/frames_bulk")
def get_frames_bulk(
    patient_id: str,
    sequence_id: str,
    dataset_id: int = Query(..., description="Dataset ID"),
    user: dict = Depends(get_current_user),
):
    import base64

    resolved = _lookup_sequence_path(user, dataset_id, patient_id, sequence_id)

    if resolved.is_dir():
        pngs = sorted(
            [f for f in resolved.iterdir() if f.suffix.lower() == ".png"],
            key=lambda p: _natural_sort_key(p.name),
        )
        frames = []
        for p in pngs:
            img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
            if img is None:
                continue
            _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            frames.append(base64.b64encode(buf.tobytes()).decode("ascii"))
        return {"frames": frames, "count": len(frames)}

    ds = _get_dicom(resolved)
    fc = _dicom_frame_count(ds)
    frames = []
    for i in range(fc):
        try:
            jpeg_bytes = _dicom_frame_to_jpeg(ds, i)
            frames.append(base64.b64encode(jpeg_bytes).decode("ascii"))
        except Exception:
            pass
    return {"frames": frames, "count": len(frames)}


# ---------------------------------------------------------------------------
# API – annotations
# ---------------------------------------------------------------------------

class AnnotationCreate(BaseModel):
    dataset_id: int
    patient_id: str
    sequence_id: str
    frame_index: int
    comment: str = ""


class SkipCreate(BaseModel):
    dataset_id: int
    patient_id: str
    sequence_id: str
    reason: str = ""


@app.get("/api/annotations")
def list_annotations(
    user: dict = Depends(require_admin),
    dataset_id: Optional[int] = Query(None),
):
    with _db() as cur:
        if dataset_id is not None:
            cur.execute(
                "SELECT dataset_id, patient_id, sequence_id, frame_index, comment, user_id, created_at "
                "FROM annotations WHERE dataset_id=? ORDER BY created_at",
                (dataset_id,),
            )
            annotations = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT dataset_id, patient_id, sequence_id, reason, user_id, created_at "
                "FROM skipped WHERE dataset_id=? ORDER BY created_at",
                (dataset_id,),
            )
            skipped = [dict(r) for r in cur.fetchall()]
        else:
            cur.execute(
                "SELECT dataset_id, patient_id, sequence_id, frame_index, comment, user_id, created_at "
                "FROM annotations ORDER BY created_at"
            )
            annotations = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT dataset_id, patient_id, sequence_id, reason, user_id, created_at "
                "FROM skipped ORDER BY created_at"
            )
            skipped = [dict(r) for r in cur.fetchall()]
    return {"annotations": annotations, "skipped": skipped}


@app.get("/api/annotations/{patient_id}/{sequence_id:path}")
def get_annotation(
    patient_id: str,
    sequence_id: str,
    dataset_id: int = Query(..., description="Dataset ID"),
    user: dict = Depends(get_current_user),
):
    _resolve_dataset(user, dataset_id)
    with _db() as cur:
        cur.execute(
            "SELECT dataset_id, patient_id, sequence_id, frame_index, comment, user_id, created_at "
            "FROM annotations WHERE dataset_id=? AND patient_id=? AND sequence_id=? ORDER BY created_at",
            (dataset_id, patient_id, sequence_id),
        )
        annotations = [{"type": "annotation", **dict(r)} for r in cur.fetchall()]
        cur.execute(
            "SELECT dataset_id, patient_id, sequence_id, reason, user_id, created_at "
            "FROM skipped WHERE dataset_id=? AND patient_id=? AND sequence_id=? ORDER BY created_at",
            (dataset_id, patient_id, sequence_id),
        )
        skipped = [{"type": "skipped", **dict(r)} for r in cur.fetchall()]
    return {"annotations": annotations, "skipped": skipped}


@app.post("/api/annotations")
def create_annotation(body: AnnotationCreate, user: dict = Depends(require_annotator)):
    _resolve_dataset(user, body.dataset_id)
    # Additional check: the sequence must exist within the dataset
    _lookup_sequence_path(user, body.dataset_id, body.patient_id, body.sequence_id)
    now = datetime.now(timezone.utc).isoformat()
    with _db() as cur:
        cur.execute(
            "DELETE FROM skipped WHERE dataset_id=? AND patient_id=? AND sequence_id=? AND user_id=?",
            (body.dataset_id, body.patient_id, body.sequence_id, user["username"]),
        )
        cur.execute(
            """INSERT INTO annotations (dataset_id, patient_id, sequence_id, frame_index, comment, user_id, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(dataset_id, patient_id, sequence_id, user_id)
               DO UPDATE SET frame_index=excluded.frame_index, comment=excluded.comment,
                             created_at=excluded.created_at""",
            (
                body.dataset_id,
                body.patient_id,
                body.sequence_id,
                body.frame_index,
                body.comment,
                user["username"],
                now,
            ),
        )
    return {"status": "ok", "frame_index": body.frame_index}


@app.post("/api/skip")
def skip_sequence(body: SkipCreate, user: dict = Depends(require_annotator)):
    _resolve_dataset(user, body.dataset_id)
    _lookup_sequence_path(user, body.dataset_id, body.patient_id, body.sequence_id)
    now = datetime.now(timezone.utc).isoformat()
    with _db() as cur:
        cur.execute(
            "DELETE FROM annotations WHERE dataset_id=? AND patient_id=? AND sequence_id=? AND user_id=?",
            (body.dataset_id, body.patient_id, body.sequence_id, user["username"]),
        )
        cur.execute(
            """INSERT INTO skipped (dataset_id, patient_id, sequence_id, reason, user_id, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(dataset_id, patient_id, sequence_id, user_id)
               DO UPDATE SET reason=excluded.reason, created_at=excluded.created_at""",
            (
                body.dataset_id,
                body.patient_id,
                body.sequence_id,
                body.reason,
                user["username"],
                now,
            ),
        )
    return {"status": "ok"}


@app.delete("/api/annotations/{patient_id}/{sequence_id:path}")
def delete_annotation(
    patient_id: str,
    sequence_id: str,
    dataset_id: int = Query(..., description="Dataset ID"),
    user: dict = Depends(require_admin),
):
    _resolve_dataset(user, dataset_id)
    with _db() as cur:
        cur.execute(
            "DELETE FROM annotations WHERE dataset_id=? AND patient_id=? AND sequence_id=?",
            (dataset_id, patient_id, sequence_id),
        )
        cur.execute(
            "DELETE FROM skipped WHERE dataset_id=? AND patient_id=? AND sequence_id=?",
            (dataset_id, patient_id, sequence_id),
        )
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API – stats
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def stats(
    user: dict = Depends(get_current_user),
    dataset_id: Optional[int] = Query(None),
):
    patients = (
        _scan_datasets_for_user(user, dataset_id)
        if dataset_id is not None
        else _scan_datasets_for_user(user)
    )
    total_seq = sum(len(p["sequences"]) for p in patients)
    done_seq = sum(1 for p in patients for s in p["sequences"] if s["status"] == "done")
    skipped_seq = sum(
        1 for p in patients for s in p["sequences"] if s["status"] == "skipped"
    )
    return {
        "total_patients": len(patients),
        "total_sequences": total_seq,
        "done": done_seq,
        "skipped": skipped_seq,
        "remaining": total_seq - done_seq - skipped_seq,
    }


# ---------------------------------------------------------------------------
# Export payload builders (deterministic / canonical)
# ---------------------------------------------------------------------------

def _build_annotations_payload_for_datasets(dataset_ids: list[int]) -> dict:
    out_patients: list[dict] = []
    totals = {"annotated": 0, "skipped": 0}

    for did in sorted(dataset_ids):
        ds = _get_dataset(did)
        if ds is None:
            continue
        patients = scan_dataset(ds)
        with _db() as cur:
            cur.execute(
                "SELECT patient_id, sequence_id, frame_index, comment, user_id, created_at "
                "FROM annotations WHERE dataset_id=? ORDER BY patient_id, sequence_id, user_id",
                (did,),
            )
            anns = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT patient_id, sequence_id, reason, user_id, created_at "
                "FROM skipped WHERE dataset_id=? ORDER BY patient_id, sequence_id, user_id",
                (did,),
            )
            skips = [dict(r) for r in cur.fetchall()]

        ann_map: dict[tuple[str, str], list[dict]] = {}
        for a in anns:
            ann_map.setdefault((a["patient_id"], a["sequence_id"]), []).append(a)
        skip_map: dict[tuple[str, str], list[dict]] = {}
        for s in skips:
            skip_map.setdefault((s["patient_id"], s["sequence_id"]), []).append(s)

        for p in patients:
            patient_entry = {
                "dataset_id": did,
                "dataset_slug": ds["slug"],
                "patient_id": p["patient_id"],
                "sequences": [],
            }
            for seq in p["sequences"]:
                key = (p["patient_id"], seq["sequence_id"])
                entry = {
                    "sequence_id": seq["sequence_id"],
                    "frame_count": seq["frame_count"],
                    "status": seq["status"],
                }
                if key in ann_map:
                    entry["annotations"] = [
                        {
                            "informative_frame": a["frame_index"],
                            "comment": a["comment"],
                            "annotated_by": a["user_id"],
                            "annotated_at": a["created_at"],
                        }
                        for a in ann_map[key]
                    ]
                if key in skip_map:
                    entry["skips"] = [
                        {
                            "reason": s["reason"],
                            "skipped_by": s["user_id"],
                            "skipped_at": s["created_at"],
                        }
                        for s in skip_map[key]
                    ]
                patient_entry["sequences"].append(entry)
            out_patients.append(patient_entry)

        totals["annotated"] += len(anns)
        totals["skipped"] += len(skips)

    return {
        "patients": out_patients,
        "summary": {
            "total_patients": len(out_patients),
            "total_sequences": sum(len(p["sequences"]) for p in out_patients),
            "annotated": totals["annotated"],
            "skipped": totals["skipped"],
        },
    }


def _build_coco_payload_for_datasets(dataset_ids: list[int]) -> dict:
    categories = [
        {"id": 1, "name": "key_frame", "supercategory": "frame_classification"},
        {"id": 2, "name": "skipped", "supercategory": "frame_classification"},
    ]

    images: list[dict] = []
    coco_annotations: list[dict] = []
    img_id = 0
    ann_id = 0

    for did in sorted(dataset_ids):
        ds = _get_dataset(did)
        if ds is None:
            continue
        patients = scan_dataset(ds)
        with _db() as cur:
            cur.execute(
                "SELECT patient_id, sequence_id, frame_index, comment, user_id, created_at "
                "FROM annotations WHERE dataset_id=? ORDER BY patient_id, sequence_id, user_id",
                (did,),
            )
            anns = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT patient_id, sequence_id, reason, user_id, created_at "
                "FROM skipped WHERE dataset_id=? ORDER BY patient_id, sequence_id, user_id",
                (did,),
            )
            skips = [dict(r) for r in cur.fetchall()]

        ann_map: dict[tuple[str, str], list[dict]] = {}
        for a in anns:
            ann_map.setdefault((a["patient_id"], a["sequence_id"]), []).append(a)
        skip_map: dict[tuple[str, str], list[dict]] = {}
        for s in skips:
            skip_map.setdefault((s["patient_id"], s["sequence_id"]), []).append(s)

        for p in patients:
            for seq in p["sequences"]:
                key = (p["patient_id"], seq["sequence_id"])
                img_id += 1
                images.append(
                    {
                        "id": img_id,
                        "file_name": f"{ds['slug']}/{p['patient_id']}/{seq['sequence_id']}",
                        "dataset_id": did,
                        "dataset_slug": ds["slug"],
                        "patient_id": p["patient_id"],
                        "sequence_id": seq["sequence_id"],
                        "frame_count": seq["frame_count"],
                        "sequence_type": seq["type"],
                        "status": seq["status"],
                    }
                )
                if key in ann_map:
                    for a in ann_map[key]:
                        ann_id += 1
                        coco_annotations.append(
                            {
                                "id": ann_id,
                                "image_id": img_id,
                                "category_id": 1,
                                "attributes": {
                                    "frame_index": a["frame_index"],
                                    "comment": a["comment"],
                                    "annotated_by": a["user_id"],
                                    "annotated_at": a["created_at"],
                                },
                            }
                        )
                if key in skip_map:
                    for s in skip_map[key]:
                        ann_id += 1
                        coco_annotations.append(
                            {
                                "id": ann_id,
                                "image_id": img_id,
                                "category_id": 2,
                                "attributes": {
                                    "reason": s["reason"],
                                    "skipped_by": s["user_id"],
                                    "skipped_at": s["created_at"],
                                },
                            }
                        )

    return {
        "info": {
            "description": "Coronary angiography key frame selection",
            "version": "1.0",
        },
        "images": images,
        "annotations": coco_annotations,
        "categories": categories,
    }


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# API – live exports (unversioned)
# ---------------------------------------------------------------------------

@app.get("/api/export/annotations")
def export_annotations(
    user: dict = Depends(get_current_user),
    dataset_id: Optional[int] = Query(None),
):
    visible = _visible_dataset_ids(user)
    ids = [dataset_id] if dataset_id is not None and dataset_id in visible else visible
    payload = _build_annotations_payload_for_datasets(ids)
    payload["exported_at"] = datetime.now(timezone.utc).isoformat()
    return payload


@app.get("/api/export/coco")
def export_coco(
    user: dict = Depends(get_current_user),
    dataset_id: Optional[int] = Query(None),
):
    visible = _visible_dataset_ids(user)
    ids = [dataset_id] if dataset_id is not None and dataset_id in visible else visible
    payload = _build_coco_payload_for_datasets(ids)
    payload["info"]["date_created"] = datetime.now(timezone.utc).isoformat()
    payload["info"]["exported_by"] = user["username"]
    return payload


# ---------------------------------------------------------------------------
# API – versioned exports
# ---------------------------------------------------------------------------

_ALLOWED_EXPORT_FORMATS = {"annotations-json", "coco"}
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")


class ExportVersionCreate(BaseModel):
    dataset_id: int
    version: str = Field(min_length=1, max_length=64)
    format: str
    notes: str = ""


class ExportVersionOut(BaseModel):
    id: int
    dataset_id: int
    version: str
    format: str
    created_by: Optional[int]
    created_at: str
    sha256: str
    size_bytes: int
    counts: dict
    notes: str


def _export_row_to_dict(row: sqlite3.Row) -> dict:
    try:
        counts = json.loads(row["counts_json"] or "{}")
    except Exception:
        counts = {}
    return {
        "id": row["id"],
        "dataset_id": row["dataset_id"],
        "version": row["version"],
        "format": row["format"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "sha256": row["sha256"],
        "size_bytes": row["size_bytes"],
        "counts": counts,
        "notes": row["notes"],
    }


@app.post("/api/export/versions", status_code=201)
async def create_export_version(
    body: ExportVersionCreate, user: dict = Depends(require_admin)
):
    if body.format not in _ALLOWED_EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail="Nieprawidłowy format")
    if not _VERSION_RE.match(body.version):
        raise HTTPException(status_code=400, detail="Nieprawidłowa wersja")

    ds = _get_dataset(body.dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail="Dataset nie znaleziony")

    # Duplicate check
    with _db() as cur:
        cur.execute(
            "SELECT 1 FROM export_versions WHERE dataset_id=? AND version=? AND format=?",
            (body.dataset_id, body.version, body.format),
        )
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="Wersja już istnieje")

    # Build payload
    if body.format == "annotations-json":
        payload = _build_annotations_payload_for_datasets([body.dataset_id])
    else:
        payload = _build_coco_payload_for_datasets([body.dataset_id])

    canonical = _canonical_json(payload)
    sha = hashlib.sha256(canonical).hexdigest()

    target_dir = (EXPORTS_DIR / ds["slug"] / body.version).resolve()
    try:
        target_dir.relative_to(EXPORTS_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Nieprawidłowa ścieżka docelowa")
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{body.format}.json"
    file_path.write_bytes(canonical)

    counts = {
        "patients": len(payload.get("patients", []))
        if body.format == "annotations-json"
        else len({i.get("patient_id") for i in payload.get("images", [])}),
        "images": len(payload.get("images", [])) if body.format == "coco" else None,
        "annotations": payload.get("summary", {}).get("annotated")
        if body.format == "annotations-json"
        else sum(1 for a in payload.get("annotations", []) if a.get("category_id") == 1),
        "skipped": payload.get("summary", {}).get("skipped")
        if body.format == "annotations-json"
        else sum(1 for a in payload.get("annotations", []) if a.get("category_id") == 2),
    }
    counts = {k: v for k, v in counts.items() if v is not None}

    now = datetime.now(timezone.utc).isoformat()
    try:
        with _db() as cur:
            cur.execute(
                """
                INSERT INTO export_versions
                    (dataset_id, version, format, created_by, created_at,
                     file_path, sha256, size_bytes, counts_json, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    body.dataset_id,
                    body.version,
                    body.format,
                    user["sub"],
                    now,
                    str(file_path),
                    sha,
                    len(canonical),
                    json.dumps(counts, sort_keys=True),
                    body.notes,
                ),
            )
            new_id = cur.lastrowid
            cur.execute("SELECT * FROM export_versions WHERE id=?", (new_id,))
            result = _export_row_to_dict(cur.fetchone())
    except sqlite3.IntegrityError:
        # Race on unique constraint
        try:
            file_path.unlink()
        except Exception:
            pass
        raise HTTPException(status_code=409, detail="Wersja już istnieje")

    # Schedule outbound sync to crai-collector (fire-and-forget).
    try:
        from crai_sync import crai_sync_config, sync_export_version
        config, reason = crai_sync_config()
        if config is not None:
            asyncio.create_task(sync_export_version(new_id))
            result["sync_triggered"] = True
            result["sync_error"] = None
        else:
            result["sync_triggered"] = False
            result["sync_error"] = reason
    except Exception as exc:  # never let sync scheduling break the export
        result["sync_triggered"] = False
        result["sync_error"] = f"sync scheduling failed: {exc}"
    return result


@app.get("/api/export/versions")
def list_export_versions(
    user: dict = Depends(get_current_user),
    dataset_id: Optional[int] = Query(None),
):
    visible = _visible_dataset_ids(user)
    if not visible:
        return []
    if dataset_id is not None:
        if dataset_id not in visible:
            return []
        ids = [dataset_id]
    else:
        ids = visible
    with _db() as cur:
        qmarks = ",".join("?" * len(ids))
        cur.execute(
            f"SELECT * FROM export_versions WHERE dataset_id IN ({qmarks}) "
            "ORDER BY created_at DESC, id DESC",
            ids,
        )
        return [_export_row_to_dict(r) for r in cur.fetchall()]


@app.get("/api/export/versions/{version_id}/download")
def download_export_version(
    version_id: int, user: dict = Depends(get_current_user)
):
    with _db() as cur:
        cur.execute("SELECT * FROM export_versions WHERE id=?", (version_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Wersja nie znaleziona")
    # Visibility
    if user["role"] != "admin" and row["dataset_id"] not in _visible_dataset_ids(user):
        raise HTTPException(status_code=404, detail="Wersja nie znaleziona")

    file_path = Path(row["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=410, detail="Plik eksportu został usunięty")

    return FileResponse(
        path=str(file_path),
        media_type="application/json",
        filename=f"{row['format']}-{row['version']}.json",
        headers={"ETag": row["sha256"]},
    )


@app.delete("/api/export/versions/{version_id}")
def delete_export_version(version_id: int, user: dict = Depends(require_admin)):
    with _db() as cur:
        cur.execute("SELECT * FROM export_versions WHERE id=?", (version_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Wersja nie znaleziona")
        cur.execute("DELETE FROM export_versions WHERE id=?", (version_id,))
    file_path = Path(row["file_path"])
    try:
        if file_path.exists():
            file_path.unlink()
        # Try to remove version dir if empty
        parent = file_path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception:
        pass
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API – crai-collector sync (admin)
# ---------------------------------------------------------------------------


@app.post("/api/export/versions/{version_id}/sync")
async def sync_export_version_endpoint(
    version_id: int,
    dry_run: bool = Query(False),
    user: dict = Depends(require_admin),
):
    """Re-run outbound replication of a specific export version."""
    from crai_sync import sync_export_version

    with _db() as cur:
        cur.execute("SELECT id FROM export_versions WHERE id=?", (version_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Wersja nie znaleziona")
    summary = await sync_export_version(version_id, dry_run=dry_run)
    return summary.to_dict()


@app.get("/api/export/versions/{version_id}/sync/log")
def get_export_version_sync_log(
    version_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_admin),
):
    """Paginated crai_sync_log rows for a given export version."""
    with _db() as cur:
        cur.execute("SELECT id FROM export_versions WHERE id=?", (version_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Wersja nie znaleziona")
        cur.execute(
            """
            SELECT id, version_id, target_endpoint, experiment_name, source_row_ref,
                   http_status, response_excerpt, attempt_count, duration_ms,
                   outcome, created_at
            FROM crai_sync_log
            WHERE version_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (version_id, limit, offset),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# API – metadata display configuration & extraction
# ---------------------------------------------------------------------------

class MetadataFieldConfig(BaseModel):
    tag: str
    label: Optional[str] = None


class MetadataConfigUpdate(BaseModel):
    fields: list[MetadataFieldConfig]


@app.get("/api/metadata/config")
def get_metadata_config(user: dict = Depends(get_current_user)):
    """Return the ordered list of fields to display. Any authenticated user can read it."""
    return {"fields": _get_metadata_config()}


@app.put("/api/metadata/config")
def update_metadata_config(
    body: MetadataConfigUpdate, user: dict = Depends(require_admin)
):
    """Replace the displayed-field list. Admin only."""
    fields = [f.model_dump() for f in body.fields]
    stored = _set_metadata_config(fields)
    return {"fields": stored}


@app.get(
    "/api/patients/{patient_id}/sequences/{sequence_id:path}/metadata"
)
def get_sequence_metadata(
    patient_id: str,
    sequence_id: str,
    dataset_id: int = Query(..., description="Dataset ID"),
    user: dict = Depends(get_current_user),
):
    """Return the configured metadata fields extracted from the sequence."""
    resolved = _lookup_sequence_path(user, dataset_id, patient_id, sequence_id)
    config = _get_metadata_config()
    tags = [f["tag"] for f in config]
    raw = _extract_sequence_metadata(resolved, tags)
    return {"fields": build_metadata_fields(raw, config)}
