#!/usr/bin/env python3
"""Convert a Wingate/Marietta Trial Balance PDF into a QBO journal CSV.

Normal Windows use: double-click the file to open its separate desktop window,
then select one or more PDF reports. CSV files are written next to the PDFs.
Mapped transaction codes are included when present and treated as zero when
absent. Zero-amount journal rows are omitted; report totals must still balance.

Dependency: pypdf  (install with: py -m pip install pypdf)
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError as exc:
    raise SystemExit(
        "Missing package 'pypdf'. Open Command Prompt and run:\n"
        "py -m pip install pypdf"
    ) from exc


# QuickBooks mapping from the supplied journal-entry example.
LOCATION = "Marietta Hotel"
ROOM_REVENUE_ACCOUNT = "Room Revenue"
BED_TAX_ACCOUNT = "Tax Payable:Bed Tax"
SALES_TAX_ACCOUNT = "Tax Payable:Sales tax Payable - Marietta Hotel"
BANK_ACCOUNT = "FC Bank AC# 0606"
GUEST_LEDGER_ACCOUNT = "Front Desk-Guest Ledger"

CSV_COLUMNS = [
    "Journal No.", "Journal Date", "Account", "Debits", "Credits",
    "Description", "Name", "Location",
]


@dataclass(frozen=True)
class TrialBalance:
    report_date: datetime
    room_revenue: Decimal
    bed_tax: Decimal
    sales_tax: Decimal
    payments: Decimal
    transaction_total: Decimal


def money(value: str) -> Decimal:
    """Parse report amounts such as '- 12.00' and '23,772.00'."""
    cleaned = value.replace(",", "").replace(" ", "")
    return Decimal(cleaned)


def extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    # Layout mode preserves each transaction code, description, and amount on
    # the same line, which makes Oracle Reports PDFs much more reliable.
    text = "\n".join(
        page.extract_text(extraction_mode="layout") or "" for page in reader.pages
    )
    if not text.strip():
        raise ValueError("The PDF contains no readable text (it may be scanned).")
    return text


def parse_trial_balance(text: str) -> TrialBalance:
    date_match = re.search(r"\b(\d{2}/\d{2}/\d{2})\b", text)
    if not date_match:
        raise ValueError("Report date was not found.")
    report_date = datetime.strptime(date_match.group(1), "%m/%d/%y")

    transaction_pattern = re.compile(
        r"(?m)^\s*(\d{4})\s+(.+?)\s+(-?\s*[\d,]+\.\d{2})\s*$"
    )
    transactions: dict[int, Decimal] = {}
    for code_text, _description, amount_text in transaction_pattern.findall(text):
        code = int(code_text)
        transactions[code] = transactions.get(code, Decimal("0")) + money(amount_text)

    def total(label: str) -> Decimal:
        match = re.search(
            rf"(?mi)^\s*{re.escape(label)}\s+(-?\s*[\d,]+\.\d{{2}})\s*$", text
        )
        if not match:
            raise ValueError(f"'{label}' was not found in the PDF.")
        return money(match.group(1))

    # Reports omit transaction codes with no activity. Include each code
    # when present and skip it otherwise, while retaining the balance check.
    zero = Decimal("0.00")

    result = TrialBalance(
        report_date=report_date,
        room_revenue=transactions.get(1000, zero) + transactions.get(1001, zero),
        bed_tax=transactions.get(7000, zero) + transactions.get(7001, zero),
        sales_tax=transactions.get(7002, zero) + transactions.get(7003, zero),
        payments=abs(total("Payment Total")),
        transaction_total=total("Transaction Total Today"),
    )

    credits = result.room_revenue + result.bed_tax + result.sales_tax
    debits = result.payments + result.transaction_total
    if credits != debits:
        raise ValueError(
            f"Journal is out of balance: debits {debits:.2f}, credits {credits:.2f}."
        )
    return result


def amount_cell(value: Decimal) -> str:
    return f"{value:.2f}"


def build_rows(data: TrialBalance, location: str) -> list[dict[str, str]]:
    date_short = data.report_date.strftime("%m/%d/%y")
    date_long = data.report_date.strftime("%m/%d/%Y")
    journal_no = f"Revenue{data.report_date.strftime('%m%d')}"

    def row(account: str, debit: Decimal | None = None,
            credit: Decimal | None = None) -> dict[str, str]:
        return {
            "Journal No.": journal_no,
            "Journal Date": date_long,
            "Account": account,
            "Debits": amount_cell(debit) if debit is not None else "",
            "Credits": amount_cell(credit) if credit is not None else "",
            "Description": "",
            "Name": "",
            "Location": location,
        }

    rows = [
        row(ROOM_REVENUE_ACCOUNT, credit=data.room_revenue),
        row(BED_TAX_ACCOUNT, credit=data.bed_tax),
        row(SALES_TAX_ACCOUNT, credit=data.sales_tax),
        row(BANK_ACCOUNT, debit=data.payments),
        row(GUEST_LEDGER_ACCOUNT, debit=data.transaction_total),
    ]
    return [item for item in rows
            if Decimal(item["Debits"] or "0") != 0
            or Decimal(item["Credits"] or "0") != 0]


def convert(pdf_path: Path, output_path: Path, location: str = LOCATION) -> None:
    data = parse_trial_balance(extract_pdf_text(pdf_path))
    rows = build_rows(data, location)
    with output_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def next_available_output_path(preferred_path: Path) -> Path:
    """Return a numbered filename when Windows has locked the preferred CSV."""
    number = 2
    while True:
        candidate = preferred_path.with_name(
            f"{preferred_path.stem}_{number}{preferred_path.suffix}"
        )
        if not candidate.exists():
            return candidate
        number += 1


def convert_with_locked_file_fallback(
    pdf_path: Path, output_path: Path, location: str = LOCATION
) -> Path:
    """Convert, choosing _2/_3/etc. if an open CSV cannot be overwritten."""
    try:
        convert(pdf_path, output_path, location)
        return output_path
    except PermissionError:
        alternate = next_available_output_path(output_path)
        convert(pdf_path, alternate, location)
        return alternate


def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("PrimeLedgerAI - Revenue PDF to QuickBooks CSV")
    root.geometry("760x500")
    root.minsize(680, 430)

    selected_pdfs: list[Path] = []
    location_var = tk.StringVar(value=LOCATION)
    status_var = tk.StringVar(value="Select one or more Trial Balance PDF files.")

    style = ttk.Style(root)
    style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
    style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
    style.configure("Action.TButton", font=("Segoe UI", 10, "bold"), padding=8)

    outer = ttk.Frame(root, padding=22)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="Revenue PDF to QuickBooks CSV",
              style="Title.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text="Select hotel Trial Balance PDFs and create balanced journal-entry CSV files.",
        style="Subtitle.TLabel",
    ).pack(anchor="w", pady=(2, 18))

    location_frame = ttk.Frame(outer)
    location_frame.pack(fill="x", pady=(0, 12))
    ttk.Label(location_frame, text="QuickBooks location:").pack(side="left")
    ttk.Entry(location_frame, textvariable=location_var, width=35).pack(
        side="left", padx=(10, 0)
    )

    ttk.Label(outer, text="Selected PDF files:").pack(anchor="w")
    list_frame = ttk.Frame(outer)
    list_frame.pack(fill="both", expand=True, pady=(5, 12))
    file_list = tk.Listbox(list_frame, font=("Segoe UI", 10), selectmode="extended")
    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=file_list.yview)
    file_list.configure(yscrollcommand=scrollbar.set)
    file_list.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def refresh_list() -> None:
        file_list.delete(0, tk.END)
        for path in selected_pdfs:
            file_list.insert(tk.END, str(path))
        status_var.set(
            f"{len(selected_pdfs)} PDF file(s) selected."
            if selected_pdfs else "Select one or more Trial Balance PDF files."
        )

    def select_files() -> None:
        paths = filedialog.askopenfilenames(
            parent=root,
            title="Select Trial Balance PDF file(s)",
            filetypes=[("PDF files", "*.pdf")],
        )
        for item in paths:
            path = Path(item)
            if path not in selected_pdfs:
                selected_pdfs.append(path)
        refresh_list()

    def clear_files() -> None:
        selected_pdfs.clear()
        refresh_list()

    def convert_selected() -> None:
        if not selected_pdfs:
            messagebox.showwarning("No PDF selected", "Please select at least one PDF file.")
            return
        location = location_var.get().strip()
        if not location:
            messagebox.showwarning("Location required", "Please enter the QuickBooks location.")
            return

        created: list[Path] = []
        errors: list[str] = []
        status_var.set("Converting PDF files...")
        root.update_idletasks()
        for pdf_path in selected_pdfs:
            output_path = pdf_path.with_name(pdf_path.stem + "_QuickBooks.csv")
            try:
                actual_output = convert_with_locked_file_fallback(
                    pdf_path, output_path, location
                )
                created.append(actual_output)
            except Exception as exc:
                errors.append(f"{pdf_path.name}: {exc}")

        if errors:
            status_var.set("Conversion completed with errors.")
            messagebox.showerror("Conversion error", "\n\n".join(errors))
        else:
            status_var.set(f"Created {len(created)} balanced CSV file(s).")
            messagebox.showinfo(
                "Conversion complete",
                "CSV file(s) created next to the PDF file(s):\n\n"
                + "\n".join(path.name for path in created),
            )

    buttons = ttk.Frame(outer)
    buttons.pack(fill="x")
    ttk.Button(buttons, text="Select PDF Files", command=select_files,
               style="Action.TButton").pack(side="left")
    ttk.Button(buttons, text="Clear", command=clear_files).pack(side="left", padx=8)
    ttk.Button(buttons, text="Create QuickBooks CSV", command=convert_selected,
               style="Action.TButton").pack(side="right")

    ttk.Separator(outer).pack(fill="x", pady=14)
    bottom = ttk.Frame(outer)
    bottom.pack(fill="x")
    ttk.Label(bottom, textvariable=status_var).pack(side="left")

    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="*", type=Path)
    parser.add_argument("-o", "--output", type=Path,
                        help="Output path (only when converting one PDF)")
    parser.add_argument("--location", default=LOCATION)
    args = parser.parse_args()

    if not args.pdfs:
        return run_gui()
    pdfs = [Path(p) for p in args.pdfs]
    if args.output and len(pdfs) != 1:
        parser.error("--output can only be used with one PDF")

    created: list[Path] = []
    errors: list[str] = []
    for pdf_path in pdfs:
        output_path = args.output or pdf_path.with_name(pdf_path.stem + "_QuickBooks.csv")
        try:
            actual_output = convert_with_locked_file_fallback(
                pdf_path, output_path, args.location
            )
            created.append(actual_output)
        except Exception as exc:
            errors.append(f"{pdf_path.name}: {exc}")

    if errors:
        print("Could not convert:\n\n" + "\n".join(errors), file=sys.stderr)
        return 1

    print("CSV file(s) created:\n\n" + "\n".join(str(p) for p in created))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
