#!/usr/bin/env python3
"""Seed roles, permissions, default Admin, and locations."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp.config import (  # noqa: E402
    DEFAULT_ADMIN_EMAIL,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USERNAME,
    PERMISSIONS,
    UPLOAD_DIR,
    OUTPUT_DIR,
)
from webapp.database import Base, SessionLocal, engine  # noqa: E402
from webapp.models import (  # noqa: E402
    ChartOfAccountRule,
    Location,
    Permission,
    Role,
    RolePermission,
    User,
)
from webapp.security import hash_password  # noqa: E402

DEFAULT_ROLE_GRANTS = {
    "Admin": [p[0] for p in PERMISSIONS],
    "Manager": [
        "dashboard.view",
        "records.view",
        "records.upload",
        "records.edit",
        "records.export",
        "records.approve",
        "convert.run",
        "convert.download",
        "tally.run",
        "tally.download",
        "audit.view_own",
        "audit.view_all",
        "audit.export",
        "settings.manage",
        "analytics.view",
    ],
    "Accountant": [
        "dashboard.view",
        "records.view",
        "records.upload",
        "records.edit",
        "records.export",
        "convert.run",
        "convert.download",
        "tally.run",
        "tally.download",
        "audit.view_own",
        "analytics.view",
    ],
    "Viewer": [
        "dashboard.view",
        "records.view",
        "records.export",
        "audit.view_own",
        "analytics.view",
    ],
}

# Permission codes added after the first release. Roles that were already
# seeded are skipped by the grant loop below, so these would otherwise reach
# nobody - not even Admin. Backfilled explicitly, and only these codes: an
# admin who revokes a permission deletes its row, so a blanket backfill would
# silently resurrect it.
BACKFILL_CODES = ["tally.run", "tally.download"]

LOCATIONS = [
    ("5164", "5164-Berkshire OH", "Cash on Hand - Berkshire OH", "FCB - Sunbury ac# 7053"),
    ("5165", "5165-Perrysburg OH", "Cash on Hand - Perrysburg,OH", "FCB - Perrysburgh ac# 7061"),
    ("5166", "5166-Jeffersonville", "Cash on Hand - Jeffersonville", "FCB - Jeffersonville ac# 2945"),
    ("5167", "5167-Elizabethtown KY", "Cash on Hand - Elizabethtown-KY", "FCB - ETown ac# 7045"),
    ("5168", "5168-Catlettsburg KY", "Cash on hand - Catlettsburg KY", "FCB - Cattletsburgh ac# 7079"),
    ("5169", "5169-Columbus OH", "Cash on Hand - Columbus OH", "FCB - Columbus ac# 7037"),
    ("7401", "7401-Youngstown", "Cash on Hand - Youngstown", "Marietta Restaurant llc (1842) - 1"),
    ("8829", "8829-Columbus OH", "Cash on Hand - 8829", "FCB - Columbus ac# 7037"),
    ("9690", "9690 - Marietta Rest", "Cash on Hand - Marietta", "Marietta Restaurant llc (1842) - 1"),
    ("9697", "9697-Findley", "Cash on Hand - Findlay", "FC Bank Ac#0167 - Nights and Bites"),
    ("HOTEL", "Marietta Hotel", "Cash on Hand - Marietta Hotel", "FC Bank AC# 0606"),
]

# Chart-of-accounts rules per tool. Each tuple is
# (source_contains, account, output_description, entry_type).
# These mirror the converters' built-in defaults so they can be edited in
# Settings without touching code; converters fall back to defaults if empty.
COA_RULES_BY_TOOL = {
    "remittance": [
        ("Fees Credit Cards QR", "Commissions & fees  *:Credit Card Fee", "Fees Credit Cards QR", "DEBIT"),
        ("Fees - Credit Cards", "Commissions & fees  *:Credit Card Fee", "Fees - Credit Cards", "DEBIT"),
        ("Amx Fees", "Commissions & fees  *:Credit Card Fee", "Fees - Credit Cards", "DEBIT"),
        ("Gross Credit Cards QR", "Credit Sales:QR Pay - Sales", "Gross Credit Cards QR", "CREDIT"),
        ("Gross - Credit Cards", "Credit Sales:Credit card - Sales to Dennys", "Gross - Credit Cards", "CREDIT"),
    ],
    # Daily Sales: matched against each output line's Description column.
    "daily_sales": [
        ("Net Sales", "Sales A/C", "", "CREDIT"),
        ("Total Sales Tax", "Tax Payable:Sales Tax Payable", "", "CREDIT"),
        ("Total Discounts", "Food Discount A/c", "", "DEBIT"),
        ("Credit Tips", "Tips Payable", "", "CREDIT"),
        ("Cash Paid Outs", "Cash Paid Outs", "", "DEBIT"),
        ("Auto-balance", "Daily Sales Variance", "", "CREDIT"),
        ("Door", "Credit Sales:Doordash - Sales", "", "DEBIT"),
        ("Uber", "Credit Sales:Uber Eats - Sales", "", "DEBIT"),
        ("Grub", "Credit Sales:Grubhub - Sales", "", "DEBIT"),
        ("QR Pay", "Credit Sales:QR Pay - Sales", "", "DEBIT"),
        ("Master Card", "Credit Sales:Credit card - Sales to Dennys", "", "DEBIT"),
        ("Visa", "Credit Sales:Credit card - Sales to Dennys", "", "DEBIT"),
        ("Discover", "Credit Sales:Credit card - Sales to Dennys", "", "DEBIT"),
        ("American Express", "Credit Sales:Credit card - Sales to Dennys", "", "DEBIT"),
        ("Debit", "Credit Sales:Credit card - Sales to Dennys", "", "DEBIT"),
        ("Gift Card", "Gift Card Payable", "", "DEBIT"),
    ],
    # Payroll: source_contains is the position (exact, case-insensitive).
    "payroll": [
        ("Cook", "Payroll:Salaries & wages:Cook & Prep Cook", "Cook", "DEBIT"),
        ("Cook Trainee", "Payroll:Salaries & wages:Training", "Cook Trainee", "DEBIT"),
        ("Diamond Shift Coord.", "Payroll:Salaries & wages:Hourly Managers", "Diamond Shift Coord.", "DEBIT"),
        ("Diamond Shift Superv", "Payroll:Salaries & wages:Hourly Managers", "Diamond Shift Superv", "DEBIT"),
        ("Host/Hostess", "Payroll:Salaries & wages:Host / Hostess", "Host/Hostess", "DEBIT"),
        ("Host/Hostess Trainee", "Payroll:Salaries & wages:Training", "Host/Hostess Trainee", "DEBIT"),
        ("Hourly RM", "Payroll:Salaries & wages:Hourly Managers", "Hourly RM", "DEBIT"),
        ("Manager In Training", "Payroll:Salaries & wages:Training", "Manager In Training", "DEBIT"),
        ("Meeting", "Payroll:Salaries & wages:Training", "Meeting", "DEBIT"),
        ("Premium Server", "Payroll:Salaries & wages:Server", "Premium Server", "DEBIT"),
        ("Server", "Payroll:Salaries & wages:Server", "Server", "DEBIT"),
        ("Server Trainee", "Payroll:Salaries & wages:Training", "Server Trainee", "DEBIT"),
        ("Service Assistant", "Payroll:Salaries & wages:Service Assistant", "Service Assistant", "DEBIT"),
        ("Full Time Cook", "Payroll:Salaries & wages:Cook & Prep Cook", "Full Time Cook", "DEBIT"),
        ("Server-Min Wage", "Payroll:Salaries & wages:Server", "Server-Min Wage", "DEBIT"),
        ("Bartender", "Payroll:Salaries & wages:Bartender", "Bartender", "DEBIT"),
        ("Service Ast. Trainee", "Payroll:Salaries & wages:Training", "Service Ast. Trainee", "DEBIT"),
        ("Service Spc Trainee", "Payroll:Salaries & wages:Training", "Service Spc Trainee", "DEBIT"),
        ("Shift Manager", "Payroll:Salaries & wages:Hourly Managers", "Shift Manager", "DEBIT"),
        ("Cashier", "Payroll:Salaries & wages:Service Assistant", "Cashier", "DEBIT"),
        ("Payroll Total", "Payroll Payable", "Payroll Total", "CREDIT"),
    ],
    # Hotel Revenue: source_contains is a fixed slot key.
    "hotel_revenue": [
        ("Room Revenue", "Room Revenue", "", "CREDIT"),
        ("Bed Tax", "Tax Payable:Bed Tax", "", "CREDIT"),
        ("Sales Tax", "Tax Payable:Sales tax Payable - Marietta Hotel", "", "CREDIT"),
        ("Bank", "FC Bank AC# 0606", "", "DEBIT"),
        ("Guest Ledger", "Front Desk-Guest Ledger", "", "DEBIT"),
    ],
    # Invoice: keyword rules for auto-account suggestions, plus two special keys.
    "invoice": [
        ("royalty", "Marketing & Franchise Fees:Franchise Royalties", "", "DEBIT"),
        ("advertising", "Marketing & Franchise Fees:Marketing Fees", "", "DEBIT"),
        ("additional bbf investment", "Marketing & Franchise Fees:BBF Investment Addtl", "", "DEBIT"),
        ("bbf investment", "Marketing & Franchise Fees:BBF Investment Addtl", "", "DEBIT"),
        ("help desk support", "Marketing & Franchise Fees:Technology Fee", "", "DEBIT"),
        ("pilot / flying j percentage rent", "Occupancy:Rent", "", "DEBIT"),
        ("Vendor Credit Account", "Denny's Inc", "Special: journal credit line", "CREDIT"),
        ("Invoice Tax Account", "Marketing & Franchise Fees:Technology Fee", "Special: page tax line", "DEBIT"),
    ],
}


def seed() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Permissions
        perm_by_code: dict[str, Permission] = {}
        for code, name, category in PERMISSIONS:
            perm = db.query(Permission).filter_by(code=code).first()
            if not perm:
                perm = Permission(code=code, name=name, category=category)
                db.add(perm)
                db.flush()
            perm_by_code[code] = perm

        # Roles
        role_meta = {
            "Admin": ("Full system access; manage users and permissions", True),
            "Manager": ("Review, approve, manage mappings; no user admin", True),
            "Accountant": ("Upload, convert, edit records", True),
            "Viewer": ("Read-only dashboard and export", True),
        }
        roles: dict[str, Role] = {}
        for name, (desc, is_system) in role_meta.items():
            role = db.query(Role).filter_by(name=name).first()
            if not role:
                role = Role(name=name, description=desc, is_system=is_system)
                db.add(role)
                db.flush()
            roles[name] = role

        # Role permissions (only seed if role has none yet)
        for role_name, codes in DEFAULT_ROLE_GRANTS.items():
            role = roles[role_name]
            existing = db.query(RolePermission).filter_by(role_id=role.id).count()
            if existing:
                continue
            for code in codes:
                db.add(
                    RolePermission(
                        role_id=role.id,
                        permission_id=perm_by_code[code].id,
                        allowed=True,
                    )
                )
        db.flush()

        # Backfill newly introduced permission codes onto already-seeded roles.
        for role_name, codes in DEFAULT_ROLE_GRANTS.items():
            role = roles[role_name]
            for code in codes:
                if code not in BACKFILL_CODES:
                    continue
                perm = perm_by_code[code]
                exists = (
                    db.query(RolePermission)
                    .filter_by(role_id=role.id, permission_id=perm.id)
                    .first()
                )
                if not exists:
                    db.add(
                        RolePermission(role_id=role.id, permission_id=perm.id, allowed=True)
                    )
                    print(f"  backfilled {role_name} -> {code}")

        # Admin user
        admin = db.query(User).filter_by(username=DEFAULT_ADMIN_USERNAME).first()
        if not admin:
            admin = User(
                username=DEFAULT_ADMIN_USERNAME,
                email=DEFAULT_ADMIN_EMAIL,
                password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
                role_id=roles["Admin"].id,
                is_active=True,
            )
            db.add(admin)

        # Locations
        for code, name, cash, bank in LOCATIONS:
            loc = db.query(Location).filter_by(code=code).first()
            if not loc:
                db.add(
                    Location(
                        code=code, name=name, cash_account=cash, bank_account=bank
                    )
                )

        # Chart of accounts rules, seeded per tool scope (idempotent per scope).
        for scope, rules in COA_RULES_BY_TOOL.items():
            if db.query(ChartOfAccountRule).filter_by(tool_scope=scope).count() == 0:
                for contains, account, desc, entry in rules:
                    db.add(
                        ChartOfAccountRule(
                            source_contains=contains,
                            account=account,
                            output_description=desc,
                            entry_type=entry,
                            tool_scope=scope,
                        )
                    )

        db.commit()
        print("Seed complete.")
        print(f"  Admin login: {DEFAULT_ADMIN_USERNAME} / {DEFAULT_ADMIN_PASSWORD}")
        print(f"  Roles: {', '.join(roles)}")
        print(f"  Permissions: {len(perm_by_code)}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
