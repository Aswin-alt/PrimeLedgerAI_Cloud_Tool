"""PrimeLedgerAI Credit Sales Report — Python 3.10+. PDF export requires ReportLab.
Install once: python -m pip install reportlab
Run: python PrimeLedgerAI_Credit_Sales_Report.py
Desktop interface: load a QuickBooks Transaction Detail by Account CSV, select
dates/locations/accounts, review classifications, enter dated opening balances,
and export CSV, an account-wise PDF, or a printable HTML report.
Deposits use the full description date period within the selected dates, inclusive.
Other transactions use posting dates. Excluded deposits are flagged for review. No QuickBooks data is modified.
Opening balances are stored beside this program, keyed by company and date.
"""
from __future__ import annotations
import csv
import html
import io
import json
import re
import tempfile
import webbrowser
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from decimal import Decimal, InvalidOperation
from pathlib import Path

ZERO = Decimal('0.00')
VERSION = '1.2'
SUMMARY_HEADERS = ['Location', 'Opening balance', 'Sales / tender charges', 'Receipts posted',
                   'Other adjustments', 'Net change', 'Period report balance']
DETAIL_HEADERS = ['Location', 'Account'] + SUMMARY_HEADERS[1:]
TX_HEADERS = ['CSV row', 'Date', 'Type', 'Number', 'Location', 'Account', 'Description',
              'Split', 'Source amount', 'Classification']

def parse_date(value):
    value = str(value).strip()
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f'Invalid date: {value!r}. Use MM/DD/YYYY.')

def money(value):
    s = str(value).strip().replace(',', '').replace('$', '').replace('−', '-')
    if not s:
        raise ValueError('Amount is blank.')
    if s in ('-', '—'):
        return ZERO
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        n = Decimal(s)
        if not n.is_finite():
            raise InvalidOperation
        if n != n.quantize(Decimal('.01')):
            raise ValueError(f'Amount has more than two decimals: {value!r}')
        return n.quantize(Decimal('.01'))
    except InvalidOperation:
        raise ValueError(f'Invalid amount: {value!r}') from None

def display(value):
    if value is None:
        return 'Opening needed'
    if isinstance(value, Decimal):
        return f'({abs(value):,.2f})' if value < 0 else f'{value:,.2f}'
    if isinstance(value, date):
        return value.strftime('%m/%d/%Y')
    return str(value)

@dataclass
class Transaction:
    row: int
    day: date
    kind: str
    number: str
    location: str
    account: str
    description: str
    split: str
    amount: Decimal

def load_csv(path):
    raw = Path(path).read_bytes()
    for enc in ('utf-8-sig', 'utf-16', 'cp1252'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeError:
            continue
    else:
        raise ValueError('Cannot decode this CSV. Export again as UTF-8 CSV.')
    rows = list(csv.reader(io.StringIO(text)))
    norm = lambda x: re.sub(r'\s+', ' ', x.strip().lower())
    aliases = {'account': ['distribution account'], 'day': ['transaction date', 'date'],
               'kind': ['transaction type', 'type'], 'number': ['num', 'number'],
               'location': ['location full name', 'location'], 'description': ['description', 'memo/description'],
               'split': ['split', 'split account'], 'amount': ['amount']}
    header_index = None
    for i, row in enumerate(rows):
        names = [norm(x) for x in row]
        if 'distribution account' in names and ('transaction date' in names or 'date' in names):
            header_index = i
            break
    if header_index is None:
        raise ValueError('Cannot find Distribution account and Transaction date columns. Export Transaction Detail by Account with these columns.')
    cols = {}
    for key, names in aliases.items():
        cols[key] = next((names_index for names_index, v in enumerate([norm(x) for x in rows[header_index]]) if v in names), None)
    missing = [k for k in ('account','day','kind','location','amount') if cols[k] is None]
    if missing:
        raise ValueError('Missing required CSV columns: ' + ', '.join(missing))
    company = next((r[0].strip() for r in rows[:header_index] if r and r[0].strip()), 'Company')
    transactions, errors = [], []
    for line, row in enumerate(rows[header_index+1:], header_index+2):
        def get(key):
            i = cols[key]
            return row[i].strip() if i is not None and i < len(row) else ''
        if not get('day') and not get('kind'):
            continue  # Report headings, bank groups, subtotals and footer.
        if norm(get('day')) == 'transaction date':
            continue
        try:
            if not get('account'):
                raise ValueError('Distribution account is missing')
            if not get('kind'):
                raise ValueError('Transaction type is missing')
            transactions.append(Transaction(line, parse_date(get('day')), get('kind'), get('number'),
                get('location') or '(Unassigned location)', get('account'), get('description'), get('split'), money(get('amount'))))
        except ValueError as e:
            errors.append(f'CSV row {line}: {e}')
    if errors:
        raise ValueError('Report stopped to avoid omitting invalid transaction rows:\n' + '\n'.join(errors[:12]))
    if not transactions:
        raise ValueError('No transaction rows found.')
    # QuickBooks Balance is deliberately not read: grouped running balances are not account opening balances.
    return company, transactions

def classify(t, prefix='DS', receipt_type='Deposit'):
    if t.kind.casefold() == 'journal entry' and t.number.casefold().startswith(prefix.casefold()) and t.amount > 0:
        return 'Sales / tender charges'
    if t.kind.casefold() == receipt_type.casefold() and t.amount < 0:
        return 'Receipts posted'
    return 'Other adjustments'

def deposit_period(description):
    """Require one full date or a full start/end pair, with no guessed years."""
    tokens = re.findall(r'(?<![\w/])(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{1,4})(?![\w/])', description)
    if len(tokens) not in (1, 2):
        raise ValueError('Missing or ambiguous description dates')
    dates=[]
    for token in tokens:
        if re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}', token):
            value=parse_date(token)
        else:
            normalized=token.replace('-', '/')
            if not re.fullmatch(r'\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})', normalized):
                raise ValueError('Invalid description date: '+token)
            value=parse_date(normalized)
        dates.append(value)
    if dates[0]>dates[-1]:
        raise ValueError('Description period is reversed')
    return dates[0],dates[-1]

def period_decision(t,start,end,receipt_type='Deposit'):
    if t.kind.casefold()!=receipt_type.casefold():
        return start<=t.day<=end, 'Transaction date outside selected period'
    try:
        first,last=deposit_period(t.description)
    except ValueError as e:
        return False,str(e)
    if start<=first<=last<=end:
        return True,''
    if first<=end and last>=start:
        return False,'Description period partially overlaps; no automatic allocation'
    return False,'Description period outside selected dates'

def calculate(transactions, locations, accounts, start, end, openings, prefix='DS', receipt_type='Deposit'):
    if start > end:
        raise ValueError('Starting date must be before or equal to ending date.')
    if not prefix.strip():
        raise ValueError('Enter your sales journal prefix, for example DS.')
    scoped = [t for t in transactions if t.location in locations and t.account in accounts]
    selected = [t for t in scoped if period_decision(t,start,end,receipt_type)[0]]
    # Include all selected location/account pairs seen in the file, even with no activity in this period.
    pairs = {(t.location,t.account) for t in transactions if t.location in locations and t.account in accounts}
    pairs |= {(l,a) for l,a in openings if l in locations and a in accounts}
    sums = {p:[ZERO,ZERO,ZERO] for p in pairs}
    for t in selected:
        category = classify(t,prefix,receipt_type)
        idx = {'Sales / tender charges':0,'Receipts posted':1,'Other adjustments':2}[category]
        sums[(t.location,t.account)][idx] += -t.amount if idx == 1 else t.amount
    details=[]
    for (loc,account), (sales,receipts,other) in sorted(sums.items()):
        opening = openings.get((loc,account))
        net = sales - receipts + other
        details.append([loc,account,opening,sales,receipts,other,net,None if opening is None else opening+net])
    summaries=[]
    for loc in sorted({r[0] for r in details}):
        rr=[r for r in details if r[0]==loc]
        opening = None if any(r[2] is None for r in rr) else sum((r[2] for r in rr),ZERO)
        amounts=[sum((r[j] for r in rr),ZERO) for j in (3,4,5,6)]
        summaries.append([loc,opening,*amounts,None if opening is None else opening+amounts[-1]])
    total=['TOTAL']
    for col in range(1,7):
        total.append(None if not summaries or any(r[col] is None for r in summaries) else sum((r[col] for r in summaries),ZERO))
    if selected and total[5] != sum((t.amount for t in selected),ZERO):
        raise ValueError('Internal reconciliation failed.')
    warnings=[f'Period: {display(start)} to {display(end)}. Opening balances must be as of {display(start-timedelta(days=1))}.',
              'Scope is limited to selected accounts in the uploaded export. Accounts absent from the export may have outstanding balances.',
              'Sales/tender charges are journal postings, not independently verified net revenue. Receipts are booked deposit allocations.',
              'Deposits use description dates: the entire description period must fall within selected dates, inclusive, regardless of posting date. Other entries use transaction dates.',
              'Report balances use description-period deposits and are not QuickBooks closing book balances or invoice-matched outstanding balances. Reconcile fees, refunds and excluded deposits.']
    if not selected:
        warnings.append('No transactions fall within the selected dates and filters.')
    if any(r[2] is None for r in details):
        warnings.append('Opening balances are missing. Blank openings are not treated as zero.')
    if any(r[7] is not None and r[7]<0 for r in details):
        warnings.append('Some accounts have credit closing balances. Review settlement allocation and adjustments.')
    if any(t.location=='(Unassigned location)' for t in selected):
        warnings.append('Unassigned location transactions are included and need review.')
    dates=sorted({t.day for t in selected if classify(t,prefix,receipt_type)=='Sales / tender charges'})
    warnings.append('Sales journal dates present: ' + (', '.join(display(d) for d in dates[:31]) + (' …' if len(dates)>31 else '') if dates else 'None'))
    if dates and (dates[0]>start or dates[-1]<end):
        warnings.append('Sales journal coverage does not reach both selected date boundaries. Confirm missing postings or zero activity.')
    if total[4] != ZERO:
        warnings.append('Review Other adjustments in the Transactions tab before relying on closing balances.')
    blank=[t.row for t in selected if classify(t,prefix,receipt_type)=='Receipts posted' and not t.description]
    if blank:
        warnings.append('Receipt descriptions are missing at CSV rows: '+', '.join(map(str,blank)))
    if start<min(t.day for t in transactions) or end>max(t.day for t in transactions):
        warnings.append('Selected dates extend beyond transaction dates in the file. Confirm export coverage.')
    excluded=defaultdict(list)
    for t in scoped:
        if t.kind.casefold()==receipt_type.casefold():
            included,reason=period_decision(t,start,end,receipt_type)
            if not included:excluded[reason].append(t.row)
    for reason,rows in sorted(excluded.items()):
        warnings.append(f'Excluded deposits - {reason}: {len(rows)} row(s). CSV rows: '+', '.join(map(str,rows)))
    warnings.append('Upload all relevant deposits, including later postings. Review exclusions in the Excluded Deposits tab and CSV export.')
    return summaries, details, selected, total, warnings

def write_pdf(path, company, source, period, report, prefix='DS', receipt_type='Deposit'):
    """Write selected account summaries and full transaction detail, grouped by location."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, PageBreak
    except ImportError:
        raise RuntimeError('PDF export requires ReportLab. Install once using: python -m pip install reportlab') from None
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Cell', fontName='Helvetica', fontSize=8, leading=10))
    styles.add(ParagraphStyle(name='Money', parent=styles['Cell'], alignment=2))
    styles.add(ParagraphStyle(name='Head', parent=styles['Cell'], textColor=colors.white, fontName='Helvetica-Bold'))
    def para(value, style='Cell'):
        return Paragraph(html.escape(display(value)).replace('\n', '<br/>'), styles[style])
    width, height = landscape(A4)
    usable = width - 64
    story = []
    def table(headers, rows, weights, numeric=(), total=False):
        data = [[para(h, 'Head') for h in headers]]
        data += [[para(v, 'Money' if i in numeric else 'Cell') for i, v in enumerate(row)] for row in rows]
        grid = LongTable(data, colWidths=[usable*w/sum(weights) for w in weights], repeatRows=1, hAlign='LEFT', splitInRow=1)
        commands = [('BACKGROUND',(0,0),(-1,0),colors.HexColor('#233D60')),
                    ('VALIGN',(0,0),(-1,-1),'TOP'), ('LEFTPADDING',(0,0),(-1,-1),6),
                    ('RIGHTPADDING',(0,0),(-1,-1),6), ('TOPPADDING',(0,0),(-1,-1),7),
                    ('BOTTOMPADDING',(0,0),(-1,-1),7),
                    ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#F1F5F9'), colors.white]),
                    ('LINEBELOW',(0,0),(-1,0),0.5,colors.HexColor('#233D60'))]
        if total and rows:
            commands += [('BACKGROUND',(0,-1),(-1,-1),colors.HexColor('#DCE7F5'))]
        grid.setStyle(TableStyle(commands))
        story.append(grid)
        story.append(Spacer(1,12))
    summary, details, tx, grand, notes = report
    story += [para('PrimeLedgerAI | Credit Sales Report', 'Title'), para(company, 'Heading2'),
              para(f'{display(period[0])} to {display(period[1])} | USD'), Spacer(1,12),
              para('Location summary', 'Heading2')]
    table(SUMMARY_HEADERS, summary+[grand], [1.8,1,1.2,1,1,1,1.2], range(1,7), True)
    story += [para('Balance calculation', 'Heading2'),
              para('Period report balance = opening balance + sales / tender charges - receipts posted + other adjustments.'),
              para('Period report balance uses deposits selected by description dates; it is not a QuickBooks closing book balance or an invoice-matched outstanding balance.'),
              Spacer(1,8), para('Report notes', 'Heading2')]
    for note in notes:
        story += [para(note), Spacer(1,5)]
    story += [para('Source: '+source)]
    grouped = defaultdict(list)
    for t in tx:
        grouped[(t.location,t.account)].append(t)
    for loc in sorted({r[0] for r in details}):
        local = [r for r in details if r[0]==loc]
        story += [PageBreak(), para('Location: '+loc, 'Heading1'), para('Account summary', 'Heading2')]
        loc_total = next(r for r in summary if r[0]==loc)
        table(DETAIL_HEADERS[1:], [r[1:] for r in local]+[['LOCATION TOTAL']+loc_total[1:]],
              [2.2,1,1.2,1,1,1,1.2], range(1,7), True)
        for row in local:
            account = row[1]
            story += [PageBreak(), para('Location: '+loc, 'Heading2'), para('Account: '+account, 'Heading1')]
            table(SUMMARY_HEADERS[1:], [row[2:]], [1,1.2,1,1,1,1.2], range(6))
            transactions = sorted(grouped[(loc,account)], key=lambda t:(t.day,t.row))
            if not transactions:
                story.append(para('No transactions in the selected period.'))
                continue
            balance = row[2]
            lines = []
            for t in transactions:
                category = classify(t,prefix,receipt_type)
                sale = t.amount if category=='Sales / tender charges' else ZERO
                receipt = -t.amount if category=='Receipts posted' else ZERO
                other = t.amount if category=='Other adjustments' else ZERO
                if balance is not None:
                    balance += t.amount
                description = t.description or '(No description)'
                if t.split:
                    description += '\nSplit: '+t.split
                lines.append([t.day, t.kind+'\n'+t.number+'\nCSV row '+str(t.row), description, sale, receipt, other, balance])
            table(['Date','Type / Number / Row','Description / Split','Sales / tender charges','Receipts posted','Other adjustments','Running report balance'],
                  lines, [0.9,1.45,3.2,1.05,1.05,1.05,1.3], range(3,7))
            table(['Period sales','Period receipts','Period adjustments','Period report balance'],
                  [[row[3],row[4],row[5],row[7]]], [1,1,1,1], range(4), True)
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#CBD5E1'))
        canvas.line(32,30,width-32,30)
        canvas.setFont('Helvetica',8)
        canvas.drawString(32,18, 'PrimeLedgerAI | '+display(period[0])+' - '+display(period[1])+' | USD')
        canvas.drawRightString(width-32,18,'Page '+str(doc.page))
        canvas.restoreState()
    doc = SimpleDocTemplate(str(path), pagesize=(width,height), leftMargin=32, rightMargin=32,
                            topMargin=30, bottomMargin=42, title='Credit Sales - Account Details', author='PrimeLedgerAI')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)

def main():
    """Launch the desktop interface.

    tkinter is imported here, not at module level, so this file can be
    imported headlessly by the PrimeLedgerAI Cloud web app.
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title('PrimeLedgerAI — Credit Sales by Location')
            self.geometry('1280x820'); self.minsize(1000,650)
            self.company=''; self.transactions=[]; self.report=None; self.signature=None
            self.store_path=Path(__file__).resolve().with_name('PrimeLedgerAI_Credit_Sales_Openings.json')
            self.store={}; self.storage_error=None
            try:
                if self.store_path.exists():
                    self.store=json.loads(self.store_path.read_text('utf-8'))
                    if not isinstance(self.store,dict):
                        raise ValueError('Opening balance file must contain a JSON object.')
            except Exception as e:
                self.storage_error=str(e)
            style=ttk.Style(self); style.theme_use('clam')
            style.configure('Treeview',rowheight=25);style.configure('Treeview.Heading',font=('Segoe UI',10,'bold'))
            ttk.Label(self,text='PrimeLedgerAI  |  Credit Sales Report',font=('Segoe UI',19,'bold')).pack(anchor='w',padx=16,pady=(14,6))
            top=ttk.Frame(self);top.pack(fill='x',padx=16)
            ttk.Button(top,text='1. Open QuickBooks CSV',command=self.open_file).pack(side='left')
            self.file_label=ttk.Label(top,text='No file selected');self.file_label.pack(side='left',padx=12)
            controls=ttk.Frame(self);controls.pack(fill='x',padx=16,pady=10)
            self.start=tk.StringVar();self.end=tk.StringVar();self.prefix=tk.StringVar(value='DS');self.receipt=tk.StringVar(value='Deposit')
            for label,var,width in [('From (MM/DD/YYYY)',self.start,13),('To (MM/DD/YYYY)',self.end,13),('Sales journal prefix',self.prefix,8),('Receipt type',self.receipt,16)]:
                ttk.Label(controls,text=label).pack(side='left',padx=(0,5));ttk.Entry(controls,textvariable=var,width=width).pack(side='left',padx=(0,14))
            ttk.Button(controls,text='2. Build report',command=self.build).pack(side='left')
            filters=ttk.Frame(self);filters.pack(fill='x',padx=16)
            self.locs=self.selector(filters,'Locations');self.accounts=self.selector(filters,'Accounts — select only the Credit Sales accounts you need')
            bar=ttk.Frame(self);bar.pack(fill='x',padx=16,pady=10)
            for text,fn in [('3. Enter opening balances',self.edit_openings),('Export CSV reports',self.export),('Save Account-wise PDF',self.export_pdf),('Print HTML',self.print_report)]:
                ttk.Button(bar,text=text,command=fn).pack(side='left',padx=(0,10))
            ttk.Button(bar,text='Help',command=self.help).pack(side='right')
            book=ttk.Notebook(self);book.pack(fill='both',expand=True,padx=16)
            self.summary=self.table(book,'Location Summary',SUMMARY_HEADERS)
            self.detail=self.table(book,'Account Detail',DETAIL_HEADERS)
            self.txview=self.table(book,'Transactions',TX_HEADERS)
            self.excludedview=self.table(book,'Excluded Deposits',TX_HEADERS+['Exclusion reason'])
            self.notes=tk.Text(self,height=7,wrap='word',font=('Segoe UI',10),background='#FFF8E6')
            self.notes.pack(fill='x',padx=16,pady=10)
            self.set_notes(['Load a CSV, select locations/accounts, then build the report. Blank opening balances remain unknown.'])
            if self.storage_error:
                self.after(300,lambda:messagebox.showwarning('Opening balances not loaded',self.storage_error+'\nThe existing file will not be overwritten. Repair or move it before restarting.'))

        def selector(self,parent,label):
            frame=ttk.LabelFrame(parent,text=label);frame.pack(side='left',fill='both',expand=True,padx=(0,12))
            box=tk.Listbox(frame,selectmode='extended',exportselection=False,height=5,font=('Segoe UI',10))
            box.pack(side='left',fill='both',expand=True)
            scroll=ttk.Scrollbar(frame,command=box.yview);scroll.pack(side='left',fill='y');box.configure(yscrollcommand=scroll.set)
            buttons=ttk.Frame(frame);buttons.pack(side='left',padx=5)
            ttk.Button(buttons,text='All',command=lambda:box.selection_set(0,'end')).pack(pady=2)
            ttk.Button(buttons,text='None',command=lambda:box.selection_clear(0,'end')).pack(pady=2)
            return box

        def table(self,book,name,headers):
            frame=ttk.Frame(book);book.add(frame,text=name)
            tree=ttk.Treeview(frame,columns=list(range(len(headers))),show='headings')
            for i,h in enumerate(headers):
                tree.heading(i,text=h);tree.column(i,width=190 if i==0 else 165,minwidth=90,anchor='w')
            sy=ttk.Scrollbar(frame,orient='vertical',command=tree.yview);sx=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview)
            tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
            tree.grid(row=0,column=0,sticky='nsew');sy.grid(row=0,column=1,sticky='ns');sx.grid(row=1,column=0,sticky='ew')
            frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
            tree.tag_configure('total',background='#DCE7F5',font=('Segoe UI',10,'bold'))
            return tree

        def set_notes(self,notes):
            self.notes.configure(state='normal');self.notes.delete('1.0','end');self.notes.insert('1.0','\n'.join(notes));self.notes.configure(state='disabled')

        def selected(self,box):
            return {box.get(i) for i in box.curselection()}

        def get_openings(self,start):
            section=self.store.get(self.company,{}).get((start-timedelta(days=1)).isoformat(),{})
            return {tuple(json.loads(k)):money(v) for k,v in section.items()}

        def open_file(self):
            path=filedialog.askopenfilename(filetypes=[('QuickBooks CSV','*.csv'),('All files','*.*')])
            if not path:return
            try:company,transactions=load_csv(path)
            except Exception as e:messagebox.showerror('Cannot load file',str(e));return
            self.company=company;self.transactions=transactions;self.source=Path(path).name;self.report=None
            self.file_label.configure(text=f'{company} — {len(transactions)} transactions')
            self.start.set(display(min(t.day for t in transactions)));self.end.set(display(max(t.day for t in transactions)))
            for box,values in [(self.locs,sorted({t.location for t in transactions})),(self.accounts,sorted({t.account for t in transactions}))]:
                box.delete(0,'end')
                for value in values:box.insert('end',value)
                box.selection_set(0,'end')
            self.build()

        def build(self):
            try:
                if not self.transactions:raise ValueError('Open a QuickBooks CSV first.')
                start,end=parse_date(self.start.get()),parse_date(self.end.get())
                locs,accounts=self.selected(self.locs),self.selected(self.accounts)
                if not locs or not accounts:raise ValueError('Select at least one location and one account.')
                self.report=calculate(self.transactions,locs,accounts,start,end,self.get_openings(start),self.prefix.get().strip(),self.receipt.get().strip())
                self.period=(start,end)
                summary,detail,tx,total,notes=self.report
                for tree,rows in [(self.summary,summary+[total]),(self.detail,detail),(self.txview,self.transaction_rows(tx)),(self.excludedview,self.excluded_rows())]:
                    tree.delete(*tree.get_children())
                    for row in rows:tree.insert('','end',values=[display(v) for v in row],tags=('total',) if row[0]=='TOTAL' else ())
                self.set_notes(notes)
                return True
            except Exception as e:
                self.report=None;messagebox.showerror('Report needs attention',str(e));return False

        def transaction_rows(self,tx):
            return [[t.row,t.day,t.kind,t.number,t.location,t.account,t.description,t.split,t.amount,
                     classify(t,self.prefix.get().strip(),self.receipt.get().strip())] for t in tx]

        def excluded_rows(self):
            start,end=self.period
            locations,accounts=self.selected(self.locs),self.selected(self.accounts)
            rows=[]
            for t in self.transactions:
                if t.location in locations and t.account in accounts and t.kind.casefold()==self.receipt.get().strip().casefold():
                    included,reason=period_decision(t,start,end,self.receipt.get().strip())
                    if not included:rows.append(self.transaction_rows([t])[0]+[reason])
            return rows

        def edit_openings(self):
            if not self.build():return
            if self.storage_error:
                messagebox.showerror('Cannot save', 'Repair or move the invalid opening balance file and restart first.');return
            start=self.period[0];details=self.report[1]
            win=tk.Toplevel(self);win.title('Opening balances as of '+display(start-timedelta(days=1)));win.geometry('850x620')
            ttk.Label(win,text='Enter the balance before the selected starting date. Blank = unknown; enter 0 only if confirmed.',wraplength=810).pack(padx=12,pady=10)
            area=ttk.Frame(win);area.pack(fill='both',expand=True)
            canvas=tk.Canvas(area);scroll=ttk.Scrollbar(area,command=canvas.yview);canvas.configure(yscrollcommand=scroll.set)
            scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
            inner=ttk.Frame(canvas);canvas.create_window((0,0),window=inner,anchor='nw')
            inner.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
            entries={}
            for i,row in enumerate(details):
                ttk.Label(inner,text=row[0],width=29).grid(row=i,column=0,padx=8,pady=4)
                ttk.Label(inner,text=row[1],width=29).grid(row=i,column=1,padx=8)
                v=tk.StringVar(value='' if row[2] is None else str(row[2]));entries[(row[0],row[1])]=v
                tk.Entry(inner,textvariable=v,width=17,background='#FFF1CC').grid(row=i,column=2,padx=8)
            def save():
                try:
                    updated=json.loads(json.dumps(self.store))
                    section=updated.setdefault(self.company,{}).setdefault((start-timedelta(days=1)).isoformat(),{})
                    for key,v in entries.items():
                        encoded=json.dumps(key)
                        if not v.get().strip():section.pop(encoded,None)
                        else:section[encoded]=str(money(v.get()))
                    temp=self.store_path.with_suffix('.tmp')
                    temp.write_text(json.dumps(updated,indent=2),encoding='utf-8');temp.replace(self.store_path)
                    self.store=updated;win.destroy();self.build()
                except Exception as e:messagebox.showerror('Cannot save opening balances',str(e),parent=win)
            ttk.Button(win,text='Save opening balances',command=save).pack(pady=12)

        def export(self):
            if not self.build():return
            folder=filedialog.askdirectory(title='Choose folder for CSV reports')
            if not folder:return
            try:
                start,end=self.period
                target=Path(folder)/('Credit_Sales_'+start.isoformat()+'_to_'+end.isoformat()+'_'+datetime.now().strftime('%H%M%S_%f'))
                target.mkdir()
                summary,detail,tx,total,notes=self.report
                for name,headers,rows in [('Location_Summary',SUMMARY_HEADERS,summary+[total]),('Account_Detail',DETAIL_HEADERS,detail),('Transactions',TX_HEADERS,self.transaction_rows(tx)),('Excluded_Deposits',TX_HEADERS+['Exclusion reason'],self.excluded_rows())]:
                    with (target/(name+'.csv')).open('w',newline='',encoding='utf-8-sig') as f:
                        writer=csv.writer(f);writer.writerow(headers)
                        for row in rows:
                            values=[]
                            for v in row:
                                s='Opening needed' if v is None else display(v) if isinstance(v,date) else str(v)
                                if isinstance(v,str) and s.startswith(('=','+','-','@')):s="'"+s
                                values.append(s)
                            writer.writerow(values)
                (target/'Report_Notes.txt').write_text(self.company+'\nSource: '+self.source+'\n\n'+'\n'.join(notes),encoding='utf-8')
                messagebox.showinfo('Reports saved',str(target))
            except Exception as e:messagebox.showerror('Export failed',str(e))

        def export_pdf(self):
            if not self.build():return
            start,end=self.period
            path=filedialog.asksaveasfilename(title='Save account-wise PDF report',defaultextension='.pdf',
                initialfile=f'Credit_Sales_Account_Details_{start.isoformat()}_to_{end.isoformat()}.pdf',
                filetypes=[('PDF report','*.pdf')])
            if not path:return
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(suffix='.pdf',dir=Path(path).parent,delete=False) as f:
                    temporary=Path(f.name)
                write_pdf(temporary,self.company,self.source,self.period,self.report,self.prefix.get().strip(),self.receipt.get().strip())
                temporary.replace(path)
                messagebox.showinfo('PDF saved','Account-wise PDF report saved:\n'+str(path))
                webbrowser.open(Path(path).resolve().as_uri())
            except Exception as e:
                messagebox.showerror('Cannot create PDF',str(e))
            finally:
                if temporary is not None:temporary.unlink(missing_ok=True)

        def print_report(self):
            if not self.build():return
            path=filedialog.asksaveasfilename(title='Save printable report',defaultextension='.html',initialfile='Credit_Sales_Report.html',filetypes=[('Printable HTML','*.html')])
            if not path:return
            try:
                summary,detail,tx,total,notes=self.report
                esc=lambda v:html.escape(display(v))
                def table(headers,rows):
                    return '<table><thead><tr>'+''.join('<th>'+esc(v)+'</th>' for v in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'
                body='''<!doctype html><html><head><meta charset="utf-8"><title>Credit Sales Report</title>
    <style>body{font:14px Arial;color:#172b4d;margin:28px}h1{font-size:24px}h2{margin-top:30px}
    table{border-collapse:collapse;width:100%;font-size:12px}th{background:#233d60;color:white;text-align:left}
    th,td{padding:8px;border-bottom:1px solid #ddd}tr:nth-child(even){background:#f3f6fa}li{margin:6px 0}
    @media print{button{display:none}thead{display:table-header-group}tr{break-inside:avoid}h2{break-after:avoid}}
    @page{size:landscape;margin:12mm}</style></head><body><button onclick="window.print()">Print / Save as PDF</button>'''
                body+='<h1>'+esc(self.company)+' — Credit Sales by Location</h1><p>'+esc(self.period[0])+' to '+esc(self.period[1])+' | USD</p>'
                body+=table(SUMMARY_HEADERS,summary+[total])+'<h2>Report notes</h2><ul>'+''.join('<li>'+esc(n)+'</li>' for n in notes)+'</ul>'
                for loc in sorted({r[0] for r in detail}):
                    body+='<h2>'+esc(loc)+'</h2>'+table(DETAIL_HEADERS[1:],[r[1:] for r in detail if r[0]==loc])
                body+='<p>Source: '+esc(self.source)+'</p></body></html>'
                Path(path).write_text(body,encoding='utf-8');webbrowser.open(Path(path).resolve().as_uri())
            except Exception as e:messagebox.showerror('Cannot create print report',str(e))

        def help(self):
            messagebox.showinfo('Using the report',
                '1. Export QuickBooks Transaction Detail by Account to CSV with Distribution account, Date, Type, Number, Location, Description, Split and Amount.\n\n'
                '2. Select the Credit Sales accounts and locations. Enter MM/DD/YYYY dates and click Build report. Use Ctrl-click for multiple selections.\n\n'
                '3. Positive Journal Entries beginning with DS are sales/tender charges. Change the prefix if needed. Negative Deposit lines are receipts. Other entries are adjustments. Review the classifications.\n\n'
                '4. Enter verified opening balances as of the day before the report starts. These are saved by company, account, location and date. Prior closing balances are not rolled forward automatically. Keep the JSON file with this program.\n\n'
                '5. Deposits use the full description date period within selected dates, regardless of posting date. Missing, invalid and partially overlapping periods are excluded for review. Export creates four CSV files plus report notes. Save Account-wise PDF creates location and account summaries plus all transaction details. Print HTML opens the browser report.\n\n'
                'PDF export requires: python -m pip install reportlab. Python must include Tkinter. This desktop program does not run as a cloud web server.')

    App().mainloop()


if __name__ == '__main__':
    main()
