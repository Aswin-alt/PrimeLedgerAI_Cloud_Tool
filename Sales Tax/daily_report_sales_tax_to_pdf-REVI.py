#!/usr/bin/env python3
"""Convert a Xenial DailyReportbyRevi_AllStores CSV into an Ohio sales-tax PDF.

Windows: install Python, then run:  pip install reportlab
Double-click this file for file selection, or run:
  python daily_report_sales_tax_to_pdf.py input.csv -o output.pdf

Optional adjustments CSV format:
  Store,Tax Adjustment
  5165,540.00
Taxable sales = Net Sales minus the 15 configured tender exceptions.
Tender amounts must represent sales excluding tax and tips.
Optional adjustments subtract tax dollars from the newly calculated tax.
Historical July adjustments are no longer applied automatically.
"""
from __future__ import annotations

import argparse, csv, json, os, re, shutil, subprocess, sys, tempfile
from xml.sax.saxutils import escape
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether

D = Decimal
CENT = D("0.01")

# Store number: name, jurisdiction, and working tax rate.
STORES = {
    "5164": ("Berkshire OH", "OH - DELAWARE", D("0.0700")),
    "5165": ("Perrysburg OH", "OH - WOOD", D("0.0675")),
    "5166": ("Jeffersonville OH", "OH - FAYETTE", D("0.0725")),
    "5167": ("Elizabethtown KY", "KY - HARDIN", D("0.0800")),
    "5168": ("Catlettsburg KY", "KY - BOYD", D("0.0600")),
    "5169": ("Columbus OH", "OH - FRANKLIN", D("0.0800")),
    "7401": ("Youngstown OH", "OH - TRUMBULL", D("0.0675")),
    "9690": ("Marietta OH", "OH - WASHINGTON", D("0.0725")),
    "9697": ("Findlay OH", "OH - HANCOCK", D("0.0675")),
}

# User-requested tender exceptions; exact normalized matches only.
TENDER_EXCEPTIONS = (
    "Tender: Door Dash",
    "Tender: Doordash Banda Burrito",
    "Tender: Doordash Nathans",
    "Tender: Doordash VRB",
    "Tender: Doordash VRM",
    "Tender: Gift Card",
    "Tender: Grub Hub",
    "Tender: Grubhub Banda Burrito",
    "Tender: Grubhub VRB",
    "Tender: Grubhub VRM",
    "Tender: Uber Eats",
    "Tender: Uber Eats Banda Burrito",
    "Tender: Uber Eats Nathans",
    "Tender: Uber Eats VR",
    "Tender: Uber Eats VRM",
)


def normalize_field(value):
    return re.sub(r"\s+", "", value).casefold()


EXCEPTION_KEYS = {normalize_field(name) for name in TENDER_EXCEPTIONS}


def q(v) -> D:
    """Safely round Decimal, integer, float, string, blank, or None values."""
    if v is None or str(v).strip() == "":
        v = "0"
    if not isinstance(v, Decimal):
        v = D(str(v).replace("$", "").replace(",", "").strip())
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def read_adjustments(path: Path | None) -> dict[str, D]:
    if not path: return {}
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            result[normalize_store(row["Store"])] = D(str(row["Tax Adjustment"]).replace("$", "").replace(",", "").strip() or "0")
    return result


def normalize_store(value):
    """Accept numeric IDs, exported location labels, and Findlay spellings."""
    value = str(value).strip()
    match = re.match(r"^(\d+)(?:\.0)?(?:\s*[:\-–—]\s*.*)?$", value)
    if match:
        return match.group(1)
    name = re.sub(r"[^a-z0-9]", "", value.casefold())
    if name in {"findlay", "findley", "findly", "findlayoh", "findleyoh", "findlyoh"}:
        return "9697"
    return value


def available_store_ids(source: Path) -> list[str]:
    """Return configured stores that are actually present in the selected CSV."""
    found = set()
    with source.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            store = normalize_store(row.get("Store", ""))
            if store in STORES:
                found.add(store)
    return [store for store in STORES if store in found]


def calculate(source: Path, adjustment_file: Path | None = None, selected_stores: list[str] | None = None):
    selected = set(available_store_ids(source) if selected_stores is None else selected_stores)
    invalid = selected.difference(STORES)
    if invalid:
        raise ValueError(f"Unknown store number(s): {', '.join(sorted(invalid))}")
    if not selected:
        raise ValueError("Please select at least one location.")
    totals = defaultdict(lambda: defaultdict(D)); dates = []
    with source.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"Date", "Store", "Field", "Value"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("CSV must contain Date, Store, Field, and Value columns.")
        for row in reader:
            store = normalize_store(row["Store"])
            if store not in selected: continue
            try: dates.append(datetime.strptime(row["Date"].strip(), "%Y%m%d"))
            except ValueError: raise ValueError(f"Invalid date for location {store}: {row['Date']}. Expected YYYYMMDD.")
            value = row["Value"].strip().replace("$", "").replace(",", "")
            if value: totals[store][normalize_field(row["Field"])] += D(value)
    if not totals: raise ValueError("No mapped restaurant locations were found.")
    if not dates: raise ValueError("No valid dates found. Expected YYYYMMDD dates.")
    missing = selected.difference(totals)
    if missing: raise ValueError("No data for selected location(s): " + ", ".join(sorted(missing)))
    for code in selected:
        if not {normalize_field("Net Sales"), normalize_field("Total Sales Tax")}.issubset(totals[code]):
            raise ValueError(f"Location {code} requires Net Sales and Total Sales Tax fields.")
    period_start, period_end = min(dates), max(dates)
    adjustments = read_adjustments(adjustment_file)
    detail=[]
    for store,(name,county,rate) in STORES.items():
        if store not in selected:
            continue
        gross=q(totals[store][normalize_field("Net Sales")])
        pos_tax=q(totals[store][normalize_field("Total Sales Tax")])
        exempt=q(sum((value for field, value in totals[store].items()
                      if field in EXCEPTION_KEYS), D("0")))
        taxable=q(gross-exempt)
        if taxable < 0:
            raise ValueError(f"Location {store}: tender deductions exceed Net Sales. Check the source amounts.")
        adjustment=q(adjustments.get(store,D("0")))
        liability=q(q(taxable*rate)-adjustment)
        if liability < 0:
            raise ValueError(f"Location {store}: tax adjustment exceeds calculated tax.")
        detail.append((store,name,county,rate,gross,pos_tax,adjustment,exempt,taxable,liability))
    return period_start,period_end,detail


def make_pdf(source: Path, output: Path, adjustment_file: Path | None = None, selected_stores: list[str] | None = None):
    start,end,rows=calculate(source,adjustment_file,selected_stores)
    gross=q(sum((r[4] for r in rows),D("0"))); exempt=q(sum((r[7] for r in rows),D("0")))
    total_taxable=q(sum((r[8] for r in rows),D("0"))); liability=q(sum((r[9] for r in rows),D("0")))
    pos_basis = {r[0]: (q(r[4] - q(r[5]/r[3])), q(r[5]/r[3])) for r in rows}
    total_pos_tax = q(sum((r[5] for r in rows), D("0")))
    total_pos_deductions = q(sum((v[0] for v in pos_basis.values()), D("0")))
    total_pos_net_sales = q(sum((v[1] for v in pos_basis.values()), D("0")))
    discount=q(liability*D("0.0075")); balance=q(liability-discount)
    doc=SimpleDocTemplate(str(output),pagesize=landscape(letter),leftMargin=.4*inch,rightMargin=.4*inch,topMargin=.35*inch,bottomMargin=.35*inch)
    styles=getSampleStyleSheet(); styles["Normal"].fontSize=8; styles["Normal"].leading=10
    story=[Paragraph("Sales Tax Calculation",styles["Title"]),
        Paragraph(f"Source: {escape(source.name)} &nbsp;&nbsp; Period: {start:%m/%d/%Y} - {end:%m/%d/%Y}",styles["Normal"]),Spacer(1,10)]
    data=[["Store","Jurisdiction","Rate","Sales","Deductions","Net Sales","POS Tax","Deductions (POS Tax)","Net Sales (POS Tax)","Tax Adj.","Tax Liability"]]
    for store,name,county,rate,g,pos,adj,ex,store_taxable,tax in rows:
        data.append([f"{store} - {name}",county,f"{rate*100:.2f}%",f"${g:,.2f}",f"${ex:,.2f}",f"${store_taxable:,.2f}",f"${pos:,.2f}",f"${pos_basis[store][0]:,.2f}",f"${pos_basis[store][1]:,.2f}",f"${adj:,.2f}",f"${tax:,.2f}"])
    data.append(["TOTAL","","",f"${gross:,.2f}",f"${exempt:,.2f}",f"${total_taxable:,.2f}",f"${total_pos_tax:,.2f}",f"${total_pos_deductions:,.2f}",f"${total_pos_net_sales:,.2f}",f"${sum((r[6] for r in rows),D('0')):,.2f}",f"${liability:,.2f}"])
    for row in data[1:]:
        row[0] = Paragraph(escape(str(row[0])), styles["Normal"])
        row[1] = Paragraph(escape(str(row[1])), styles["Normal"])
    header_style = ParagraphStyle("TableHeader", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=colors.white)
    data[0] = [Paragraph(escape(label), header_style) for label in data[0]]
    table=Table(data,colWidths=[w*inch for w in [1.4,.85,.45,.85,.9,.9,.8,.95,.95,.55,.85]],repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#17365D")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7),("ALIGN",(2,1),(-1,-1),"RIGHT"),
        ("GRID",(0,0),(-1,-1),.35,colors.grey),("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#D9EAF7")),
        ("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    story += [table,Spacer(1,12)]
    summary=[["Sales",f"${gross:,.2f}"],["Deductions",f"${exempt:,.2f}"],["Net Sales after deductions",f"${total_taxable:,.2f}"],
             ["Total POS Tax",f"${total_pos_tax:,.2f}"],["Deductions (POS Tax)",f"${total_pos_deductions:,.2f}"],["Net Sales (POS Tax)",f"${total_pos_net_sales:,.2f}"],
             ["Tax Liability",f"${liability:,.2f}"],["Timely-Filing Discount",f"${discount:,.2f}"],["Balance Due",f"${balance:,.2f}"]]
    st=Table(summary,colWidths=[2.2*inch,1.25*inch],hAlign="RIGHT")
    st.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.35,colors.grey),("ALIGN",(1,0),(1,-1),"RIGHT"),("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),
        ("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#D9EAF7")),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    story += [st,Spacer(1,10),Paragraph(
        "Calculation: Sales = source Net Sales. Deductions = listed tenders. Net Sales = Sales - Deductions. "
        "Tax Liability = (Net Sales after deductions x Location Rate, rounded to cents) - optional Tax Adjustment. "
        "Net Sales (POS Tax) = POS Tax / Location Rate, rounded to cents. Deductions (POS Tax) = Sales - Net Sales (POS Tax). "
        "These two columns are inferred from POS Tax for comparison; totals sum the location amounts. Tender deductions are used as supplied and must exclude tax and tips. "
        "Historical July adjustments are not automatic. Rates and the 0.75% discount are existing program settings; review before filing.",
        styles["BodyText"])]
    # Append the actual tender amounts, restricted to the selected locations.
    deductions = defaultdict(lambda: defaultdict(D))
    selected = {r[0] for r in rows}
    with source.open(encoding="utf-8-sig", newline="") as stream:
        for item in csv.DictReader(stream):
            code = normalize_store(item["Store"])
            key = normalize_field(item["Field"])
            if code in selected and key in EXCEPTION_KEYS:
                value = item["Value"].strip().replace("$", "").replace(",", "")
                if value:
                    deductions[code][key] += D(value)
    for report_row in rows:
        code, name = report_row[:2]
        detail_data = [["Deduction / Tender", "Amount"]]
        amounts = [q(deductions[code][normalize_field(tender)]) for tender in TENDER_EXCEPTIONS]
        for tender, amount in zip(TENDER_EXCEPTIONS, amounts):
            detail_data.append([tender, f"${amount:,.2f}"])
        rounding = q(report_row[7] - sum(amounts, D("0")))
        if rounding:
            detail_data.append(["Rounding adjustment", f"${rounding:,.2f}"])
        detail_data.append(["TOTAL DEDUCTIONS", f"${report_row[7]:,.2f}"])
        deduction_table = Table(detail_data, colWidths=[5.7*inch, 1.4*inch], hAlign="LEFT", repeatRows=1)
        deduction_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#17365D")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("ALIGN", (1,1), (1,-1), "RIGHT"),
            ("GRID", (0,0), (-1,-1), .35, colors.grey),
            ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#D9EAF7")),
            ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
            ("TOPPADDING", (0,0), (-1,-1), 3),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ]))
        story.append(KeepTogether([
            Spacer(1,12),
            Paragraph(f"Deduction Details - {escape(code)} - {escape(name)}", styles["Heading2"]),
            deduction_table,
        ]))
    doc.build(story)
    return gross,exempt,total_taxable,liability,discount,balance


SETTINGS = Path.home() / 'PrimeLedgerAI' / 'sales_tax_locations.json'


def load_locations():
    if SETTINGS.exists():
        data = json.loads(SETTINGS.read_text(encoding='utf-8'))
        updated = {}
        for code, item in data.items():
            rate = D(item[2])
            if not rate.is_finite() or not 0 < rate <= 1:
                raise ValueError(f'Invalid saved rate for {code}')
            # Correct the old Youngstown mapping even when previous settings exist.
            county = item[1]
            if code == "7401" and ("MAHONING" in county.upper() or
                    ("TRUMBULL" in county.upper() and rate == D("0.0750"))):
                county, rate = "OH - TRUMBULL", D("0.0675")
            updated[code] = (item[0], county, rate)
        STORES.update(updated)


def save_location(code, name, county, percent):
    if not re.fullmatch(r'\d+', code) or not name or not county:
        raise ValueError('Enter a numeric store ID, location name, and jurisdiction.')
    rate = D(percent) / 100
    if not rate.is_finite() or not 0 < rate <= 1:
        raise ValueError('Tax rate must be greater than 0 and at most 100 percent.')
    updated = dict(STORES)
    updated[code] = (name, county, rate)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS.with_suffix('.tmp')
    temporary.write_text(json.dumps({k: [v[0], v[1], str(v[2])] for k, v in updated.items()}, indent=2), encoding='utf-8')
    temporary.replace(SETTINGS)
    STORES.update(updated)


def source_stores(source):
    found = set()
    with source.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not {'Date', 'Store', 'Field', 'Value'}.issubset(reader.fieldnames or []):
            raise ValueError('CSV must contain Date, Store, Field, and Value columns.')
        for row in reader:
            code = normalize_store(row['Store'])
            if code and code.lower() not in ('total', 'grand total'):
                found.add(code)
    return sorted(found)


def generate_batch(source, folder, selected, adjustments=None, combined=True):
    # Validate every selected location before creating files.
    calculate(source, adjustments, selected)
    folder.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='Sales_Tax_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_', dir=folder))
    paths = []
    try:
        for code in selected:
            name = re.sub(r'[^a-zA-Z0-9_-]+', '_', STORES[code][0]).strip('_')[:65]
            path = run / f'{code}_{name}_Sales_Tax.pdf'
            make_pdf(source, path, adjustments, [code])
            paths.append(path)
        if combined:
            path = run / 'All_Selected_Locations_Sales_Tax.pdf'
            make_pdf(source, path, adjustments, selected)
            paths.append(path)
    except Exception:
        shutil.rmtree(run)
        raise
    return paths


def gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    root = tk.Tk()
    root.title('PrimeLedgerAI - Sales Tax PDF Generator')
    root.geometry('1100x820')
    root.minsize(850, 650)
    try:
        load_locations()
    except Exception as exc:
        messagebox.showerror('Location settings', str(exc), parent=root)
    source = tk.StringVar()
    folder = tk.StringVar(value=str(Path.home() / 'Documents' / 'PrimeLedgerAI' / 'Sales_Tax_PDFs'))
    adjustment = tk.StringVar()
    status = tk.StringVar(value='Choose your DailyReport CSV to load locations.')
    combined = tk.BooleanVar(value=True)
    variables, found, paths = {}, [], []
    panel = ttk.Frame(root, padding=14)
    panel.pack(fill='both', expand=True)
    panel.columnconfigure(0, weight=1)
    panel.rowconfigure(2, weight=2)
    panel.rowconfigure(5, weight=1)
    ttk.Label(panel, text='Sales Tax Reports', font=('Segoe UI', 19, 'bold')).grid(row=0, sticky='w', pady=(0,10))
    inputs = ttk.LabelFrame(panel, text='1. Input and output', padding=10)
    inputs.grid(row=1, sticky='ew')
    inputs.columnconfigure(1, weight=1)
    def browse_source():
        value = filedialog.askopenfilename(parent=root, title='Select DailyReport CSV', filetypes=[('CSV files', '*.csv')])
        if not value: return
        try:
            codes = source_stores(Path(value))
            if not codes: raise ValueError('No locations found in this file.')
            source.set(value)
            found[:] = codes
            refresh()
        except Exception as exc:
            messagebox.showerror('Input error', str(exc), parent=root)
    def browse_folder():
        value = filedialog.askdirectory(parent=root, title='Choose PDF output folder')
        if value: folder.set(value)
    def browse_adjustment():
        value = filedialog.askopenfilename(parent=root, title='Optional tax adjustments CSV', filetypes=[('CSV files', '*.csv')])
        if value: adjustment.set(value)
    for row, (label, variable, action) in enumerate([
        ('DailyReport CSV', source, browse_source), ('Output folder', folder, browse_folder),
        ('Adjustments CSV (optional)', adjustment, browse_adjustment)]):
        ttk.Label(inputs, text=label).grid(row=row, column=0, sticky='w', padx=(0,12), pady=4)
        ttk.Entry(inputs, textvariable=variable, state='readonly' if row == 0 else 'normal').grid(row=row, column=1, sticky='ew')
        ttk.Button(inputs, text='Browse...', command=action).grid(row=row, column=2, padx=8)
    locations = ttk.LabelFrame(panel, text='2. Select locations - tick one or more', padding=8)
    locations.grid(row=2, sticky='nsew', pady=10)
    locations.columnconfigure(0, weight=1)
    locations.rowconfigure(0, weight=1)
    canvas = tk.Canvas(locations, highlightthickness=0, height=210)
    canvas.grid(row=0, column=0, sticky='nsew')
    scroll = ttk.Scrollbar(locations, orient='vertical', command=canvas.yview)
    scroll.grid(row=0, column=1, sticky='ns')
    canvas.configure(yscrollcommand=scroll.set)
    choices = ttk.Frame(canvas)
    window = canvas.create_window((0,0), window=choices, anchor='nw')
    choices.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))
    def edit_location(code=''):
        dialog = tk.Toplevel(root)
        dialog.title('Add / Edit Location')
        dialog.transient(root)
        dialog.grab_set()
        form = ttk.Frame(dialog, padding=18)
        form.pack(fill='both', expand=True)
        old = STORES.get(code, ('', '', D('0')))
        values = [tk.StringVar(value=v) for v in [code, old[0], old[1], str(old[2]*100) if code in STORES else '']]
        for i, label in enumerate(['Store ID (matches CSV)', 'Location name', 'Jurisdiction / county', 'Tax rate % (example: 7.25)']):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky='w', pady=6)
            ttk.Entry(form, textvariable=values[i], width=35, state='readonly' if i == 0 and code else 'normal').grid(row=i, column=1, padx=12)
        def save():
            try:
                save_location(*(v.get().strip() for v in values))
                refresh()
                dialog.destroy()
            except Exception as exc: messagebox.showerror('Cannot save location', str(exc), parent=dialog)
        ttk.Button(form, text='Save Location', command=save).grid(row=4, column=1, pady=12)
    def refresh():
        previous = {k: v.get() for k,v in variables.items()}
        for child in choices.winfo_children(): child.destroy()
        variables.clear()
        for row, code in enumerate(sorted(set(STORES).union(found))):
            mapped = code in STORES
            item = STORES.get(code)
            text = f'{code} - {item[0]} | {item[1]} | {item[2]*100:g}%' if item else f'{code} - NOT MAPPED: click Set Up'
            if source.get() and code not in found:
                text += ' | No data in selected CSV'
            variables[code] = tk.BooleanVar(value=previous.get(code, code in found and mapped) and code in found)
            ttk.Checkbutton(choices, text=text, variable=variables[code], state='normal' if mapped and code in found else 'disabled').grid(row=row, column=0, sticky='w', padx=5, pady=3)
            ttk.Button(choices, text='Edit' if mapped else 'Set Up', command=lambda c=code: edit_location(c)).grid(row=row, column=1, padx=8, pady=2)
        status.set(f'{len(found)} locations in CSV. Set up any unmapped locations before selecting them.' if found else 'Choose your DailyReport CSV to load locations.')
    controls = ttk.Frame(locations)
    controls.grid(row=1, column=0, sticky='ew', pady=(8,0))
    def select_all(value):
        for code, variable in variables.items(): variable.set(value and code in STORES and code in found)
    for label, command in [('Select All', lambda: select_all(True)), ('Clear All', lambda: select_all(False)), ('Add Location', edit_location)]:
        ttk.Button(controls, text=label, command=command).pack(side='left', padx=4)
    actions = ttk.Frame(panel)
    actions.grid(row=3, sticky='ew')
    ttk.Checkbutton(actions, text='Also create combined PDF for all selected locations', variable=combined).pack(side='left')
    def generate():
        try:
            selected = [code for code, var in variables.items() if var.get() and code in found]
            if not source.get(): raise ValueError('Choose a DailyReport CSV first.')
            if not selected: raise ValueError('Select at least one location.')
            if not folder.get().strip(): raise ValueError('Choose an output folder.')
            generate_button.configure(state='disabled')
            status.set('Generating PDFs...')
            root.update_idletasks()
            produced = generate_batch(Path(source.get()), Path(folder.get()).expanduser(), selected,
                Path(adjustment.get()) if adjustment.get().strip() else None, combined.get())
            paths[:] = produced
            results.delete(0, 'end')
            for path in paths: results.insert('end', str(path))
            results.selection_set(0)
            status.set(f'Created {len(paths)} PDFs. Double-click a file below to open it.')
            messagebox.showinfo('PDFs created', f'Created {len(paths)} PDF files in:\n{paths[0].parent}', parent=root)
        except Exception as exc:
            status.set('Generation failed - see error message.')
            messagebox.showerror('Cannot generate PDFs', str(exc), parent=root)
        finally: generate_button.configure(state='normal')
    generate_button = ttk.Button(actions, text='Generate PDFs', command=generate)
    generate_button.pack(side='right', pady=5)
    ttk.Label(panel, textvariable=status, wraplength=900).grid(row=4, sticky='w', pady=8)
    outputs = ttk.LabelFrame(panel, text='3. Generated PDF files', padding=8)
    outputs.grid(row=5, sticky='nsew')
    outputs.columnconfigure(0, weight=1)
    outputs.rowconfigure(0, weight=1)
    results = tk.Listbox(outputs, height=6, exportselection=False)
    results.grid(row=0, column=0, sticky='nsew')
    vs = ttk.Scrollbar(outputs, orient='vertical', command=results.yview)
    vs.grid(row=0, column=1, sticky='ns')
    hs = ttk.Scrollbar(outputs, orient='horizontal', command=results.xview)
    hs.grid(row=1, column=0, sticky='ew')
    results.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
    def open_path(path):
        try:
            if not path.exists(): raise ValueError('File or folder does not exist yet. Generate PDFs first.')
            if sys.platform == 'win32': os.startfile(str(path))
            else: subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])
        except Exception as exc: messagebox.showerror('Cannot open', str(exc), parent=root)
    def open_pdf():
        if results.curselection(): open_path(paths[results.curselection()[0]])
    results.bind('<Double-Button-1>', lambda e: open_pdf())
    buttons = ttk.Frame(outputs)
    buttons.grid(row=2, column=0, sticky='w', pady=7)
    ttk.Button(buttons, text='Open Selected PDF', command=open_pdf).pack(side='left', padx=4)
    ttk.Button(buttons, text='Open Output Folder', command=lambda: open_path(paths[0].parent if paths else Path(folder.get()))).pack(side='left', padx=4)
    ttk.Label(panel, text='Sales minus Deductions = Net Sales for tax. Deductions include the 15 specified tenders. Tender amounts must exclude tax and tips.', wraplength=950).grid(row=6, sticky='w', pady=(10,0))
    refresh()
    root.mainloop()

def main():
    p=argparse.ArgumentParser();p.add_argument("input",nargs="?");p.add_argument("-o","--output");p.add_argument("-a","--adjustments")
    p.add_argument("-s","--stores",help="Comma-separated store numbers, for example: 5164,5165,5169")
    a=p.parse_args()
    if not a.input:return gui()
    load_locations()
    src=Path(a.input);out=Path(a.output) if a.output else src.with_name(src.stem+"_Sales_Tax.pdf")
    selected=[x.strip() for x in a.stores.split(",") if x.strip()] if a.stores else None
    vals=make_pdf(src,out,Path(a.adjustments) if a.adjustments else None,selected)
    print("Created",out);print(*(f"${v:,.2f}" for v in vals))

if __name__=="__main__":main()
