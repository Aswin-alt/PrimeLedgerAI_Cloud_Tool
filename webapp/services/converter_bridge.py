"""Bridge that loads legacy converter scripts via importlib (no edits to them)."""
from __future__ import annotations

import csv
import importlib.util
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from webapp.config import LEGACY_ROOT, OUTPUT_DIR

_module_cache: dict[str, ModuleType] = {}


def _settings_location_index() -> tuple[dict[str, str], dict[str, str]]:
    """Return (by_code, by_name) canonical location names from Settings.

    by_code maps a short code (e.g. ``5166``) to its canonical name; by_name
    maps a lowercased name (e.g. ``marietta hotel``) to its canonical name.
    """
    from webapp.database import SessionLocal
    from webapp.models import Location

    by_code: dict[str, str] = {}
    by_name: dict[str, str] = {}
    db = SessionLocal()
    try:
        for loc in db.query(Location).all():
            name = (loc.name or "").strip()
            if not name:
                continue
            code = (loc.code or "").strip()
            if code:
                by_code[code] = name
            by_name[name.casefold()] = name
    finally:
        db.close()
    return by_code, by_name


def _canonical_location(text: str, by_code: dict[str, str], by_name: dict[str, str]) -> str:
    """Resolve a single location cell to the Settings name when possible.

    Matches on the leading numeric code first (handles 6-digit unit numbers vs
    4-digit codes via suffix matching), then falls back to a by-name match
    (e.g. "Marietta Hotel"). Returns the original text if nothing matches.
    """
    raw = (text or "").strip()
    if not raw:
        return text
    match = re.match(r"\s*(\d{3,})", raw)
    if match:
        digits = match.group(1)
        if digits in by_code:
            return by_code[digits]
        for code, name in by_code.items():
            if digits.endswith(code) or code.endswith(digits):
                return name
    if raw.casefold() in by_name:
        return by_name[raw.casefold()]
    return raw


def coa_rules_for(tool_scope: str) -> list[tuple[str, str, str, str]]:
    """Return active COA rules for a tool from Settings.

    Each rule is (source_contains, account, output_description, entry_type).
    Returns an empty list if none are configured (callers fall back to defaults).
    """
    from webapp.database import SessionLocal
    from webapp.models import ChartOfAccountRule

    db = SessionLocal()
    try:
        rules = (
            db.query(ChartOfAccountRule)
            .filter_by(tool_scope=tool_scope, is_active=True)
            .all()
        )
        return [
            (r.source_contains, r.account, r.output_description or "", r.entry_type or "DEBIT")
            for r in rules
        ]
    finally:
        db.close()


def normalize_account_by_description(csv_path, tool_scope: str) -> None:
    """Remap the 'Account' column of a generated CSV using Settings COA rules.

    A row's account is replaced with a rule's account when the rule's
    source_contains text appears in the row's Description (most specific rule
    wins). Rows that match no rule keep their converter-produced account, so
    this is a safe overlay that falls back to defaults.
    """
    rules = coa_rules_for(tool_scope)
    if not rules:
        return
    path = Path(csv_path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return
    header = [h.strip().lower() for h in rows[0]]
    if "account" not in header or "description" not in header:
        return
    acct_idx = header.index("account")
    desc_idx = header.index("description")
    ordered = sorted(rules, key=lambda r: len(r[0]), reverse=True)
    for row in rows[1:]:
        if desc_idx >= len(row) or acct_idx >= len(row):
            continue
        description = (row[desc_idx] or "").casefold()
        for source_contains, account, _desc, _entry in ordered:
            key = (source_contains or "").strip().casefold()
            if key and key in description:
                row[acct_idx] = account
                break
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def normalize_location_column(csv_path) -> None:
    """Rewrite the 'Location' column of a generated CSV using Settings names.

    Safe no-op if the file is missing, has no Location column, or Settings has
    no locations configured. This makes every converter's output location names
    driven by the editable Settings -> Locations table.
    """
    path = Path(csv_path)
    if not path.exists():
        return
    by_code, by_name = _settings_location_index()
    if not by_code and not by_name:
        return
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return
    header = [h.strip().lower() for h in rows[0]]
    if "location" not in header:
        return
    loc_idx = header.index("location")
    for row in rows[1:]:
        if loc_idx < len(row):
            row[loc_idx] = _canonical_location(row[loc_idx], by_code, by_name)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


@dataclass
class ConversionResult:
    success: bool
    output_path: str = ""
    message: str = ""
    journals: int = 0
    lines: int = 0
    discrepancy: float = 0.0


def _load_module(key: str, relative_path: str) -> ModuleType:
    if key in _module_cache:
        return _module_cache[key]
    path = LEGACY_ROOT / relative_path
    if not path.exists():
        raise FileNotFoundError(f"Legacy script not found: {path}")
    spec = importlib.util.spec_from_file_location(f"legacy_{key}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # Avoid tkinter mainloop side effects for modules that only define classes
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _module_cache[key] = module
    return module


def run_daily_sales(input_path: Path, output_path: Optional[Path] = None) -> ConversionResult:
    mod = _load_module("daily_sales", "Daily Sales/Dennys_Daily_Sales.py")
    out = output_path or (OUTPUT_DIR / f"{input_path.stem}_Journal_Output.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        journals, lines = mod.convert(str(input_path), str(out))
        normalize_account_by_description(out, "daily_sales")
        normalize_location_column(out)
        return ConversionResult(True, str(out), f"Created {journals} journals / {lines} lines", journals, lines)
    except Exception as exc:
        return ConversionResult(False, message=str(exc))


class _SuffixMap(dict):
    """Dict whose lookups also match when the key and a stored key share a
    suffix. The remittance file uses full unit numbers (e.g. ``249690``) while
    the Settings → Locations table stores short codes (e.g. ``9690``)."""

    def _resolve(self, key):
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        key = str(key)
        for stored in self:
            s = str(stored)
            if s and (key.endswith(s) or s.endswith(key)):
                return dict.__getitem__(self, stored)
        return None

    def get(self, key, default=None):
        value = self._resolve(key)
        return default if value is None else value

    def __contains__(self, key):
        return self._resolve(key) is not None


def _remittance_mapping_from_settings(mod: ModuleType):
    """Build (locations, banks, coa_rules) from the Settings tables, falling
    back to the legacy script defaults for any category left unconfigured."""
    from webapp.database import SessionLocal
    from webapp.models import ChartOfAccountRule, Location

    locations = _SuffixMap()
    banks = _SuffixMap()
    coa_rules: list[tuple] = []

    db = SessionLocal()
    try:
        for loc in db.query(Location).filter_by(is_active=True).all():
            code = (loc.code or "").strip()
            if not code:
                continue
            locations[code] = (loc.name or code).strip()
            bank = (loc.bank_account or "").strip()
            if bank:
                banks[code] = bank
        coa_rules = [
            (
                rule.source_contains,
                rule.account,
                rule.output_description or rule.source_contains,
                rule.entry_type,
            )
            for rule in db.query(ChartOfAccountRule)
            .filter_by(tool_scope="remittance")
            .all()
        ]
    finally:
        db.close()

    if not locations:
        locations = _SuffixMap(mod.LOCATIONS)
    if not banks:
        banks = _SuffixMap(mod.BANK_ACCOUNTS)
    if not coa_rules:
        coa_rules = list(mod.DEFAULT_COA_RULES)
    return locations, banks, coa_rules


def run_daily_remittance(
    input_path: Path,
    output_path: Optional[Path] = None,
    mapping_path: Optional[Path] = None,
) -> ConversionResult:
    mod = _load_module("daily_remittance", "Daily Remittance/Daily_Remittance_Converter_CSV_FIXED.py")
    out = output_path or (OUTPUT_DIR / f"{input_path.stem}_QuickBooks_Journal.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        # An uploaded mapping workbook still takes precedence; otherwise the
        # mapping is driven by the editable Settings → Locations / COA tables.
        if mapping_path:
            locations, banks, coa_rules = mod.load_mapping(str(mapping_path))
        else:
            locations, banks, coa_rules = _remittance_mapping_from_settings(mod)

        groups = mod.read_remittance(str(input_path))
        rows, warnings = mod.build_journal(groups, locations, banks, coa_rules)
        mod.write_csv(rows, str(out))
        normalize_location_column(out)

        debits = sum(float(row[3] or 0) for row in rows)
        credits = sum(float(row[4] or 0) for row in rows)
        if abs(debits - credits) > 0.01:
            warnings.append(
                f"Journal is not balanced: debits {debits:.2f}, credits {credits:.2f}."
            )
        units, row_count = len(groups), len(rows)

        disc = abs(float(debits) - float(credits))
        msg = f"Units={units} rows={row_count} debits={debits:.2f} credits={credits:.2f}"
        if warnings:
            msg += " | " + "; ".join(warnings[:5])
        return ConversionResult(True, str(out), msg, units, row_count, disc)
    except Exception as exc:
        return ConversionResult(False, message=str(exc))


def run_payroll(input_path: Path, output_path: Optional[Path] = None) -> ConversionResult:
    mod = _load_module("payroll", "Payroll/Dennys_payroll_xformity.py")
    import csv
    from collections import defaultdict
    from decimal import Decimal

    try:
        entry_date = mod.payroll_date(Path(input_path))
        journal_no = f"Pay{entry_date:%m%d}"
        date_text = entry_date.strftime("%m/%d/%Y")

        # Location names come from the editable Settings -> Locations table.
        by_code, by_name = _settings_location_index()

        # Account + description per position come from the Settings COA table
        # (tool_scope="payroll"), falling back to the legacy POSITION_MAPPING.
        position_map: dict[str, tuple[str, str]] = {}
        payable_account = "Payroll Payable"
        for source_contains, account, out_desc, _entry in coa_rules_for("payroll"):
            key = (source_contains or "").strip()
            if key.casefold() == "payroll payable":
                payable_account = account
                continue
            position_map[key.casefold()] = (account, out_desc or key)
        for position, (account, description) in mod.POSITION_MAPPING.items():
            position_map.setdefault(position.casefold(), (account, description))

        def resolve_position(position: str) -> tuple[str, str]:
            return position_map.get(
                position.casefold(),
                (f"Payroll:Salaries & wages:{position}", position),
            )

        # Reuse the legacy parsing so the CSV matches the desktop journal
        # (only the output format, location, and COA source differ).
        grouped: dict = defaultdict(Decimal)
        with Path(input_path).open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                position = row["Position"].strip()
                account, description = resolve_position(position)
                location = _canonical_location(
                    mod.location_name(row["Store"]), by_code, by_name
                )
                grouped[(location, account, description)] += mod.money(row["Total Wages"])

        by_location: dict = defaultdict(list)
        for (location, account, description), amount in grouped.items():
            by_location[location].append((account, description, amount))

        headers = [
            "Journal No.", "Journal Date", "Account", "Debits", "Credits",
            "Description", "Name", "Location",
        ]
        rows: list[list] = []
        for location in sorted(by_location):
            location_total = Decimal("0")
            for account, description, amount in sorted(by_location[location]):
                rows.append([journal_no, date_text, account, f"{amount:.2f}", "", description, "", location])
                location_total += amount
            rows.append([journal_no, date_text, payable_account, "", f"{location_total:.2f}", "Payroll Total", "", location])

        out = output_path or (OUTPUT_DIR / f"Payroll_Journal_{journal_no}.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)

        return ConversionResult(
            True,
            str(out),
            f"Payroll journal created: {out.name} ({len(by_location)} locations / {len(rows)} lines)",
            len(by_location),
            len(rows),
        )
    except Exception as exc:
        return ConversionResult(False, message=str(exc))


_HOTEL_ACCOUNT_ATTRS = {
    "room revenue": "ROOM_REVENUE_ACCOUNT",
    "bed tax": "BED_TAX_ACCOUNT",
    "sales tax": "SALES_TAX_ACCOUNT",
    "bank": "BANK_ACCOUNT",
    "guest ledger": "GUEST_LEDGER_ACCOUNT",
}


def run_hotel_revenue(input_path: Path, output_path: Optional[Path] = None, location: str = "Marietta Hotel") -> ConversionResult:
    mod = _load_module("hotel_revenue", "Hotel Revenue/Marietta_hotel_revenue_daily.py")
    out = output_path or (OUTPUT_DIR / f"{input_path.stem}_QuickBooks.csv")
    out.parent.mkdir(parents=True, exist_ok=True)

    # Override the module's fixed account constants from Settings for this run,
    # then restore them so the module defaults remain the fallback.
    saved: dict[str, str] = {}
    for source_contains, account, _desc, _entry in coa_rules_for("hotel_revenue"):
        attr = _HOTEL_ACCOUNT_ATTRS.get((source_contains or "").strip().casefold())
        if attr and account:
            saved[attr] = getattr(mod, attr)
            setattr(mod, attr, account)
    try:
        actual = mod.convert_with_locked_file_fallback(Path(input_path), Path(out), location)
        normalize_location_column(actual)
        return ConversionResult(True, str(actual), f"Hotel revenue CSV: {Path(actual).name}")
    except Exception as exc:
        return ConversionResult(False, message=str(exc))
    finally:
        for attr, value in saved.items():
            setattr(mod, attr, value)


def run_mclane_invoice(input_path: Path, output_path: Optional[Path] = None) -> ConversionResult:
    mod = _load_module("mclane_invoice", "McLane Invoice/McLane_Invoice_Converter.py")
    out = output_path or (OUTPUT_DIR / f"QuickBooks_Ready_{input_path.stem}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        row_count, invoice_count, total = mod.convert(str(input_path), str(out))
        normalize_location_column(out)
        return ConversionResult(
            True, str(out),
            f"McLane invoice CSV created: {invoice_count} invoice(s), {row_count} rows, total ${total:,.2f}",
            invoice_count, row_count,
        )
    except Exception as exc:
        return ConversionResult(False, message=str(exc))


def run_payroll_isolve(input_path: Path, output_path: Optional[Path] = None) -> ConversionResult:
    mod = _load_module("payroll_isolve", "Payroll-Isolve/Islove_payroll_summary.py")
    out = output_path or (OUTPUT_DIR / f"{input_path.stem}_journal.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        result_path = mod.convert(input_path, out)
        normalize_location_column(result_path)
        return ConversionResult(True, str(result_path), f"iSolved payroll journal created: {Path(result_path).name}")
    except Exception as exc:
        return ConversionResult(False, message=str(exc))


def run_sales_tax(input_path: Path, output_path: Optional[Path] = None) -> ConversionResult:
    mod = _load_module("sales_tax", "Sales Tax/daily_report_sales_tax_to_pdf.py")
    out = output_path or (OUTPUT_DIR / f"{input_path.stem}_Sales_Tax.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        vals = mod.make_pdf(Path(input_path), Path(out))
        msg = "Sales tax PDF created: " + ", ".join(f"${v:,.2f}" for v in vals)
        return ConversionResult(True, str(out), msg)
    except Exception as exc:
        return ConversionResult(False, message=str(exc))


_INVOICE_SPECIAL_KEYS = {"vendor credit account", "invoice tax account"}


def _apply_invoice_rules(mod: ModuleType) -> None:
    """Override the invoice module's keyword->account rules from Settings.

    Leaves the module defaults in place if Settings has no invoice rules.
    """
    keyword_rules = []
    for source_contains, account, _desc, _entry in coa_rules_for("invoice"):
        key = (source_contains or "").strip()
        if not key or not account or key.casefold() in _INVOICE_SPECIAL_KEYS:
            continue
        keyword_rules.append((key.casefold(), account, True))
    if keyword_rules:
        mod.DEFAULT_RULES = keyword_rules


def invoice_special_accounts() -> tuple[str, str]:
    """Return (credit_account, tax_account) for invoices from Settings."""
    credit_account = "Denny's Inc"
    tax_account = "Marketing & Franchise Fees:Technology Fee"
    for source_contains, account, _desc, _entry in coa_rules_for("invoice"):
        key = (source_contains or "").strip().casefold()
        if not account:
            continue
        if key == "vendor credit account":
            credit_account = account
        elif key == "invoice tax account":
            tax_account = account
    return credit_account, tax_account


def parse_invoice_pdf(pdf_path: Path) -> tuple[list[Any], dict[str, str]]:
    mod = _load_module("invoice", "Dennys Invoice/Dennys_weekly_invoice_DFO.py")
    _apply_invoice_rules(mod)
    return mod.parse_pdf(str(pdf_path))


def invoice_module() -> ModuleType:
    mod = _load_module("invoice", "Dennys Invoice/Dennys_weekly_invoice_DFO.py")
    _apply_invoice_rules(mod)
    return mod


TOOLS = {
    "daily_sales": {
        "name": "Daily Sales",
        "work_type": "Daily Sales",
        "accept": ".csv,.xlsx,.xlsm",
        "runner": run_daily_sales,
    },
    "daily_remittance": {
        "name": "Daily Remittance",
        "work_type": "Daily Remittance",
        "accept": ".csv",
        "runner": run_daily_remittance,
    },
    "payroll": {
        "name": "Weekly Payroll",
        "work_type": "Weekly Payroll",
        "accept": ".csv",
        "runner": run_payroll,
    },
    "hotel_revenue": {
        "name": "Marietta Hotel Daily Revenue",
        "work_type": "Marietta Hotel Daily Revenue",
        "accept": ".pdf",
        "runner": run_hotel_revenue,
    },
    "sales_tax": {
        "name": "Sales Tax",
        "work_type": "Sales Tax",
        "accept": ".csv",
        "runner": run_sales_tax,
    },
    "payroll_isolve": {
        "name": "iSolved Payroll",
        "work_type": "Payroll-Isolve",
        "accept": ".pdf",
        "runner": run_payroll_isolve,
    },
    "mclane_invoice": {
        "name": "McLane Invoice",
        "work_type": "McLane Invoice",
        "accept": ".xlsx,.xlsm,.csv",
        "runner": run_mclane_invoice,
    },
    "invoice": {
        "name": "Weekly Denny's Invoice",
        "work_type": "Weekly Denny's Invoice",
        "accept": ".pdf",
        "runner": None,
    },
}


def safe_traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()
