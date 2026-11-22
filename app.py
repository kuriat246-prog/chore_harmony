import os
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import (
    init_db, get_db, SessionLocal,
    User, Chore, ChoreAssignment, SwapRequest, NotificationLog,
    get_setting, set_setting
)
from scheduler import (
    start_scheduler, ensure_assignments_for_date,
    process_morning_reminders, reschedule_morning_reminders
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure tables and background scheduler are up
    init_db()
    from seed_data import seed
    seed()
    start_scheduler()
    yield
    # Shutdown logic if needed


app = FastAPI(title="ChoreHarmony", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, message: str = "", db: Session = Depends(get_db)):
    today = date.today()
    ensure_assignments_for_date(db, today)

    # 1. Today's assignments
    today_assignments = db.query(ChoreAssignment).filter(
        ChoreAssignment.due_date == today
    ).all()

    # 2. Week calculation (Monday to Sunday)
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

    # 3. Pending swap requests
    pending_swaps = db.query(SwapRequest).filter(
        SwapRequest.status == "pending"
    ).all()

    # 4. Users sorted by karma & streak
    users = db.query(User).filter(User.is_active == True).order_by(
        User.karma_points.desc(), User.current_streak.desc()
    ).all()

    # 5. Recent notification logs
    recent_logs = db.query(NotificationLog).order_by(
        NotificationLog.timestamp.desc()
    ).limit(10).all()

    reminder_hour = int(get_setting(db, "REMINDER_HOUR", "8") or 8)
    reminder_min = int(get_setting(db, "REMINDER_MINUTE", "0") or 0)
    reminder_time_str = f"{reminder_hour:02d}:{reminder_min:02d}"

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
            "reminder_time": reminder_time_str
        }
    )


@app.get("/assignments/{assignment_id}/complete")
@app.post("/assignments/{assignment_id}/complete")
def complete_assignment(assignment_id: int, db: Session = Depends(get_db)):
    assignment = db.query(ChoreAssignment).filter(ChoreAssignment.id == assignment_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    if assignment.status != "completed":
        assignment.status = "completed"
        assignment.completed_at = datetime.utcnow()

        # Reward karma & increment streak
        user = assignment.user
        if user:
            user.karma_points = (user.karma_points or 0) + 10
            user.current_streak = (user.current_streak or 0) + 1

        db.commit()

    return RedirectResponse(
        url="/?message=Chore+marked+as+completed!+10+Karma+points+awarded+🎉",
        status_code=303
    )


@app.post("/assignments/{assignment_id}/reset")
def reset_assignment(assignment_id: int, db: Session = Depends(get_db)):
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
        url="/?message=Chore+reset+back+to+pending!+You+can+now+test+marking+it+done+again.",
        status_code=303
    )


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

    return RedirectResponse(
        url="/?message=Swap+request+submitted+successfully!+🤝",
        status_code=303
    )


@app.post("/swaps/{swap_id}/respond")
def respond_swap(
    swap_id: int,
    action: str = Form(...),  # accept or decline
    db: Session = Depends(get_db)
):
    swap = db.query(SwapRequest).filter(SwapRequest.id == swap_id).first()
    if not swap:
        raise HTTPException(status_code=404, detail="Swap request not found")

    if action == "accept":
        swap.status = "accepted"
        assignment = swap.assignment
        # Transfer responsibility
        assignment.user_id = swap.target_user_id
        assignment.notified = False  # trigger re-notification if needed
        db.commit()
        return RedirectResponse(
            url="/?message=Chore+swap+accepted!+Schedule+updated+seamlessly.",
            status_code=303
        )
    else:
        swap.status = "declined"
        db.commit()
        return RedirectResponse(
            url="/?message=Swap+request+declined.",
            status_code=303
        )


@app.get("/chores", response_class=HTMLResponse)
def list_chores(request: Request, message: str = "", db: Session = Depends(get_db)):
    chores = db.query(Chore).filter(Chore.is_active == True).all()
    users = db.query(User).filter(User.is_active == True).all()

    return templates.TemplateResponse(
        request=request,
        name="chores.html",
        context={
            "active_page": "chores",
            "message": message,
            "chores": chores,
            "users": users
        }
    )


@app.post("/chores/create")
def create_chore(
    title: str = Form(...),
    icon: str = Form("🧹"),
    description: str = Form(""),
    days_of_week: List[str] = Form(...),
    rotation_user_ids: List[str] = Form(...),
    db: Session = Depends(get_db)
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

    # Pre-populate assignments for today & this week
    today = date.today()
    ensure_assignments_for_date(db, today)

    return RedirectResponse(
        url="/chores?message=New+chore+added+to+rotation+successfully!",
        status_code=303
    )


@app.post("/chores/{chore_id}/delete")
def delete_chore(chore_id: int, db: Session = Depends(get_db)):
    chore = db.query(Chore).filter(Chore.id == chore_id).first()
    if chore:
        chore.is_active = False
        db.commit()
    return RedirectResponse(
        url="/chores?message=Chore+removed+from+active+rotation.",
        status_code=303
    )


@app.get("/members", response_class=HTMLResponse)
def list_members(request: Request, message: str = "", db: Session = Depends(get_db)):
    users = db.query(User).filter(User.is_active == True).all()
    return templates.TemplateResponse(
        request=request,
        name="members.html",
        context={
            "active_page": "members",
            "message": message,
            "users": users
        }
    )


@app.post("/members/create")
def create_member(
    name: str = Form(...),
    email: str = Form(""),
    telegram_chat_id: str = Form(""),
    avatar_color: str = Form("#4F46E5"),
    db: Session = Depends(get_db)
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

    return RedirectResponse(
        url="/members?message=New+housemate+added+to+household!",
        status_code=303
    )


@app.post("/members/{user_id}/update")
def update_member(
    user_id: int,
    name: str = Form(...),
    email: str = Form(""),
    avatar_color: str = Form("#4F46E5"),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.name = name.strip()
        user.email = email.strip() or None
        user.avatar_color = avatar_color
        db.commit()
    return RedirectResponse(
        url="/members?message=Member+details+updated+successfully!",
        status_code=303
    )


@app.get("/history", response_class=HTMLResponse)
def history_view(request: Request, db: Session = Depends(get_db)):
    assignments = db.query(ChoreAssignment).order_by(
        ChoreAssignment.due_date.desc(),
        ChoreAssignment.id.desc()
    ).limit(100).all()

    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={
            "active_page": "history",
            "assignments": assignments
        }
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request, message: str = "", db: Session = Depends(get_db)):
    keys = [
        "REMINDER_HOUR", "REMINDER_MINUTE",
        "TELEGRAM_BOT_TOKEN",
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
        "APP_URL"
    ]
    settings_data = {k: get_setting(db, k, "") for k in keys}

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "message": message,
            "settings": settings_data
        }
    )


@app.post("/settings/save")
def save_settings(
    REMINDER_HOUR: int = Form(8),
    REMINDER_MINUTE: int = Form(0),
    TELEGRAM_BOT_TOKEN: str = Form(""),
    SMTP_HOST: str = Form(""),
    SMTP_PORT: str = Form("587"),
    SMTP_USER: str = Form(""),
    SMTP_PASSWORD: str = Form(""),
    APP_URL: str = Form("http://localhost:8000"),
    db: Session = Depends(get_db)
):
    set_setting(db, "REMINDER_HOUR", str(REMINDER_HOUR))
    set_setting(db, "REMINDER_MINUTE", str(REMINDER_MINUTE))
    set_setting(db, "TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN.strip())
    set_setting(db, "SMTP_HOST", SMTP_HOST.strip())
    set_setting(db, "SMTP_PORT", SMTP_PORT.strip())
    set_setting(db, "SMTP_USER", SMTP_USER.strip())
    if SMTP_PASSWORD:
        set_setting(db, "SMTP_PASSWORD", SMTP_PASSWORD.replace(" ", "").strip())
    set_setting(db, "APP_URL", APP_URL.strip())

    # Dynamically update the scheduler
    reschedule_morning_reminders(REMINDER_HOUR, REMINDER_MINUTE)

    return RedirectResponse(
        url="/settings?message=Settings+saved+and+morning+schedule+updated!",
        status_code=303
    )


@app.post("/trigger-morning-reminders")
def trigger_reminders():
    count = process_morning_reminders(force_notify=True)
    return RedirectResponse(
        url=f"/?message=Dispatched+morning+reminders+for+{count}+chore(s)+successfully!+☀️",
        status_code=303
    )
