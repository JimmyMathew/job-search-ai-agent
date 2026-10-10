# Advanced: use an existing sheet (service account)

**Most people should skip this page.** The normal setup signs you in with your Google account and
creates your sheet for you (see the README). Use this page only if you already have a Google Sheet
you want the agent to fill, or you do not want to sign in through the browser. The app can only
open sheets it created itself, so a hand-made sheet needs this "robot account" route.

You create a small "robot" Google account called a **service account** and share your sheet with
it, just like sharing with a friend. Nobody else can see your sheet. When you set `sheet:` in
`config.yaml` and the key file below exists, the app uses this route automatically; you can also
force it with `auth: service_account`.

You will end up with one file, `service-account.json`. Treat it like a password: never share it,
never upload it anywhere. About 15 minutes, once.

## 1. Create a Google Cloud project

1. Go to <https://console.cloud.google.com/> and sign in with your Google account.
2. Click the project picker at the top (next to the Google Cloud logo) → **New project**.
3. Name it `job-search`, click **Create**, then make sure it is the selected project.

(Google may ask you to accept terms. You do not need to enable billing; everything here is free.)

## 2. Turn on the Google Sheets API

1. Open <https://console.cloud.google.com/apis/library/sheets.googleapis.com>.
2. Check that your `job-search` project is selected at the top, then click **Enable**.

## 3. Create the service account

1. Open <https://console.cloud.google.com/iam-admin/serviceaccounts>.
2. Click **Create service account**.
3. Name: `job-agent`. Click **Create and continue**.
4. Skip the optional "roles" and "grant users access" steps: click **Continue**, then **Done**.

## 4. Download its key

1. In the service account list, click the `job-agent` email address.
2. Open the **Keys** tab → **Add key** → **Create new key** → **JSON** → **Create**.
3. A file downloads. Rename it to `service-account.json` and move it into the `secrets` folder of
   this project.

(If Google says key creation is blocked by an organization policy, you are probably signed in
with a work account. Use a personal Google account.)

## 5. Create your sheet and share it with the robot

1. Go to <https://sheets.google.com> and create a **blank spreadsheet**. Name it anything.
2. Click **Share**. Paste the robot's email address (it looks like
   `job-agent@job-search-12345.iam.gserviceaccount.com`). You can see it by running the doctor
   (step 6); it prints it, or open `service-account.json` and copy the `client_email` value.
3. Set the role to **Editor**, untick "Notify people", click **Share**.
4. Copy the sheet's web address from your browser's address bar.
5. Open `profiles/<your-name>/config.yaml` and paste it after `sheet:` (and optionally add the
   line `auth: service_account`).

## 6. Check everything

```
uv run python scripts/doctor.py --profile <your-name>
```

It prints OK or FIX for each item, with the exact fix for anything wrong. When everything is OK,
the doctor has also created the tabs in your sheet.

## Troubleshooting

| What you see | What it means | Fix |
| --- | --- | --- |
| `Can't find the sheet` | Wrong address, or not shared with the robot | Re-check the address in `config.yaml`; share with the robot's email as Editor |
| `Google refused access` | The Sheets API is off, or the sheet is not shared | Redo step 2; check sharing |
| `Google key not found` | `service-account.json` is not in `secrets/` | Redo step 4, check the file name |
| `isn't a valid service-account key` | Wrong file downloaded | Create a new JSON key (step 4) |
