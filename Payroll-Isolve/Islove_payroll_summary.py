#!/usr/bin/env python3
"""Convert an iSolved Payroll Summary PDF into a QuickBooks journal CSV.

Install once:  py -m pip install pdfplumber
Run:           py Islove_payroll_summary.py
Command line:  py Islove_payroll_summary.py payroll.pdf --locations locations.csv

Select multiple PDFs with Ctrl/Shift. One combined CSV is created.
The supplied location list is built in; no location selection is needed.
Journal numbers use isMMDD-location number, for example is0909-9697.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    pdfplumber = None


MONEY = Decimal("0.01")
ACCOUNT_PAYROLL_EXPENSE = "Payroll:Payroll expenses"
ACCOUNT_EMPLOYER_TAX = "Payroll:Salaries & Wages-payroll tax:Payroll tax - Employer"
ACCOUNT_PAYROLL_PAYABLE = "Payroll Payable"
ACCOUNT_FUNDING = "Allstate CPA - Payroll"

# Exact names from the supplied Marietta Hotel_Location List(4).csv.
DEFAULT_LOCATIONS = (
    "5164-Berkshire OH", "5165-Perrysburg OH", "5166-Jeffersonville",
    "5167-Elizabethtown KY", "5168-Catlettsburg KY", "5169-Columbus OH",
    "7401-Youngstown", "9690 - Marietta Rest", "9697-Findley",
    "Common Exp", "Marietta Hotel", "Welcome Diner",
)


@dataclass(frozen=True)
class PayrollData:
    period_begin: datetime
    period_end: datetime
    pay_group: str
    payroll_billing: Decimal
    total_funding: Decimal
    employer_tax: Decimal


def money(value: str) -> Decimal:
    return Decimal(value.replace("$", "").replace(",", "").strip()).quantize(MONEY)


def first(pattern: str, text: str, label: str, flags: int = re.IGNORECASE) -> str:
    match = re.search(pattern, text, flags)
    if not match:
        raise ValueError(f"Could not find {label} in the PDF.")
    return match.group(1).strip()


def extract_pdf_text(pdf_path: Path) -> tuple[str, list[str]]:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber is not installed. Run: py -m pip install pdfplumber")
    with pdfplumber.open(pdf_path) as pdf:
        pages = [(page.extract_text(x_tolerance=2, y_tolerance=3) or "") for page in pdf.pages]
    if not any(pages):
        raise ValueError("No readable text was found. This may be a scanned PDF.")
    return "\n".join(pages), pages


def parse_pdf(pdf_path: Path) -> PayrollData:
    text, pages = extract_pdf_text(pdf_path)
    begin = first(r"Period Begin Date:\s*(\d{1,2}/\d{1,2}/\d{4})", text, "period begin date")
    end = first(r"Period End Date:\s*(\d{1,2}/\d{1,2}/\d{4})", text, "period end date")
    pay_group = first(r"Pay Group:[ \t]*([^\n]+)", text, "pay group")
    pay_group = re.split(r"\s+Period End Date:", pay_group, flags=re.I)[0].strip()
    # Text extraction merges the company column into the Pay Group line.
    client = first(r"Client ID:[ \t]*([^\n]+)", text, "client ID")
    client = re.split(r"\s+Period Begin Date:", client, flags=re.I)[0].strip()
    company = re.split(r"\s+-\s+", client, maxsplit=1)[-1].strip()
    if company and pay_group.casefold().endswith(company.casefold()):
        pay_group = pay_group[:-len(company)].strip()
    billing_line = first(r"(Payroll Billing[^\n]+)", text, "payroll billing line")
    billing_values = re.findall(r"\$?([\d,]+\.\d{2})", billing_line)
    if not billing_values:
        raise ValueError("Could not find payroll billing in the PDF.")
    billing = billing_values[-1]
    funding = first(r"Total Payroll Funding \(all items\):[^\n$]*\$?([\d,]+\.\d{2})", text, "total payroll funding")

    # The requested value is specifically on page 3 under EMPLOYER TAX EXPENSE.
    employer_page = next((p for p in pages if "EMPLOYER TAX EXPENSE" in p.upper()), "")
    if not employer_page:
        raise ValueError("Could not find the EMPLOYER TAX EXPENSE page.")
    employer = first(r"Total Employer Tax\s+([\d,]+\.\d{2})", employer_page, "page 3 total employer tax")

    return PayrollData(
        datetime.strptime(begin, "%m/%d/%Y"),
        datetime.strptime(end, "%m/%d/%Y"),
        pay_group,
        money(billing),
        money(funding),
        money(employer),
    )


def load_locations(path: Path) -> list[str]:
    """Read a QuickBooks export, skipping title rows above its header."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    rows = list(csv.reader(text.splitlines()))
    for i, row in enumerate(rows):
        for column, value in enumerate(row):
            if value.strip().casefold() in {"location full name", "location", "location name"}:
                names = list(dict.fromkeys(
                    r[column].strip() for r in rows[i + 1:]
                    if len(r) > column and r[column].strip()
                ))
                if names:
                    return names
    raise ValueError("No locations found. Select a QuickBooks Location List CSV with a 'Location full name' header.")


def location_key(value: str) -> str:
    value = re.sub(r"^\s*\d+\s*[-_ ]+", "", value.casefold())
    value = re.sub(r"[,_–—-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+(?:oh|ohio|ky|kentucky|pa|pennsylvania)$", "", value)
    # Explicit known payroll/QuickBooks spelling difference; no fuzzy matching.
    return {"jefffersonville": "jeffersonville", "marietta hotels llc": "marietta hotel", "findlay": "findley", "marietta rest": "marietta restaurant", "marietta": "marietta restaurant"}.get(value, value)


def clean_location(pdf_path: Path, pay_group: str, locations: list[str]) -> str:
    name = re.sub(r"^\s*(?:bi[ -]?weekly|weekly|semi[ -]?monthly|monthly)(?:\s*[-:–—]\s*|\s+)", "", pay_group, flags=re.I)
    matches = [loc for loc in locations if location_key(loc) == location_key(name)]
    # A filename store number can confirm a match, but cannot override the PDF.
    file_match = re.match(r"^\s*(\d{3,})\s*[-_ ]", pdf_path.stem)
    if len(matches) == 1:
        location = matches[0]
        location_code = re.match(r"^(\d+)\s*[-_ ]", location)
        if file_match and location_code and file_match[1] != location_code[1]:
            raise ValueError(f"Filename location {file_match[1]} conflicts with PDF pay group '{pay_group}' ({location}).")
        return location
    reason = "Multiple locations match" if matches else "No location matches"
    raise ValueError(f"{reason} PDF pay group '{pay_group}'. Update/select your Location List CSV. Available locations: " + "; ".join(locations))


def build_rows(data: PayrollData, pdf_path: Path, locations: list[str]) -> list[dict[str, str]]:
    location = clean_location(pdf_path, data.pay_group, locations)
    journal_date = data.period_end.strftime("%m/%d/%Y")
    location_number = re.match(r"^(\d+)\s*[-_ ]", location)
    location_code = "MH" if location == "Marietta Hotel" else (location_number[1] if location_number else None)
    if not location_code:
        raise ValueError(f"Location '{location}' has no location number. Update the Location List CSV to include its number before exporting.")
    journal_no = f"is{data.period_end:%m%d}-{location_code}"
    description = f"{data.period_begin:%m/%d/%Y} {data.period_end:%m/%d/%Y}"
    payable = (data.total_funding - data.payroll_billing - data.employer_tax).quantize(MONEY)
    if payable < 0:
        raise ValueError("Calculated Payroll Payable is negative; please review the PDF values.")

    def row(account: str, debit: Decimal | None = None, credit: Decimal | None = None):
        return {
            "Journal no.": journal_no,
            "Journal date": journal_date,
            "Account": account,
            "Debits": f"{debit:.2f}" if debit is not None else "",
            "Credits": f"{credit:.2f}" if credit is not None else "",
            "Description": description,
            "Name": "",
            "Location": location,
        }

    rows = [
        row(ACCOUNT_PAYROLL_EXPENSE, debit=data.payroll_billing),
        row(ACCOUNT_EMPLOYER_TAX, debit=data.employer_tax),
        row(ACCOUNT_PAYROLL_PAYABLE, debit=payable),
        row(ACCOUNT_FUNDING, credit=data.total_funding),
    ]
    debits = sum((Decimal(r["Debits"]) for r in rows if r["Debits"]), Decimal("0"))
    credits = sum((Decimal(r["Credits"]) for r in rows if r["Credits"]), Decimal("0"))
    if debits != credits:
        raise AssertionError(f"Journal is not balanced: debits {debits}, credits {credits}")
    return rows


def convert_many(pdf_paths: list[Path], csv_path: Path | None = None,
                 locations_path: Path | None = None) -> Path:
    if not pdf_paths:
        raise ValueError("Select at least one payroll PDF.")
    paths = [p.resolve() for p in pdf_paths]
    if len(set(paths)) != len(paths):
        raise ValueError("The same PDF was selected more than once.")
    locations = load_locations(locations_path) if locations_path else list(DEFAULT_LOCATIONS)
    default_name = f"{paths[0].stem}_journal.csv" if len(paths) == 1 else "Payroll_Combined_Journal.csv"
    destination = (csv_path or paths[0].with_name(default_name)).resolve()
    if destination in paths or (locations_path and destination == locations_path.resolve()):
        raise ValueError("Choose an output CSV different from your input files.")
    rows = []
    seen = {}
    errors = []
    for path in paths:
        try:
            batch = build_rows(parse_pdf(path), path, locations)
            number = batch[0]["Journal no."]
            if number in seen:
                raise ValueError(f"Duplicate journal number {number}, also in {seen[number]}. Select only one report for each journal number.")
            seen[number] = path.name
            rows.extend(batch)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    if errors:
        raise ValueError("No CSV was written. Correct these files and try again:\n\n" + "\n\n".join(errors))
    # All PDFs must validate before touching the requested output.
    import os
    import tempfile
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8-sig",
                                         dir=destination.parent, suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return destination


def convert(pdf_path: Path, csv_path: Path | None = None, locations_path: Path | None = None) -> Path:
    return convert_many([pdf_path], csv_path, locations_path)


def choose_with_gui() -> tuple[list[Path], Path | None, Path | None]:
    import tkinter as tk
    from tkinter import filedialog, messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askopenfilenames(parent=root, title="Select payroll PDFs (Ctrl/Shift for multiple)", filetypes=[("PDF files", "*.pdf")])
        if not selected:
            return [], None, None
        locations = None
        default = f"{Path(selected[0]).stem}_journal.csv" if len(selected) == 1 else "Payroll_Combined_Journal.csv"
        destination = filedialog.asksaveasfilename(parent=root, title="Save combined Journal CSV", defaultextension=".csv", initialfile=default, filetypes=[("CSV files", "*.csv")])
        return [Path(p) for p in selected], Path(destination) if destination else None, locations
    finally:
        root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert one or multiple iSolved Payroll Summary PDFs to a combined journal CSV")
    parser.add_argument("pdf", nargs="*", type=Path, help="input Payroll Summary PDF(s)")
    parser.add_argument("-o", "--output", type=Path, help="output combined CSV path")
    parser.add_argument("-l", "--locations", type=Path, help="optional updated QuickBooks Location List CSV")
    args = parser.parse_args()
    gui = not args.pdf
    try:
        pdf_paths, csv_path, locations_path = (args.pdf, args.output, args.locations) if not gui else choose_with_gui()
        if not pdf_paths or (gui and csv_path is None):
            return 0
        result = convert_many(pdf_paths, csv_path, locations_path)
        message = f"Converted {len(pdf_paths)} payroll PDF(s).\nCombined CSV created successfully:\n{result}"
        print(message)
        if gui:
            from tkinter import messagebox
            messagebox.showinfo("Payroll Converter", message)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if gui:
            from tkinter import messagebox
            messagebox.showerror("Payroll Converter", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
