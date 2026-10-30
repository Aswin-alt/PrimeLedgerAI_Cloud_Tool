"""PrimeLedgerAI cash/deposit reconciliation. Python 3; no pip packages needed.
Run: python PrimeLedgerAI_Cash_Deposit_Match.py
Select a QuickBooks Transaction Report CSV and enter inclusive MM/DD/YYYY dates.
Matches Journal Entry / Tender: Cash (net) by transaction date against Deposit
by the single full date in Description, separately for each cash account/location.
Deposit amount = negative of source Amount (preserves reversals).
Difference = tender - deposit. This is a reconciliation, not an import journal.
Export QuickBooks through the latest deposit posting date so later deposits
are available even when reconciling an earlier sales date.
"""
import csv
import io
import html
import tempfile
import webbrowser
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ZERO = Decimal('0.00')
HEADERS = ['Location', 'Match Date', 'Tender Cash Net', 'Deposit', 'Difference',
           'Status', 'Journal Numbers', 'Deposit Posting Dates',
           'Deposit Descriptions', 'Tender Rows', 'Deposit Rows', 'Source Lines']


def parse_date(value):
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError('Invalid date: ' + repr(value))


def input_date(value):
    if not re.fullmatch(r'\d{2}/\d{2}/\d{4}', value.strip()):
        raise ValueError('Enter dates as MM/DD/YYYY.')
    return parse_date(value)


def description_date(value):
    tokens = re.findall(r'(?<!\d)(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/(?:\d{4}|\d{2}))(?!\d)', value)
    dates = {parse_date(t) for t in tokens}
    if len(dates) != 1:
        raise ValueError('Deposit description must contain one unambiguous full date.')
    return dates.pop()


def money(value):
    s = value.strip().replace(',', '').replace('$', '').replace(' ', '')
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        amount = Decimal(s)
        if not amount.is_finite():
            raise InvalidOperation
        return amount
    except InvalidOperation:
        raise ValueError('Invalid amount: ' + repr(value))


def column_name(value):
    value = re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip()
    aliases = {
        'date': 'transaction date', 'type': 'transaction type',
        'memo description': 'description', 'memo': 'description',
        'memo description line': 'description',
        'account': 'account name', 'distribution account': 'account name',
        'full account name': 'account name',
        'number': 'num', 'no': 'num', 'transaction number': 'num',
        'location name': 'location',
    }
    return aliases.get(value, value)


def is_cash_account(value):
    return bool(re.search(r'\bcash\s+on\s+hand\b', value, re.I))


def account_location(account):
    # Keep an actual subaccount/location; a bare parent is not a location.
    tail = re.split(r'cash\s+on\s+hand', account, flags=re.I)[-1]
    return tail.strip(' :-–—') if is_cash_account(account) else ''


def read_transactions(path):
    data = Path(path).read_bytes()
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = data.decode('utf-16')
    else:
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = data.decode('cp1252')
    # Score delimiters by recognizable headers; report preambles confuse Sniffer.
    candidates = []
    for delimiter in (',', '\t', ';'):
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        score = max((len(set(map(column_name, r)) &
                         {'transaction date', 'transaction type', 'description',
                          'amount', 'debit', 'credit'}) for r in rows), default=0)
        candidates.append((score, rows))
    raw = max(candidates, key=lambda candidate: candidate[0])[1]
    header = None
    section = ''
    records, issues = [], []
    for line, row in enumerate(raw, 1):
        normalized = [column_name(v) for v in row]
        if 'transaction date' in normalized and 'transaction type' in normalized:
            header = {name: idx for idx, name in enumerate(normalized) if name}
            if 'description' not in header:
                raise ValueError('Include Memo/Description in the QuickBooks export.')
            if 'amount' not in header and not {'debit', 'credit'} <= header.keys():
                raise ValueError('Include Amount, or both Debit and Credit, in the export.')
            continue
        if header is None:
            continue
        def get(name):
            i = header.get(name, -1)
            return row[i].strip() if 0 <= i < len(row) else ''
        kind = re.sub(r'\s+', ' ', get('transaction type').casefold())
        desc = get('description')
        # A heading may be indented into any column. Never carry an old cash
        # section into a subsequent noncash account section.
        cells = [v.strip() for v in row if v.strip()]
        if not get('transaction date') and not kind:
            if any(re.match(r'^(?:total|subtotal)\b', v, re.I) for v in cells):
                section = ''
            elif len(cells) == 1:
                section = cells[0]
            continue
        if len(cells) == 1 and not re.search(r'\d[/]\d', cells[0]):
            section = '' if re.match(r'^(?:total|subtotal)\b', cells[0], re.I) else cells[0]
            continue
        tender = kind in ('journal entry', 'journal') and bool(re.search(
            r'\btender\s*:\s*cash\s*\(\s*net\s*\)', desc, re.I))
        if kind != 'deposit' and not tender:
            continue
        account = get('account name') or section
        location = get('location') or account_location(account)
        amount_text = get('amount') if 'amount' in header else get('debit') + ' / ' + get('credit')
        try:
            if account and not is_cash_account(account):
                # Account identity, when present, takes precedence over confirmation.
                continue
            if not account:
                raise ValueError('Cash account not identified. Export Account/Distribution Account, '
                                 'so Cash on Hand rows can be identified.')
            if not location:
                raise ValueError('Missing location. Include Location or a location-specific Cash on Hand account in the CSV.')
            posting = parse_date(get('transaction date'))
            match = posting if tender else description_date(desc)
            amount = (money(get('amount')) if 'amount' in header else
                      money(get('debit') or '0') - money(get('credit') or '0'))
            records.append(dict(location=location, account=account, date=match,
                                posting=posting, amount=amount, tender=bool(tender),
                                desc=desc, num=get('num'), line=line))
        except ValueError as exc:
            issues.append([str(line), location, get('transaction date'),
                           get('transaction type'), desc, amount_text, str(exc)])
    if header is None:
        raise ValueError('No transaction header found. Export a detail CSV with Date and Transaction Type.')
    return records, issues


def reconcile(records, start, end, locations=None):
    if start > end:
        raise ValueError('Starting date must be on or before ending date.')
    groups = defaultdict(list)
    for r in records:
        if start <= r['date'] <= end and (locations is None or r['location'] in locations):
            groups[(r['location'], r['date'])].append(r)
    result = []
    for (location, date), group in sorted(groups.items()):
        tenders = [r for r in group if r['tender']]
        deposits = [r for r in group if not r['tender']]
        cash = sum((r['amount'] for r in tenders), ZERO)
        deposit = -sum((r['amount'] for r in deposits), ZERO)
        diff = cash - deposit
        status = ('Missing tender' if not tenders else 'Missing deposit' if not deposits
                  else 'Matched' if diff == ZERO else 'Difference')
        result.append([location, date.strftime('%m/%d/%Y'), f'{cash:.2f}',
                       f'{deposit:.2f}', f'{diff:.2f}', status,
                       ' | '.join(sorted({r['num'] for r in tenders if r['num']})),
                       ' | '.join(d.strftime('%m/%d/%Y') for d in sorted({r['posting'] for r in deposits})),
                       ' | '.join(r['desc'] for r in deposits), len(tenders), len(deposits),
                       ' | '.join(str(r['line']) for r in group)])
    return result


def with_totals(rows, locations):
    """Add display/export subtotals without changing the underlying detail rows."""
    def total_row(label, items, status):
        result = [label, '']
        result.extend(f"{sum((Decimal(r[i]) for r in items), ZERO):.2f}" for i in (2, 3, 4))
        result.extend([status, '', '', '', sum(int(r[9]) for r in items),
                       sum(int(r[10]) for r in items), ''])
        return result
    output = []
    for location in sorted(locations):
        items = [r for r in rows if r[0] == location]
        output.extend(items)
        output.append(total_row(location, items, 'LOCATION TOTAL'))
    output.append(total_row('ALL SELECTED LOCATIONS', rows, 'GRAND TOTAL'))
    return output


def write_csv(path, headers, rows):
    # Prevent spreadsheet formula execution in descriptive text cells.
    def safe(v):
        if isinstance(v, str) and v.lstrip().startswith(('=', '+', '@', '-')):
            try:
                Decimal(v)
            except InvalidOperation:
                return "'" + v
        return v
    with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows([[safe(v) for v in row] for row in rows])


def print_report(rows, start, end, locations, source, issues):
    """Return a standalone HTML print preview, with no external dependencies."""
    esc = lambda value: html.escape(str(value), quote=True)
    def totals(items):
        return [sum((Decimal(r[i]) for r in items), ZERO) for i in (2, 3, 4)]
    sections = []
    for location in sorted(locations):
        items = [r for r in rows if r[0] == location]
        body = ''.join('<tr>' + ''.join(
            '<td class="' + ('money' if i in (2, 3, 4) else '') + '">' + esc(r[i]) + '</td>'
            for i in (1, 2, 3, 4, 5, 6, 7)) + '</tr>' for r in items)
        total = totals(items)
        sections.append('<section><h2>' + esc(location) + '</h2><table><thead><tr>' +
                        ''.join('<th>' + esc(h) + '</th>' for h in
                                ('Match Date', 'Tender Cash Net', 'Deposit', 'Difference', 'Status', 'Journal Numbers', 'Deposit Posting Dates')) +
                        '</tr></thead><tbody>' + (body or '<tr><td colspan="7">No transactions in this date range.</td></tr>') +
                        '<tr class="subtotal"><td>Location total</td>' +
                        ''.join('<td class="money">' + f'{v:,.2f}' + '</td>' for v in total) +
                        '<td colspan="3"></td></tr></tbody></table></section>')
    review = ''
    if issues:
        review = '<h2>Rows needing review — excluded from totals</h2><p>All source dates for selected locations, plus rows without a location.</p><ul>' + ''.join(
            '<li>' + esc(' | '.join(row)) + '</li>' for row in issues) + '</ul>'
    grand = totals(rows)
    return '''<!doctype html><html><head><meta charset="utf-8"><title>PrimeLedgerAI Cash &amp; Deposit Report</title>
<style>
@page { size: landscape; margin: 12mm; }
body { font: 12px Arial, sans-serif; color: #172b40; margin: 24px; }
h1 { font-size: 24px; margin-bottom: 5px; } h2 { font-size: 17px; margin-top: 24px; break-after: avoid; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; }
th, td { padding: 7px; border: 1px solid #cbd5df; text-align: left; overflow-wrap: anywhere; }
th { background: #e8eef4; } .money { text-align: right; font-variant-numeric: tabular-nums; }
.subtotal { font-weight: bold; background: #edf4f6; } thead { display: table-header-group; }
tr { break-inside: avoid; } .summary { padding: 12px; background: #e8eef4; font-weight: bold; }
button { background: #174b70; color: white; padding: 12px 22px; border: 0; cursor: pointer; }
@media print { .toolbar { display: none; } body { margin: 0; } }
</style></head><body><div class="toolbar"><button onclick="window.print()">Print / Save as PDF</button>
<p>Choose your printer or Save as PDF in the print dialog. Use landscape orientation.</p></div>
<h1>PrimeLedgerAI</h1><h2>Cash &amp; Deposit Reconciliation</h2>''' +         '<p><b>Period:</b> ' + esc(start) + ' to ' + esc(end) + '<br><b>Source:</b> ' + esc(Path(source).name) +         '<br><b>Selected locations:</b> ' + esc(', '.join(sorted(locations))) + '</p>' +         '<p>Difference = Tender Cash Net − Deposit. Deposits match by Description date. Missing means absent from the source CSV.</p>' + ''.join(sections) + '<p class="summary">Grand total — Tender: $' + f'{grand[0]:,.2f}' + ' | Deposit: $' + f'{grand[1]:,.2f}' + ' | Difference: $' + f'{grand[2]:,.2f}' + '</p>' + review + '</body></html>'


def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    root = tk.Tk()
    root.title('PrimeLedgerAI | Cash & Deposit Reconciliation')
    root.geometry('1180x730')
    root.minsize(850, 550)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('Treeview', rowheight=28, font=('Segoe UI', 10))
    style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'))
    style.configure('TButton', padding=8)
    shell = ttk.Frame(root, padding=20)
    shell.pack(fill='both', expand=True)
    ttk.Label(shell, text='PrimeLedgerAI', font=('Segoe UI', 22, 'bold')).pack(anchor='w')
    ttk.Label(shell, text='Cash & Deposit Reconciliation', font=('Segoe UI', 14)).pack(anchor='w', pady=(0, 12))
    filename = tk.StringVar()
    start = tk.StringVar()
    end = tk.StringVar()
    note = tk.StringVar(value='Choose a Transaction Report CSV to begin.')
    state = {'records': [], 'issues': [], 'rows': [], 'locations': set(), 'all_locations': [], 'selected_issues': []}
    location_note = tk.StringVar(value='No locations loaded')
    filebar = ttk.Frame(shell)
    filebar.pack(fill='x')
    ttk.Entry(filebar, textvariable=filename, state='readonly').pack(side='left', fill='x', expand=True)
    def browse():
        path = filedialog.askopenfilename(filetypes=[('CSV files', '*.csv'), ('Tab-separated files', '*.tsv')])
        if not path:
            return
        try:
            records, issues = read_transactions(path)
            state.update(records=records, issues=issues, rows=[], selected_issues=[])
            start.set('')
            end.set('')
            filename.set(path)
            state['all_locations'] = sorted({r['location'] for r in records} | {r[1] for r in issues if r[1]})
            state['locations'] = set(state['all_locations'])
            location_note.set(f"{len(state['locations'])} of {len(state['all_locations'])} locations selected")
            tree.delete(*tree.get_children())
            issuebox.delete('1.0', 'end')
            for row in issues:
                issuebox.insert('end', f'Line {row[0]} | {row[1]} | {row[4]} | {row[-1]}\n')
            dates = [r['date'] for r in records if r['tender']] or [r['date'] for r in records]
            if dates:
                start.set(min(dates).strftime('%m/%d/%Y'))
                end.set(max(dates).strftime('%m/%d/%Y'))
            note.set(f'{len(records)} eligible transactions loaded. {len(issues)} rows need review. Enter dates and click Match.')
        except Exception as exc:
            messagebox.showerror('Cannot load CSV', str(exc))
    ttk.Button(filebar, text='Select CSV', command=browse).pack(side='left', padx=(8, 0))
    location_bar = ttk.Frame(shell)
    location_bar.pack(fill='x', pady=(10, 0))
    def choose_locations():
        if not state['all_locations']:
            messagebox.showinfo('Locations', 'Select a CSV containing locations first.')
            return
        dialog = tk.Toplevel(root)
        dialog.title('Select locations')
        dialog.geometry('450x430')
        dialog.transient(root)
        dialog.grab_set()
        ttk.Label(dialog, text='Check all locations to include', font=('Segoe UI', 12, 'bold')).pack(pady=12)
        buttons = ttk.Frame(dialog)
        buttons.pack(fill='x', padx=14)
        variables = {name: tk.BooleanVar(value=name in state['locations']) for name in state['all_locations']}
        ttk.Button(buttons, text='Select All', command=lambda: [v.set(True) for v in variables.values()]).pack(side='left')
        ttk.Button(buttons, text='Clear All', command=lambda: [v.set(False) for v in variables.values()]).pack(side='left', padx=8)
        area = ttk.Frame(dialog)
        area.pack(fill='both', expand=True, padx=14, pady=10)
        canvas = tk.Canvas(area, highlightthickness=0)
        scrollbar = ttk.Scrollbar(area, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        checks = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=checks, anchor='nw')
        checks.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        for name, var in variables.items():
            ttk.Checkbutton(checks, text=name, variable=var).pack(anchor='w', pady=5)
        def apply():
            selected = {name for name, var in variables.items() if var.get()}
            if not selected:
                messagebox.showwarning('Select locations', 'Select at least one location.', parent=dialog)
                return
            state['locations'] = selected
            location_note.set(f"{len(selected)} of {len(state['all_locations'])} locations selected")
            dialog.destroy()
            calculate()
        ttk.Button(dialog, text='Apply Selection', command=apply).pack(pady=(0, 12))
    ttk.Button(location_bar, text='Select Locations', command=choose_locations).pack(side='left')
    ttk.Label(location_bar, textvariable=location_note).pack(side='left', padx=12)
    controls = ttk.Frame(shell)
    controls.pack(fill='x', pady=14)
    for label, var in [('Starting date', start), ('Ending date', end)]:
        ttk.Label(controls, text=label).pack(side='left', padx=(0, 6))
        ttk.Entry(controls, textvariable=var, width=13).pack(side='left', padx=(0, 18))
    def calculate():
        state['rows'] = []
        tree.delete(*tree.get_children())
        try:
            if not filename.get():
                raise ValueError('Select a CSV file first.')
            if not state['locations']:
                raise ValueError('Select at least one location.')
            rows = reconcile(state['records'], input_date(start.get()), input_date(end.get()), state['locations'])
            state['selected_issues'] = [r for r in state['issues'] if not r[1] or r[1] in state['locations']]
            issuebox.delete('1.0', 'end')
            for issue in state['selected_issues']:
                issuebox.insert('end', ' | '.join(issue) + '\n')
            state['rows'] = rows
            tree.delete(*tree.get_children())
            for row in with_totals(rows, state['locations']):
                tag = ('total' if row[5] in ('LOCATION TOTAL', 'GRAND TOTAL')
                       else 'ok' if row[5] == 'Matched' else 'review')
                tree.insert('', 'end', values=row, tags=(tag,))
            total = lambda i: sum((Decimal(r[i]) for r in rows), ZERO)
            note.set(f'{len(rows)} location/date rows | Tender: ${total(2):,.2f} | Deposit: ${total(3):,.2f} | Difference: ${total(4):,.2f} | Review issues: {len(state["selected_issues"])}')
            return True
        except Exception as exc:
            messagebox.showerror('Check input', str(exc))
            note.set('Check the date range and location selection, then click Match.')
            return False
    ttk.Button(controls, text='Match', command=calculate).pack(side='left')
    def export():
        if not calculate():
            return
        if not state['rows']:
            messagebox.showinfo('No results', 'No matching data to export. Check your dates and CSV.')
            return
        path = filedialog.asksaveasfilename(defaultextension='.csv', initialfile='Cash_Deposit_Reconciliation.csv', filetypes=[('CSV files', '*.csv')])
        if not path:
            return
        try:
            write_csv(path, HEADERS, with_totals(state['rows'], state['locations']))
            extra = ''
            if state['selected_issues']:
                issuepath = str(Path(path).with_name(Path(path).stem + '_Review_Issues.csv'))
                write_csv(issuepath, ['Source Line', 'Location', 'Posting Date', 'Type', 'Description', 'Amount', 'Issue'], state['selected_issues'])
                extra = '\nReview issues saved to: ' + issuepath
            messagebox.showinfo('Saved', 'Report saved to:\n' + path + extra)
        except Exception as exc:
            messagebox.showerror('Save failed', str(exc))
    ttk.Button(controls, text='Save CSV', command=export).pack(side='left', padx=8)
    def preview_print():
        if not calculate():
            return
        if not state['rows']:
            messagebox.showinfo('No results', 'No transactions for the selected locations and dates.')
            return
        try:
            report = print_report(state['rows'], start.get(), end.get(), state['locations'], filename.get(), state['selected_issues'])
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.html', prefix='PrimeLedgerAI_Print_', delete=False) as stream:
                stream.write(report)
                path = Path(stream.name)
            if not webbrowser.open(path.as_uri()):
                messagebox.showinfo('Print preview saved', 'Open this file in your browser, then press Ctrl+P:\n' + str(path))
        except Exception as exc:
            messagebox.showerror('Cannot print', str(exc))
    ttk.Button(controls, text='Print Preview', command=preview_print).pack(side='left', padx=4)
    ttk.Label(shell, text='Dates: MM/DD/YYYY • Difference = Tender Cash Net − Deposit • Match uses deposit DESCRIPTION date.').pack(anchor='w')
    ttk.Label(shell, text='Export Cash on Hand detail with Location, Date, Type, Memo/Description and Amount. Include later deposit postings; filters must retain both Journal Entry and Deposit rows.', wraplength=1050).pack(anchor='w', pady=(3, 10))
    frame = ttk.Frame(shell)
    frame.pack(fill='both', expand=True)
    tree = ttk.Treeview(frame, columns=HEADERS, show='headings')
    for col in HEADERS:
        tree.heading(col, text=col)
        tree.column(col, width=150 if col not in ('Deposit Descriptions', 'Journal Numbers') else 220, anchor='w')
    vs = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
    hs = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
    tree.grid(row=0, column=0, sticky='nsew')
    vs.grid(row=0, column=1, sticky='ns')
    hs.grid(row=1, column=0, sticky='ew')
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    tree.tag_configure('total', background='#dce8f2', font=('Segoe UI', 10, 'bold'))
    tree.tag_configure('ok', background='#e4f4eb')
    tree.tag_configure('review', background='#fff2d9')
    ttk.Label(shell, textvariable=note, wraplength=1080, font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=10)
    ttk.Label(shell, text='Source rows needing review (not included in totals; all source dates):').pack(anchor='w')
    issuebox = tk.Text(shell, height=4, font=('Segoe UI', 9), wrap='word')
    issuebox.pack(fill='x')
    root.mainloop()


if __name__ == '__main__':
    main()
