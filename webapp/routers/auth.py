"""Authentication routes."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from webapp.database import get_db
from webapp.models import Role, User
from webapp.security import client_ip, get_current_user, hash_password, verify_password
from webapp.services.audit_service import write_audit
from webapp.templating import templates_ctx as templates

router = APIRouter(tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _signup_error(request: Request, error: str, form: Optional[dict] = None):
    return templates.TemplateResponse(
        "auth/signup.html",
        {
            "request": request,
            "user": None,
            "error": error,
            "form": form or {},
        },
        status_code=400,
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "user": None, "error": None},
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username.strip()).first()
    ip = client_ip(request)
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        write_audit(db, user if user else None, "login.failed", "user", username, "Invalid credentials", ip)
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "user": None, "error": "Invalid username or password."},
            status_code=400,
        )
    request.session["user_id"] = user.id
    user.last_login = datetime.utcnow()
    db.commit()
    write_audit(db, user, "login.success", "user", user.id, "Signed in", ip)
    return RedirectResponse("/", status_code=303)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        "auth/signup.html",
        {"request": request, "user": None, "error": None, "form": {}},
    )


@router.post("/signup")
def signup_submit(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    form = {
        "username": username.strip(),
        "email": email.strip(),
    }
    username = form["username"]
    email = form["email"]

    if len(username) < 3:
        write_audit(db, None, "signup.failed", "user", username, "Username too short", ip)
        return _signup_error(request, "Username must be at least 3 characters.", form)
    if not EMAIL_RE.match(email):
        write_audit(db, None, "signup.failed", "user", username, "Invalid email", ip)
        return _signup_error(request, "Please enter a valid email address.", form)
    if len(password) < 8:
        write_audit(db, None, "signup.failed", "user", username, "Password too short", ip)
        return _signup_error(request, "Password must be at least 8 characters.", form)
    if password != confirm_password:
        write_audit(db, None, "signup.failed", "user", username, "Password mismatch", ip)
        return _signup_error(request, "Password and confirmation do not match.", form)

    if db.query(User).filter(User.username == username).first():
        write_audit(db, None, "signup.failed", "user", username, "Username taken", ip)
        return _signup_error(request, "That username is already taken.", form)
    if db.query(User).filter(User.email == email).first():
        write_audit(db, None, "signup.failed", "user", username, "Email taken", ip)
        return _signup_error(request, "That email is already registered.", form)

    viewer = db.query(Role).filter_by(name="Viewer").first()
    if not viewer:
        write_audit(db, None, "signup.failed", "user", username, "Viewer role missing", ip)
        return _signup_error(
            request,
            "Signup is unavailable (Viewer role not configured). Ask an Admin to run seed.",
            form,
        )

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role_id=viewer.id,
        is_active=True,
        last_login=datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    request.session["user_id"] = user.id
    write_audit(db, user, "signup.success", "user", user.id, "Registered as Viewer", ip)
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        write_audit(db, user, "logout", "user", user.id, "Signed out", client_ip(request))
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
