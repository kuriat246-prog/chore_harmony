import os
import hashlib
import logging
import secrets
import tempfile
from datetime import datetime, date
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, Date, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.pool import NullPool

load_dotenv()

logger = logging.getLogger("chore_database")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip().strip('"').strip("'")
IS_POSTGRES = False

if DATABASE_URL:
    # Neon, Supabase, and Heroku often provide postgres:// URLs, but SQLAlchemy 2.0 requires postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    IS_POSTGRES = DATABASE_URL.startswith("postgresql://")
    connect_args = {}
else:
    # Local SQLite fallback
    # On Vercel, the root filesystem is read-only, so fallback to system temp dir if no DB URL is supplied
    if os.getenv("VERCEL"):
        DB_PATH = os.path.join(tempfile.gettempdir(), "chores.db")
    else:
        DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chores.db")
    DATABASE_URL = f"sqlite:///{DB_PATH}"
    connect_args = {"check_same_thread": False}

# Use NullPool on Vercel Serverless to avoid stale/dead pooled SSL connections across Lambda freezes
engine_kwargs = {"connect_args": connect_args, "pool_pre_ping": True}
if os.getenv("VERCEL") and IS_POSTGRES:
    engine_kwargs["poolclass"] = NullPool

engine = create_engine(DATABASE_URL, **engine_kwargs)
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


class AdminSession(Base):
    """Stores admin session tokens for secure admin login."""
    __tablename__ = "admin_sessions"

    token = Column(String(128), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _ensure_admin_password():
    """Create a default admin password hash if none exists, honoring ADMIN_PASSWORD env var."""
    db = SessionLocal()
    try:
        env_admin_pass = os.getenv("ADMIN_PASSWORD", "").strip()
        setting = db.query(AppSetting).filter(AppSetting.key == "ADMIN_PASSWORD_HASH").first()
        if not setting or not setting.value:
            initial_pw = env_admin_pass or "admin123"
            default_hash = _hash_password(initial_pw)
            if setting:
                setting.value = default_hash
            else:
                db.add(AppSetting(key="ADMIN_PASSWORD_HASH", value=default_hash))
            db.commit()
    finally:
        db.close()


_db_initialized = False


def init_db():
    global _db_initialized
    Base.metadata.create_all(bind=engine)
    _ensure_admin_password()
    _db_initialized = True


# Ensure tables are created immediately when database module loads
# (Essential for Serverless / Vercel where ASGI lifespan startup events may not be fired)
try:
    init_db()
except Exception as _e:
    logger.warning(f"Deferred DB init (will retry on first request): {_e}")


def verify_admin_password(db, password: str) -> bool:
    stored = get_setting(db, "ADMIN_PASSWORD_HASH", "")
    if not stored:
        return False
    return stored == _hash_password(password)


def set_admin_password(db, new_password: str):
    set_setting(db, "ADMIN_PASSWORD_HASH", _hash_password(new_password))


def create_admin_session(db) -> str:
    """Create a new admin session token (valid 8 hours)."""
    from datetime import timedelta
    token = secrets.token_urlsafe(64)
    expires = datetime.utcnow() + timedelta(hours=8)
    session = AdminSession(token=token, expires_at=expires)
    db.add(session)
    db.commit()
    return token


def validate_admin_session(db, token: str) -> bool:
    """Returns True if the session token is valid and not expired."""
    if not token:
        return False
    session = db.query(AdminSession).filter(AdminSession.token == token).first()
    if not session:
        return False
    if session.expires_at < datetime.utcnow():
        db.delete(session)
        db.commit()
        return False
    return True


def get_db():
    if not _db_initialized:
        init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _detect_cloud_app_url() -> str:
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if render_url:
        return render_url
    vercel_prod = os.getenv("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
    if vercel_prod:
        return vercel_prod if vercel_prod.startswith("http") else f"https://{vercel_prod}"
    vercel_url = os.getenv("VERCEL_URL", "").strip()
    if vercel_url:
        return vercel_url if vercel_url.startswith("http") else f"https://{vercel_url}"
    return ""


def get_setting(db, key: str, default: str = "") -> str:
    cloud_url = _detect_cloud_app_url()
    setting = db.query(AppSetting).filter(AppSetting.key == key).first()
    if setting and setting.value is not None and setting.value.strip() != "":
        val = setting.value.strip()
        # If running on Render/Vercel cloud but APP_URL was left as localhost/placeholder, auto-upgrade to real URL
        if key == "APP_URL" and cloud_url and ("localhost" in val or "127.0.0.1" in val or "your-project" in val):
            return cloud_url
        return val

    if key == "APP_URL":
        env_url = os.getenv("APP_URL", "").strip()
        if env_url and "your-project" not in env_url and "localhost" not in env_url:
            return env_url
        if cloud_url:
            return cloud_url

    return os.getenv(key, default).strip() if isinstance(os.getenv(key, default), str) else os.getenv(key, default)


def set_setting(db, key: str, value: str):
    setting = db.query(AppSetting).filter(AppSetting.key == key).first()
    if setting:
        setting.value = value
    else:
        setting = AppSetting(key=key, value=value)
        db.add(setting)
    db.commit()
