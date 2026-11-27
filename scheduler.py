import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError
from database import SessionLocal, Chore, ChoreAssignment, User, get_setting
from notifier import dispatch_chore_reminder

logger = logging.getLogger("chore_scheduler")
scheduler = BackgroundScheduler()


def get_current_time_in_tz(tz_name: str = "Africa/Nairobi") -> datetime:
    try:
        return datetime.now(ZoneInfo(tz_name or "Africa/Nairobi"))
    except Exception:
        return datetime.now()


def ensure_assignments_for_date(db, target_date: date):
    """
    Ensures that every active chore scheduled for target_date
    has an assigned user according to the rotation order.
    Uses anchored date-based rotation to eliminate drift:
      - Nov 24: Frank cleaning, Victor cooking
      - Nov 25 (Wed): Victor cleaning, Winnie cooking
      - Nov 26 (Thu): Winnie cleaning, Tracy cooking
      - Nov 27 (Fri): Tracy cleaning, Frank cooking
    """
    weekday_str = str(target_date.weekday())  # 0 = Monday, 6 = Sunday
    active_chores = db.query(Chore).filter(Chore.is_active == True).all()

    u_frank = db.query(User).filter(User.name.ilike("Frank%")).first()
    u_victor = db.query(User).filter(User.name.ilike("Victor%")).first()
    u_winnie = db.query(User).filter(User.name.ilike("Winnie%")).first()
    u_tracy = db.query(User).filter(User.name.ilike("Tracy%")).first()

    c_clean = db.query(Chore).filter(Chore.title.ilike("%Cleaning%")).first()
    c_cook = db.query(Chore).filter(Chore.title == "Cooking").first()

    if u_frank and u_victor and u_winnie and u_tracy and c_clean and c_cook:
        clean_rotation = [u_frank.id, u_victor.id, u_winnie.id, u_tracy.id]
        cook_rotation = [u_victor.id, u_winnie.id, u_tracy.id, u_frank.id]
        ref_date = date(2026, 11, 24)
        diff = (target_date - ref_date).days
        idx = diff % 4

        for chore, rot in [(c_clean, clean_rotation), (c_cook, cook_rotation)]:
            if not chore.is_active:
                continue
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
                assignment = ChoreAssignment(
                    chore_id=chore.id,
                    user_id=expected_uid,
                    due_date=target_date,
                    status="pending",
                    notified=False
                )
                db.add(assignment)

        db.commit()
        return

    # Fallback to general rotation
    for chore in active_chores:
        allowed_days = [d.strip() for d in (chore.days_of_week or "").split(",") if d.strip()]
        if allowed_days and weekday_str not in allowed_days:
            continue

        existing = db.query(ChoreAssignment).filter(
            ChoreAssignment.chore_id == chore.id,
            ChoreAssignment.due_date == target_date
        ).first()

        if existing:
            continue

        user_ids = [int(uid.strip()) for uid in (chore.rotation_user_ids or "").split(",") if uid.strip().isdigit()]
        if not user_ids:
            all_users = db.query(User).filter(User.is_active == True).order_by(User.id).all()
            user_ids = [u.id for u in all_users]

        if not user_ids:
            continue

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
    Main reminder job called every morning (e.g. 8:00 AM).
    1. Ensures assignments exist for today.
    2. Sends notifications to the assigned housemates.
    Returns the count of dispatched reminders.
    """
    db = SessionLocal()
    try:
        tz = get_setting(db, "TIMEZONE", "Africa/Nairobi") or "Africa/Nairobi"
        today = get_current_time_in_tz(tz).date()
        logger.info(f"[Scheduler] Running morning reminder job for {today} ({tz})...")
        ensure_assignments_for_date(db, today)

        # Get all pending assignments for today
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
            try:
                results = dispatch_chore_reminder(db, assignment, app_url=app_url)
                dispatched_count += 1
                logger.info(f"[Scheduler] Dispatched reminder for assignment #{assignment.id}")
            except Exception as e:
                logger.error(f"[Scheduler] Error dispatching reminder for assignment #{assignment.id}: {e}", exc_info=True)

        logger.info(f"[Scheduler] Morning chore check complete. Dispatched {dispatched_count} reminders for {today}.")
        return dispatched_count
    except Exception as e:
        logger.error(f"[Scheduler] Fatal error in morning reminder job: {e}", exc_info=True)
        return 0
    finally:
        db.close()


def check_and_catchup_reminders():
    """
    Self-healing catch-up:
    If the server was sleeping (e.g. Render free tier spinning down overnight)
    and woke up after the scheduled morning hour, this checks if today's
    assignments are still un-notified and dispatches them immediately.
    """
    db = SessionLocal()
    try:
        hour = int(get_setting(db, "REMINDER_HOUR", "8") or 8)
        minute = int(get_setting(db, "REMINDER_MINUTE", "0") or 0)
        tz = get_setting(db, "TIMEZONE", "Africa/Nairobi") or "Africa/Nairobi"

        now = get_current_time_in_tz(tz)
        today = now.date()
        current_minute_of_day = now.hour * 60 + now.minute
        scheduled_minute_of_day = hour * 60 + minute

        # Only catch up if current time is past the scheduled time
        if current_minute_of_day >= scheduled_minute_of_day:
            ensure_assignments_for_date(db, today)
            unnotified = db.query(ChoreAssignment).filter(
                ChoreAssignment.due_date == today,
                ChoreAssignment.status == "pending",
                ChoreAssignment.notified == False
            ).count()

            if unnotified > 0:
                logger.info(f"[Scheduler] Catch-up triggered: {unnotified} pending chores for {today} not yet notified. Dispatching now...")
                count = process_morning_reminders(force_notify=False)
                logger.info(f"[Scheduler] Catch-up complete: {count} reminders sent.")
    except Exception as e:
        logger.error(f"[Scheduler] Catch-up check failed: {e}", exc_info=True)
    finally:
        db.close()


def start_scheduler():
    """Starts the background scheduler to run every morning with 8-hour misfire grace time."""
    if scheduler.running:
        logger.info("[Scheduler] Already running — skipping start.")
        return

    db = SessionLocal()
    try:
        hour = int(get_setting(db, "REMINDER_HOUR", "8") or 8)
        minute = int(get_setting(db, "REMINDER_MINUTE", "0") or 0)
        tz = get_setting(db, "TIMEZONE", "Africa/Nairobi") or "Africa/Nairobi"
    finally:
        db.close()

    trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
    # misfire_grace_time=28800 (8 hours): if server was asleep at 8:00 AM, runs on wakeup!
    scheduler.add_job(
        process_morning_reminders,
        trigger=trigger,
        id="daily_morning_reminders",
        name="Daily Morning Chore Reminders",
        replace_existing=True,
        misfire_grace_time=28800,
        coalesce=True
    )
    scheduler.start()
    logger.info(f"[Scheduler] Started. Morning reminders at {hour:02d}:{minute:02d} ({tz}) daily (with 8h misfire grace).")

    # Run catch-up check in case server just started after morning hour
    try:
        check_and_catchup_reminders()
    except Exception as e:
        logger.warning(f"[Scheduler] Initial catch-up check warning: {e}")


def reschedule_morning_reminders(hour: int, minute: int, timezone: str = "Africa/Nairobi"):
    """Dynamically updates the morning reminder schedule without restarting the server."""
    trigger = CronTrigger(hour=hour, minute=minute, timezone=timezone)
    if scheduler.running:
        try:
            scheduler.reschedule_job(
                "daily_morning_reminders",
                trigger=trigger
            )
            logger.info(f"[Scheduler] Rescheduled morning reminders to {hour:02d}:{minute:02d} ({timezone}).")
        except JobLookupError:
            scheduler.add_job(
                process_morning_reminders,
                trigger=trigger,
                id="daily_morning_reminders",
                name="Daily Morning Chore Reminders",
                replace_existing=True,
                misfire_grace_time=28800,
                coalesce=True
            )
            logger.info(f"[Scheduler] Created new job at {hour:02d}:{minute:02d} ({timezone}).")
    else:
        logger.warning("[Scheduler] Scheduler not running — cannot reschedule.")


def get_scheduler_status() -> dict:
    """Returns current scheduler status and next run time."""
    if not scheduler.running:
        return {"running": False, "next_run": None, "job_count": 0}

    jobs = scheduler.get_jobs()
    next_run = None
    for job in jobs:
        if job.id == "daily_morning_reminders" and job.next_run_time:
            next_run = job.next_run_time.isoformat()
            break

    return {
        "running": True,
        "job_count": len(jobs),
        "next_run": next_run,
        "jobs": [{"id": j.id, "name": j.name, "next_run": j.next_run_time.isoformat() if j.next_run_time else None} for j in jobs]
    }
