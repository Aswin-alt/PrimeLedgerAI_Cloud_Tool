# PrimeLedgerAI Cloud Web Platform

Professional multi-user web application for Denny's & Marietta Hotel accounting conversions.  
The original desktop Tkinter tools are **unchanged** and still work; the web app loads their pure conversion functions via `importlib`.

## Stack

- **FastAPI** + Jinja2 + Tailwind (CDN) + HTMX + Alpine + Chart.js
- **SQLAlchemy 2** with **local MySQL** (or SQLite for zero-setup)
- Dynamic RBAC: Admin / Manager / Accountant / Viewer (editable anytime)
- Time-bound per-user permission grants
- Full audit log / activity history

## Quick start (SQLite — recommended for first run)

```bash
cd PrimeLedgerAI_Cloud_Base-v2-1
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# .env already defaults to sqlite:///./primeledger.db
python scripts/seed.py

uvicorn webapp.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://127.0.0.1:8000 and sign in:

| Field    | Value       |
|----------|-------------|
| Username | `admin`     |
| Password | `Admin@123` |

## MySQL setup (production-style)

1. Create database and user:

```sql
CREATE DATABASE primeledger CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'pla'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL ON primeledger.* TO 'pla'@'localhost';
FLUSH PRIVILEGES;
```

2. Edit `.env`:

```env
DATABASE_URL=mysql+pymysql://pla:your_password@127.0.0.1:3306/primeledger?charset=utf8mb4
SECRET_KEY=replace-with-a-long-random-string
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=Admin@123
```

3. Seed and run:

```bash
python scripts/seed.py
uvicorn webapp.main:app --host 0.0.0.0 --port 8000
```

Optional Alembic:

```bash
alembic upgrade head
```

## Import legacy desktop tracker

If you previously used `PrimeLedgerAI_Accounting_Dashboard.py`:

```bash
python scripts/migrate_json.py
# or: python scripts/migrate_json.py /path/to/dashboard_data.json
```

## Roles & permissions (editable in UI)

Default seeded grants (Admin can change anytime under **Roles & Permissions**):

| Role       | Highlights |
|------------|------------|
| Admin      | Everything, including users + permission matrix |
| Manager    | Upload, convert, approve, settings; no user admin |
| Accountant | Upload, convert, edit records |
| Viewer     | Read-only + export |

### Per-user management

Open **Users → Manage** on any user to:

- Change role anytime (e.g. Viewer → Accountant)
- Add permanent or **temporary** grants/denies (`starts_at` / `expires_at` + reason)
- Reset password / activate / deactivate

Last active Admin cannot be demoted or deactivated. Removing `users.manage` from the Admin role is blocked.

### Audit Log / Activity

**Audit Log / History** lists every login, upload, conversion, status change, and permission edit. Filter by user, action, date; export CSV.

## Converters (legacy tools reused)

| Web path | Legacy script |
|----------|---------------|
| `/converters/daily_sales` | `Daily Sales/Dennys_Daily_Sales.py` |
| `/converters/daily_remittance` | `Daily Remittance/Daily_Remittance_Converter_CSV_FIXED.py` |
| `/converters/payroll` | `Payroll/Dennys_payroll_xformity.py` |
| `/converters/invoice` | `Dennys Invoice/Dennys_weekly_invoice_DFO.py` |
| `/converters/hotel_revenue` | `Hotel Revenue/Marietta_hotel_revenue_daily.py` |
| `/converters/sales_tax` | `Sales Tax/daily_report_sales_tax_to_pdf.py` |

Desktop apps remain runnable by double-click / `python` as before.

## Phase-2 add-ons included

- Dashboard Chart.js analytics
- Approval via status **Approved** (`records.approve`)
- Locations + chart-of-accounts editor
- API tokens page (`/settings/tokens`) for future QBO/automation
- Multi-file upload on Records; invoice multi-PDF parse

## Project layout

```
webapp/           FastAPI app, templates, static
scripts/seed.py   Create roles, permissions, admin, locations
scripts/migrate_json.py
alembic/          Schema migrations
```

## Security notes

- Change `SECRET_KEY` and the default admin password before any shared deployment.
- Passwords are bcrypt-hashed (`passlib`).
- Sessions use signed httpOnly cookies.
