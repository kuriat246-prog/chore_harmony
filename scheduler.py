import logging
from datetime import date, datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from database import SessionLocal, Chore, ChoreAssignment, User, get_setting
from notifier import dispatch_chore_reminder

logger = logging.getLogger("chore_scheduler")
scheduler = BackgroundScheduler()


def ensure_assignments_for_date(db, target_date: date):
    """
    Ensures that every active chore that is scheduled for target_date
    has an assigned user according to the rotation order.
    """
    weekday_str = str(target_date.weekday())  # 0 = Monday, 6 = Sunday
    active_chores = db.query(Chore).filter(Chore.is_active == True).all()

    for chore in active_chores:
        # Check if chore runs on this day of the week
        allowed_days = [d.strip() for d in (chore.days_of_week or "").split(",") if d.strip()]
        if allowed_days and weekday_str not in allowed_days:
            continue

        # Check if an assignment already exists for this chore and date
        existing = db.query(ChoreAssignment).filter(
            ChoreAssignment.chore_id == chore.id,
            ChoreAssignment.due_date == target_date
        ).first()

        if existing:
            continue

        # Determine next user from rotation
        user_ids = [int(uid.strip()) for uid in (chore.rotation_user_ids or "").split(",") if uid.strip().isdigit()]
        if not user_ids:
            # Fallback to all active users if chore has no rotation list
            all_users = db.query(User).filter(User.is_active == True).order_by(User.id).all()
            user_ids = [u.id for u in all_users]

        if not user_ids:
            continue

        # Look for the most recent assignment of this chore before target_date to determine next user
        last_assignment = db.query(ChoreAssignment).filter(
            ChoreAssignment.chore_id == chore.id,
            ChoreAssignment.due_date < target_date
        ).order_by(ChoreAssignment.due_date.desc(), ChoreAssignment.id.desc()).first()

        next_user_id = user_ids[0]
        if last_assignment and last_assignment.user_id in user_ids:
            current_index = user_ids.index(last_assignment.user_id)
            next_user_id = user_ids[(current_index + 1) % len(user_ids)]

        assignment = ChoreAssignment(
            chore_id=chore.id,
            user_id=next_user_id,
            due_date=target_date,
            status="pending",
            notified=False
        )
        db.add(assignment)

    db.commit()


def process_morning_reminders(force_notify: bool = False):
    """
    Main job called every morning (e.g. 8:00 AM).
    1. Ensures assignments exist for today.
    2. Sends notifications to the assigned friends.
    """
    db = SessionLocal()
    try:
        today = date.today()
        ensure_assignments_for_date(db, today)

        # Get all assignments for today
        query = db.query(ChoreAssignment).filter(
            ChoreAssignment.due_date == today,
            ChoreAssignment.status == "pending"
        )
        if not force_notify:
            query = query.filter(ChoreAssignment.notified == False)

        assignments_today = query.all()
        app_url = get_setting(db, "APP_URL", "http://localhost:8000")

        dispatched_count = 0
        for assignment in assignments_today:
            dispatch_chore_reminder(db, assignment, app_url=app_url)
            dispatched_count += 1

        logger.info(f"Morning chore check complete. Dispatched {dispatched_count} reminders for {today}.")
        return dispatched_count
    finally:
        db.close()


def start_scheduler():
    """Starts the background scheduler to run every morning."""
    if scheduler.running:
        return

    db = SessionLocal()
    try:
        hour = int(get_setting(db, "REMINDER_HOUR", "8") or 8)
        minute = int(get_setting(db, "REMINDER_MINUTE", "0") or 0)
    finally:
        db.close()

    trigger = CronTrigger(hour=hour, minute=minute)
    scheduler.add_job(
        process_morning_reminders,
        trigger=trigger,
        id="daily_morning_reminders",
        name="Daily Morning Chore Reminders",
        replace_existing=True
    )
    scheduler.start()
    logger.info(f"Scheduler started. Morning reminders configured for {hour:02d}:{minute:02d} daily.")


def reschedule_morning_reminders(hour: int, minute: int):
    """Dynamically updates the morning reminder schedule without restarting the server."""
    if scheduler.running:
        trigger = CronTrigger(hour=hour, minute=minute)
        scheduler.reschedule_job("daily_morning_reminders", trigger=trigger)
        logger.info(f"Rescheduled morning reminders to {hour:02d}:{minute:02d} daily.")
