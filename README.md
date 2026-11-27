# 🏠 ChoreHarmony — Conflict-Free Roommate & Friend Chore Engine

> **A Python web platform with automated morning reminders designed specifically to eliminate household chore conflict.**

---

## 💡 The Core Problem: Why Do Chores Cause Roommate Conflict?

Behavioral research into shared living arrangements reveals five main causes of chore friction:
1. **The "Nagging" Trap**: When one person acts as the household manager and reminds others, a toxic parent-child dynamic emerges.
2. **Ambiguity Over "Clean"**: One person thinks doing the dishes means running the machine; another thinks it means drying, wiping the counters, and scrubbing the sink.
3. **The Egocentric Bias**: Psychological studies show flatmates overestimate their own contribution by **1.5× to 2×** while underestimating others' contributions.
4. **Inflexibility & Guilt**: When exams, sickness, or late work shifts happen, rigid chore schedules lead to missed chores and silent resentment.
5. **No Morning Prompt**: People don't forget chores out of malice; they simply get caught in their morning rush before noticing.

---

## 🛠️ How ChoreHarmony Solves This

1. **The Neutral Arbiter (Automated Morning Alerts)**:
   - The app sends a personalized reminder to the assigned friend **every morning at 8:00 AM** (customizable).
   - Reminders arrive via **Telegram Push Notification** (free, instant on phone) and/or **Email (SMTP)**.
   - Nobody ever has to nag a friend again — the system does it automatically.

2. **Explicit "Definition of Done"**:
   - Every chore has bulleted acceptance criteria (e.g., *"1. Empty rack, 2. Wipe stove, 3. Take out counter trash"*). No ambiguity.

3. **1-Click Chore Swap System ("Need a Hand?")**:
   - Busy today? Tap **🤝 Swap with Friend**. The system asks the selected flatmate to cover and updates the board automatically once accepted. Zero awkward texts.

4. **100% Transparent Weekly Matrix & Ledger**:
   - A 7-day Monday–Sunday visual grid shows whose turn is coming up.
   - An immutable history ledger records every chore completed with timestamps.

5. **Karma & Streaks Gamification**:
   - Rewards flatmates with +10 Karma points and streak counters upon completion. Positive reinforcement replaces guilt and passive aggression.

---

## 🚀 Quick Start Guide

### 1. Requirements
- Python 3.10+ (Tested on Python 3.14)

### 2. Setup & Installation
```bash
# Navigate to the project directory
cd chore_reminder_app

# Install dependencies
python -m pip install -r requirements.txt

# Seed realistic demo data (4 flatmates, 4 core chores, rotation history)
python seed_data.py
```

### 3. Run the Web Application
```bash
python -m uvicorn app:app --reload --port 8000
```
Open your browser and navigate to:
**[http://localhost:8000](http://localhost:8000)**

---

## 📱 Notification Channels Setup

### 🔹 Telegram Push Notifications (Recommended: Free & Instant)
Telegram is ideal because flatmates receive a notification directly on their lock screen in the morning.
1. Message **@BotFather** on Telegram and type `/newbot`.
2. Follow the prompt to name your bot and copy the **HTTP API Token**.
3. In ChoreHarmony, go to **⚙️ Reminders & Setup** and paste the token.
4. Each flatmate finds their Telegram Chat ID (by messaging `@userinfobot` or `@myidbot` on Telegram) and adds it to their profile under **👥 Friends**.
5. Test it immediately using the **☀️ Send Morning Alert** button on the top right!

### 🔹 Email Notifications (SMTP)
Compatible with Gmail App Passwords, SendGrid, Outlook, or Amazon SES:
- Go to **⚙️ Reminders & Setup**.
- Fill in:
  - `SMTP_HOST`: e.g. `smtp.gmail.com`
  - `SMTP_PORT`: `587`
  - `SMTP_USER`: `your_email@gmail.com`
  - `SMTP_PASSWORD`: Your 16-character Google App Password

---

## 📁 Project Architecture

```
chore_reminder_app/
├── app.py              # FastAPI application & web route controllers
├── database.py         # SQLAlchemy models (User, Chore, ChoreAssignment, SwapRequest, Logs)
├── scheduler.py        # APScheduler engine for daily morning cron & fair round-robin rotations
├── notifier.py         # Multi-channel notification dispatcher (Telegram, Email HTML, In-App)
├── seed_data.py        # Helper to pre-populate realistic household members & chores
├── requirements.txt    # Project Python dependencies
├── .env.example        # Environment variable reference
└── templates/          # Responsive Tailwind CSS mobile-first web interface
    ├── base.html       # Layout, sticky navigation, confetti celebrations
    ├── index.html      # Main dashboard: Today's roster, weekly grid, leaderboard, live feed
    ├── chores.html     # Chore creator with Definition of Done & rotation selector
    ├── members.html    # Friend profiles with contact channels & stats
    ├── history.html    # Transparency ledger
    └── settings.html   # Reminder time scheduler & notification API setup
```

---

## 🌐 Deploying to the Cloud (Free / Low Cost)
To keep this running 24/7 for your apartment or friend group:
1. **Render.com / Railway.app**: Free or \$5/mo tier. Deploy directly from a GitHub repository using `uvicorn app:app --host 0.0.0.0 --port $PORT`.
2. **Raspberry Pi / Home Server**: Can be run locally at home on your Wi-Fi network using systemd or Docker.
