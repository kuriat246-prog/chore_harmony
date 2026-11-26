import os
import ssl
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from datetime import datetime
from database import NotificationLog, get_setting

logger = logging.getLogger("chore_notifier")


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
    app_url: str
) -> tuple[bool, str]:
    if not smtp_host or not smtp_user or not to_email:
        return False, "SMTP configuration or recipient email is missing."

    # Normalize credentials
    smtp_user = smtp_user.strip()
    smtp_pass = (smtp_pass or "").replace(" ", "").strip()
    smtp_host = smtp_host.strip()
    to_email = to_email.strip()

    if not smtp_pass:
        return False, "SMTP password is empty. Please configure your Gmail App Password in Settings."

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Morning Chore Reminder: {chore_title}"
        msg["From"] = from_email or smtp_user
        msg["To"] = to_email

        text_body = f"""Good morning, {user_name}!
Today is your day to take care of: {chore_title}

Checklist / Definition of Done:
{chore_description or 'No specific instructions. Just leave it clean and tidy!'}

Household Chore Board:
{app_url}

When finished, your household admin will review and mark the chore as done.
Thanks for keeping our home clean and harmonious!
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
    .btn {{ display: inline-block; background-color: #4f46e5; color: #ffffff !important; text-decoration: none; padding: 14px 28px; border-radius: 12px; font-weight: 700; font-size: 15px; margin-top: 15px; text-align: center; box-shadow: 0 4px 6px -1px rgba(79, 70, 229, 0.3); }}
    .footer {{ text-align: center; font-size: 12px; color: #9ca3af; padding: 20px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1 style="margin: 0; font-size: 24px; font-weight: 800;">Good morning, {user_name}!</h1>
      <p style="margin: 6px 0 0 0; opacity: 0.9; font-size: 14px;">Here is your scheduled chore for today</p>
    </div>
    <div class="content">
      <p style="font-size: 15px; line-height: 1.6; margin-top: 0;">
        Keeping shared spaces peaceful starts with clear expectations. Today is your assigned day:
      </p>
      <div class="chore-box">
        <div class="chore-title">{chore_icon} {chore_title}</div>
        <p class="chore-desc">{chore_description or 'No extra notes. Just ensure the area is left neat and clean!'}</p>
      </div>
      <p style="font-size: 13px; color: #6b7280; margin: 16px 0 8px 0; text-align: center;">
        Once complete, your household admin will inspect and mark it as done on the roster.
      </p>
      <div style="text-align: center; margin: 18px 0 10px 0;">
        <a href="{app_url}" class="btn">View Household Chore Board</a>
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
        # Fallback between ports automatically if one times out or is blocked.
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
                    with smtplib.SMTP_SSL(smtp_host, port, context=ssl_context, timeout=20) as server:
                        server.login(smtp_user, smtp_pass)
                        server.send_message(msg)
                    logger.info(f"Email sent to {to_email} via SSL port 465.")
                    return True, "Email sent successfully via SSL (port 465)."
                else:
                    with smtplib.SMTP(smtp_host, port, timeout=20) as server:
                        server.ehlo()
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                        server.login(smtp_user, smtp_pass)
                        server.send_message(msg)
                    logger.info(f"Email sent to {to_email} via STARTTLS port {port}.")
                    return True, f"Email sent successfully via STARTTLS (port {port})."
            except smtplib.SMTPAuthenticationError as auth_err:
                err_detail = auth_err.smtp_error.decode() if isinstance(getattr(auth_err, 'smtp_error', None), bytes) else str(auth_err)
                return False, f"Gmail Auth Failed (535): Invalid App Password. {err_detail}."
            except Exception as err:
                last_error = err
                logger.warning(f"Email port {port} failed: {err}. Trying next port...")
                continue

        err_msg = str(last_error) if last_error else "All SMTP ports timed out."
        return False, f"SMTP failed on all ports (Render/Cloud may block SMTP): {err_msg}"

    except smtplib.SMTPAuthenticationError as e:
        return False, f"Gmail Login Failed: {str(e)}"
    except Exception as e:
        return False, f"Email failed: {str(e)}"


def send_brevo_api_notification(
    api_key: str,
    from_email: str,
    from_name: str,
    to_email: str,
    user_name: str,
    chore_title: str,
    chore_icon: str,
    chore_description: str,
    app_url: str
) -> tuple[bool, str]:
    """
    Sends email via Brevo REST API over HTTPS (Port 443).
    Bypasses Render / cloud host SMTP port blocks completely.
    """
    if not api_key or not to_email:
        return False, "Brevo API Key or recipient email is missing."

    api_key = api_key.strip()
    to_email = to_email.strip()
    sender_email = (from_email or "").strip()

    if not sender_email:
        return False, "Brevo sender email is missing. Please add your verified Brevo sender email in Settings."

    text_body = f"""Good morning, {user_name}!
Today is your day to take care of: {chore_title}

Checklist / Definition of Done:
{chore_description or 'No specific instructions. Just leave it clean and tidy!'}

Household Chore Board:
{app_url}

When finished, your household admin will review and mark the chore as done.
Thanks for keeping our home clean and harmonious!
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
    .btn {{ display: inline-block; background-color: #4f46e5; color: #ffffff !important; text-decoration: none; padding: 14px 28px; border-radius: 12px; font-weight: 700; font-size: 15px; margin-top: 15px; text-align: center; box-shadow: 0 4px 6px -1px rgba(79, 70, 229, 0.3); }}
    .footer {{ text-align: center; font-size: 12px; color: #9ca3af; padding: 20px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1 style="margin: 0; font-size: 24px; font-weight: 800;">Good morning, {user_name}!</h1>
      <p style="margin: 6px 0 0 0; opacity: 0.9; font-size: 14px;">Here is your scheduled chore for today</p>
    </div>
    <div class="content">
      <p style="font-size: 15px; line-height: 1.6; margin-top: 0;">
        Keeping shared spaces peaceful starts with clear expectations. Today is your assigned day:
      </p>
      <div class="chore-box">
        <div class="chore-title">{chore_icon} {chore_title}</div>
        <p class="chore-desc">{chore_description or 'No extra notes. Just ensure the area is left neat and clean!'}</p>
      </div>
      <p style="font-size: 13px; color: #6b7280; margin: 16px 0 8px 0; text-align: center;">
        Once complete, your household admin will inspect and mark it as done on the roster.
      </p>
      <div style="text-align: center; margin: 18px 0 10px 0;">
        <a href="{app_url}" class="btn">View Household Chore Board</a>
      </div>
    </div>
    <div class="footer">
      Shared Harmony Chore Engine &bull; Automated reminder to eliminate roommate conflict
    </div>
  </div>
</body>
</html>
"""

    try:
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json"
        }
        payload = {
            "sender": {
                "name": from_name or "ChoreHarmony",
                "email": sender_email
            },
            "to": [
                {
                    "email": to_email,
                    "name": user_name
                }
            ],
            "subject": f"Morning Chore Reminder: {chore_title}",
            "htmlContent": html_body,
            "textContent": text_body
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        if resp.status_code in (200, 201, 202):
            logger.info(f"Brevo email sent to {to_email} for chore: {chore_title}")
            return True, "Email sent successfully via Brevo HTTPS API."
        else:
            err = resp.text
            logger.error(f"Brevo API error {resp.status_code}: {err}")
            return False, f"Brevo API error ({resp.status_code}): {err}"
    except requests.exceptions.ConnectionError:
        return False, "Brevo API: Network connection error. Check internet connectivity."
    except requests.exceptions.Timeout:
        return False, "Brevo API: Request timed out (20s). Brevo may be unreachable."
    except Exception as e:
        return False, f"Brevo API request failed: {str(e)}"


def send_telegram_notification(bot_token: str, chat_id: str, text: str) -> tuple[bool, str]:
    if not bot_token or not chat_id:
        return False, "Telegram Bot Token or Chat ID is missing."

    bot_token = bot_token.strip()
    chat_id = chat_id.strip()

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False
        }
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if data.get("ok"):
            return True, "Telegram notification sent."
        else:
            err_desc = data.get('description', 'Unknown error')
            logger.error(f"Telegram API error for chat {chat_id}: {err_desc}")
            return False, f"Telegram API error: {err_desc}"
    except requests.exceptions.ConnectionError:
        return False, "Telegram: Network connection error."
    except requests.exceptions.Timeout:
        return False, "Telegram: Request timed out (15s)."
    except Exception as e:
        return False, f"Telegram network error: {str(e)}"


def dispatch_chore_reminder(db, assignment, app_url: str = "http://localhost:8000") -> list[dict]:
    """
    Sends morning reminders via Email (with Brevo API -> SMTP auto-fallback),
    Telegram push, and in-app activity feed.
    """
    user = assignment.user
    chore = assignment.chore

    if not user or not chore:
        logger.error(f"Assignment {assignment.id} has no user or chore attached. Skipping.")
        return []

    # Fetch configuration
    smtp_host = get_setting(db, "SMTP_HOST", "")
    smtp_port_str = get_setting(db, "SMTP_PORT", "587")
    smtp_port = int(smtp_port_str) if smtp_port_str.isdigit() else 587
    smtp_user = get_setting(db, "SMTP_USER", "")
    smtp_pass = get_setting(db, "SMTP_PASSWORD", "")
    smtp_from = get_setting(db, "SMTP_FROM", "") or smtp_user
    telegram_token = get_setting(db, "TELEGRAM_BOT_TOKEN", "")
    brevo_api_key = get_setting(db, "BREVO_API_KEY", "")
    brevo_sender = get_setting(db, "BREVO_SENDER_EMAIL", "") or smtp_user

    results = []

    # 1. Telegram Push (Instant & Free)
    if telegram_token and user.telegram_chat_id:
        tg_text = (
            f"Good morning, {user.name}!\n\n"
            f"Today is your turn for:\n"
            f"{chore.icon} *{chore.title}*\n\n"
            f"Definition of Done:\n"
            f"_{chore.description or 'Keep it neat and clean!'}_\n\n"
            f"Board: {app_url}\n"
            f"(Your admin will mark it done once finished.)"
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

    # 2. Email Notification with Automatic Resilient Fallback
    if user.email:
        email_sent = False
        email_msg = ""
        channel_used = "email"

        # Try Brevo API first (port 443 HTTPS, works on Render/Cloud without port blocks)
        if brevo_api_key and brevo_sender:
            email_ok, email_msg = send_brevo_api_notification(
                api_key=brevo_api_key,
                from_email=brevo_sender,
                from_name="ChoreHarmony",
                to_email=user.email,
                user_name=user.name,
                chore_title=chore.title,
                chore_icon=chore.icon,
                chore_description=chore.description,
                app_url=app_url
            )
            channel_used = "email (brevo api)"
            if email_ok:
                email_sent = True
            else:
                logger.warning(f"Brevo API failed for {user.email}: {email_msg}. Trying SMTP fallback...")

        # If Brevo was not configured or failed, try SMTP fallback
        if not email_sent and smtp_host and smtp_user and smtp_pass:
            smtp_ok, smtp_msg = send_email_notification(
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
                app_url=app_url
            )
            if smtp_ok:
                email_sent = True
                channel_used = "email (smtp fallback)" if brevo_api_key else "email (smtp)"
                email_msg = smtp_msg
            else:
                email_msg = f"{email_msg} | SMTP fallback also failed: {smtp_msg}" if email_msg else smtp_msg
                channel_used = "email (all providers failed)"

        if not brevo_api_key and not (smtp_host and smtp_user and smtp_pass):
            email_msg = "No email provider configured. Please configure Brevo API key or Gmail SMTP in Settings."
            channel_used = "email (unconfigured)"

        log_entry = NotificationLog(
            user_id=user.id,
            chore_title=chore.title,
            channel=channel_used,
            status="sent" if email_sent else "failed",
            details=email_msg
        )
        db.add(log_entry)
        results.append({"channel": "email", "success": email_sent, "details": email_msg})
    else:
        log_entry = NotificationLog(
            user_id=user.id,
            chore_title=chore.title,
            channel="email",
            status="skipped",
            details=f"{user.name} has no email address configured."
        )
        db.add(log_entry)
        results.append({"channel": "email", "success": False, "details": f"{user.name} has no email address configured."})

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

    logger.info(f"Dispatched reminders for {user.name} / {chore.title}. Results: {results}")
    return results
