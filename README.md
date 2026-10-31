# UMANG SOCIAL — starter project

A Flask-based social networking MVP with open registration, profiles, posts, image uploads, likes, comments, following, user search, activity notifications, reporting and blocking.

## 1. Requirements
- Python 3.10 or newer
- Internet connection for installing packages
- A terminal

## 2. Run on Zorin OS / Ubuntu

Open Terminal and run these commands one at a time:

```bash
cd ~/Downloads
unzip umang_social.zip
cd umang_social
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

If `venv` creation fails, install it with:
```bash
sudo apt update
sudo apt install python3-venv
```

## 3. Important before public deployment

This is a learning MVP, not a fully audited production social network. Before inviting the public:
- Set a long random `SECRET_KEY` using an environment variable.
- Use PostgreSQL and persistent file/object storage; local SQLite and local uploads are not suitable for multiple production instances.
- Put the app behind HTTPS.
- Configure a production rate-limit store such as Redis (the included in-memory limiter resets on restart and is not shared across workers).
- Add email verification, password reset, account deletion/export, clear privacy/community rules, moderation/admin review tools and a process to respond to reports.
- Review image uploads and add stronger content moderation.
- Back up the database and uploads.
- Do not collect sensitive personal information. Do not use real names, phone numbers, home addresses or school details in public profiles.
- Keep private messaging disabled until abuse reporting, blocking and moderation workflows are ready.

## 4. Security notes
- Passwords are stored as Werkzeug password hashes, never as plain text.
- Forms use CSRF protection.
- Registration and selected actions have rate limits.
- Uploaded files are limited to PNG/JPG/WEBP and 5 MB, with randomized filenames.
- Never commit `.env`, API keys, passwords or production secrets to GitHub.

## 5. Project structure

```text
umang_social/
├── app.py
├── requirements.txt
├── .env.example
├── README.md
├── templates/
└── static/
    ├── style.css
    └── uploads/
```
