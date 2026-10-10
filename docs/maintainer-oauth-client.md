# Maintainer: create the app's Google sign-in client (once, for the whole project)

This is for the project owner, not for users. Users never see the Google Cloud console. They only
see Google's normal "Sign in with Google" screen when they set the app up.

You create one Google Cloud project and one OAuth client. The client file is committed to the
repo as `scripts/google_oauth_client.json`; every user's copy of the app uses it to ask Google for
permission to create and edit files **the app itself created** in the user's own Drive
(`drive.file`, a non-sensitive scope). Desktop-app client secrets are not confidential, so
committing the file is fine.

## Steps

1. Go to <https://console.cloud.google.com/>. Click the project picker, then **New project**, and
   name it `job-search-agent`. Use a **personal Google account** so the audience can be External.
2. **APIs & Services** → **Library**. Enable both **Google Sheets API** and **Google Drive API**.
3. Open <https://console.cloud.google.com/auth/overview> and click **Get started**.
   - App name: `Job Search Agent`
   - User support email: a dedicated alias is better, because every user sees it
   - Audience: **External**
   - Contact email: your email
   - Agree to the policy, then **Create**.
4. **Data Access** → **Add or remove scopes**. Filter for `drive.file`, tick
   `.../auth/drive.file`, **Update**, then **Save**. It must be listed under **non-sensitive
   scopes**.
5. **Branding**: do **not** upload a logo (a logo triggers a verification review).
6. **Audience** → **Publish app** → **Confirm**. The status must read **In production**.
   "Testing" status makes refresh tokens expire after 7 days and caps the app at 100 test users.
   If Google asks for verification, a sensitive scope slipped in: remove it.
7. **Clients** → **Create client** → application type **Desktop app**, name
   `job-search-agent desktop` → **Create**. Download the JSON immediately and save it as
   `scripts/google_oauth_client.json`. The top-level key in the file must be `"installed"`.
8. Never delete this project or this client. Deleting either signs every user out and breaks
   their saved sheets until they sign in again.

## Things to verify once

- With the app in production and only non-sensitive scopes, sign-in shows **no** "Google hasn't
  verified this app" screen.
- `files.list` under `drive.file` finds the sheet the app created. Crash recovery relies on this:
  if the local record of a user's sheet is lost, `sheet.py connect` looks the sheet up by its title
  (`Job Search - <profile>`) instead of creating a second one.
- GitHub push protection may flag the committed client secret. It can be bypassed: desktop client
  secrets are not confidential.
- Whether the console demands a privacy policy or homepage link. If so, use the GitHub repo URL.
