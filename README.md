# OuraAPI

A personal-use tool that pulls Oura Ring data through the Oura API V2.

## Setup

1. Install Python 3.9 or newer. No extra packages are required.
2. Copy `.env.example` to `.env` and fill in the Client ID and Client Secret from the Oura developer portal (cloud.ouraring.com/oauth/applications).
3. Confirm the app's Redirect URI in the Oura portal is exactly:
   `http://localhost:8989/callback`

## Authorize (one time)

Option A: the account owner signs in on the computer that runs the script:

```
python oura_pull.py auth
```

A browser opens. He signs in to Oura and approves access. The script saves the tokens.

Option B: the account owner approves from his own device:

```
python oura_pull.py auth --paste
```

The script prints a link. He opens it, signs in, and approves. His browser then shows a page that won't load, at an address starting with `http://localhost:8989/callback?code=...`. He copies that full address and sends it to you within a few minutes, and you paste it into the script.

## Pull data

```
python oura_pull.py pull --days 30
```

This writes CSV files to the `data/` folder: daily sleep, readiness, activity, SpO2, and detailed sleep periods.

Tokens refresh automatically. If access is revoked or the Oura membership lapses, run `auth` again.

## Files never committed

`.env`, `tokens.json`, and `data/` hold secrets or personal health data. They are listed in `.gitignore` and must never be uploaded to GitHub.
