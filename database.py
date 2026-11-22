import os
from datetime import datetime, date
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, Date, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chores.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=True)
    telegram_chat_id = Column(String(100), nullable=True)
    avatar_color = Column(String(20), default="#4F46E5")  # Tailwind indigo-600
    karma_points = Column(Integer, default=0)
    current_streak = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    assignments = relationship("ChoreAssignment", back_populates="user", cascade="all, delete-orphan")


class Chore(Base):
    __tablename__ = "chores"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)  # Clear definition of "Done" to eliminate ambiguity
    frequency = Column(String(50), default="daily")  # daily, weekly, weekdays, custom
    days_of_week = Column(String(50), default="0,1,2,3,4,5,6")  # 0=Monday, 6=Sunday
    icon = Column(String(10), default="🧹")
    rotation_user_ids = Column(String(255), default="")  # Comma separated user IDs for rotation
    is_active = Column(Boolean, default=True)

    assignments = relationship("ChoreAssignment", back_populates="chore", cascade="all, delete-orphan")


class ChoreAssignment(Base):
    __tablename__ = "chore_assignments"

    id = Column(Integer, primary_key=True, index=True)
    chore_id = Column(Integer, ForeignKey("chores.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    due_date = Column(Date, nullable=False, index=True)
    status = Column(String(20), default="pending")  # pending, completed, swapped, skipped
    completed_at = Column(DateTime, nullable=True)
    notified = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)

    chore = relationship("Chore", back_populates="assignments")
    user = relationship("User", back_populates="assignments")


class SwapRequest(Base):
    __tablename__ = "swap_requests"

    id = Column(Integer, primary_key=True, index=True)
    assignment_id = Column(Integer, ForeignKey("chore_assignments.id"), nullable=False)
    requester_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    target_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(20), default="pending")  # pending, accepted, declined
    created_at = Column(DateTime, default=datetime.utcnow)

    assignment = relationship("ChoreAssignment")
    requester = relationship("User", foreign_keys=[requester_id])
    target_user = relationship("User", foreign_keys=[target_user_id])


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    chore_title = Column(String(150), nullable=False)
    channel = Column(String(50), default="in_app")  # email, telegram, in_app
    status = Column(String(20), default="sent")  # sent, failed
    details = Column(Text, nullable=True)

    user = relationship("User")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_setting(db, key: str, default: str = "") -> str:
    setting = db.query(AppSetting).filter(AppSetting.key == key).first()
    if setting and setting.value is not None:
        return setting.value
    return os.getenv(key, default)


def set_setting(db, key: str, value: str):
    setting = db.query(AppSetting).filter(AppSetting.key == key).first()
    if setting:
        setting.value = value
    else:
        setting = AppSetting(key=key, value=value)
        db.add(setting)
    db.commit()
