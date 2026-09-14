#!/usr/bin/env python3
import argparse
import csv
import html
import json
import mimetypes
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
DEFAULT_DELAY_SECONDS = 30
DEFAULT_PROFILE_PATH = "sender_profile.json"

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
REQUIRED_COLUMNS = ["Full name", "Company", "Email"]
TRACKING_COLUMNS = ["Status", "Last sent at", "Error"]


def clean_value(value):
    return (value or "").strip()


def looks_like_email(value):
    return bool(EMAIL_RE.match(clean_value(value)))


def normalize_status(value):
    return clean_value(value).lower()


def load_sender_profile(profile_path):
    profile_path = Path(profile_path)
    if not profile_path.exists():
        raise FileNotFoundError(f"Sender profile not found: {profile_path}")

    with open(profile_path, encoding="utf-8") as f:
        profile = json.load(f)

    if not clean_value(profile.get("full_name")):
        raise ValueError("sender_profile.json is missing full_name")
    if not clean_value(profile.get("intro_paragraph")):
        raise ValueError("sender_profile.json is missing intro_paragraph")
    if not clean_value(profile.get("experience_paragraph")):
        raise ValueError("sender_profile.json is missing experience_paragraph")
    if not clean_value(profile.get("ask_paragraph")):
        raise ValueError("sender_profile.json is missing ask_paragraph")
    if not (profile.get("attachments") or {}).get("resume"):
        raise ValueError("sender_profile.json attachments.resume is required")

    profile.setdefault("short_name", profile["full_name"].split()[0])
    profile.setdefault("subject_template", "Interested in opportunities at {company}")
    profile.setdefault("closing_paragraph", "I've attached my resume for your reference.")
    profile.setdefault("role_focus", "")
    profile.setdefault("availability", "")
    return profile


def fill_template(template, company, profile):
    return template.format(
        company=company,
        full_name=clean_value(profile.get("full_name")),
        short_name=clean_value(profile.get("short_name")),
        role_focus=clean_value(profile.get("role_focus")),
        availability=clean_value(profile.get("availability")),
    )


def ensure_tracking_columns(fieldnames):
    result = list(fieldnames or [])
    for col in TRACKING_COLUMNS:
        if col not in result:
            result.append(col)
    return result


def load_csv_rows(csv_path):
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        missing_columns = [col for col in REQUIRED_COLUMNS if col not in fieldnames]
        if missing_columns:
            raise ValueError("CSV is missing mandatory column(s): " + ", ".join(missing_columns))

        rows = []
        for row_number, row in enumerate(reader, start=2):
            for col in TRACKING_COLUMNS:
                row.setdefault(col, "")
            row["_row_number"] = row_number
            rows.append(row)

    return rows, ensure_tracking_columns(fieldnames)


def write_csv_rows(csv_path, fieldnames, rows):
    csv_path = Path(csv_path)
    temp_path = csv_path.with_suffix(csv_path.suffix + ".tmp")
    output_rows = []
    for row in rows:
        clean_row = {field: row.get(field, "") for field in fieldnames}
        output_rows.append(clean_row)

    with open(temp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    temp_path.replace(csv_path)


def collect_recipients(rows):
    recipients = []
    skipped = []
    seen_emails = set()

    for idx, row in enumerate(rows):
        row_number = row.get("_row_number", idx + 2)
        full_name = clean_value(row.get("Full name"))
        company = clean_value(row.get("Company"))
        email = clean_value(row.get("Email")).lower()
        status = normalize_status(row.get("Status"))

        missing = []
        if not full_name:
            missing.append("Full name")
        if not company:
            missing.append("Company")
        if not email:
            missing.append("Email")
        if missing:
            row["Status"] = "Skipped"
            row["Error"] = "Missing mandatory field(s): " + ", ".join(missing)
            skipped.append((row_number, email or "(blank email)", row["Error"]))
            continue

        if not looks_like_email(email):
            row["Status"] = "Skipped"
            row["Error"] = "Invalid email format"
            skipped.append((row_number, email, row["Error"]))
            continue

        if status == "sent":
            skipped.append((row_number, email, "Already marked Sent"))
            continue

        if email in seen_emails:
            row["Status"] = "Skipped"
            row["Error"] = "Duplicate email in current CSV run"
            skipped.append((row_number, email, row["Error"]))
            continue

        seen_emails.add(email)
        recipients.append({
            "index": idx,
            "row": row_number,
            "full_name": full_name,
            "company": company,
            "email": email,
        })

    return recipients, skipped


def find_file(attachments_dir, exact_filename, default_filenames, label):
    attachments_dir = Path(attachments_dir)

    if exact_filename:
        file_path = attachments_dir / exact_filename
        if not file_path.exists():
            raise FileNotFoundError(f"{label} file not found: {file_path}")
        if file_path.stat().st_size == 0:
            raise FileNotFoundError(f"{label} file is empty: {file_path}")
        return file_path

    for filename in default_filenames:
        file_path = attachments_dir / filename
        if file_path.exists() and file_path.stat().st_size > 0:
            return file_path

    raise FileNotFoundError(
        f"Could not find {label}. Put this file in the attachments folder: "
        + ", ".join(default_filenames)
    )


def find_attachments(args, profile):
    attachments_cfg = profile.get("attachments") or {}
    paths = []

    resume_name = args.resume_filename or attachments_cfg.get("resume")
    paths.append(
        find_file(args.attachments_dir, resume_name, [resume_name], "resume PDF")
    )

    optional_files = [
        (args.masters_transcript_filename or attachments_cfg.get("masters_transcript"), "master's transcript PDF"),
        (args.bachelors_transcript_filename or attachments_cfg.get("bachelors_transcript"), "bachelor's transcript PDF"),
    ]
    for filename, label in optional_files:
        if filename:
            paths.append(find_file(args.attachments_dir, filename, [filename], label))

    return paths


def build_subject(company, profile):
    return fill_template(profile["subject_template"], company, profile)


def signature_lines(profile, html_mode=False):
    lines = [clean_value(profile.get("full_name"))]
    email = clean_value(profile.get("email"))
    phone = clean_value(profile.get("phone"))
    location = clean_value(profile.get("location"))
    linkedin_url = clean_value(profile.get("linkedin_url"))
    portfolio_url = clean_value(profile.get("portfolio_url"))

    if email:
        if html_mode:
            lines.append(f'<a href="mailto:{html.escape(email)}">{html.escape(email)}</a>')
        else:
            lines.append(email)
    if phone:
        lines.append(html.escape(phone) if html_mode else phone)
    if location:
        lines.append(html.escape(location) if html_mode else location)
    if linkedin_url:
        if html_mode:
            lines.append(f'<a href="{html.escape(linkedin_url)}">LinkedIn</a>')
        else:
            lines.append("LinkedIn")
    if portfolio_url:
        if html_mode:
            lines.append(f'<a href="{html.escape(portfolio_url)}">Portfolio</a>')
        else:
            lines.append("Portfolio")
    return lines


def build_plain_body(full_name, company, profile):
    paragraphs = [
        f"Hi {full_name},",
        fill_template(profile["intro_paragraph"], company, profile),
        fill_template(profile["experience_paragraph"], company, profile),
        fill_template(profile["ask_paragraph"], company, profile),
        fill_template(profile["closing_paragraph"], company, profile),
        "Thank you for your time and consideration.",
    ]
    signature = "\n".join(["Best regards,"] + signature_lines(profile, html_mode=False))
    return "\n\n".join(paragraphs) + "\n\n" + signature + "\n"


def build_html_body(full_name, company, profile):
    safe_full_name = html.escape(full_name)
    html_paragraphs = [
        f"<p>Hi {safe_full_name},</p>",
        f"<p>{html.escape(fill_template(profile['intro_paragraph'], company, profile))}</p>",
        f"<p>{html.escape(fill_template(profile['experience_paragraph'], company, profile))}</p>",
        f"<p>{html.escape(fill_template(profile['ask_paragraph'], company, profile)).replace(' — ', ' &mdash; ')}</p>",
        f"<p>{html.escape(fill_template(profile['closing_paragraph'], company, profile))}</p>",
        "<p>Thank you for your time and consideration.</p>",
    ]
    signature_html = "<br>\n      ".join(signature_lines(profile, html_mode=True))
    return f"""
<html>
  <body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.5; color: #222222;">
    {''.join(html_paragraphs)}

    <p>
      Best regards,<br>
      {signature_html}
    </p>
  </body>
</html>
"""


def attach_file(message, file_path):
    file_path = Path(file_path)
    content_type, _ = mimetypes.guess_type(str(file_path))
    if content_type is None:
        content_type = "application/octet-stream"
    maintype, subtype = content_type.split("/", 1)

    with open(file_path, "rb") as f:
        message.add_attachment(
            f.read(),
            maintype=maintype,
            subtype=subtype,
            filename=file_path.name,
        )


def build_message(sender_email, recipient, attachment_paths, profile):
    full_name = recipient["full_name"]
    company = recipient["company"]
    to_email = recipient["email"]

    msg = EmailMessage()
    msg["From"] = sender_email
    msg["To"] = to_email
    msg["Subject"] = build_subject(company, profile)

    plain_body = build_plain_body(full_name, company, profile)
    html_body = build_html_body(full_name, company, profile)

    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    for attachment_path in attachment_paths:
        attach_file(msg, attachment_path)

    return msg, plain_body


def preview_email(recipient, plain_body, attachment_paths, profile):
    print("=" * 80)
    print(f"To: {recipient['email']}")
    print(f"Subject: {build_subject(recipient['company'], profile)}")
    print("-" * 80)
    print(plain_body)
    print("Attachments:")
    for path in attachment_paths:
        print(f"- {Path(path).name}")
    print("=" * 80)
    print()


def write_log(log_file, row):
    log_path = Path(log_file)
    file_exists = log_path.exists()
    fieldnames = ["timestamp", "row", "full_name", "company", "email", "status", "attempts", "error"]
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def send_one_message(sender_email, app_password, recipient, attachment_paths, timeout_seconds, profile):
    msg, _ = build_message(sender_email, recipient, attachment_paths, profile)
    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=timeout_seconds) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(sender_email, app_password)
        server.send_message(msg)


def sleep_with_countdown(seconds):
    if seconds <= 0:
        return
    print(f"Waiting {seconds} second(s) before next email...")
    time.sleep(seconds)


def mark_row(rows, recipient, status, error=""):
    now = datetime.now().isoformat(timespec="seconds")
    row = rows[recipient["index"]]
    row["Status"] = status
    if status == "Sent":
        row["Last sent at"] = now
        row["Error"] = ""
    else:
        row["Error"] = error


def send_messages(sender_email, app_password, recipients, attachment_paths, rows, fieldnames, args, profile):
    sent = 0
    failed = 0

    print("Sending emails...")
    print(f"Delay between emails: {args.delay_seconds} second(s)")
    print(f"Retries per email: {args.retries}")
    print(f"Log file: {args.log_file}")
    print(f"CSV tracking file: {args.csv}")
    print()

    for index, recipient in enumerate(recipients, start=1):
        success = False
        last_error = ""
        max_attempts = max(1, args.retries + 1)

        for attempt in range(1, max_attempts + 1):
            try:
                print(f"[{index}/{len(recipients)}] Sending to {recipient['email']} ({recipient['company']}) - attempt {attempt}/{max_attempts}")
                send_one_message(
                    sender_email,
                    app_password,
                    recipient,
                    attachment_paths,
                    args.timeout_seconds,
                    profile,
                )
                print(f"SUCCESS: Sent to {recipient['email']}")
                sent += 1
                success = True
                mark_row(rows, recipient, "Sent")
                write_csv_rows(args.csv, fieldnames, rows)
                write_log(args.log_file, {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "row": recipient.get("row", ""),
                    "full_name": recipient["full_name"],
                    "company": recipient["company"],
                    "email": recipient["email"],
                    "status": "sent",
                    "attempts": attempt,
                    "error": "",
                })
                break
            except smtplib.SMTPAuthenticationError as e:
                last_error = "SMTP authentication failed. Check EMAIL_USER and EMAIL_APP_PASSWORD."
                print(f"ERROR: {last_error}")
                print(f"Details: {e}")
                failed += 1
                mark_row(rows, recipient, "Failed", last_error)
                write_csv_rows(args.csv, fieldnames, rows)
                write_log(args.log_file, {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "row": recipient.get("row", ""),
                    "full_name": recipient["full_name"],
                    "company": recipient["company"],
                    "email": recipient["email"],
                    "status": "failed",
                    "attempts": attempt,
                    "error": last_error,
                })
                print("Stopping because authentication errors will fail for every recipient.")
                return sent, failed + (len(recipients) - index)
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, smtplib.SMTPDataError, TimeoutError, OSError) as e:
                last_error = f"Temporary/send error: {type(e).__name__}: {e}"
                print(f"WARNING: {last_error}")
                if attempt < max_attempts:
                    retry_wait = max(10, min(60, args.delay_seconds))
                    print(f"Retrying in {retry_wait} second(s)...")
                    time.sleep(retry_wait)
            except Exception as e:
                last_error = f"Unexpected error: {type(e).__name__}: {e}"
                print(f"ERROR: {last_error}")
                if attempt < max_attempts:
                    retry_wait = max(10, min(60, args.delay_seconds))
                    print(f"Retrying in {retry_wait} second(s)...")
                    time.sleep(retry_wait)

        if not success:
            failed += 1
            print(f"FAILED: Could not send to {recipient['email']} after {max_attempts} attempt(s)")
            mark_row(rows, recipient, "Failed", last_error)
            write_csv_rows(args.csv, fieldnames, rows)
            write_log(args.log_file, {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "row": recipient.get("row", ""),
                "full_name": recipient["full_name"],
                "company": recipient["company"],
                "email": recipient["email"],
                "status": "failed",
                "attempts": max_attempts,
                "error": last_error,
            })
            if args.stop_on_error:
                print("Stopping because --stop-on-error was used.")
                return sent, failed + (len(recipients) - index)

        if index < len(recipients):
            sleep_with_countdown(args.delay_seconds)

    return sent, failed


def profile_needs_copy_review(profile):
    haystack = " ".join([
        profile.get("experience_paragraph", ""),
        profile.get("intro_paragraph", ""),
        profile.get("ask_paragraph", ""),
        profile.get("full_name", ""),
    ]).upper()
    return "TODO" in haystack


def main():
    parser = argparse.ArgumentParser(
        description="Send personalized outreach emails for Akash using a sender profile, CSV tracking, and optional attachments."
    )
    parser.add_argument("--csv", required=True, help="CSV file with mandatory columns: Full name, Company, Email. Optional tracking columns: Status, Last sent at, Error")
    parser.add_argument("--profile", default=DEFAULT_PROFILE_PATH, help=f"JSON sender profile. Default: {DEFAULT_PROFILE_PATH}")
    parser.add_argument("--attachments-dir", default=".", help="Folder containing resume and optional transcript PDFs")
    parser.add_argument("--resume-filename", default=None, help="Optional exact resume PDF filename")
    parser.add_argument("--masters-transcript-filename", default=None, help="Optional exact master's transcript PDF filename")
    parser.add_argument("--bachelors-transcript-filename", default=None, help="Optional exact bachelor's transcript PDF filename")
    parser.add_argument("--send", action="store_true", help="Actually send emails. Without this, only previews are shown.")
    parser.add_argument("--delay-seconds", type=int, default=DEFAULT_DELAY_SECONDS, help=f"Delay between emails. Default: {DEFAULT_DELAY_SECONDS} seconds")
    parser.add_argument("--retries", type=int, default=2, help="Retries per email after the first attempt. Default: 2")
    parser.add_argument("--timeout-seconds", type=int, default=60, help="SMTP timeout in seconds. Default: 60")
    parser.add_argument("--log-file", default="email_send_log.csv", help="CSV log file for sent/failed results")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop after the first failed recipient")
    parser.add_argument("--limit", type=int, default=None, help="Optional max number of unsent recipients to process")
    args = parser.parse_args()

    if args.delay_seconds < 0:
        print("Error: --delay-seconds cannot be negative.")
        sys.exit(1)
    if args.retries < 0:
        print("Error: --retries cannot be negative.")
        sys.exit(1)
    if args.timeout_seconds <= 0:
        print("Error: --timeout-seconds must be greater than 0.")
        sys.exit(1)

    try:
        profile = load_sender_profile(args.profile)
        rows, fieldnames = load_csv_rows(args.csv)
        recipients, skipped = collect_recipients(rows)
        if args.limit is not None:
            recipients = recipients[:args.limit]
        attachment_paths = find_attachments(args, profile)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Sender: {profile.get('full_name')}")
    print(f"Loaded {len(recipients)} unsent valid recipient(s).")
    if skipped:
        print(f"Skipped {len(skipped)} row(s):")
        for row_number, email, reason in skipped:
            print(f"- Row {row_number}: {email} - {reason}")
    print("Using attachments:")
    for path in attachment_paths:
        print(f"- {path}")
    print()

    if not recipients:
        print("No unsent valid recipients found in CSV.")
        print("Rows with Status = Sent are skipped automatically.")
        return

    preview_from = os.getenv("EMAIL_USER") or clean_value(profile.get("email")) or "akash@example.com"

    if not args.send:
        print("DRY RUN MODE - no emails will be sent and CSV will not be updated. Add --send to send emails.\n")
        if profile_needs_copy_review(profile):
            print("NOTE: sender_profile.json still has placeholder copy. Fill it from Akash's resume before sending.\n")
        for recipient in recipients:
            _, plain_body = build_message(preview_from, recipient, attachment_paths, profile)
            preview_email(recipient, plain_body, attachment_paths, profile)
        return

    if profile_needs_copy_review(profile):
        print("Error: sender_profile.json still has placeholder copy (TODO / incomplete name).")
        print("Update the profile from Akash's resume before using --send.")
        sys.exit(1)

    sender_email = os.getenv("EMAIL_USER") or clean_value(profile.get("email"))
    app_password = os.getenv("EMAIL_APP_PASSWORD")

    if not sender_email or not app_password:
        print("Error: EMAIL_USER and EMAIL_APP_PASSWORD environment variables are required to send.")
        print("PowerShell example:")
        example_email = clean_value(profile.get("email")) or "akash@gmail.com"
        print(f'$env:EMAIL_USER="{example_email}"')
        print('$env:EMAIL_APP_PASSWORD="your_app_password_here"')
        sys.exit(1)

    print("Before sending, confirm:")
    print(f"- Sender: {sender_email}")
    print(f"- Unsent recipients to send now: {len(recipients)}")
    print(f"- Delay: {args.delay_seconds} seconds between emails")
    print(f"- Attachments: {len(attachment_paths)} file(s)")
    print(f"- CSV will be updated after each email: {args.csv}")
    print("- Successfully sent rows will be marked Status = Sent")
    print()
    confirm = input(f"Type SEND to send {len(recipients)} email(s): ").strip()
    if confirm != "SEND":
        print("Cancelled. No emails sent and CSV was not updated.")
        return

    try:
        sent, failed = send_messages(
            sender_email, app_password, recipients, attachment_paths, rows, fieldnames, args, profile
        )
        print(f"\nDone. Sent: {sent}, Failed: {failed}")
        print(f"CSV updated: {args.csv}")
        print(f"Log saved to: {args.log_file}")
    except KeyboardInterrupt:
        print("\nStopped by user. Some emails may have already been sent. Check the CSV Status column and the log file.")
        sys.exit(130)


if __name__ == "__main__":
    main()
