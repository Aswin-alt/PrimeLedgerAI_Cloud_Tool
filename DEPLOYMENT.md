# Deploy PrimeLedgerAI Cloud to tool.primeledgerai.com

Production runbook for hosting this FastAPI app on an **Ubuntu/Debian** Linux server with:

- **Domain:** `https://tool.primeledgerai.com`
- **Database:** MySQL on the same host (`localhost`)
- **Process manager:** systemd + Gunicorn (`UvicornWorker`)
- **Reverse proxy / TLS:** Nginx + Let's Encrypt (Certbot)

Do **not** copy the local development SQLite file (`primeledger.db`) to the server. Seed a fresh MySQL database instead.

```mermaid
flowchart LR
    browser["Browser"] -->|HTTPS| nginx["Nginx + TLS"]
    nginx -->|"127.0.0.1:8000"| gunicorn["Gunicorn x3"]
    gunicorn --> app["FastAPI"]
    app --> mysql["MySQL primeledger"]
```

---

## 1. DNS

At your DNS provider for `primeledgerai.com`, create:

| Type | Name | Value |
|------|------|--------|
| A    | `tool` | Your server's public IPv4 |

Confirm from any machine:

```bash
dig +short tool.primeledgerai.com
# should print your server IP
```

Wait until DNS resolves before running Certbot (step 10).

---

## 2. Install system packages

SSH into the server, then:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
  python3 python3-venv python3-pip git nginx mysql-server \
  build-essential libssl-dev libffi-dev python3-dev
```

Optional hardening for MySQL:

```bash
sudo mysql_secure_installation
```

---

## 3. Deploy application files

On your **local** machine (from the project root):

```bash
rsync -avz \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.git' \
  --exclude 'primeledger.db' \
  --exclude 'webapp/uploads' \
  --exclude 'webapp/outputs' \
  --exclude '.env' \
  ./ youruser@YOUR_SERVER_IP:/opt/primeledger/
```

On the **server**, ensure the directory exists and is owned by your deploy user during setup:

```bash
sudo mkdir -p /opt/primeledger
sudo chown "$USER:$USER" /opt/primeledger
```

Required folders that must be present on the server (runtime converters):

- `webapp/`
- `scripts/`
- `alembic/`, `alembic.ini`
- `Daily Sales/`, `Daily Remittance/`, `Payroll/`
- `Dennys Invoice/`, `Hotel Revenue/`, `Sales Tax/`
- `requirements.txt`, `deploy/`

---

## 4. Python virtualenv and dependencies

```bash
cd /opt/primeledger
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` includes `gunicorn` for production.

---

## 5. Create MySQL database and user

```bash
sudo mysql <<'SQL'
CREATE DATABASE primeledger CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'pla'@'localhost' IDENTIFIED BY 'REPLACE_WITH_STRONG_DB_PASSWORD';
GRANT ALL PRIVILEGES ON primeledger.* TO 'pla'@'localhost';
FLUSH PRIVILEGES;
SQL
```

Replace `REPLACE_WITH_STRONG_DB_PASSWORD` with a strong secret and reuse it in `.env` below.

---

## 6. Create production `.env`

```bash
cd /opt/primeledger
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
nano .env   # or vim / your editor
```

Set at least:

| Variable | Value |
|----------|--------|
| `DATABASE_URL` | `mysql+pymysql://pla:YOUR_DB_PASSWORD@127.0.0.1:3306/primeledger?charset=utf8mb4` |
| `SECRET_KEY` | Output of the `secrets.token_urlsafe(48)` command |
| `SESSION_HTTPS_ONLY` | `true` |
| `DEFAULT_ADMIN_PASSWORD` | A strong password (not the example) |
| `UPLOAD_DIR` / `OUTPUT_DIR` / `LEGACY_ROOT` | Absolute paths under `/opt/primeledger` (already in `.env.example`) |

`.env` is gitignored and must never be committed.

---

## 7. Initialize schema and seed data

```bash
cd /opt/primeledger
source .venv/bin/activate
python scripts/seed.py
```

This creates all tables and seeds:

- Roles / permissions / Admin user
- Locations (including `9690-Marietta Rest`, `9697-Findley`, `8829`)
- Default remittance COA rules

Optional (if you prefer Alembic instead of / in addition to `create_all` inside seed):

```bash
alembic upgrade head
```

Smoke-test before installing the service:

```bash
uvicorn webapp.main:app --host 127.0.0.1 --port 8000
# other terminal:
curl http://127.0.0.1:8000/health
# expect: {"status":"ok","app":"PrimeLedgerAI Cloud",...}
# Ctrl+C to stop uvicorn
```

---

## 8. systemd service

```bash
sudo cp /opt/primeledger/deploy/primeledger.service /etc/systemd/system/primeledger.service
sudo mkdir -p /opt/primeledger/webapp/uploads /opt/primeledger/webapp/outputs
sudo chown -R www-data:www-data /opt/primeledger
sudo systemctl daemon-reload
sudo systemctl enable --now primeledger
sudo systemctl status primeledger
curl -s http://127.0.0.1:8000/health
```

The unit runs **3** Gunicorn workers (safe with MySQL) bound to `127.0.0.1:8000` only.

Logs:

```bash
journalctl -u primeledger -f
```

---

## 9. Nginx reverse proxy

```bash
sudo cp /opt/primeledger/deploy/nginx-primeledger.conf /etc/nginx/sites-available/primeledger
sudo ln -sf /etc/nginx/sites-available/primeledger /etc/nginx/sites-enabled/
# Remove default site if it conflicts:
# sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Confirm HTTP responds (before TLS):

```bash
curl -I http://tool.primeledgerai.com/health
```

---

## 10. HTTPS (Let's Encrypt)

DNS for `tool.primeledgerai.com` must already point at this server.

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d tool.primeledgerai.com
```

Follow the prompts (email, agree to ToS). Certbot rewrites the Nginx site for 443 and installs a renewal timer.

Verify:

```bash
curl -I https://tool.primeledgerai.com/health
sudo certbot renew --dry-run
```

---

## 11. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

Do not expose MySQL (`3306`) or Gunicorn (`8000`) publicly; they listen on localhost only.

---

## 12. First login

1. Open `https://tool.primeledgerai.com`
2. Sign in with `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD` from `.env`
3. Immediately change the admin password under **Users**
4. Confirm Settings → Locations shows remittance codes and COA rules

---

## Updates (later)

From your laptop:

```bash
rsync -avz --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
  --exclude 'primeledger.db' --exclude 'webapp/uploads' --exclude 'webapp/outputs' \
  --exclude '.env' \
  ./ youruser@YOUR_SERVER_IP:/opt/primeledger/
```

On the server:

```bash
cd /opt/primeledger
sudo -u www-data /opt/primeledger/.venv/bin/pip install -r requirements.txt
# Or temporarily chown, pip as deploy user, then chown back to www-data
sudo systemctl restart primeledger
```

If ownership makes pip awkward, a common pattern is:

```bash
sudo chown -R "$USER:$USER" /opt/primeledger
source .venv/bin/activate
pip install -r requirements.txt
sudo chown -R www-data:www-data /opt/primeledger
sudo systemctl restart primeledger
```

---

## Backups

MySQL (example daily cron as root):

```bash
sudo mkdir -p /var/backups/primeledger
# crontab -e
0 2 * * * mysqldump -u pla -p'YOUR_DB_PASSWORD' primeledger | gzip > /var/backups/primeledger/primeledger-$(date +\%F).sql.gz
```

Also back up:

- `/opt/primeledger/webapp/uploads/`
- `/opt/primeledger/webapp/outputs/`
- `/opt/primeledger/.env` (store securely offline)

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| 502 Bad Gateway | `systemctl status primeledger`; `journalctl -u primeledger -n 100` |
| DB connection errors | MySQL running? Password in `.env`? User `'pla'@'localhost'`? |
| Session / login loops over HTTPS | `SESSION_HTTPS_ONLY=true` and Certbot succeeded |
| Upload failures | Nginx `client_max_body_size 50M`; `www-data` can write `uploads/` / `outputs/` |
| Converter import errors | Legacy folders present under `LEGACY_ROOT` (`/opt/primeledger`) |

---

## Files added for this deploy

| Path | Purpose |
|------|---------|
| [`.env.example`](.env.example) | Production env template (MySQL + HTTPS cookie) |
| [`deploy/primeledger.service`](deploy/primeledger.service) | systemd unit |
| [`deploy/nginx-primeledger.conf`](deploy/nginx-primeledger.conf) | Nginx site for `tool.primeledgerai.com` |
| [`requirements.txt`](requirements.txt) | Includes `gunicorn>=21.2.0` |
| [`webapp/config.py`](webapp/config.py) | `SESSION_HTTPS_ONLY` |
| [`webapp/main.py`](webapp/main.py) | Session cookie `https_only` flag |
