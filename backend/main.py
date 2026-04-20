"""
Keyselector – FastAPI backend for coronary angiography frame selection.
Handles DICOM (multi-frame) and PNG folder sequences.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pydicom
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from auth import (
    create_jwt,
    decode_jwt,
    generate_api_key,
    hash_password,
    verify_password,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent / "data"))
DB_PATH = Path(
    os.environ.get(
        "DB_PATH",
        Path(__file__).resolve().parent.parent / "keyselector.db",
    )
)
JPEG_QUALITY = 85

app = FastAPI(title="Keyselector API", version="0.1.0")

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


def _init_db() -> None:
    """Create tables if they don't exist and migrate legacy JSON data."""
    with _db() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS annotations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id  TEXT NOT NULL,
                sequence_id TEXT NOT NULL,
                frame_index INTEGER NOT NULL,
                comment     TEXT NOT NULL DEFAULT '',
                user_id     TEXT NOT NULL DEFAULT 'default',
                created_at  TEXT NOT NULL,
                UNIQUE(patient_id, sequence_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS skipped (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id  TEXT NOT NULL,
                sequence_id TEXT NOT NULL,
                reason      TEXT NOT NULL DEFAULT '',
                user_id     TEXT NOT NULL DEFAULT 'default',
                created_at  TEXT NOT NULL,
                UNIQUE(patient_id, sequence_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'annotator' CHECK(role IN ('admin','annotator','viewer')),
                api_token     TEXT UNIQUE,
                created_at    TEXT NOT NULL
            )
        """)

    # Migrate from legacy annotations.json if present and DB is empty
    legacy = Path(__file__).resolve().parent.parent / "annotations.json"
    if legacy.exists():
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except Exception:
            return
        with _db() as cur:
            cur.execute("SELECT COUNT(*) c FROM annotations")
            if cur.fetchone()["c"] == 0:
                for a in data.get("annotations", []):
                    cur.execute(
                        "INSERT OR IGNORE INTO annotations (patient_id, sequence_id, frame_index, comment, user_id, created_at) VALUES (?,?,?,?,?,?)",
                        (a["patient_id"], a["sequence_id"], a["frame_index"], a.get("comment", ""), a.get("user_id", "default"), a.get("timestamp", datetime.now(timezone.utc).isoformat())),
                    )
                for s in data.get("skipped", []):
                    cur.execute(
                        "INSERT OR IGNORE INTO skipped (patient_id, sequence_id, reason, user_id, created_at) VALUES (?,?,?,?,?)",
                        (s["patient_id"], s["sequence_id"], s.get("reason", ""), s.get("user_id", "default"), s.get("timestamp", datetime.now(timezone.utc).isoformat())),
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


@app.on_event("startup")
def _on_startup():
    _init_db()


# ---------------------------------------------------------------------------
# Helpers – query annotations from DB
# ---------------------------------------------------------------------------

def _annotated_keys() -> set[tuple[str, str]]:
    with _db() as cur:
        cur.execute("SELECT patient_id, sequence_id FROM annotations")
        return {(r["patient_id"], r["sequence_id"]) for r in cur.fetchall()}


def _skipped_keys() -> set[tuple[str, str]]:
    with _db() as cur:
        cur.execute("SELECT patient_id, sequence_id FROM skipped")
        return {(r["patient_id"], r["sequence_id"]) for r in cur.fetchall()}


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
    # API key (starts with ks_)
    if token.startswith("ks_"):
        with _db() as cur:
            cur.execute("SELECT id, username, role FROM users WHERE api_token=?", (token,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nieprawidłowy klucz API")
            return {"sub": row["id"], "username": row["username"], "role": row["role"]}
    # JWT
    return decode_jwt(token)


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Wymagane uprawnienia administratora")
    return user


async def require_annotator(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] not in ("admin", "annotator"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Brak uprawnień do adnotacji")
    return user


# ---------------------------------------------------------------------------
# Helpers – DICOM frame extraction
# ---------------------------------------------------------------------------

def _apply_windowing(pixel_array: np.ndarray, ds: pydicom.Dataset) -> np.ndarray:
    """Apply DICOM windowing (level/width) to get a viewable 8-bit image."""
    # Try to get window center / width from the dataset
    wc = getattr(ds, "WindowCenter", None)
    ww = getattr(ds, "WindowWidth", None)

    if wc is not None and ww is not None:
        wc = float(wc[0]) if isinstance(wc, pydicom.multival.MultiValue) else float(wc)
        ww = float(ww[0]) if isinstance(ww, pydicom.multival.MultiValue) else float(ww)
    else:
        # Auto-window based on data range
        wc = float(np.mean(pixel_array))
        ww = float(np.std(pixel_array) * 4) or 1.0

    img_min = wc - ww / 2
    img_max = wc + ww / 2
    img = np.clip(pixel_array, img_min, img_max)
    img = ((img - img_min) / (img_max - img_min) * 255).astype(np.uint8)
    return img


def _dicom_frame_to_jpeg(ds: pydicom.Dataset, frame_idx: int) -> bytes:
    """Extract a single frame from a (multi-frame) DICOM and return JPEG bytes."""
    pixel_array = ds.pixel_array
    if pixel_array.ndim == 3:
        # Multi-frame: shape (frames, H, W) or (frames, H, W, 3)
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

    # Handle photometric interpretation
    pi = getattr(ds, "PhotometricInterpretation", "MONOCHROME2")
    if pi == "MONOCHROME1":
        img = 255 - img

    # Encode as JPEG
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
_PATH_INDEX: dict[tuple[str, str], Path] = {}


def _get_dicom(path: Path) -> pydicom.Dataset:
    key = str(path)
    if key not in _DICOM_CACHE:
        _DICOM_CACHE[key] = pydicom.dcmread(str(path))
    return _DICOM_CACHE[key]


def _natural_sort_key(s: str):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


def scan_data_dir() -> list[dict]:
    """
    Scan DATA_DIR recursively and return a list of patients with their sequences.
    Discovers .dcm/.DCM files and directories containing .png files at any depth.
    Patient ID is the first path component relative to DATA_DIR.
    Sequence ID is the remaining relative path (stem for DICOM, dir name for PNG).
    """
    global _PATH_INDEX

    if not DATA_DIR.exists():
        return []

    patients: dict[str, dict[str, Any]] = {}
    annotated = _annotated_keys()
    skipped = _skipped_keys()
    path_index: dict[tuple[str, str], Path] = {}

    # Discover DICOM files recursively
    for dcm_path in sorted(DATA_DIR.rglob("*"), key=lambda p: _natural_sort_key(str(p))):
        if dcm_path.name.startswith(".") or not dcm_path.is_file():
            continue
        if dcm_path.suffix.lower() != ".dcm":
            continue

        rel = dcm_path.relative_to(DATA_DIR)
        parts = rel.parts

        if len(parts) == 1:
            # Top-level DICOM: patient_id = stem, sequence_id = stem
            patient_id = dcm_path.stem
            seq_id = dcm_path.stem
        else:
            # Nested: patient_id = first dir, sequence_id = rest of path without extension
            patient_id = parts[0]
            seq_id = str(Path(*parts[1:])).rsplit(".", 1)[0] if len(parts) > 1 else dcm_path.stem

        if patient_id not in patients:
            patients[patient_id] = {"patient_id": patient_id, "sequences": []}

        try:
            ds = _get_dicom(dcm_path)
            fc = _dicom_frame_count(ds)
            seq_status = "done" if (patient_id, seq_id) in annotated else (
                "skipped" if (patient_id, seq_id) in skipped else "todo"
            )
            patients[patient_id]["sequences"].append({
                "sequence_id": seq_id,
                "type": "dicom",
                "frame_count": fc,
                "status": seq_status,
            })
            path_index[(patient_id, seq_id)] = dcm_path
        except Exception:
            pass

    # Discover PNG folders recursively — find directories that directly contain .png files
    png_dirs: set[Path] = set()
    for png_file in sorted(DATA_DIR.rglob("*"), key=lambda p: _natural_sort_key(str(p))):
        if png_file.is_file() and png_file.suffix.lower() == ".png" and not png_file.name.startswith("."):
            png_dirs.add(png_file.parent)

    for png_dir in sorted(png_dirs, key=lambda p: _natural_sort_key(str(p))):
        rel = png_dir.relative_to(DATA_DIR)
        parts = rel.parts

        if len(parts) < 1:
            continue

        patient_id = parts[0]
        if len(parts) == 1:
            seq_id = png_dir.name
        else:
            seq_id = str(Path(*parts[1:]))

        pngs = sorted(
            [f for f in png_dir.iterdir() if f.suffix.lower() == ".png" and not f.name.startswith(".")],
            key=lambda p: _natural_sort_key(p.name),
        )
        if not pngs:
            continue

        if patient_id not in patients:
            patients[patient_id] = {"patient_id": patient_id, "sequences": []}

        seq_status = "done" if (patient_id, seq_id) in annotated else (
            "skipped" if (patient_id, seq_id) in skipped else "todo"
        )
        patients[patient_id]["sequences"].append({
            "sequence_id": seq_id,
            "type": "png",
            "frame_count": len(pngs),
            "status": seq_status,
        })
        path_index[(patient_id, seq_id)] = png_dir

    _PATH_INDEX = path_index
    return sorted(patients.values(), key=lambda p: _natural_sort_key(p["patient_id"]))


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
        cur.execute("SELECT id, username, role, api_token, created_at FROM users ORDER BY id")
        return [dict(r) for r in cur.fetchall()]


@app.post("/api/admin/users")
def create_user(body: UserCreate, user: dict = Depends(require_admin)):
    if body.role not in ("admin", "annotator", "viewer"):
        raise HTTPException(status_code=400, detail="Nieprawidłowa rola: admin, annotator, viewer")
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _db() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, role, api_token, created_at) VALUES (?,?,?,?,?)",
                (body.username, hash_password(body.password), body.role, generate_api_key(), now),
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
# API – export (REST, token-secured)
# ---------------------------------------------------------------------------

@app.get("/api/export/annotations")
def export_annotations(user: dict = Depends(get_current_user)):
    """Export all annotations with sequence names and informative frame indices."""
    patients_data = scan_data_dir()
    with _db() as cur:
        cur.execute(
            "SELECT patient_id, sequence_id, frame_index, comment, user_id, created_at "
            "FROM annotations ORDER BY patient_id, sequence_id"
        )
        annotations = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT patient_id, sequence_id, reason, user_id, created_at "
            "FROM skipped ORDER BY patient_id, sequence_id"
        )
        skipped_list = [dict(r) for r in cur.fetchall()]

    ann_map = {(a["patient_id"], a["sequence_id"]): a for a in annotations}
    skip_map = {(s["patient_id"], s["sequence_id"]): s for s in skipped_list}

    result = []
    for p in patients_data:
        patient = {"patient_id": p["patient_id"], "sequences": []}
        for seq in p["sequences"]:
            key = (p["patient_id"], seq["sequence_id"])
            entry = {
                "sequence_id": seq["sequence_id"],
                "frame_count": seq["frame_count"],
                "status": seq["status"],
            }
            if key in ann_map:
                a = ann_map[key]
                entry["informative_frame"] = a["frame_index"]
                entry["comment"] = a["comment"]
                entry["annotated_by"] = a["user_id"]
                entry["annotated_at"] = a["created_at"]
            elif key in skip_map:
                s = skip_map[key]
                entry["skip_reason"] = s["reason"]
                entry["skipped_by"] = s["user_id"]
                entry["skipped_at"] = s["created_at"]
            patient["sequences"].append(entry)
        result.append(patient)

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "patients": result,
        "summary": {
            "total_patients": len(result),
            "total_sequences": sum(len(p["sequences"]) for p in result),
            "annotated": len(annotations),
            "skipped": len(skipped_list),
        },
    }


# ---------------------------------------------------------------------------
# API – patients / sequences
# ---------------------------------------------------------------------------

@app.get("/api/patients")
def list_patients(user: dict = Depends(get_current_user)):
    return scan_data_dir()


@app.get("/api/patients/{patient_id}/sequences/{sequence_id:path}/frames/{frame_idx}")
def get_frame(
    patient_id: str,
    sequence_id: str,
    frame_idx: int,
    window_center: float | None = Query(None),
    window_width: float | None = Query(None),
    user: dict = Depends(get_current_user),
):
    """Return a single frame as JPEG."""

    # Ensure path index is populated
    if not _PATH_INDEX:
        scan_data_dir()

    resolved = _PATH_INDEX.get((patient_id, sequence_id))
    if resolved is None:
        raise HTTPException(404, "Sequence not found")

    # PNG folder
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

    # DICOM file
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
def get_frames_bulk(patient_id: str, sequence_id: str, user: dict = Depends(get_current_user)):
    """
    Return ALL frames of a sequence as a JSON list of base64-encoded JPEGs.
    Used by the frontend to pre-buffer the entire sequence for smooth playback.
    """
    import base64

    # Ensure path index is populated
    if not _PATH_INDEX:
        scan_data_dir()

    resolved = _PATH_INDEX.get((patient_id, sequence_id))
    if resolved is None:
        raise HTTPException(404, "Sequence not found")

    # PNG folder
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

    # DICOM
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
    patient_id: str
    sequence_id: str
    frame_index: int
    comment: str = ""


class SkipCreate(BaseModel):
    patient_id: str
    sequence_id: str
    reason: str = ""


@app.get("/api/annotations")
def list_annotations(user: dict = Depends(require_admin)):
    with _db() as cur:
        cur.execute("SELECT patient_id, sequence_id, frame_index, comment, user_id, created_at FROM annotations ORDER BY created_at")
        annotations = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT patient_id, sequence_id, reason, user_id, created_at FROM skipped ORDER BY created_at")
        skipped = [dict(r) for r in cur.fetchall()]
    return {"annotations": annotations, "skipped": skipped}


@app.get("/api/annotations/{patient_id}/{sequence_id:path}")
def get_annotation(patient_id: str, sequence_id: str, user: dict = Depends(get_current_user)):
    """Return annotation for a specific sequence (if exists)."""
    with _db() as cur:
        cur.execute(
            "SELECT patient_id, sequence_id, frame_index, comment, user_id, created_at FROM annotations WHERE patient_id=? AND sequence_id=?",
            (patient_id, sequence_id),
        )
        row = cur.fetchone()
        if row:
            return {"type": "annotation", **dict(row)}
        cur.execute(
            "SELECT patient_id, sequence_id, reason, user_id, created_at FROM skipped WHERE patient_id=? AND sequence_id=?",
            (patient_id, sequence_id),
        )
        row = cur.fetchone()
        if row:
            return {"type": "skipped", **dict(row)}
    return None


@app.post("/api/annotations")
def create_annotation(body: AnnotationCreate, user: dict = Depends(require_annotator)):
    now = datetime.now(timezone.utc).isoformat()
    with _db() as cur:
        cur.execute("DELETE FROM skipped WHERE patient_id=? AND sequence_id=?", (body.patient_id, body.sequence_id))
        cur.execute(
            """INSERT INTO annotations (patient_id, sequence_id, frame_index, comment, user_id, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(patient_id, sequence_id)
               DO UPDATE SET frame_index=excluded.frame_index, comment=excluded.comment,
                             user_id=excluded.user_id, created_at=excluded.created_at""",
            (body.patient_id, body.sequence_id, body.frame_index, body.comment, user["username"], now),
        )
    return {"status": "ok", "frame_index": body.frame_index}


@app.post("/api/skip")
def skip_sequence(body: SkipCreate, user: dict = Depends(require_annotator)):
    now = datetime.now(timezone.utc).isoformat()
    with _db() as cur:
        cur.execute("DELETE FROM annotations WHERE patient_id=? AND sequence_id=?", (body.patient_id, body.sequence_id))
        cur.execute(
            """INSERT INTO skipped (patient_id, sequence_id, reason, user_id, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(patient_id, sequence_id)
               DO UPDATE SET reason=excluded.reason, user_id=excluded.user_id, created_at=excluded.created_at""",
            (body.patient_id, body.sequence_id, body.reason, user["username"], now),
        )
    return {"status": "ok"}


@app.delete("/api/annotations/{patient_id}/{sequence_id:path}")
def delete_annotation(patient_id: str, sequence_id: str, user: dict = Depends(require_admin)):
    with _db() as cur:
        cur.execute("DELETE FROM annotations WHERE patient_id=? AND sequence_id=?", (patient_id, sequence_id))
        cur.execute("DELETE FROM skipped WHERE patient_id=? AND sequence_id=?", (patient_id, sequence_id))
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API – stats
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def stats(user: dict = Depends(get_current_user)):
    patients = scan_data_dir()
    total_seq = sum(len(p["sequences"]) for p in patients)
    done_seq = sum(
        1 for p in patients for s in p["sequences"] if s["status"] == "done"
    )
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
