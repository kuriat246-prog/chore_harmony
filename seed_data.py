from datetime import date, timedelta
from database import init_db, SessionLocal, User, Chore, ChoreAssignment, NotificationLog
from scheduler import ensure_assignments_for_date

def seed():
    init_db()
    db = SessionLocal()

    # Check if data already exists
    if db.query(User).count() > 0:
        print("Database already has records. Skipping seed.")
        db.close()
        return

    print("Seeding initial friends and chores...")

    # 1. Create Friends
    u1 = User(name="Tracy", email="kuriat246@gmail.com", avatar_color="#4F46E5", karma_points=40, current_streak=4)
    u2 = User(name="Frank", email="Wamwithafrank@gmail.com", avatar_color="#059669", karma_points=30, current_streak=3)
    u3 = User(name="Winnie", email="w4024569@gmail.com", avatar_color="#D97706", karma_points=20, current_streak=2)
    u4 = User(name="Victor", email="victormlupi87@gmail.com", avatar_color="#7C3AED", karma_points=10, current_streak=1)

    db.add_all([u1, u2, u3, u4])
    db.commit()

    # 2. Create Common Conflict Chores
    c1 = Chore(
        title="Cleaning kitchen counters and cooking pots",
        icon="🍽️",
        description="1. Clean the cooking pots.\n2. Clean the sink & wipe down counters .\n3. Scrub out sink.",
        days_of_week="0,1,2,3,4,5,6",  # Everyday
        frequency="daily",
        rotation_user_ids=f"{u2.id},{u4.id},{u3.id},{u1.id}",  # Frank, Victor, Winnie, Tracy
        is_active=True
    )

    c2 = Chore(
        title="Cooking",
        icon="🍳",
        description="1. Cook the specified meal of the day.\n2. Serve the ready meal.",
        days_of_week="0,1,2,3,4,5,6",  # Everyday
        frequency="daily",
        rotation_user_ids=f"{u4.id},{u3.id},{u1.id},{u2.id}",  # Victor, Winnie, Tracy, Frank
        is_active=True
    )

    
    db.add_all([c1, c2,])
    db.commit()

    # 3. Generate past assignments to build realistic history & today's assignments
    today = date.today()
    for days_ago in range(4, 0, -1):
        past_date = today - timedelta(days=days_ago)
        ensure_assignments_for_date(db, past_date)
        # Mark past assignments as completed
        past_assignments = db.query(ChoreAssignment).filter(ChoreAssignment.due_date == past_date).all()
        for a in past_assignments:
            a.status = "completed"
            a.completed_at = past_date

    # Ensure today and next few days
    ensure_assignments_for_date(db, today)
    for days_ahead in range(1, 4):
        future_date = today + timedelta(days=days_ahead)
        ensure_assignments_for_date(db, future_date)

    # 4. Add mock initial notification log
    log = NotificationLog(
        user_id=u1.id,
        chore_title=" Cleaning kitchen counters & claening cooking pots",
        channel="in_app",
        status="sent",
        details="Morning reminder dispatched successfully at 08:00 AM."
    )
    db.add(log)

    db.commit()
    db.close()
    print("Seed complete! Created 4 friends, 4 core chores, and realistic schedule history.")

if __name__ == "__main__":
    seed()
