"""Tally operations bridge: wraps the desktop scripts in Tally/ for the web app.

Mirrors converter_bridge: the legacy scripts are imported in-process with
importlib and remain the source of truth for every calculation. Nothing in
this module re-implements reconciliation or reporting logic.
"""
from __future__ import annotations

import html
import importlib.util
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from webapp.config import LEGACY_ROOT

ZERO = Decimal("0.00")

TALLY_TOOLS = {
    "cash_deposit": {
        "name": "Cash & Deposit Match",
        "accept": ".csv,.tsv",
        "help": "Match Tender: Cash (net) journals against the deposits that cleared them, per location and date.",
        "tip": "Export Cash on Hand detail with Date, Type, Memo/Description and Amount. "
               "Include later deposit postings — deposits match on the date inside the description, not the posting date.",
    },
    "credit_sales": {
        "name": "Credit Sales Report",
        "accept": ".csv",
        "help": "Summarise credit/delivery sales and the receipts posted against them, by location and account.",
        "tip": "Export Transaction Detail by Account including the Distribution account column. "
               "A deposit counts only when its whole description period falls inside your date range.",
    },
}

_module_cache: dict[str, ModuleType] = {}


def _load_module(key: str, relative_path: str) -> ModuleType:
    if key in _module_cache:
        return _module_cache[key]
    path = LEGACY_ROOT / relative_path
    if not path.exists():
        raise FileNotFoundError(f"Tally script not found: {path}")
    spec = importlib.util.spec_from_file_location(f"tally_{key}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    # Both scripts keep tkinter inside main(), so this import is headless-safe.
    spec.loader.exec_module(module)
    _module_cache[key] = module
    return module


def cash_module() -> ModuleType:
    return _load_module("cash_deposit", "Tally/PrimeLedgerAI_Cash_Deposit_Match.py")


def credit_module() -> ModuleType:
    return _load_module("credit_sales", "Tally/PrimeLedgerAI_Credit_Sales_Report.py")


class TallyError(Exception):
    """A user-facing problem with the uploaded file or the chosen options."""


def parse_input_date(value: str, label: str) -> date:
    """Accept the browser's YYYY-MM-DD as well as the scripts' MM/DD/YYYY."""
    text = (value or "").strip()
    if not text:
        raise TallyError(f"{label} is required.")
    mod = cash_module()
    try:
        return mod.parse_date(text)
    except ValueError:
        raise TallyError(f"{label} must be a date like 10/01/2026.") from None


def to_us(value: date) -> str:
    return value.strftime("%m/%d/%Y")


def to_iso(value) -> str:
    """MM/DD/YYYY -> YYYY-MM-DD, so <input type="date"> can show a stored run."""
    if isinstance(value, date):
        return value.isoformat()
    try:
        return cash_module().parse_date(str(value)).isoformat()
    except Exception:
        return ""


def money_or_none(value: str) -> Optional[Decimal]:
    """Blank stays None — an unknown opening balance is never zero."""
    text = (value or "").strip()
    if not text:
        return None
    cleaned = text.replace(",", "").replace("$", "").replace("−", "-")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        amount = Decimal(cleaned)
        if not amount.is_finite():
            raise InvalidOperation
        return amount.quantize(Decimal(".01"))
    except (InvalidOperation, ArithmeticError):
        raise TallyError(f"{value!r} is not a valid amount.") from None


# ───────────────────────── Cash & Deposit Match ─────────────────────────

def load_cash_deposit(path: Path):
    try:
        return cash_module().read_transactions(str(path))
    except ValueError as exc:
        raise TallyError(str(exc)) from None
    except Exception as exc:  # unreadable file, bad encoding, …
        raise TallyError(f"Could not read this CSV: {exc}") from None


def cash_deposit_report(path: Path, date_from: date, date_to: date,
                        locations: Optional[list[str]] = None) -> dict[str, Any]:
    """Re-parse the stored upload and reconcile. Stateless by design."""
    mod = cash_module()
    records, issues = load_cash_deposit(path)
    all_locations = sorted({r["location"] for r in records} | {r[1] for r in issues if r[1]})
    selected = sorted(set(locations) & set(all_locations)) if locations else list(all_locations)
    if not selected:
        selected = list(all_locations)
    if not selected:
        raise TallyError("No Cash on Hand locations were found in this file.")
    try:
        rows = mod.reconcile(records, date_from, date_to, set(selected))
    except ValueError as exc:
        raise TallyError(str(exc)) from None

    display_rows = mod.with_totals(rows, set(selected)) if rows else []
    scoped_issues = [r for r in issues if not r[1] or r[1] in set(selected)]

    def column_total(index: int) -> Decimal:
        return sum((Decimal(r[index]) for r in rows), ZERO)

    return {
        "rows": rows,
        "display_rows": display_rows,
        "issues": scoped_issues,
        "all_locations": all_locations,
        "locations": selected,
        "headers": mod.HEADERS,
        "totals": {
            "rows": len(rows),
            "tender": column_total(2),
            "deposit": column_total(3),
            "difference": column_total(4),
            "review": len(scoped_issues),
        },
    }


def cash_deposit_html(report: dict[str, Any], date_from: date, date_to: date, source: str) -> str:
    return cash_module().print_report(
        report["rows"], to_us(date_from), to_us(date_to),
        set(report["locations"]), source, report["issues"],
    )


def cash_deposit_pdf(path: Path, report: dict[str, Any], date_from: date, date_to: date,
                     source: str) -> Path:
    """Render the same document print_report() produces, as a real PDF."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (LongTable, Paragraph, SimpleDocTemplate,
                                        Spacer, TableStyle)
    except ImportError:
        raise TallyError("PDF export requires ReportLab. Install it with: "
                         "python -m pip install reportlab") from None

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Cell", fontName="Helvetica", fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="Money", parent=styles["Cell"], alignment=2))
    styles.add(ParagraphStyle(name="Head", parent=styles["Cell"],
                              textColor=colors.white, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="Note", fontName="Helvetica", fontSize=9, leading=13))

    def para(value, style="Cell"):
        return Paragraph(html.escape(str(value)).replace("\n", "<br/>"), styles[style])

    width, height = landscape(A4)
    usable = width - 64
    story: list[Any] = []
    rows = report["rows"]
    columns = ("Match Date", "Tender Cash Net", "Deposit", "Difference",
               "Status", "Journal Numbers", "Deposit Posting Dates")
    weights = [1.0, 1.15, 1.15, 1.05, 1.1, 1.5, 1.3]

    def amount(text: str) -> str:
        return f"{Decimal(text):,.2f}"

    def totals(items, index):
        return sum((Decimal(r[index]) for r in items), ZERO)

    story += [
        para("PrimeLedgerAI", "Title"),
        para("Cash &amp; Deposit Reconciliation", "Heading2"),
        para(f"<b>Period:</b> {to_us(date_from)} to {to_us(date_to)}<br/>"
             f"<b>Source:</b> {html.escape(Path(source).name)}<br/>"
             f"<b>Selected locations:</b> {html.escape(', '.join(sorted(report['locations'])))}", "Note"),
        Spacer(1, 6),
        para("Difference = Tender Cash Net − Deposit. Deposits match by Description date. "
             "Missing means absent from the source CSV.", "Note"),
        Spacer(1, 12),
    ]

    for location in sorted(report["locations"]):
        items = [r for r in rows if r[0] == location]
        data = [[para(h, "Head") for h in columns]]
        for r in items:
            data.append([
                para(r[1]), para(amount(r[2]), "Money"), para(amount(r[3]), "Money"),
                para(amount(r[4]), "Money"), para(r[5]), para(r[6]), para(r[7]),
            ])
        if not items:
            data.append([para("No transactions in this date range.")] + [para("")] * 6)
        data.append([
            para("<b>Location total</b>"),
            para(f"<b>{totals(items, 2):,.2f}</b>", "Money"),
            para(f"<b>{totals(items, 3):,.2f}</b>", "Money"),
            para(f"<b>{totals(items, 4):,.2f}</b>", "Money"),
            para(""), para(""), para(""),
        ])
        grid = LongTable(data, colWidths=[usable * w / sum(weights) for w in weights],
                         repeatRows=1, hAlign="LEFT", splitInRow=1)
        grid.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#233D60")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.HexColor("#F1F5F9"), colors.white]),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DCE7F5")),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#233D60")),
        ]))
        story += [para(location, "Heading2"), grid, Spacer(1, 12)]

    story += [para(
        f"<b>Grand total — Tender: ${totals(rows, 2):,.2f} | "
        f"Deposit: ${totals(rows, 3):,.2f} | Difference: ${totals(rows, 4):,.2f}</b>", "Note")]

    if report["issues"]:
        story += [Spacer(1, 14), para("Rows needing review — excluded from totals", "Heading2"),
                  para("All source dates for selected locations, plus rows without a location.", "Note")]
        for issue in report["issues"]:
            story.append(para(" | ".join(str(v) for v in issue), "Note"))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.line(32, 30, width - 32, 30)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(32, 18, f"PrimeLedgerAI | {to_us(date_from)} - {to_us(date_to)} | USD")
        canvas.drawRightString(width - 32, 18, "Page " + str(doc.page))
        canvas.restoreState()

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=(width, height), leftMargin=32, rightMargin=32,
                            topMargin=30, bottomMargin=42,
                            title="Cash and Deposit Reconciliation", author="PrimeLedgerAI")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path


# ───────────────────────── Credit Sales Report ─────────────────────────

def load_credit_sales(path: Path):
    try:
        return credit_module().load_csv(str(path))
    except ValueError as exc:
        raise TallyError(str(exc)) from None
    except Exception as exc:
        raise TallyError(f"Could not read this CSV: {exc}") from None


def credit_sales_report(path: Path, date_from: date, date_to: date,
                        locations: Optional[list[str]], accounts: Optional[list[str]],
                        openings: dict[tuple[str, str], Decimal],
                        prefix: str = "DS", receipt_type: str = "Deposit") -> dict[str, Any]:
    mod = credit_module()
    company, transactions = load_credit_sales(path)
    all_locations = sorted({t.location for t in transactions})
    all_accounts = sorted({t.account for t in transactions})
    sel_locations = sorted(set(locations) & set(all_locations)) if locations else list(all_locations)
    sel_accounts = sorted(set(accounts) & set(all_accounts)) if accounts else list(all_accounts)
    if not sel_locations:
        raise TallyError("Select at least one location.")
    if not sel_accounts:
        raise TallyError("Select at least one account.")
    try:
        summary, details, selected, total, warnings = mod.calculate(
            transactions, set(sel_locations), set(sel_accounts), date_from, date_to,
            openings, prefix.strip() or "DS", receipt_type.strip() or "Deposit")
    except ValueError as exc:
        raise TallyError(str(exc)) from None

    def tx_row(t):
        return [t.row, t.day, t.kind, t.number, t.location, t.account,
                t.description, t.split, t.amount,
                mod.classify(t, prefix.strip() or "DS", receipt_type.strip() or "Deposit")]

    excluded = []
    for t in transactions:
        if (t.location in set(sel_locations) and t.account in set(sel_accounts)
                and t.kind.casefold() == (receipt_type.strip() or "Deposit").casefold()):
            included, reason = mod.period_decision(t, date_from, date_to,
                                                   receipt_type.strip() or "Deposit")
            if not included:
                excluded.append(tx_row(t) + [reason])

    return {
        "company": company,
        "transaction_count": len(transactions),
        "summary": summary,
        "details": details,
        "total": total,
        "warnings": warnings,
        "transactions": [tx_row(t) for t in sorted(selected, key=lambda t: (t.day, t.row))],
        "excluded": excluded,
        "all_locations": all_locations,
        "all_accounts": all_accounts,
        "locations": sel_locations,
        "accounts": sel_accounts,
        "as_of": date_from - timedelta(days=1),
        "summary_headers": mod.SUMMARY_HEADERS,
        "detail_headers": mod.DETAIL_HEADERS,
        "tx_headers": mod.TX_HEADERS,
    }


def display(value) -> str:
    """The scripts' own formatting: None -> 'Opening needed', negatives in parentheses."""
    return credit_module().display(value)


def credit_sales_pdf(output_path: Path, input_path: Path, report: dict[str, Any],
                     date_from: date, date_to: date,
                     prefix: str = "DS", receipt_type: str = "Deposit") -> Path:
    """Account-wise PDF. write_pdf() needs the Transaction objects, so re-read them."""
    mod = credit_module()
    _, transactions = load_credit_sales(input_path)
    keys = {(r[0], r[4], r[5]) for r in report["transactions"]}
    chosen = [t for t in transactions if (t.row, t.location, t.account) in keys]
    payload = (report["summary"], report["details"], chosen, report["total"], report["warnings"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mod.write_pdf(output_path, report["company"], input_path.name, (date_from, date_to),
                      payload, prefix, receipt_type)
    except RuntimeError as exc:
        raise TallyError(str(exc)) from None
    return output_path


def credit_sales_html(report: dict[str, Any], date_from: date, date_to: date, source: str) -> str:
    """Standalone printable report, matching the desktop Print HTML output."""
    mod = credit_module()
    esc = lambda v: html.escape(mod.display(v))

    def table(headers, rows):
        return ("<table><thead><tr>"
                + "".join("<th>" + esc(v) + "</th>" for v in headers)
                + "</tr></thead><tbody>"
                + "".join("<tr>" + "".join("<td>" + esc(v) + "</td>" for v in row) + "</tr>"
                          for row in rows)
                + "</tbody></table>")

    body = '''<!doctype html><html><head><meta charset="utf-8"><title>Credit Sales Report</title>
<style>body{font:14px Arial;color:#172b4d;margin:28px}h1{font-size:24px}h2{margin-top:30px}
table{border-collapse:collapse;width:100%;font-size:12px}th{background:#233d60;color:white;text-align:left}
th,td{padding:8px;border-bottom:1px solid #ddd}tr:nth-child(even){background:#f3f6fa}li{margin:6px 0}
@media print{button{display:none}thead{display:table-header-group}tr{break-inside:avoid}h2{break-after:avoid}}
@page{size:landscape;margin:12mm}</style></head><body><button onclick="window.print()">Print / Save as PDF</button>'''
    body += ("<h1>" + esc(report["company"]) + " — Credit Sales by Location</h1><p>"
             + esc(date_from) + " to " + esc(date_to) + " | USD</p>")
    body += table(report["summary_headers"], report["summary"] + [report["total"]])
    body += "<h2>Report notes</h2><ul>" + "".join("<li>" + esc(n) + "</li>"
                                                  for n in report["warnings"]) + "</ul>"
    for loc in sorted({r[0] for r in report["details"]}):
        body += ("<h2>" + esc(loc) + "</h2>"
                 + table(report["detail_headers"][1:],
                         [r[1:] for r in report["details"] if r[0] == loc]))
    if report["excluded"]:
        body += ("<h2>Excluded deposits</h2>"
                 + table(report["tx_headers"] + ["Exclusion reason"], report["excluded"]))
    body += "<p>Source: " + esc(Path(source).name) + "</p></body></html>"
    return body
