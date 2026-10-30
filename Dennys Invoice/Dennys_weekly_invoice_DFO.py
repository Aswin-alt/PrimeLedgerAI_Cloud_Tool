"""Denny's DFO PDF to QuickBooks journal CSV mapper.

Desktop GUI for Windows/macOS/Linux. The user chooses a PDF, selects invoice
rows, assigns QuickBooks accounts, and exports a balanced Excel journal.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import pdfplumber
except ImportError as exc:  # friendly startup message
    raise SystemExit(
        "Required package is missing. Run: pip install pdfplumber"
    ) from exc


APP_DIR = Path.home() / ".denny_pdf_mapper"
CONFIG_FILE = APP_DIR / "mapping.json"
COLUMN_MAPPING_FILE = APP_DIR / "column_mapping.json"

DEFAULT_RULES = [
    ("royalty", "Marketing & Franchise Fees:Franchise Royalties", True),
    ("advertising", "Marketing & Franchise Fees:Marketing Fees", True),
    ("additional bbf investment", "Marketing & Franchise Fees:BBF Investment Addtl", True),
    ("bbf investment", "Marketing & Franchise Fees:BBF Investment Addtl", True),
    ("help desk support", "Marketing & Franchise Fees:Technology Fee", True),
    ("pilot / flying j percentage rent", "Occupancy:Rent", True),
]

CSV_COLUMNS = ["Journal Date", "Journal No.", "Account", "Debits", "Credits", "Description", "Location"]
SOURCE_OPTIONS = [
    "Journal Date", "Journal No.", "Account", "Debit Amount", "Credit Amount",
    "Invoice Number(s)", "PDF Description", "Vendor Name", "Restaurant #", "Location", "Blank",
]
DEFAULT_COLUMN_MAPPING = {
    "Journal Date": "Journal Date", "Journal No.": "Journal No.", "Account": "Account",
    "Debits": "Debit Amount", "Credits": "Credit Amount", "Description": "PDF Description",
    "Location": "Location",
}

QUICKBOOKS_LOCATIONS = [
    "5164-Berkshire OH",
    "5165-Perrysburg OH",
    "5166-Jeffersonville",
    "5167-Elizabethtown KY",
    "5168-Catlettsburg KY",
    "5169-Columbus OH",
    "7401-Youngstown",
    "9690 - Marietta Rest",
    "9697-Findley",
]


def location_for_restaurant(restaurant: str) -> str:
    """Match a PDF restaurant number to a QuickBooks location by its final four digits."""
    digits = re.sub(r"\D", "", restaurant)
    for location in QUICKBOOKS_LOCATIONS:
        code_match = re.match(r"\s*(\d{4})", location)
        if code_match and digits.endswith(code_match.group(1)):
            return location
    return ""


def journal_number(date_text: str, location: str = "") -> str:
    """Return Dinv + MMDD + location code, for example Dinv0904-5164."""
    date_part = ""
    for date_format in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            date_part = datetime.strptime(date_text, date_format).strftime("%m%d")
            break
        except ValueError:
            continue
    location_match = re.match(r"\s*(\d{4})", location)
    location_part = f"-{location_match.group(1)}" if location_match else ""
    return "Dinv" + date_part + location_part

MONEY_RE = re.compile(r"\$?\(?([0-9][0-9,]*\.\d{2})\)?")
INVOICE_RE = re.compile(r"\b(IV[- ]?\d+)\b", re.I)
DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
RESTAURANT_RE = re.compile(r"(?:Restaurant|Restaurant\s*#)\s*#?\s*(\d{4,8})", re.I)


@dataclass
class InvoiceRow:
    selected: bool
    invoice: str
    description: str
    amount: Decimal
    account: str
    page: int
    source_file: str = ""
    journal_date: str = ""
    journal_no: str = ""
    restaurant: str = ""
    location: str = ""
    page_tax: str = "0.00"


def money(value: str | Decimal) -> Decimal:
    try:
        return Decimal(str(value).replace("$", "").replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid amount: {value}") from exc


def _sort_rules(rules: list[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
    """Put the most specific description rules first.

    This is important for descriptions such as "Help Desk Support - Software":
    the saved specific rule must be checked before the generic "Help Desk Support" rule.
    """
    return sorted(rules, key=lambda r: len(r[0]), reverse=True)


def default_mapping(description: str) -> tuple[str, bool]:
    lowered = " ".join(description.lower().split())
    for keyword, account, auto_select in _sort_rules(DEFAULT_RULES):
        if keyword in lowered:
            return account, auto_select
    return "", False


def load_rules() -> list[tuple[str, str, bool]]:
    global DEFAULT_RULES
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            DEFAULT_RULES = [
                (
                    str(x["keyword"]).lower(),
                    "Marketing & Franchise Fees:BBF Investment Addtl"
                    if str(x["account"]) == "Marketing & Franchise Fees:BBF Investment Adc"
                    else str(x["account"]),
                    bool(x.get("auto_select", True)),
                )
                for x in raw
            ]
            # Upgrade earlier saved mappings to the audited chart-of-accounts names.
            DEFAULT_RULES = [
                (
                    keyword,
                    "Marketing & Franchise Fees:Franchise Royalties"
                    if keyword == "royalty" and account == "Marketing & Franchise Fees:Royalty Fee"
                    else "Marketing & Franchise Fees:Marketing Fees"
                    if keyword == "advertising" and account == "Marketing & Franchise Fees"
                    else account,
                    auto_select,
                )
                for keyword, account, auto_select in DEFAULT_RULES
            ]
        except Exception:
            pass
    DEFAULT_RULES = _sort_rules(DEFAULT_RULES)
    return DEFAULT_RULES


def save_rule(keyword: str, account: str) -> None:
    global DEFAULT_RULES
    keyword = keyword.strip().lower()
    if not keyword or not account.strip():
        return
    DEFAULT_RULES = [r for r in DEFAULT_RULES if r[0] != keyword]
    DEFAULT_RULES.append((keyword, account.strip(), True))
    DEFAULT_RULES = _sort_rules(DEFAULT_RULES)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(
            [{"keyword": k, "account": a, "auto_select": s} for k, a, s in DEFAULT_RULES],
            indent=2,
        ),
        encoding="utf-8",
    )


def load_column_mapping() -> dict[str, str]:
    mapping = dict(DEFAULT_COLUMN_MAPPING)
    if COLUMN_MAPPING_FILE.exists():
        try:
            saved = json.loads(COLUMN_MAPPING_FILE.read_text(encoding="utf-8"))
            for column in CSV_COLUMNS:
                if saved.get(column) in SOURCE_OPTIONS:
                    mapping[column] = saved[column]
        except Exception:
            pass
    # Description must contain the combined invoice number and extracted PDF text.
    mapping["Description"] = "PDF Description"
    return mapping


def save_column_mapping(mapping: dict[str, str]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    COLUMN_MAPPING_FILE.write_text(json.dumps(mapping, indent=2), encoding="utf-8")


def parse_pdf(path: str) -> tuple[list[InvoiceRow], dict[str, str]]:
    rows: list[InvoiceRow] = []
    metadata = {"date": "", "restaurant": "", "invoice": "", "tax": "0.00", "total": "0.00"}

    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=2, y_tolerance=3, layout=True) or ""
            flat = " ".join(text.split())
            page_date_match = DATE_RE.search(flat)
            page_restaurant_match = RESTAURANT_RE.search(flat)
            page_date = page_date_match.group(1) if page_date_match else metadata["date"]
            page_restaurant = page_restaurant_match.group(1) if page_restaurant_match else ""
            if not metadata["date"]:
                metadata["date"] = page_date
            if not metadata["restaurant"]:
                metadata["restaurant"] = page_restaurant

            lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
            page_rows: list[InvoiceRow] = []
            current = ""
            for line in lines + ["__END__"]:
                starts_invoice = bool(INVOICE_RE.search(line))
                if starts_invoice or line == "__END__":
                    if current:
                        parsed = parse_invoice_line(current, page_no)
                        if parsed:
                            page_rows.append(parsed)
                    current = line if starts_invoice else ""
                elif current:
                    # Wrapped descriptions are continued until the next invoice row.
                    if re.search(r"\b(Net Amount|Tax|TOTAL)\b", line, re.I):
                        parsed = parse_invoice_line(current, page_no)
                        if parsed:
                            page_rows.append(parsed)
                        current = ""
                    else:
                        current += " " + line

            page_tax = "0.00"
            for line in lines:
                amounts = MONEY_RE.findall(line)
                if re.search(r"^Tax\s*:?", line, re.I) and amounts:
                    page_tax = amounts[-1]
                    if metadata["tax"] == "0.00":
                        metadata["tax"] = page_tax
                if re.search(r"^TOTAL\s*", line, re.I) and amounts:
                    metadata["total"] = amounts[-1]

            display_date = page_date
            if display_date:
                try:
                    display_date = datetime.strptime(display_date, "%Y-%m-%d").strftime("%m/%d/%Y")
                except ValueError:
                    pass
            for parsed in page_rows:
                parsed.journal_date = display_date
                parsed.restaurant = page_restaurant
                parsed.location = location_for_restaurant(page_restaurant)
                parsed.journal_no = journal_number(page_date, parsed.location)
                parsed.page_tax = page_tax
            rows.extend(page_rows)

    if rows and not metadata["invoice"]:
        metadata["invoice"] = rows[0].invoice
    # De-duplicate only exact repeated extraction artifacts.
    unique: list[InvoiceRow] = []
    seen: set[tuple] = set()
    for row in rows:
        key = (row.invoice, row.description, row.amount, row.page)
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique, metadata


def parse_invoice_line(line: str, page_no: int) -> InvoiceRow | None:
    inv_match = INVOICE_RE.search(line)
    values = list(MONEY_RE.finditer(line))
    if not inv_match or not values:
        return None
    # The invoice number itself can resemble a number; only accept decimal money.
    last = values[-1]
    try:
        amount = money(last.group(1))
    except ValueError:
        return None
    description = line[inv_match.end():last.start()]
    # Remove a possible Sales amount and Rate immediately before the final Amount.
    description = re.sub(r"\$?[0-9][0-9,]*\.\d{2}\s+[0-9.]+%\s*$", "", description).strip(" -")
    description = " ".join(description.split())
    if not description or re.search(r"invoice\s+number", description, re.I):
        return None
    account, auto = default_mapping(description)
    return InvoiceRow(auto, inv_match.group(1).replace(" ", "-").upper(), description, amount, account, page_no)


class MapperApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Denny's PDF to QuickBooks Journal Mapper")
        self.geometry("1380x820")
        self.minsize(980, 620)
        load_rules()
        self.column_mapping = load_column_mapping()
        self.rows: list[InvoiceRow] = []
        self.pdf_paths: list[str] = []
        self.documents: dict[str, dict[str, str]] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Button(top, text="1. Select Invoice PDFs", command=self.open_pdf).pack(side="left")
        self.file_label = ttk.Label(top, text="No PDFs selected", padding=(10, 0))
        self.file_label.pack(side="left", fill="x", expand=True)

        info = ttk.LabelFrame(self, text="Journal information", padding=8)
        info.pack(fill="x", padx=10)
        self.vars = {
            "date": tk.StringVar(), "journal": tk.StringVar(), "location": tk.StringVar(),
            "restaurant": tk.StringVar(),
            "credit": tk.StringVar(value="Denny's Inc"), "tax": tk.StringVar(value="0.00"),
            "tax_account": tk.StringVar(value="Marketing & Franchise Fees:Technology Fee"),
        }
        labels = [("Date", "date", 12), ("Journal no.", "journal", 22),
                  ("Credit account", "credit", 26), ("Invoice tax", "tax", 12)]
        for col, (label, key, width) in enumerate(labels):
            ttk.Label(info, text=label).grid(row=0, column=col, sticky="w", padx=3)
            ttk.Entry(info, textvariable=self.vars[key], width=width).grid(row=1, column=col, sticky="ew", padx=3)
        info.columnconfigure(1, weight=1)
        info.columnconfigure(2, weight=1)

        mapping_frame = ttk.LabelFrame(self, text="CSV Column Mapping", padding=8)
        mapping_frame.pack(fill="x", padx=10, pady=(8, 0))
        self.column_vars: dict[str, tk.StringVar] = {}
        for col, excel_column in enumerate(CSV_COLUMNS):
            ttk.Label(mapping_frame, text=excel_column).grid(row=0, column=col, sticky="w", padx=3)
            variable = tk.StringVar(value=self.column_mapping[excel_column])
            self.column_vars[excel_column] = variable
            ttk.Combobox(
                mapping_frame, textvariable=variable, values=SOURCE_OPTIONS,
                state="readonly", width=17,
            ).grid(row=1, column=col, sticky="ew", padx=3)
            mapping_frame.columnconfigure(col, weight=1)
        ttk.Button(mapping_frame, text="Save All Column Mapping", command=self.save_all_column_mapping).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=3, pady=(7, 0)
        )

        table_frame = ttk.Frame(self, padding=(10, 8))
        table_frame.pack(fill="both", expand=True)
        columns = ("use", "file", "restaurant", "location", "page", "invoice", "description", "amount", "account")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
        headings = {"use": "Use", "file": "PDF file", "restaurant": "Restaurant #",
                    "location": "Location", "page": "Page", "invoice": "Invoice",
                    "description": "PDF description", "amount": "Amount", "account": "QuickBooks account"}
        widths = {"use": 50, "file": 135, "restaurant": 95, "location": 175, "page": 45,
                  "invoice": 95, "description": 300, "amount": 85, "account": 285}
        for c in columns:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], anchor="e" if c == "amount" else "w")
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.toggle_row)

        controls = ttk.Frame(self, padding=(10, 0, 10, 5))
        controls.pack(fill="x")
        ttk.Button(controls, text="Select / unselect rows", command=self.toggle_selected).pack(side="left")
        ttk.Label(controls, text="Account:").pack(side="left", padx=(15, 4))
        self.account_var = tk.StringVar()
        self.account_box = ttk.Combobox(controls, textvariable=self.account_var, width=48)
        self.account_box["values"] = sorted({a for _, a, _ in DEFAULT_RULES})
        self.account_box.pack(side="left", fill="x", expand=True)
        ttk.Button(controls, text="Apply account", command=self.apply_account).pack(side="left", padx=5)
        ttk.Button(controls, text="Save mapping rule", command=self.save_mapping_dialog).pack(side="left")

        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill="x")
        self.status = ttk.Label(bottom, text="Select a PDF to begin.")
        self.status.pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="2. Export Balanced CSV Journal", command=self.export_csv).pack(side="right")

    def open_pdf(self) -> None:
        paths = filedialog.askopenfilenames(title="Select all DFO invoice PDFs", filetypes=[("PDF files", "*.pdf")])
        if not paths:
            return
        all_rows: list[InvoiceRow] = []
        documents: dict[str, dict[str, str]] = {}
        errors: list[str] = []
        for path in paths:
            try:
                pdf_rows, meta = parse_pdf(path)
            except Exception as exc:
                errors.append(f"{Path(path).name}: {exc}")
                continue
            date = meta["date"]
            if date:
                try:
                    date = datetime.strptime(date, "%Y-%m-%d").strftime("%m/%d/%Y")
                except ValueError:
                    pass
            document_key = str(Path(path).resolve())
            documents[document_key] = {
                "file": Path(path).name, "date": date, "tax": meta["tax"],
            }
            for row in pdf_rows:
                row.source_file = document_key
                if not row.journal_date:
                    row.journal_date = date
                if not row.restaurant:
                    row.restaurant = meta["restaurant"]
                    row.location = location_for_restaurant(row.restaurant)
                if not row.journal_no:
                    row.journal_no = journal_number(meta["date"], row.location)
            all_rows.extend(pdf_rows)
        if not all_rows:
            messagebox.showerror("PDF error", "No invoice rows could be read.\n" + "\n".join(errors))
            return
        self.rows = all_rows
        self.documents = documents
        self.pdf_paths = list(paths)
        self.file_label.config(text=f"{len(documents)} PDFs selected")
        first_row = all_rows[0]
        if len({(r.source_file, r.page) for r in all_rows}) == 1:
            self.vars["date"].set(first_row.journal_date)
            self.vars["journal"].set(first_row.journal_no)
            self.vars["tax"].set(first_row.page_tax)
        else:
            for key in ("date", "journal"):
                self.vars[key].set("Multiple - see invoice rows")
            self.vars["tax"].set("Per page")
        self.refresh()
        message = f"Loaded {len(documents)} PDFs and found {len(self.rows)} invoice rows."
        if errors:
            message += f" {len(errors)} PDF(s) could not be read."
        self.status.config(text=message)

    def save_all_column_mapping(self) -> None:
        self.column_mapping = {column: self.column_vars[column].get() for column in CSV_COLUMNS}
        save_column_mapping(self.column_mapping)
        messagebox.showinfo("Mapping saved", "All Excel column mappings were saved.")

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(i), values=(
                "YES" if row.selected else "", Path(row.source_file).name, row.restaurant,
                row.location or "NOT MAPPED", row.page, row.invoice, row.description,
                f"{row.amount:,.2f}", row.account,
            ))
        self.update_status()

    def selected_indices(self) -> list[int]:
        return [int(i) for i in self.tree.selection()]

    def toggle_row(self, event=None) -> None:
        item = self.tree.identify_row(event.y) if event else ""
        if item:
            self.rows[int(item)].selected = not self.rows[int(item)].selected
            self.refresh()

    def toggle_selected(self) -> None:
        for i in self.selected_indices():
            self.rows[i].selected = not self.rows[i].selected
        self.refresh()

    def apply_account(self) -> None:
        account = self.account_var.get().strip()
        if not account:
            messagebox.showwarning("Account required", "Enter or choose a QuickBooks account.")
            return
        for i in self.selected_indices():
            self.rows[i].account = account
            self.rows[i].selected = True
        self.refresh()

    def save_mapping_dialog(self) -> None:
        indices = self.selected_indices()
        account = self.account_var.get().strip()
        if not indices or not account:
            messagebox.showinfo("Save mapping", "Select one or more rows and enter the QuickBooks account first.")
            return

        # Save every selected PDF description, not only the first row.
        # These rules are written to the user's permanent config folder and
        # are automatically loaded the next time the program starts.
        saved_descriptions = []
        for i in indices:
            description = self.rows[i].description.strip()
            if description:
                save_rule(description, account)
                self.rows[i].account = account
                self.rows[i].selected = True
                saved_descriptions.append(description)

        self.account_box["values"] = sorted({a for _, a, _ in DEFAULT_RULES})
        self.refresh()
        messagebox.showinfo(
            "Mapping saved permanently",
            f"Saved {len(saved_descriptions)} description mapping(s).\n\n"
            "They will be loaded automatically every time this program opens.",
        )

    def selected_total(self) -> Decimal:
        return sum((r.amount for r in self.rows if r.selected), Decimal("0.00"))

    def update_status(self) -> None:
        chosen = [r for r in self.rows if r.selected]
        missing = sum(1 for r in chosen if not r.account.strip())
        self.status.config(text=f"Selected: {len(chosen)} | Debit before tax: ${self.selected_total():,.2f} | Missing accounts: {missing}")

    def export_csv(self) -> None:
        chosen = [r for r in self.rows if r.selected]
        if not chosen:
            messagebox.showwarning("Nothing selected", "Select at least one invoice row.")
            return
        missing = [r.description for r in chosen if not r.account.strip()]
        if missing:
            messagebox.showwarning("Account missing", "Assign an account to every selected row.")
            return
        unmapped = sorted({r.restaurant for r in chosen if not r.location})
        if unmapped:
            messagebox.showwarning(
                "Location missing",
                "These restaurant numbers do not have a location mapping:\n" + "\n".join(unmapped),
            )
            return
        output = filedialog.asksaveasfilename(
            title="Save QuickBooks journal CSV", defaultextension=".csv",
            initialfile="Denny_Invoice_Combined_Journals.csv",
            filetypes=[("CSV file", "*.csv")],
        )
        if not output:
            return
        debit_total = self._write_csv(output, chosen)
        messagebox.showinfo(
            "CSV created",
            f"Combined journal created for {len({(r.source_file, r.page) for r in chosen})} invoice pages:\n{output}"
            f"\n\nDebits = Credits = ${debit_total:,.2f}",
        )

    def _write_csv(self, output: str, chosen: list[InvoiceRow]) -> Decimal:
        self.column_mapping = {column: self.column_vars[column].get() for column in CSV_COLUMNS}
        save_column_mapping(self.column_mapping)
        output_rows: list[list[object]] = []
        grand_total = Decimal("0.00")
        # Combine multiple PDF files/pages into one journal when date and location match.
        journal_keys = list(dict.fromkeys((r.journal_date, r.location, r.journal_no) for r in chosen))
        for journal_date, location, journal_no in journal_keys:
            document_rows = [
                r for r in chosen
                if (r.journal_date, r.location, r.journal_no) == (journal_date, location, journal_no)
            ]
            first_row = document_rows[0]
            meta = {
                "file": ", ".join(sorted({Path(r.source_file).name for r in document_rows})),
                "date": journal_date, "journal": journal_no,
                "restaurant": first_row.restaurant, "location": location,
            }
            grouped: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
            invoices: dict[str, set[str]] = defaultdict(set)
            for row in document_rows:
                grouped[row.account] += row.amount
                invoices[row.account].add(row.invoice)
            tax = Decimal("0.00")
            seen_pages: set[tuple[str, int]] = set()
            for row in document_rows:
                page_key = (row.source_file, row.page)
                if page_key in seen_pages:
                    continue
                seen_pages.add(page_key)
                try:
                    tax += money(row.page_tax or "0")
                except ValueError:
                    pass
            if tax:
                grouped[self.vars["tax_account"].get().strip() or "Tax Expense"] += tax
            document_total = sum(grouped.values(), Decimal("0.00"))
            grand_total += document_total
            all_invoices = sorted({r.invoice for r in document_rows})
            for account, amount in grouped.items():
                invoice_text = ", ".join(sorted(invoices.get(account, set())) or all_invoices)
                descriptions = "; ".join(
                    dict.fromkeys(
                        f"{r.invoice} - {r.description}"
                        for r in document_rows if r.account == account
                    )
                )
                values = self.output_values(meta, account, float(amount), None, invoice_text, descriptions)
                output_rows.append([values[self.column_mapping[column]] for column in CSV_COLUMNS])
            values = self.output_values(
                meta, self.vars["credit"].get().strip(), None, float(document_total),
                ", ".join(all_invoices),
                "; ".join(dict.fromkeys(f"{r.invoice} - {r.description}" for r in document_rows)),
            )
            output_rows.append([values[self.column_mapping[column]] for column in CSV_COLUMNS])
        with open(output, "w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(output_rows)
        return grand_total

    def output_values(self, meta: dict[str, str], account: str, debit, credit,
                      invoice_text: str, description: str) -> dict[str, object]:
        """Build the selectable values used by all Excel output columns."""
        return {
            "Journal Date": meta["date"],
            "Journal No.": meta["journal"],
            "Account": account,
            "Debit Amount": debit,
            "Credit Amount": credit,
            "Invoice Number(s)": invoice_text,
            "PDF Description": description,
            "Vendor Name": "Denny's Inc",
            "Restaurant #": meta["restaurant"],
            "Location": meta["location"],
            "Blank": "",
        }


if __name__ == "__main__":
    MapperApp().mainloop()
