"""Application configuration loaded from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

APP_NAME = "PrimeLedgerAI Cloud"
APP_VERSION = "2.1.0"

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-primeledger-dev-secret-key-32chars")
SESSION_COOKIE = "pla_session"
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", str(60 * 60 * 12)))
# Set SESSION_HTTPS_ONLY=true in production behind HTTPS so the session cookie
# is never sent over plain HTTP. Keep false for local HTTP development.
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"

# Default to local MySQL; override with DATABASE_URL for SQLite (sqlite:///./primeledger.db)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://root:@127.0.0.1:3306/primeledger?charset=utf8mb4",
)

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "webapp" / "uploads")))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR / "webapp" / "outputs")))
LEGACY_ROOT = Path(os.getenv("LEGACY_ROOT", str(BASE_DIR)))

DEFAULT_ADMIN_USERNAME = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "Admin@123")
DEFAULT_ADMIN_EMAIL = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@primeledgerai.com")

WORK_TYPES = [
    "Daily Sales",
    "Daily Remittance",
    "Weekly Payroll",
    "Weekly Denny's Invoice",
    "Marietta Hotel Daily Revenue",
    "Sales Tax",
]

STATUSES = ["Received", "In Progress", "Completed", "Needs Review", "On Hold", "Approved"]

# Permission catalog codes used across the app
PERMISSIONS = [
    ("dashboard.view", "View dashboard", "Dashboard"),
    ("records.view", "View accounting records", "Records"),
    ("records.upload", "Upload accounting files", "Records"),
    ("records.edit", "Edit status and follow-up", "Records"),
    ("records.remove", "Remove records from dashboard", "Records"),
    ("records.export", "Export tracker", "Records"),
    ("records.approve", "Approve completed records", "Records"),
    ("convert.run", "Run converters", "Converters"),
    ("convert.download", "Download conversion output", "Converters"),
    ("tally.run", "Run Tally reports", "Tally"),
    ("tally.download", "Download Tally reports", "Tally"),
    ("audit.view_own", "View own activity", "Audit"),
    ("audit.view_all", "View all activity", "Audit"),
    ("audit.export", "Export audit log", "Audit"),
    ("users.manage", "Manage users", "Admin"),
    ("roles.manage", "Manage roles and permissions", "Admin"),
    ("settings.manage", "Manage locations and mappings", "Admin"),
    ("analytics.view", "View analytics charts", "Analytics"),
]
