# Job Hunt Ledger

Job Hunt Ledger is a local personal job-search dashboard for junior frontend and full-stack roles in Metro Manila, the Philippines, and remote-friendly markets. It collects listings from approved structured job sources, records readable match reasons, prevents duplicate rows, and keeps application tracking under the user's control.

The backend is FastAPI with SQLite, HTTPX, Pydantic settings, and APScheduler. The frontend is React, Vite, and TypeScript. Both run as separate loopback-only development servers. There is no deployment configuration, browser scraping, auto-apply, email sending, telemetry, external AI processing, or external cron service.

## What the application does

- Searches Adzuna, RemoteOK, We Work Remotely, Remotive, Jobicy, configured Greenhouse boards, and configured Lever sites through official APIs or feeds.
- Runs the same ingestion pipeline manually and every day at 08:00 Asia/Manila.
- Normalizes listings, applies deterministic preferences, records match reasons, and rejects duplicates with a unique SHA-256 identity.
- Preserves manual statuses, notes, and immutable status history across every refresh.
- Marks old listings stale without deleting them and reactivates them when rediscovered.
- Provides filters, sorting, pagination, saved views, filtered CSV export, and consistent SQLite backups.
- Optionally reads probable job-application messages through Gmail's read-only API and presents status suggestions for explicit confirmation.

The supported tracking statuses are `New`, `Applied`, `Not Interested`, and `Declined`. A source refresh never changes them. Gmail never changes them without the confirmation dialog.

## Prerequisites

- Python 3.11 or newer
- Node.js 20.19+ or 22.12+
- npm
- An internet connection for enabled job sources and optional Gmail checks
- Optional Adzuna application credentials
- Optional Google Desktop OAuth credentials for Gmail

The commands below use Windows PowerShell because this checkout is on Windows. Run them from the project root unless a command changes directory.

## First-time backend setup

Create the virtual environment, install the backend packages, and make a local environment file:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

The application starts without Adzuna or Gmail credentials. A job refresh reports a source-specific `missing_credentials` result when enabled Adzuna has no credentials; other configured sources continue. Gmail controls remain disconnected until its credentials are configured.

### Keep private data outside this OneDrive checkout

Git ignores `.env`, databases, tokens, backups, logs, `node_modules`, and build output. Git ignore rules do not prevent OneDrive from synchronizing those files. Keep the database, backup directory, Gmail token, and preferably real credentials outside this project.

The following environment variables last for the current PowerShell session and keep runtime data under the Windows local application-data directory:

```powershell
$localData = Join-Path $env:LOCALAPPDATA 'JobSearchDashboard'
New-Item -ItemType Directory -Force -Path $localData | Out-Null

$env:DATABASE_URL = 'sqlite:///' + (($localData -replace '\\', '/') + '/job_dashboard.db')
$env:BACKUP_DIR = Join-Path $localData 'backups'
$env:GMAIL_TOKEN_PATH = Join-Path $localData 'gmail_token.json'
```

You can move the checkout to a non-synced directory and use the root `.env` instead. Never commit or synchronize real API keys, OAuth secrets, tokens, databases, backups, fetched jobs, Gmail metadata, notes, or private logs.

## Environment configuration

The backend reads the root `.env` through `python-dotenv` and Pydantic settings. Environment variables set in the shell override values in that file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development` | Local environment label. |
| `DATABASE_URL` | `sqlite:///./data/job_dashboard.db` | Local SQLite URL. Network and non-SQLite URLs are rejected. |
| `BACKUP_DIR` | `./backups` | Directory used by user-requested backups. |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated or JSON list of permitted loopback frontend origins. |
| `ADZUNA_APP_ID` | empty | Adzuna application ID. |
| `ADZUNA_APP_KEY` | empty | Adzuna application key. |
| `GMAIL_CLIENT_ID` | empty | Google Desktop OAuth client ID. |
| `GMAIL_CLIENT_SECRET` | empty | Google Desktop OAuth client secret. |
| `GMAIL_REDIRECT_URI` | `http://localhost:8000/api/gmail/auth/callback` | Fixed loopback OAuth callback. |
| `GMAIL_TOKEN_PATH` | OS user-data path | OAuth token file; paths inside this project are rejected. |

The frontend reads `frontend/.env`:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

Only loopback hosts are accepted. Keep the frontend URL, `CORS_ORIGINS`, and ports aligned if you change the defaults.

## Start the application

Start FastAPI in the first terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Prepare and start Vite in a second terminal:

```powershell
Set-Location frontend
npm install
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
npm run dev
```

Open `http://localhost:5173`. Stop each server with `Ctrl+C`.

Use one backend process and do not add `--workers`. The refresh and Gmail locks are process-local, and this personal application is designed for one local FastAPI instance. Do not expose either server to the local network or internet.

## Get Adzuna credentials

1. Register or sign in at the [Adzuna developer portal](https://developer.adzuna.com/).
2. Create an application and copy its application ID and application key.
3. Put them in a non-synced root `.env`, or set them for the backend terminal:

```powershell
$env:ADZUNA_APP_ID = 'your-application-id'
$env:ADZUNA_APP_KEY = 'your-application-key'
```

4. Restart FastAPI and use **Find jobs now**.

Adzuna is currently disabled so the dashboard can search the public feeds without credentials. Enable it after adding credentials. Its configured country remains `ph`; Adzuna's published country list currently omits the Philippines, so the live endpoint may reject that country. The application reports the failure and never silently switches countries. See the [Adzuna search API](https://developer.adzuna.com/docs/search), [country documentation](https://www.adzuna.co.uk/jobs/xml-specification.html), and [published API limits](https://developer.adzuna.com/docs/terms_of_service).

## Configure job sources

Edit `backend/config/sources.json`. The entire file is validated at startup and for refresh work. Unknown source names, unknown fields, malformed JSON, URLs in slug lists, and out-of-range limits are rejected. Valid edits are picked up by the next refresh.

Each source has an `enabled` flag. A failure from one enabled source is recorded in the refresh result and does not stop the others.

```json
{
  "sources": {
    "adzuna": {
      "enabled": false,
      "country": "ph",
      "results_per_page": 50,
      "max_pages": 3,
      "query": "developer",
      "min_request_interval_seconds": 2.5
    },
    "remoteok": {
      "enabled": false,
      "max_items": 250,
      "min_request_interval_seconds": 2.5
    },
    "we_work_remotely": {
      "enabled": false,
      "max_items": 250,
      "min_request_interval_seconds": 2.5
    },
    "remotive": {
      "enabled": false,
      "max_items": 200,
      "category": "software-dev",
      "min_request_interval_seconds": 2.5
    },
    "jobicy": {
      "enabled": false,
      "max_items": 200,
      "industry": "engineering",
      "min_request_interval_seconds": 2.5
    },
    "himalayas": {
      "enabled": true,
      "max_pages": 3,
      "query": "developer",
      "country": "Philippines",
      "seniority": "Entry-level",
      "employment_type": "Full Time",
      "exclude_worldwide": true,
      "min_request_interval_seconds": 2.5
    },
    "greenhouse": {
      "enabled": false,
      "company_slugs": [],
      "max_items_per_company": 500,
      "min_request_interval_seconds": 1.0
    },
    "lever": {
      "enabled": false,
      "company_slugs": [],
      "results_per_page": 100,
      "max_pages": 5,
      "min_request_interval_seconds": 1.0
    }
  }
}
```

- **Adzuna** uses its search API. `query` controls the upstream search, while local preferences decide which returned jobs qualify.
- **RemoteOK** uses its public JSON feed. `max_items` limits local processing.
- **We Work Remotely** uses the official All Programming RSS feed. HTML pages are never scraped.
- **Remotive** uses its public remote-jobs API, requests the `software-dev` category, preserves Remotive attribution, and enforces the provider's limit of four local requests per rolling day.
- **Jobicy** uses its public remote-jobs API and requests the `engineering` industry. Its published guidance permits the dashboard's daily polling interval.
- **Himalayas** uses its public search API with no API key. It requests entry-level, full-time remote developer jobs restricted to the Philippines and excludes generic worldwide results.
- **Greenhouse** uses its public Job Board JSON API with source descriptions enabled.
- **Lever** uses its public Postings JSON API. Application endpoints are never called.

Official source references: [RemoteOK feeds](https://remoteok.com/faq#feeds-sponsorship), [We Work Remotely RSS](https://weworkremotely.com/remote-job-rss-feed), [Remotive API](https://github.com/remotive-com/remote-jobs-api), [Jobicy API](https://github.com/Jobicy/remote-jobs-api), [Himalayas Jobs API](https://himalayas.app/api), [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html), and [Lever Postings API](https://github.com/lever/postings-api).

### Manual search desk

The dashboard links to PhilJobNet, Kalibrr, LinkedIn Jobs, JobStreet, and Indeed for manual searches. These links only open the official sites in a new tab. The app does not scrape them, sign in, submit applications, or import data from their pages.

LinkedIn, JobStreet, and Indeed do not provide an approved public listings API for this dashboard. Scraping their rendered pages would be brittle, could violate site terms and access controls, and could lead to account or IP restrictions. PhilJobNet and Kalibrr are also kept manual because no suitable documented public API was identified. If one of these services publishes an official API or feed with compatible usage terms later, it can be added as a normal adapter.

### Add Greenhouse company slugs

Open a company's public Greenhouse job-board link yourself. For an address shaped like `https://job-boards.greenhouse.io/acme`, the slug is `acme`. Add only that final board token, not the full URL:

```json
"greenhouse": {
  "enabled": true,
  "company_slugs": ["acme", "example_company"],
  "max_items_per_company": 500,
  "min_request_interval_seconds": 1.0
}
```

### Add Lever company slugs

Open a company's public Lever jobs link yourself. For an address shaped like `https://jobs.lever.co/acme`, the site slug is `acme`:

```json
"lever": {
  "enabled": true,
  "company_slugs": ["acme", "example-company"],
  "results_per_page": 100,
  "max_pages": 5,
  "min_request_interval_seconds": 1.0
}
```

Slugs may contain letters, digits, underscores, and hyphens. Empty slug lists make no board requests even if a board source is enabled.

## Adjust job preferences

Edit `backend/config/job_preferences.json`. Matching is deterministic and case-insensitive. Text is normalized for casing, repeated spacing, punctuation, and common variants, while original source title and location remain available for display.

| Field | Effect |
| --- | --- |
| `included_title_keywords` | A title must match at least one phrase. |
| `excluded_seniority_keywords` | A clear title match rejects the listing. Defaults cover senior and leadership roles. |
| `accepted_locations` | Accepted local and remote-compatible location labels. Missing locations never create a location match. |
| `accepted_work_arrangements` | Accepted `Remote`, `Hybrid`, and `On-site` values. |
| `accepted_employment_types` | Accepted employment types when the source provides one. |
| `preferred_companies` | Adds a visible `Preferred company` reason and tag. |
| `excluded_companies` | Rejects listings from matching companies. |
| `excluded_keywords` | Rejects a match in the company, title, or description. |
| `stale_job_threshold_days` | Days since last successful relevant sighting before a listing becomes stale. |
| `minimum_salary` | Optional threshold; `null` means salary never excludes by default. |
| `minimum_salary_currency` | Required three-letter currency when a minimum is enabled. |
| `minimum_salary_period` | Required `year`, `month`, `week`, `day`, or `hour` when a minimum is enabled. |

Example additions belong inside the existing object; keep every other required field:

```json
"preferred_companies": ["Acme Philippines"],
"excluded_companies": ["Example Agency"],
"excluded_keywords": ["unpaid", "commission only"],
"stale_job_threshold_days": 30,
"minimum_salary": null,
"minimum_salary_currency": null,
"minimum_salary_period": null
```

Extra fields are rejected. A minimum salary compares only listings with a known matching currency and pay period; the application performs no currency conversion and does not guess a missing period.

### Why a job matched

A listing qualifies only when its title matches an included phrase, its title has no excluded seniority phrase, its company/title/description has no excluded company or keyword, and its location or work arrangement satisfies the configured rules. The stored reasons are readable tags such as:

```text
Title: react developer
Location: Remote
Work arrangement: Remote
Employment type: Full-time
Preferred company
```

There is no opaque relevance score and no external AI service.

## Manual and scheduled refreshes

**Find jobs now** sends `POST /api/refresh`, receives a run ID, and polls the run until it completes. The daily APScheduler task runs at 08:00 Asia/Manila. Both call the identical fetch, normalize, match, deduplicate, and persistence service.

The scheduler is in-process. FastAPI and the computer must be running at 08:00. An application that was closed at that time does not use an external service to catch up. Use **Find jobs now** after reopening it.

Only one refresh can run at a time. A process-level lock rejects overlap with HTTP 409, and the unique database index remains the final concurrency safeguard. Every run is retained in `sync_runs` with its trigger, timestamps, counts, sources, outcome, and sanitized source errors.

## Deduplication and rediscovery

Each normalized job receives a SHA-256 hash built from:

```text
normalize(company) + "|" + normalize(title) + "|" + normalize(source_url)
```

`jobs.dedupe_hash` is unique. Rediscovery updates `last_seen_at` and current source fields but preserves `date_found`, `first_seen_at`, status, notes, and status history. Repeated manual runs, repeated scheduled runs, and concurrent insert attempts cannot create a second row with the same hash.

Different source URLs produce different hashes. The application deliberately avoids fuzzy cross-site deduplication because merging unrelated or reposted jobs could destroy useful records.

## Stale listings

A successful refresh marks an unseen listing stale only when it belongs to the same source and search configuration and its `last_seen_at` exceeds `stale_job_threshold_days`, initially 30 days. A failed source cannot stale its listings.

Stale listings are never deleted or permanently hidden. Their notes, status, and history remain intact and they can be filtered in the dashboard. Rediscovery clears the stale flag and updates `last_seen_at`. Stale means “not seen recently within this bounded source search,” not proof that the employer closed the role.

## Manual application tracking

- New listings start as `New`.
- Change status through the accessible dashboard selector.
- Edit notes and select **Save notes**; notes do not leave the local API.
- Every actual status change creates immutable history with its previous status, new status, source, and UTC timestamp.
- Refreshes never overwrite tracking fields.
- The application never opens or submits an application form for you.

External job links open in a new tab with `noopener noreferrer`. Applying remains a manual action on the employer's site.

## Gmail read-only suggestions

Gmail is optional. The integration calls Gmail through HTTPS but performs no write operation. It requests exactly:

```text
https://www.googleapis.com/auth/gmail.readonly
```

It never requests Gmail send, compose, modify, label, delete, archive, or broad mail scopes. The implementation uses Google's desktop loopback OAuth flow with PKCE and a one-time CSRF state value. See Google's [desktop OAuth guide](https://developers.google.com/identity/protocols/oauth2/native-app), [Gmail scope reference](https://developers.google.com/workspace/gmail/api/auth/scopes), and [`messages.list` reference](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list).

### Create Google Desktop OAuth credentials

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create or select a private project.
2. Open **APIs & Services → Library**, find **Gmail API**, and enable it.
3. Configure the OAuth consent screen. For an External app in testing, add only your own Google account as a test user.
4. Open **APIs & Services → Credentials** and create an **OAuth client ID** with application type **Desktop app**.
5. Copy the client ID and client secret.
6. Set them in a non-synced `.env` or only in the backend terminal:

```powershell
$env:GMAIL_CLIENT_ID = 'your-client-id.apps.googleusercontent.com'
$env:GMAIL_CLIENT_SECRET = 'your-client-secret'
$env:GMAIL_REDIRECT_URI = 'http://localhost:8000/api/gmail/auth/callback'
$env:GMAIL_TOKEN_PATH = Join-Path $env:LOCALAPPDATA 'JobSearchDashboard\gmail_token.json'
```

7. Restart FastAPI, open the dashboard, select **Connect Gmail**, review Google's consent page, and grant read-only access.

The authorization token is written atomically outside the repository. On Windows the default is `%LOCALAPPDATA%\JobSearchDashboard\gmail_token.json`; Linux uses `XDG_DATA_HOME` or `~/.local/share`. Uvicorn request logging is disabled so the OAuth callback code is not recorded in access logs.

To disconnect locally, stop FastAPI and remove the token file yourself. To revoke the grant, also remove the app's access from your Google Account permissions.

### Gmail checks and privacy

The scheduler checks every 15 minutes in Asia/Manila while FastAPI is running. **Check Gmail now** runs the same locked service. It searches probable application mail using terms such as application, thank you for applying, unfortunately, interview, assessment, next steps, and recruiter.

For each newly processed message the database may retain only:

- Gmail message ID
- sender
- subject
- received time
- confidently inferred company
- suggested status, if one is supported
- short sanitized excerpt with email addresses and links redacted
- linked job, detection phrase, and confirm/dismiss audit state

The complete body is held only long enough in memory for local deterministic pattern checks. It is not persisted. Attachments are not fetched. Processed Gmail IDs are stored separately so a message that creates no suggestion is not repeatedly read. No Gmail text is sent to an AI or telemetry service.

One manual action is required for every suggestion:

- **Confirm Applied** or **Confirm Declined** changes the linked job and creates `gmail_suggestion_confirmed` status history.
- **Dismiss** records the decision and changes no job.
- Ambiguous company correlations remain unmatched and cannot be confirmed against a job.

## Filters, sorting, and saved views

The dashboard supports status, source, date-found range, stale/fresh state, work arrangement, employment type, preferred company, and literal text search across title, company, and location. Sort by date found, posting date, company, title, source, or status in ascending or descending order. Unknown posting dates fall back to date found.

Saved views persist the current filters and sort settings under a local name. Loading, renaming, or deleting a saved view never changes jobs or application tracking.

## CSV export

**Export CSV** downloads every job matching the current filters and sort order, independent of the visible page. It includes title, company, location, source, URL, discovery/posting timestamps, match reasons, salary, work arrangement, employment type, stale state, status, and notes.

Cells beginning with spreadsheet formula markers are escaped to reduce formula-injection risk. Treat the exported file as private because it may contain notes and source data.

## Database backup and recovery

**Backup database** uses SQLite's backup API to create a consistent timestamped copy in `BACKUP_DIR`. The frontend cannot choose an arbitrary server path. Keep backups outside the synced checkout.

To restore:

1. Stop FastAPI.
2. Keep the current database as a separate safety copy.
3. Copy the chosen backup over the file named by `DATABASE_URL`.
4. Restart FastAPI and check `/api/health`.

Startup initialization and schema upgrades are additive and repeatable; they never intentionally destroy existing user data. A database created by a newer unsupported application version is refused instead of modified.

## API reference

The machine-readable OpenAPI document is available locally at `http://localhost:8000/openapi.json`. CDN-backed Swagger and ReDoc pages are disabled so opening the API does not load third-party scripts.

| Method and path | Purpose |
| --- | --- |
| `GET /api/health` | Application, database, scheduler, and active-refresh health. |
| `GET /api/jobs` | Filtered, sorted, paginated jobs and counters. |
| `GET /api/jobs/{id}` | Full job, notes, match reasons, and status history. |
| `PATCH /api/jobs/{id}` | Change only `status` and/or `notes`. |
| `POST /api/refresh` | Start a manual source refresh. |
| `GET /api/refresh/{id}` | Read refresh progress and source errors. |
| `GET /api/sync-runs` | Recent refresh history. |
| `/api/saved-views` | Saved-view CRUD. |
| `GET /api/export/jobs.csv` | Export the current filter query. |
| `POST /api/backup` | Create a local SQLite backup. |
| `GET /api/gmail/status` | Gmail configuration and connection state. |
| `GET /api/gmail/auth/start` | Begin local Google consent. |
| `POST /api/gmail/check` | Run a manual read-only Gmail check. |
| `GET /api/gmail/suggestions` | List Gmail suggestions, optionally by state/link. |
| `POST /api/gmail/suggestions/{id}/confirm` | Explicitly accept one linked status suggestion. |
| `POST /api/gmail/suggestions/{id}/dismiss` | Dismiss one pending suggestion. |

Example PowerShell calls:

```powershell
Invoke-RestMethod http://localhost:8000/api/health

$run = Invoke-RestMethod -Method Post http://localhost:8000/api/refresh
Invoke-RestMethod ("http://localhost:8000/api/refresh/$($run.run_id)")

Invoke-RestMethod 'http://localhost:8000/api/jobs?status=New&stale=false&sort_by=date_found&sort_order=desc&page=1&page_size=25'
Invoke-RestMethod -Method Patch -ContentType 'application/json' -Body '{"status":"Applied"}' http://localhost:8000/api/jobs/1

Invoke-WebRequest 'http://localhost:8000/api/export/jobs.csv?status=New' -OutFile jobs.csv
Invoke-RestMethod -Method Post http://localhost:8000/api/backup
Invoke-RestMethod http://localhost:8000/api/gmail/status
Invoke-RestMethod -Method Post http://localhost:8000/api/gmail/check
Invoke-RestMethod 'http://localhost:8000/api/gmail/suggestions?state=pending'
```

Write APIs accept only local browser origins. Errors use a structured `error.code` and safe `error.message`; validation errors also include field details.

## Tests

Backend tests use temporary SQLite databases and synthetic HTTP fixtures. They require no credentials and do not contact live job boards or Gmail:

```powershell
.\.venv\Scripts\python.exe -m pytest -c backend/pytest.ini backend/tests -q --basetemp=backend/.pytest_tmp -p no:cacheprovider
.\.venv\Scripts\python.exe -m pip check
```

Frontend tests use a mocked dashboard API:

```powershell
Set-Location frontend
npm test -- --run
npm run build
npm audit --omit=dev
```

Coverage includes adapters and fixtures, configuration validation, matching reasons, normalization, salary parsing, retry/backoff, rate budgets, deduplication and concurrent insertion, stale handling, tracking preservation, source-failure isolation, API filters and CRUD, export and backup, shared scheduling, Gmail scope and message deduplication, explicit suggestion decisions, dashboard states, accessible controls, and safe external links.

`backend/requirements.txt` contains supported version ranges for Python 3.11+. `backend/requirements-tested.txt` records the exact environment used for project verification.

## Troubleshooting

### The backend does not start

- Read the Pydantic validation message. Both JSON configuration files reject malformed or unknown fields.
- Confirm `DATABASE_URL` begins with `sqlite:///` and points to a local file.
- Confirm `GMAIL_TOKEN_PATH` resolves outside the project directory.
- If the database says it has a newer schema, use the application version that created it; do not downgrade the file in place.

### The dashboard says the backend is offline

- Confirm FastAPI is running at `http://localhost:8000`.
- Confirm `frontend/.env` has `VITE_API_BASE_URL=http://localhost:8000`.
- Confirm the browser uses `http://localhost:5173`, matching `CORS_ORIGINS`.
- Restart Vite after changing its `.env`.

### Adzuna reports missing credentials

- Set both `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`, then restart FastAPI.
- Startup works without them; the error appears only when enabled Adzuna is refreshed.
- If credentials are valid but `country: "ph"` is rejected, see the Adzuna country limitation above or temporarily disable Adzuna and enable another approved source.

### A source returns no jobs

- Check the refresh result and `/api/sync-runs` for a source-specific error.
- Verify the source is enabled and its limits are positive.
- For Greenhouse or Lever, verify the slug rather than pasting a full URL.
- A fetched listing still must pass title, seniority, exclusion, location/arrangement, employment-type, and optional salary rules.
- The application intentionally does not scrape an HTML page when a structured endpoint is unavailable.

### A repeated refresh shows no new jobs

This is expected when the same listings were already stored. Check the existing-jobs count and each row's `last_seen_at`. The unique dedupe hash prevents repeated manual and scheduled runs from adding duplicate rows.

### A job became stale

Stale means the listing was absent from a successful refresh of the same source/search scope beyond the configured threshold. It was not deleted. Increase `stale_job_threshold_days` if desired, or wait for rediscovery to clear the flag.

### Gmail will not connect

- Confirm the Gmail API is enabled for the Google Cloud project.
- Confirm the OAuth client type is **Desktop app**.
- If the consent screen is in testing, add your Gmail account as a test user.
- Confirm the callback is exactly `http://localhost:8000/api/gmail/auth/callback` and the backend is on port 8000.
- Confirm both Gmail credential variables are present in the environment used to launch FastAPI.
- Restart FastAPI after changing credentials.

### Gmail checks fail after previously working

- Check `/api/gmail/status` for the safe last-error message.
- The refresh token may have expired or been revoked. Stop FastAPI, remove the local token file, restart, and connect again.
- Confirm the computer has internet access and Google has not disabled the testing grant.
- A failed message-detail request is retried later because its ID is recorded only after successful local processing.

### Backup fails

- Confirm `BACKUP_DIR` is a writable local directory.
- Confirm there is enough disk space.
- Do not place `BACKUP_DIR` inside the SQLite database file or use a network URL.

### Times look different from the source

Internal timestamps are stored in UTC where practical. Schedules and displayed dashboard times use Asia/Manila. Date-found filters represent inclusive Manila calendar days.

## Privacy and safety boundaries

- Job searches send only the configured query and normal API parameters to enabled approved sources.
- Gmail content goes only between the local backend and Google's Gmail API under the user's read-only grant.
- Job data, email excerpts, notes, databases, and tokens are not sent to analytics, telemetry, AI, or hosting services.
- The application cannot apply for jobs, submit employer forms, send email, or modify Gmail.
- Source and Gmail refreshes cannot change application status.
- Every Gmail-driven status change requires an explicit dashboard confirmation and remains visible in status history.
