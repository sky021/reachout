# Cold Email Sender — Akash Agrawal

A Python script to send personalized job-outreach emails using Gmail SMTP.
It reads contacts from a CSV, sends an HTML email with Akash's LinkedIn and portfolio links, attaches his resume, updates the CSV after successful sends, and writes a send log.

Sender copy and contact details live in `sender_profile.json` (filled from `Akash_Agrawal.pdf`).

## Features

- Personalizes each email with recipient **Full name**, **Company**, and **Email**
- HTML signature with clickable LinkedIn and portfolio links
- Attaches resume (`Akash_Agrawal.pdf`); transcripts are optional
- Skips invalid or incomplete rows
- Adds tracking columns: `Status`, `Last sent at`, `Error`
- Marks successful rows as `Status = Sent` and skips them on later runs
- Delay between emails, retries on failure
- Send log: `email_send_log.csv`
- Dry-run preview unless you pass `--send`

## Folder structure

```text
Reachout/
├── send_cold_emails_final.py
├── sender_profile.json
├── README.md
├── .gitignore
├── MailList_sample.csv
├── MailList.csv              # private, do not commit
├── email_send_log.csv        # private, do not commit
└── Akash_Agrawal.pdf         # private, do not commit
```

## CSV format

Required columns:

```csv
Full name,Company,Email
Example Person,Example Company,example@example.com
Jane Smith,Sample Analytics Inc,jane.smith@example.com
```

Use `MailList_sample.csv` as a template. Your real list should be `MailList.csv`.

After sending, tracking columns are added:

```csv
Full name,Company,Email,Status,Last sent at,Error
Example Person,Example Company,example@example.com,Sent,2026-09-14 16:54:00,
```

Rows with `Status = Sent` are skipped next time.

## Attachments

Default resume filename (also set in `sender_profile.json`):

```text
Akash_Agrawal.pdf
```

Keep it in the same folder as the script, or pass `--attachments-dir`.
Transcripts are not attached unless you set `masters_transcript` / `bachelors_transcript` in the profile.

## Gmail App Password setup

Do **not** use your normal Gmail password. Create a Google App Password:

1. Open [Google Account security](https://myaccount.google.com/security)
2. Turn on **2-Step Verification**
3. Create an **App password** named e.g. `Python Cold Email Script`
4. Use the 16-character password with spaces removed

Never paste this password into the script, README, GitHub, or chat.

## Set environment variables in PowerShell

```powershell
$env:EMAIL_USER="agrawalakash021@gmail.com"
$env:EMAIL_APP_PASSWORD="your16characterapppassword"
```

## Preview emails before sending

Does **not** send:

```powershell
python send_cold_emails_final.py --csv MailList.csv --attachments-dir .
```

Check recipient names, emails, companies, subject, body, and attachments.

To preview against the sample file:

```powershell
python send_cold_emails_final.py --csv MailList_sample.csv --attachments-dir .
```

## Send emails

```powershell
python send_cold_emails_final.py --csv MailList.csv --attachments-dir . --send
```

Type `SEND` when prompted.

Default delay between emails is 30 seconds. Custom delay:

```powershell
python send_cold_emails_final.py --csv MailList.csv --attachments-dir . --send --delay-seconds 60
```

## Retry behavior

Failed sends are retried and are **not** marked `Sent`. Errors are stored in `MailList.csv` and `email_send_log.csv`.

## Running again

Add new rows to `MailList.csv` with blank `Status`. The script sends only unsent rows.

## Security

Do not commit:

```text
MailList.csv
email_send_log.csv
*.pdf
.env
app_password.txt
```

`sender_profile.json` contains public contact info from the resume (email, phone, LinkedIn). It does not contain the Gmail app password.

## Troubleshooting

**EMAIL_USER / EMAIL_APP_PASSWORD** — set them again in the same PowerShell window before `--send`.

**SMTPAuthenticationError** — usually a normal password instead of an app password, 2FA off, or a revoked app password.

**Attachments not found** — place `Akash_Agrawal.pdf` next to the script (or pass `--resume-filename`).

**Rows skipped** — `Status = Sent`. Clear that only if you intend to resend.
