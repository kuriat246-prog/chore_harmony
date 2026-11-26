import os
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Form, Depends, HTTPException, Cookie, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import (
    init_db, get_db, SessionLocal,
    User, Chore, ChoreAssignment, SwapRequest, NotificationLog,
    get_setting, set_setting,
    verify_admin_password, set_admin_password,
    create_admin_session, validate_admin_session
)
from scheduler import (
    start_scheduler, ensure_assignments_for_date,
    process_morning_reminders, reschedule_morning_reminders,
    get_scheduler_status, check_and_catchup_reminders
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

ADMIN_COOKIE_NAME = "ch_admin_token"


# ---------------------------------------------------------------------------
# Admin auth helpers
# ---------------------------------------------------------------------------

def get_admin_token(request: Request) -> Optional[str]:
    return request.cookies.get(ADMIN_COOKIE_NAME)


def require_admin(request: Request, db: Session = Depends(get_db)):
    """Dependency — raises 403 if admin not logged in."""
    token = get_admin_token(request)
    if not validate_admin_session(db, token):
        raise HTTPException(status_code=403, detail="Admin access required. Please log in at /admin/login.")


def is_admin(request: Request, db: Session) -> bool:
    token = get_admin_token(request)
    return validate_admin_session(db, token)


# ---------------------------------------------------------------------------
# Household rotation seeding
# ---------------------------------------------------------------------------

def apply_exact_household_rotation(db: Session):
    """
    Enforces the exact household rotation:
    - Nov 24 (Tue): Frank cleaning, Victor cooking
    - Nov 25 (Wed): Victor cleaning, Winnie cooking
    - Nov 26 (Thu): Winnie cleaning, Tracy cooking
    - Nov 27 (Fri): Tracy cleaning, Frank cooking
    And cycles continuously.
    """
    u_frank = db.query(User).filter(User.name.ilike("Frank%")).first()
    u_victor = db.query(User).filter(User.name.ilike("Victor%")).first()
    u_winnie = db.query(User).filter(User.name.ilike("Winnie%")).first()
    u_tracy = db.query(User).filter(User.name.ilike("Tracy%")).first()

    if not (u_frank and u_victor and u_winnie and u_tracy):
        return

    c_clean = db.query(Chore).filter(Chore.title.ilike("%Cleaning%")).first()
    c_cook = db.query(Chore).filter(Chore.title == "Cooking").first()

    if not (c_clean and c_cook):
        return

    # Frank -> Victor -> Winnie -> Tracy
    clean_rotation = [u_frank.id, u_victor.id, u_winnie.id, u_tracy.id]
    # Victor -> Winnie -> Tracy -> Frank
    cook_rotation = [u_victor.id, u_winnie.id, u_tracy.id, u_frank.id]

    c_clean.rotation_user_ids = ",".join(map(str, clean_rotation))
    c_clean.days_of_week = "0,1,2,3,4,5,6"
    c_clean.frequency = "daily"

    c_cook.rotation_user_ids = ",".join(map(str, cook_rotation))
    c_cook.days_of_week = "0,1,2,3,4,5,6"
    c_cook.frequency = "daily"

    today = date.today()
    ref_date = date(2026, 11, 24)

    for offset in range(14):
        target_date = today + timedelta(days=offset)
        diff = (target_date - ref_date).days
        idx = diff % 4

        for chore, rot in [(c_clean, clean_rotation), (c_cook, cook_rotation)]:
            expected_uid = rot[idx]
            existing = db.query(ChoreAssignment).filter(
                ChoreAssignment.chore_id == chore.id,
                ChoreAssignment.due_date == target_date
            ).first()

            if existing:
                if existing.status == "pending" and existing.user_id != expected_uid:
                    existing.user_id = expected_uid
                    existing.notified = False
            else:
                a = ChoreAssignment(
                    chore_id=chore.id,
                    user_id=expected_uid,
                    due_date=target_date,
                    status="pending",
                    notified=False
                )
                db.add(a)

    db.commit()


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure tables and background scheduler are up
    init_db()
    from seed_data import seed
    seed()
    db = SessionLocal()
    try:
        apply_exact_household_rotation(db)
    finally:
        db.close()
    start_scheduler()
    yield
    # Shutdown — scheduler stops automatically


app = FastAPI(title="ChoreHarmony", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, message: str = "", db: Session = Depends(get_db)):
    today = date.today()
    ensure_assignments_for_date(db, today)
    check_and_catchup_reminders()

    today_assignments = db.query(ChoreAssignment).filter(
        ChoreAssignment.due_date == today
    ).all()

    start_of_week = today - timedelta(days=today.weekday())
    week_days = []
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for i in range(7):
        day_date = start_of_week + timedelta(days=i)
        ensure_assignments_for_date(db, day_date)
        day_assignments = db.query(ChoreAssignment).filter(
            ChoreAssignment.due_date == day_date
        ).all()
        week_days.append({
            "date": day_date,
            "day_name": day_names[i],
            "formatted_date": day_date.strftime("%b %d"),
            "is_today": (day_date == today),
            "assignments": day_assignments
        })

    pending_swaps = db.query(SwapRequest).filter(SwapRequest.status == "pending").all()
    users = db.query(User).filter(User.is_active == True).order_by(
        User.karma_points.desc(), User.current_streak.desc()
    ).all()
    recent_logs = db.query(NotificationLog).order_by(
        NotificationLog.timestamp.desc()
    ).limit(10).all()

    reminder_hour = int(get_setting(db, "REMINDER_HOUR", "8") or 8)
    reminder_min = int(get_setting(db, "REMINDER_MINUTE", "0") or 0)
    reminder_time_str = f"{reminder_hour:02d}:{reminder_min:02d}"

    admin_logged_in = is_admin(request, db)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "active_page": "dashboard",
            "message": message,
            "formatted_date": today.strftime("%A, %B %d, %Y"),
            "today_assignments": today_assignments,
            "week_days": week_days,
            "pending_swaps": pending_swaps,
            "users": users,
            "recent_logs": recent_logs,
            "reminder_time": reminder_time_str,
            "admin_logged_in": admin_logged_in,
        }
    )


# ---------------------------------------------------------------------------
# Assignment actions (ADMIN ONLY: mark complete, reset)
# ---------------------------------------------------------------------------

@app.get("/assignments/{assignment_id}/complete")
@app.post("/assignments/{assignment_id}/complete")
def complete_assignment(
    assignment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    assignment = db.query(ChoreAssignment).filter(ChoreAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    if assignment.status != "completed":
        assignment.status = "completed"
        assignment.completed_at = datetime.utcnow()
        user = assignment.user
        if user:
            user.karma_points = (user.karma_points or 0) + 10
            user.current_streak = (user.current_streak or 0) + 1
        db.commit()

    return RedirectResponse(
        url="/?message=Chore+marked+as+completed!+10+Karma+points+awarded.",
        status_code=303
    )


@app.post("/assignments/{assignment_id}/reset")
def reset_assignment(
    assignment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    assignment = db.query(ChoreAssignment).filter(ChoreAssignment.id == assignment_id).first()
    if assignment and assignment.status == "completed":
        assignment.status = "pending"
        assignment.completed_at = None
        user = assignment.user
        if user and user.karma_points and user.karma_points >= 10:
            user.karma_points -= 10
        if user and user.current_streak and user.current_streak > 0:
            user.current_streak -= 1
        db.commit()
    return RedirectResponse(
        url="/?message=Chore+reset+back+to+pending.",
        status_code=303
    )


# ---------------------------------------------------------------------------
# Swap routes
# ---------------------------------------------------------------------------

@app.post("/swaps/create")
def create_swap(
    assignment_id: int = Form(...),
    target_user_id: int = Form(...),
    db: Session = Depends(get_db)
):
    assignment = db.query(ChoreAssignment).filter(ChoreAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    swap = SwapRequest(
        assignment_id=assignment.id,
        requester_id=assignment.user_id,
        target_user_id=target_user_id,
        status="pending"
    )
    db.add(swap)
    db.commit()

    return RedirectResponse(url="/?message=Swap+request+submitted+successfully!", status_code=303)


@app.post("/swaps/{swap_id}/respond")
def respond_swap(
    swap_id: int,
    request: Request,
    action: str = Form(...),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    swap = db.query(SwapRequest).filter(SwapRequest.id == swap_id).first()
    if not swap:
        raise HTTPException(status_code=404, detail="Swap request not found")

    if action == "accept":
        swap.status = "accepted"
        assignment = swap.assignment
        assignment.user_id = swap.target_user_id
        assignment.notified = False
        db.commit()
        return RedirectResponse(url="/?message=Chore+swap+accepted!+Schedule+updated.", status_code=303)
    else:
        swap.status = "declined"
        db.commit()
        return RedirectResponse(url="/?message=Swap+request+declined.", status_code=303)


# ---------------------------------------------------------------------------
# Chores
# ---------------------------------------------------------------------------

@app.get("/chores", response_class=HTMLResponse)
def list_chores(request: Request, message: str = "", db: Session = Depends(get_db)):
    chores = db.query(Chore).filter(Chore.is_active == True).all()
    users = db.query(User).filter(User.is_active == True).all()
    admin_logged_in = is_admin(request, db)
    return templates.TemplateResponse(
        request=request,
        name="chores.html",
        context={
            "active_page": "chores",
            "message": message,
            "chores": chores,
            "users": users,
            "admin_logged_in": admin_logged_in,
        }
    )


@app.post("/chores/create")
def create_chore(
    request: Request,
    title: str = Form(...),
    icon: str = Form("🧹"),
    description: str = Form(""),
    days_of_week: List[str] = Form(...),
    rotation_user_ids: List[str] = Form(...),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    chore = Chore(
        title=title,
        icon=icon,
        description=description,
        days_of_week=",".join(days_of_week),
        rotation_user_ids=",".join(rotation_user_ids),
        frequency="weekly" if len(days_of_week) < 7 else "daily",
        is_active=True
    )
    db.add(chore)
    db.commit()
    ensure_assignments_for_date(db, date.today())
    return RedirectResponse(url="/chores?message=New+chore+added+to+rotation+successfully!", status_code=303)


@app.post("/chores/{chore_id}/delete")
def delete_chore(
    chore_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    chore = db.query(Chore).filter(Chore.id == chore_id).first()
    if chore:
        chore.is_active = False
        db.commit()
    return RedirectResponse(url="/chores?message=Chore+removed+from+active+rotation.", status_code=303)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

@app.get("/members", response_class=HTMLResponse)
def list_members(request: Request, message: str = "", db: Session = Depends(get_db)):
    users = db.query(User).filter(User.is_active == True).all()
    admin_logged_in = is_admin(request, db)
    return templates.TemplateResponse(
        request=request,
        name="members.html",
        context={
            "active_page": "members",
            "message": message,
            "users": users,
            "admin_logged_in": admin_logged_in,
        }
    )


@app.post("/members/create")
def create_member(
    request: Request,
    name: str = Form(...),
    email: str = Form(""),
    telegram_chat_id: str = Form(""),
    avatar_color: str = Form("#4F46E5"),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    user = User(
        name=name.strip(),
        email=email.strip() or None,
        telegram_chat_id=telegram_chat_id.strip() or None,
        avatar_color=avatar_color,
        karma_points=0,
        current_streak=0,
        is_active=True
    )
    db.add(user)
    db.commit()
    return RedirectResponse(url="/members?message=New+housemate+added+to+household!", status_code=303)


@app.post("/members/{user_id}/update")
def update_member(
    user_id: int,
    request: Request,
    name: str = Form(...),
    email: str = Form(""),
    avatar_color: str = Form("#4F46E5"),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.name = name.strip()
        user.email = email.strip() or None
        user.avatar_color = avatar_color
        db.commit()
    return RedirectResponse(url="/members?message=Member+details+updated+successfully!", status_code=303)


@app.post("/members/{user_id}/deactivate")
def deactivate_member(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = False
        db.commit()
    return RedirectResponse(url="/members?message=Member+removed+from+household.", status_code=303)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@app.get("/history", response_class=HTMLResponse)
def history_view(request: Request, db: Session = Depends(get_db)):
    assignments = db.query(ChoreAssignment).order_by(
        ChoreAssignment.due_date.desc(),
        ChoreAssignment.id.desc()
    ).limit(100).all()
    admin_logged_in = is_admin(request, db)
    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={
            "active_page": "history",
            "assignments": assignments,
            "admin_logged_in": admin_logged_in,
        }
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request, message: str = "", db: Session = Depends(get_db)):
    keys = [
        "REMINDER_HOUR", "REMINDER_MINUTE",
        "BREVO_API_KEY", "BREVO_SENDER_EMAIL",
        "TELEGRAM_BOT_TOKEN",
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
        "APP_URL", "TIMEZONE"
    ]
    settings_data = {k: get_setting(db, k, "") for k in keys}
    admin_logged_in = is_admin(request, db)
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "message": message,
            "settings": settings_data,
            "admin_logged_in": admin_logged_in,
        }
    )


@app.post("/settings/save")
def save_settings(
    request: Request,
    REMINDER_HOUR: int = Form(8),
    REMINDER_MINUTE: int = Form(0),
    BREVO_API_KEY: str = Form(""),
    BREVO_SENDER_EMAIL: str = Form(""),
    TELEGRAM_BOT_TOKEN: str = Form(""),
    SMTP_HOST: str = Form(""),
    SMTP_PORT: str = Form("587"),
    SMTP_USER: str = Form(""),
    SMTP_PASSWORD: str = Form(""),
    SMTP_FROM: str = Form(""),
    APP_URL: str = Form("http://localhost:8000"),
    TIMEZONE: str = Form("Africa/Nairobi"),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    set_setting(db, "REMINDER_HOUR", str(REMINDER_HOUR))
    set_setting(db, "REMINDER_MINUTE", str(REMINDER_MINUTE))
    set_setting(db, "BREVO_API_KEY", BREVO_API_KEY.strip())
    set_setting(db, "BREVO_SENDER_EMAIL", BREVO_SENDER_EMAIL.strip())
    set_setting(db, "TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN.strip())
    set_setting(db, "SMTP_HOST", SMTP_HOST.strip())
    set_setting(db, "SMTP_PORT", SMTP_PORT.strip())
    set_setting(db, "SMTP_USER", SMTP_USER.strip())
    if SMTP_PASSWORD:
        set_setting(db, "SMTP_PASSWORD", SMTP_PASSWORD.replace(" ", "").strip())
    if SMTP_FROM:
        set_setting(db, "SMTP_FROM", SMTP_FROM.strip())
    set_setting(db, "APP_URL", APP_URL.strip())
    set_setting(db, "TIMEZONE", TIMEZONE.strip() or "Africa/Nairobi")

    reschedule_morning_reminders(REMINDER_HOUR, REMINDER_MINUTE, TIMEZONE.strip() or "Africa/Nairobi")

    return RedirectResponse(
        url="/settings?message=Settings+saved+and+morning+schedule+updated!",
        status_code=303
    )


# ---------------------------------------------------------------------------
# Admin-only sync/trigger
# ---------------------------------------------------------------------------

@app.get("/admin/sync-rotation")
@app.post("/admin/sync-rotation")
def sync_rotation(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    apply_exact_household_rotation(db)
    return RedirectResponse(url="/?message=Chore+schedule+synchronized!", status_code=303)


@app.get("/trigger-morning-reminders")
@app.post("/trigger-morning-reminders")
def trigger_reminders(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    apply_exact_household_rotation(db)
    count = process_morning_reminders(force_notify=True)
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(
            url=f"/?message=Dispatched+morning+reminders+for+{count}+chore(s)+successfully!",
            status_code=303
        )
    return {"status": "success", "dispatched_count": count}


# ---------------------------------------------------------------------------
# ADMIN PANEL routes
# ---------------------------------------------------------------------------

@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"error": error}
    )


@app.post("/admin/login")
def admin_login(
    response: Response,
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    if verify_admin_password(db, password):
        token = create_admin_session(db)
        redir = RedirectResponse(url="/admin", status_code=303)
        redir.set_cookie(
            key=ADMIN_COOKIE_NAME,
            value=token,
            httponly=True,
            max_age=28800,  # 8 hours
            samesite="lax"
        )
        return redir
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"error": "Incorrect password. Please try again."},
        status_code=401
    )


@app.get("/admin/logout")
def admin_logout(request: Request):
    redir = RedirectResponse(url="/", status_code=303)
    redir.delete_cookie(ADMIN_COOKIE_NAME)
    return redir


@app.get("/admin", response_class=HTMLResponse)
def admin_panel(
    request: Request,
    message: str = "",
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    today = date.today()
    users = db.query(User).filter(User.is_active == True).order_by(User.name).all()
    chores = db.query(Chore).filter(Chore.is_active == True).all()
    all_assignments = db.query(ChoreAssignment).filter(
        ChoreAssignment.due_date >= today - timedelta(days=3),
        ChoreAssignment.due_date <= today + timedelta(days=7)
    ).order_by(ChoreAssignment.due_date, ChoreAssignment.id).all()

    pending_swaps = db.query(SwapRequest).filter(SwapRequest.status == "pending").all()
    recent_logs = db.query(NotificationLog).order_by(
        NotificationLog.timestamp.desc()
    ).limit(30).all()

    scheduler_status = get_scheduler_status()

    # Email config check
    brevo_key = get_setting(db, "BREVO_API_KEY", "")
    brevo_sender = get_setting(db, "BREVO_SENDER_EMAIL", "")
    smtp_host = get_setting(db, "SMTP_HOST", "")
    smtp_user = get_setting(db, "SMTP_USER", "")
    smtp_pass = get_setting(db, "SMTP_PASSWORD", "")
    app_url = get_setting(db, "APP_URL", "http://localhost:8000")

    email_configured = bool(
        (brevo_key and brevo_sender) or
        (smtp_host and smtp_user and smtp_pass)
    )
    email_method = "Brevo API" if (brevo_key and brevo_sender) else ("SMTP" if (smtp_host and smtp_user) else "Not configured")

    # Count stats
    total_pending = db.query(ChoreAssignment).filter(
        ChoreAssignment.due_date == today,
        ChoreAssignment.status == "pending"
    ).count()
    total_completed = db.query(ChoreAssignment).filter(
        ChoreAssignment.due_date == today,
        ChoreAssignment.status == "completed"
    ).count()
    not_notified = db.query(ChoreAssignment).filter(
        ChoreAssignment.due_date == today,
        ChoreAssignment.status == "pending",
        ChoreAssignment.notified == False
    ).count()

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "active_page": "admin",
            "message": message,
            "admin_logged_in": True,
            "users": users,
            "chores": chores,
            "all_assignments": all_assignments,
            "pending_swaps": pending_swaps,
            "recent_logs": recent_logs,
            "scheduler_status": scheduler_status,
            "email_configured": email_configured,
            "email_method": email_method,
            "app_url": app_url,
            "today": today,
            "today_pending": total_pending,
            "today_completed": total_completed,
            "not_notified": not_notified,
        }
    )


@app.post("/admin/assignments/{assignment_id}/complete")
def admin_complete_assignment(
    assignment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    assignment = db.query(ChoreAssignment).filter(ChoreAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if assignment.status != "completed":
        assignment.status = "completed"
        assignment.completed_at = datetime.utcnow()
        user = assignment.user
        if user:
            user.karma_points = (user.karma_points or 0) + 10
            user.current_streak = (user.current_streak or 0) + 1
        db.commit()
    return RedirectResponse(url="/admin?message=Chore+marked+as+completed!", status_code=303)


@app.post("/admin/assignments/{assignment_id}/reset")
def admin_reset_assignment(
    assignment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    assignment = db.query(ChoreAssignment).filter(ChoreAssignment.id == assignment_id).first()
    if assignment and assignment.status == "completed":
        assignment.status = "pending"
        assignment.completed_at = None
        user = assignment.user
        if user and user.karma_points and user.karma_points >= 10:
            user.karma_points -= 10
        if user and user.current_streak and user.current_streak > 0:
            user.current_streak -= 1
        db.commit()
    return RedirectResponse(url="/admin?message=Chore+reset+to+pending.", status_code=303)


@app.post("/admin/assignments/{assignment_id}/delete")
def admin_delete_assignment(
    assignment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    assignment = db.query(ChoreAssignment).filter(ChoreAssignment.id == assignment_id).first()
    if assignment:
        db.delete(assignment)
        db.commit()
    return RedirectResponse(url="/admin?message=Assignment+removed.", status_code=303)


@app.post("/admin/logs/clear")
def admin_clear_logs(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    db.query(NotificationLog).delete()
    db.commit()
    return RedirectResponse(url="/admin?message=Notification+logs+cleared.", status_code=303)


@app.post("/admin/users/{user_id}/reset-karma")
def admin_reset_karma(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.karma_points = 0
        user.current_streak = 0
        db.commit()
    return RedirectResponse(url="/admin?message=Karma+and+streak+reset+for+member.", status_code=303)


@app.post("/admin/change-password")
def admin_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    if not verify_admin_password(db, current_password):
        return RedirectResponse(url="/admin?message=ERROR:+Current+password+is+incorrect.", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse(url="/admin?message=ERROR:+New+passwords+do+not+match.", status_code=303)
    if len(new_password) < 6:
        return RedirectResponse(url="/admin?message=ERROR:+Password+must+be+at+least+6+characters.", status_code=303)
    set_admin_password(db, new_password)
    return RedirectResponse(url="/admin?message=Admin+password+changed+successfully!", status_code=303)


@app.post("/admin/test-email")
def admin_test_email(
    request: Request,
    test_email: str = Form(...),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    """Sends a live diagnostic test email to the specified address."""
    from notifier import send_brevo_api_notification, send_email_notification
    brevo_key = get_setting(db, "BREVO_API_KEY", "")
    brevo_sender = get_setting(db, "BREVO_SENDER_EMAIL", "")
    smtp_host = get_setting(db, "SMTP_HOST", "")
    smtp_port_str = get_setting(db, "SMTP_PORT", "587")
    smtp_port = int(smtp_port_str) if smtp_port_str.isdigit() else 587
    smtp_user = get_setting(db, "SMTP_USER", "")
    smtp_pass = get_setting(db, "SMTP_PASSWORD", "")
    smtp_from = get_setting(db, "SMTP_FROM", "") or smtp_user
    app_url = get_setting(db, "APP_URL", "http://localhost:8000")

    result_msg = ""
    success = False

    if brevo_key and brevo_sender:
        ok, msg = send_brevo_api_notification(
            api_key=brevo_key,
            from_email=brevo_sender,
            from_name="ChoreHarmony",
            to_email=test_email.strip(),
            user_name="Admin Tester",
            chore_title="Kitchen Cleanliness Test",
            chore_icon="🧹",
            chore_description="Diagnostic test email from ChoreHarmony admin panel.",
            app_url=app_url
        )
        if ok:
            success = True
            result_msg = f"Brevo API Success: {msg}"
        else:
            result_msg = f"Brevo API Error: {msg}"

    if not success and smtp_host and smtp_user and smtp_pass:
        smtp_ok, smtp_msg = send_email_notification(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_pass=smtp_pass,
            from_email=smtp_from,
            to_email=test_email.strip(),
            user_name="Admin Tester",
            chore_title="Kitchen Cleanliness Test",
            chore_icon="🧹",
            chore_description="Diagnostic test email from ChoreHarmony admin panel.",
            app_url=app_url
        )
        if smtp_ok:
            success = True
            result_msg = f"SMTP Success: {smtp_msg}"
        else:
            result_msg = f"{result_msg} | SMTP Error: {smtp_msg}" if result_msg else f"SMTP Error: {smtp_msg}"

    if not success and not result_msg:
        result_msg = "Neither Brevo API nor SMTP credentials are fully configured."

    prefix = "Test+email+sent+successfully!+" if success else "ERROR:+Email+failed.+"
    clean_msg = result_msg.replace(" ", "+").replace("\n", "+")[:120]
    return RedirectResponse(url=f"/admin?message={prefix}{clean_msg}", status_code=303)


# ---------------------------------------------------------------------------
# Scheduler health check & Cron API
# ---------------------------------------------------------------------------

@app.get("/api/scheduler/status")
def scheduler_status_api(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin)
):
    return get_scheduler_status()


@app.get("/api/health")
def health_check():
    """Public health check endpoint for uptime monitors."""
    status = get_scheduler_status()
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "scheduler_running": status["running"],
        "scheduler_next_run": status.get("next_run")
    }


@app.get("/api/cron/trigger")
@app.post("/api/cron/trigger")
def cron_trigger():
    """
    Webhook endpoint for external cron jobs (e.g. Render Cron Job, cron-job.org).
    Executes morning reminder checks and self-heals any missed assignments.
    """
    count = process_morning_reminders(force_notify=False)
    return {
        "status": "ok",
        "dispatched_count": count,
        "timestamp": datetime.utcnow().isoformat()
    }
