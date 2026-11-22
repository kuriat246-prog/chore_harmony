import os
import ssl
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from datetime import datetime
from database import NotificationLog, get_setting


def send_email_notification(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    from_email: str,
    to_email: str,
    user_name: str,
    chore_title: str,
    chore_icon: str,
    chore_description: str,
    app_url: str,
    complete_url: str = ""
) -> tuple[bool, str]:
    if not smtp_host or not smtp_user or not to_email:
        return False, "SMTP configuration or recipient email is missing."

    # Normalize credentials: strip spaces (especially from 16-character Google App Passwords)
    smtp_user = smtp_user.strip()
    smtp_pass = (smtp_pass or "").replace(" ", "").strip()
    smtp_host = smtp_host.strip()
    complete_url = complete_url or app_url

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"☀️ Morning Chore Reminder: {chore_title}"
        msg["From"] = from_email or smtp_user
        msg["To"] = to_email

        text_body = f"""Good morning, {user_name}!
Today is your day to take care of: {chore_title} {chore_icon}

Checklist / Details:
{chore_description or 'No specific instructions. Just leave it tidy!'}

Mark done in 1 click: {complete_url}
Or visit the board: {app_url}
Thanks for being an awesome flatmate!
"""

        html_body = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f3f4f6; margin: 0; padding: 20px; }}
    .card {{ max-width: 540px; margin: 0 auto; background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); border: 1px solid #e5e7eb; }}
    .header {{ background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%); padding: 32px 24px; text-align: center; color: white; }}
    .content {{ padding: 28px 24px; color: #374151; }}
    .chore-box {{ background-color: #f9fafb; border-left: 4px solid #4f46e5; border-radius: 8px; padding: 18px; margin: 20px 0; }}
    .chore-title {{ font-size: 20px; font-weight: 700; color: #111827; margin: 0 0 6px 0; }}
    .chore-desc {{ font-size: 14px; color: #6b7280; margin: 0; line-height: 1.5; }}
    .btn {{ display: inline-block; background-color: #059669; color: #ffffff !important; text-decoration: none; padding: 14px 28px; border-radius: 12px; font-weight: 700; font-size: 15px; margin-top: 15px; text-align: center; box-shadow: 0 4px 6px -1px rgba(16, 185, 129, 0.3); }}
    .footer {{ text-align: center; font-size: 12px; color: #9ca3af; padding: 20px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div style="font-size: 40px; margin-bottom: 8px;">{chore_icon}</div>
      <h1 style="margin: 0; font-size: 24px; font-weight: 800;">Good morning, {user_name}!</h1>
      <p style="margin: 6px 0 0 0; opacity: 0.9; font-size: 14px;">Here is your scheduled chore for today</p>
    </div>
    <div class="content">
      <p style="font-size: 15px; line-height: 1.6; margin-top: 0;">
        Keeping shared spaces peaceful starts with clear expectations. Today is your assigned day:
      </p>
      <div class="chore-box">
        <div class="chore-title">{chore_icon} {chore_title}</div>
        <p class="chore-desc">{chore_description or 'No extra notes. When you finish, tap the button below to mark it done!'}</p>
      </div>
      <div style="text-align: center; margin: 24px 0 10px 0;">
        <a href="{complete_url}" class="btn">✅ Mark as Done (+10 Karma)</a>
      </div>
      <div style="text-align: center; margin-top: 12px;">
        <a href="{app_url}" style="font-size: 13px; color: #6366f1; text-decoration: underline;">View Household Chore Board</a>
      </div>
    </div>
    <div class="footer">
      Shared Harmony Chore Engine &bull; Automated reminder to eliminate roommate conflict
    </div>
  </div>
</body>
</html>
"""
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        # Connection strategy:
        # Port 587 uses STARTTLS. Port 465 uses direct SSL.
        # If port 587 drops (common on some ISP/mobile networks), automatically fallback to port 465 SSL.
        ports_to_try = [smtp_port]
        if smtp_port == 587 and 465 not in ports_to_try:
            ports_to_try.append(465)
        elif smtp_port == 465 and 587 not in ports_to_try:
            ports_to_try.append(587)

        last_error = None
        for port in ports_to_try:
            try:
                if port == 465:
                    ssl_context = ssl.create_default_context()
                    with smtplib.SMTP_SSL(smtp_host, port, context=ssl_context, timeout=15) as server:
                        try:
                            server.login(smtp_user, smtp_pass)
                        except smtplib.SMTPServerDisconnected:
                            return False, "Gmail Authentication Failed: Google rejected the App Password (535 Bad Credentials). Please generate a fresh 16-character App Password at myaccount.google.com/apppasswords."
                        server.send_message(msg)
                    return True, "Email sent successfully via SSL (port 465)."
                else:
                    with smtplib.SMTP(smtp_host, port, timeout=15) as server:
                        server.ehlo()
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                        try:
                            server.login(smtp_user, smtp_pass)
                        except smtplib.SMTPServerDisconnected:
                            return False, "Gmail Authentication Failed: Google rejected the App Password (535 Bad Credentials). Please generate a fresh 16-character App Password at myaccount.google.com/apppasswords."
                        server.send_message(msg)
                    return True, f"Email sent successfully via STARTTLS (port {port})."
            except Exception as err:
                last_error = err
                continue

        if last_error:
            raise last_error

        return False, "Failed to connect to email server."
    except smtplib.SMTPAuthenticationError as e:
        return False, f"Gmail Login Failed: Invalid Google App Password. (Error: {e.smtp_error.decode() if hasattr(e, 'smtp_error') and isinstance(e.smtp_error, bytes) else str(e)})"
    except Exception as e:
        return False, f"Email failed: {str(e)}"


def send_telegram_notification(bot_token: str, chat_id: str, text: str) -> tuple[bool, str]:
    if not bot_token or not chat_id:
        return False, "Telegram Bot Token or Chat ID is missing."

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False
        }
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return True, "Telegram notification sent."
        else:
            return False, f"Telegram API error: {data.get('description', 'Unknown error')}"
    except Exception as e:
        return False, f"Telegram network error: {str(e)}"


def dispatch_chore_reminder(db, assignment, app_url: str = "http://localhost:8000") -> list[dict]:
    """
    Sends morning reminders via Email, Telegram, and In-App notification log.
    """
    user = assignment.user
    chore = assignment.chore

    # Fetch configuration
    smtp_host = get_setting(db, "SMTP_HOST", "")
    smtp_port = int(get_setting(db, "SMTP_PORT", "587") or 587)
    smtp_user = get_setting(db, "SMTP_USER", "")
    smtp_pass = get_setting(db, "SMTP_PASSWORD", "")
    smtp_from = get_setting(db, "SMTP_FROM", "Roommate Chore Bot <no-reply@chores.local>")
    telegram_token = get_setting(db, "TELEGRAM_BOT_TOKEN", "")

    results = []

    # 1. Telegram Push (Instant & Free)
    if telegram_token and user.telegram_chat_id:
        tg_text = (
            f"☀️ *Good morning, {user.name}!*\n\n"
            f"Today is your turn for:\n"
            f"*{chore.icon} {chore.title}*\n\n"
            f"📋 *Definition of Done:*\n"
            f"_{chore.description or 'Keep it neat and tidy!'}\n\n"
            f"👉 [Open Chore Dashboard]({app_url}) to mark it done or request a swap!"
        )
        tg_ok, tg_msg = send_telegram_notification(telegram_token, user.telegram_chat_id, tg_text)
        log_entry = NotificationLog(
            user_id=user.id,
            chore_title=chore.title,
            channel="telegram",
            status="sent" if tg_ok else "failed",
            details=tg_msg
        )
        db.add(log_entry)
        results.append({"channel": "telegram", "success": tg_ok, "details": tg_msg})

    # 2. Email Notification
    if smtp_host and smtp_user and user.email:
        complete_url = f"{app_url}/assignments/{assignment.id}/complete"
        email_ok, email_msg = send_email_notification(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_pass=smtp_pass,
            from_email=smtp_from,
            to_email=user.email,
            user_name=user.name,
            chore_title=chore.title,
            chore_icon=chore.icon,
            chore_description=chore.description,
            app_url=app_url,
            complete_url=complete_url
        )
        log_entry = NotificationLog(
            user_id=user.id,
            chore_title=chore.title,
            channel="email",
            status="sent" if email_ok else "failed",
            details=email_msg
        )
        db.add(log_entry)
        results.append({"channel": "email", "success": email_ok, "details": email_msg})

    # 3. Always log in-app dashboard reminder
    in_app_msg = f"Morning reminder generated for {user.name}: {chore.icon} {chore.title}"
    log_entry = NotificationLog(
        user_id=user.id,
        chore_title=chore.title,
        channel="in_app",
        status="sent",
        details=in_app_msg
    )
    db.add(log_entry)
    results.append({"channel": "in_app", "success": True, "details": in_app_msg})

    assignment.notified = True
    db.commit()

    return results
