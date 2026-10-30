#!/usr/bin/env python3
"""Convert a Xenial DailyReportbyRevi_AllStores CSV into an Ohio sales-tax PDF.

Windows: install Python, then run:  pip install reportlab
Double-click this file for file selection, or run:
  python daily_report_sales_tax_to_pdf.py input.csv -o output.pdf

Optional adjustments CSV format:
  Store,Tax Adjustment
  5165,540.00
The adjustment is tax dollars to subtract from POS Total Sales Tax.
"""
from __future__ import annotations

import argparse, csv
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

D = Decimal
CENT = D("0.01")

# Store number: County and July 2026 filing rate.
STORES = {
    "5164": ("Berkshire OH", "DELAWARE", D("0.0700")),
    "5165": ("Perrysburg OH", "WOOD", D("0.0675")),
    "5166": ("Jeffersonville OH", "FAYETTE", D("0.0725")),
    "5169": ("Columbus OH", "FRANKLIN", D("0.0800")),
    "9697": ("Findlay OH", "HANCOCK", D("0.0675")),
}

# Adjustments documented by the supplied July filed return. They are applied
# only when the input reporting period is July 2026. Future months default to 0.
JULY_2026_TAX_ADJUSTMENTS = {"5165": D("540.00"), "5166": D("580.00"), "9697": D("1012.50")}


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
            result[str(row["Store"]).strip().split(":")[0]] = D(str(row["Tax Adjustment"]).replace("$", "").replace(",", "").strip() or "0")
    return result


def calculate(source: Path, adjustment_file: Path | None = None):
    totals = defaultdict(lambda: defaultdict(D)); dates = []
    with source.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"Date", "Store", "Field", "Value"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("CSV must contain Date, Store, Field, and Value columns.")
        for row in reader:
            store = row["Store"].strip().split(":")[0]
            if store not in STORES: continue
            try: dates.append(datetime.strptime(row["Date"].strip(), "%Y%m%d"))
            except ValueError: pass
            value = row["Value"].strip().replace("$", "").replace(",", "")
            if value: totals[store][row["Field"].strip()] += D(value)
    if not totals: raise ValueError("No configured Ohio stores were found.")
    period_start, period_end = min(dates), max(dates)
    is_july_sample = period_start.strftime("%Y%m") == "202607" and period_end.strftime("%Y%m") == "202607"
    adjustments = dict(JULY_2026_TAX_ADJUSTMENTS if is_july_sample else {})
    adjustments.update(read_adjustments(adjustment_file))
    detail=[]
    for store,(name,county,rate) in STORES.items():
        gross=q(totals[store]["Net Sales"]); pos_tax=q(totals[store]["Total Sales Tax"])
        adjustment=q(adjustments.get(store,D("0"))); liability=q(pos_tax-adjustment)
        taxable=q(liability/rate); exempt=q(gross-taxable)
        detail.append((store,name,county,rate,gross,pos_tax,adjustment,exempt,taxable,liability))
    return period_start,period_end,detail


def make_pdf(source: Path, output: Path, adjustment_file: Path | None = None):
    start,end,rows=calculate(source,adjustment_file)
    gross=q(sum((r[4] for r in rows),D("0"))); exempt=q(sum((r[7] for r in rows),D("0")))
    total_taxable=q(sum((r[8] for r in rows),D("0"))); liability=q(sum((r[9] for r in rows),D("0")))
    discount=q(liability*D("0.0075")); balance=q(liability-discount)
    doc=SimpleDocTemplate(str(output),pagesize=landscape(letter),leftMargin=.4*inch,rightMargin=.4*inch,topMargin=.35*inch,bottomMargin=.35*inch)
    styles=getSampleStyleSheet(); story=[Paragraph("Ohio Sales Tax Calculation",styles["Title"]),
        Paragraph(f"Source: {source.name} &nbsp;&nbsp; Period: {start:%m/%d/%Y} - {end:%m/%d/%Y}",styles["Normal"]),Spacer(1,10)]
    data=[["Store","County","Rate","Net Sales","POS Tax","Tax Adj.","Exempt Sales","Taxable Sales","Tax Liability"]]
    for store,name,county,rate,g,pos,adj,ex,store_taxable,tax in rows:
        data.append([f"{store} - {name}",county,f"{rate*100:.2f}%",f"${g:,.2f}",f"${pos:,.2f}",f"${adj:,.2f}",f"${ex:,.2f}",f"${store_taxable:,.2f}",f"${tax:,.2f}"])
    data.append(["TOTAL","","",f"${gross:,.2f}","",f"${sum((r[6] for r in rows),D('0')):,.2f}",f"${exempt:,.2f}",f"${total_taxable:,.2f}",f"${liability:,.2f}"])
    table=Table(data,colWidths=[1.55*inch,.7*inch,.45*inch,.85*inch,.72*inch,.7*inch,.87*inch,.9*inch,.82*inch],repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#17365D")),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),7.4),("ALIGN",(2,1),(-1,-1),"RIGHT"),
        ("GRID",(0,0),(-1,-1),.35,colors.grey),("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#D9EAF7")),
        ("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    story += [table,Spacer(1,12)]
    summary=[["Gross Sales",f"${gross:,.2f}"],["Exempt Sales",f"${exempt:,.2f}"],["Net Taxable Sales",f"${total_taxable:,.2f}"],
             ["Tax Liability",f"${liability:,.2f}"],["Timely-Filing Discount",f"${discount:,.2f}"],["Balance Due",f"${balance:,.2f}"]]
    st=Table(summary,colWidths=[2.2*inch,1.25*inch],hAlign="RIGHT")
    st.setStyle(TableStyle([("GRID",(0,0),(-1,-1),.35,colors.grey),("ALIGN",(1,0),(1,-1),"RIGHT"),("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),
        ("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#D9EAF7")),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    story += [st,Spacer(1,10),Paragraph("Calculation: Taxable Sales = (POS Total Sales Tax - Tax Adjustment) / County Rate. "
        "Gross Sales = Net Sales. Exempt Sales = Gross Sales - Taxable Sales. Verify county rates and adjustments before filing; this PDF does not submit the return.",styles["BodyText"])]
    doc.build(story)
    return gross,exempt,total_taxable,liability,discount,balance


def gui():
    from tkinter import Tk,filedialog,messagebox
    root=Tk();root.withdraw()
    source=filedialog.askopenfilename(title="Select DailyReport CSV",filetypes=[("CSV","*.csv")])
    if not source:return
    output=filedialog.asksaveasfilename(title="Save Sales Tax PDF",defaultextension=".pdf",initialfile=Path(source).stem+"_Sales_Tax.pdf",filetypes=[("PDF","*.pdf")])
    if not output:return
    try: make_pdf(Path(source),Path(output));messagebox.showinfo("Completed",f"PDF created:\n{output}")
    except Exception as e: messagebox.showerror("Error",str(e))


def main():
    p=argparse.ArgumentParser();p.add_argument("input",nargs="?");p.add_argument("-o","--output");p.add_argument("-a","--adjustments")
    a=p.parse_args()
    if not a.input:return gui()
    src=Path(a.input);out=Path(a.output) if a.output else src.with_name(src.stem+"_Sales_Tax.pdf")
    vals=make_pdf(src,out,Path(a.adjustments) if a.adjustments else None)
    print("Created",out);print(*(f"${v:,.2f}" for v in vals))

if __name__=="__main__":main()
