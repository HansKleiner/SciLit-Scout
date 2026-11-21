"""Install runtime dependencies only when the lock-input file changes.

The SHA-256 marker makes subsequent launches skip pip entirely. The marker is
written only after a successful installation, so interrupted installs retry.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
MARKER = ROOT / ".venv" / ".scilit-requirements.sha256"


def main() -> int:
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    if MARKER.exists() and MARKER.read_text(encoding="ascii").strip() == digest:
        print("Dependencies are up to date; skipping pip install.")
        return 0

    print("Installing SciLit Scout runtime dependencies (first launch or update)...")
    command = [
        sys.executable, "-m", "pip", "install",
        "--disable-pip-version-check",
        "-r", str(REQUIREMENTS),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    MARKER.write_text(digest + "\n", encoding="ascii")
    print("Dependency setup complete.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Dependency installation failed (exit code {exc.returncode}).", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
