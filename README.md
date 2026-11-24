# Expo Web Data Scraping

A free tool (no AI, no API keys) that reads an expo exhibitor-list link (or a PDF, image, Excel, Google Sheet, pasted text) and writes the company data into Google Sheets.

- One Google Sheet per website, one tab per year, the same columns in every tab
- No duplicates when a list is read again; existing or manual values are never overwritten
- Conflicting values go to **Extra** and the row is marked **Check**
- Runs on your own Windows laptop as a small web page at `http://localhost:8601`
- Google side: a small Google Apps Script web app (`apps_script/Code.gs`) that only touches one Drive folder

## Setup (short)

1. Install Python 3.11+ and run `install.bat` (installs packages and the auto-start).
2. In Google Apps Script, paste `apps_script/Code.gs`, set Script properties `SECRET` and `FOLDER_ID`, then Deploy as Web app (Execute as: Me, Access: Anyone).
3. Copy `config.example.json` to `config.json` and fill in `apps_script_url` and `secret`.
4. Run `restart.bat` and open `http://localhost:8601`.

## Important

`config.json` holds your secret password. It is listed in `.gitignore`. **Never upload it, never share it.**

## Folders

| Path | What it is |
|---|---|
| `app.py` | The web page (Streamlit) |
| `expo/` | Reading, matching and saving code |
| `apps_script/Code.gs` | Google Apps Script (the Sheets "hands") |
| `tests/` | Automatic tests |
| `*.bat`, `start_hidden.vbs` | Install, restart, auto-start on Windows |
