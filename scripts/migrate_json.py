#!/usr/bin/env python3
"""Import legacy dashboard_data.json into MySQL/SQLite FileRecord rows."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp.database import Base, SessionLocal, engine  # noqa: E402
from webapp.models import FileRecord  # noqa: E402


def migrate(json_path: Path | None = None) -> None:
    Base.metadata.create_all(bind=engine)
    path = json_path or (
        ROOT / "PrimeLedgerAI_Dashboard_Data" / "dashboard_data.json"
    )
    if not path.exists():
        print(f"No dashboard JSON found at {path}")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records", [])
    db = SessionLocal()
    try:
        imported = 0
        for r in records:
            existing = (
                db.query(FileRecord)
                .filter_by(file_name=r.get("file", ""), stored_path=r.get("path", ""))
                .first()
            )
            if existing:
                continue
            db.add(
                FileRecord(
                    work_type=r.get("work_type", ""),
                    work_date=r.get("date", ""),
                    location_name=r.get("location", "All"),
                    file_name=r.get("file", ""),
                    stored_path=r.get("path", ""),
                    status=r.get("status", "Received"),
                    discrepancy=r.get("discrepancy", "0.00"),
                    follow_up=r.get("follow_up", ""),
                )
            )
            imported += 1
        db.commit()
        print(f"Imported {imported} record(s) from {path}")
    finally:
        db.close()


if __name__ == "__main__":
    arg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    migrate(arg)
