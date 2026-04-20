"""
Generate synthetic demo data (PNG sequences) for testing the Keyselector app.
Creates simple gradient/circle images simulating angiography frames.
"""
import os
from pathlib import Path

import cv2
import numpy as np

DATA_DIR = Path(__file__).resolve().parent / "data"

PATIENTS = {
    "patient_001": ["LAO_30_CRA_20", "RAO_20", "AP_CRA_30"],
    "patient_002": ["LAO_45", "RAO_30_CAU_15"],
    "patient_003": ["AP", "LAO_60_CRA_10", "RAO_10"],
}

FRAME_COUNT = 25
IMG_SIZE = 512


def make_frame(seq_name: str, frame_idx: int, total: int) -> np.ndarray:
    """Create a synthetic angiography-like frame."""
    img = np.zeros((IMG_SIZE, IMG_SIZE), dtype=np.uint8)

    # Background gradient
    for y in range(IMG_SIZE):
        img[y, :] = int(40 + 20 * np.sin(y / IMG_SIZE * np.pi))

    # Simulate vessel (curved line that changes with frame)
    phase = frame_idx / total * 2 * np.pi
    pts = []
    for t in np.linspace(0, 1, 80):
        x = int(IMG_SIZE * (0.2 + 0.6 * t))
        y = int(
            IMG_SIZE * (0.5 + 0.15 * np.sin(2 * np.pi * t + phase) + 0.05 * np.sin(4 * np.pi * t))
        )
        pts.append((x, y))

    # Draw vessel with varying thickness (simulating contrast filling)
    contrast = np.sin(phase) * 0.5 + 0.5  # 0..1
    thickness = max(1, int(2 + 4 * contrast))
    brightness = int(120 + 135 * contrast)

    for i in range(len(pts) - 1):
        cv2.line(img, pts[i], pts[i + 1], brightness, thickness, cv2.LINE_AA)

    # Add some branching
    for branch_start in [20, 40, 55]:
        if branch_start < len(pts):
            bx, by = pts[branch_start]
            angle = np.pi / 4 + hash(seq_name) % 10 * 0.1
            for k in range(15):
                ex = int(bx + k * 4 * np.cos(angle))
                ey = int(by - k * 4 * np.sin(angle))
                if 0 <= ex < IMG_SIZE and 0 <= ey < IMG_SIZE:
                    cv2.circle(img, (ex, ey), max(1, thickness - 1), int(brightness * 0.7), -1)

    # Add noise
    noise = np.random.RandomState(frame_idx).randint(0, 15, (IMG_SIZE, IMG_SIZE), dtype=np.uint8)
    img = cv2.add(img, noise)

    return img


def main():
    for patient_id, sequences in PATIENTS.items():
        for seq_name in sequences:
            seq_dir = DATA_DIR / patient_id / seq_name
            seq_dir.mkdir(parents=True, exist_ok=True)

            for i in range(FRAME_COUNT):
                frame = make_frame(seq_name, i, FRAME_COUNT)
                fname = seq_dir / f"{i:04d}.png"
                cv2.imwrite(str(fname), frame)

            print(f"  Created {FRAME_COUNT} frames: {seq_dir}")

    print(f"\nDone! Data written to {DATA_DIR}")


if __name__ == "__main__":
    main()
