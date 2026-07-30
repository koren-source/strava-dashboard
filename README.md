# Strava Cycling Performance Dashboard

Live at: [koren-source.github.io/strava-dashboard](https://koren-source.github.io/strava-dashboard/)

Personal cycling performance dashboard powered by Strava API + data-backed workout recommendations.

## How It Works

```
Strava API  -->  GitHub Actions (every 3hrs)  -->  JSON data files  -->  Static site on GitHub Pages
                       |
              Deterministic training engine  -->  next-session recommendation
```

1. **GitHub Actions** runs `scripts/fetch-strava.py` on a cron schedule (every 3 hours)
2. The script refreshes the Strava OAuth token, fetches recent rides and athlete stats
3. Data is written to `data/rides.json` and `data/athlete.json`
4. A second script (`scripts/generate-recommendation.py`) evaluates the last 14 days of load and builds a recovery, endurance, or controlled quality session
5. The recommendation is written to `data/recommendation.json`
6. All data files are committed and pushed, triggering a GitHub Pages rebuild
7. The static `index.html` loads these JSON files at runtime and renders the dashboard

No API keys are exposed client-side. All API calls happen in GitHub Actions.

## Setup

### 1. Create a Strava API Application

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create an application (use `https://localhost` as the callback URL)
3. Note your **Client ID** and **Client Secret**

### 2. Get Your Refresh Token

Run the OAuth flow manually:

1. Visit: `https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://localhost&scope=read,activity:read_all`
2. Authorize and copy the `code` parameter from the redirect URL
3. Exchange for tokens:
   ```bash
   curl -X POST https://www.strava.com/oauth/token \
     -d client_id=YOUR_CLIENT_ID \
     -d client_secret=YOUR_CLIENT_SECRET \
     -d code=YOUR_CODE \
     -d grant_type=authorization_code
   ```
4. Save the `refresh_token` from the response

### 3. Configure GitHub Secrets

In your repo settings (Settings > Secrets and variables > Actions), add:

| Secret | Description |
|--------|-------------|
| `STRAVA_CLIENT_ID` | Your Strava API client ID |
| `STRAVA_CLIENT_SECRET` | Your Strava API client secret |
| `STRAVA_REFRESH_TOKEN` | Your Strava refresh token |

### 4. Enable GitHub Pages

Settings > Pages > Source: Deploy from branch > Branch: `main`, folder: `/ (root)`

### 5. Trigger the First Sync

Go to Actions > "Strava Sync & Training Plan" > "Run workflow"

## Training Recommendations

The next-session recommendation uses:

- Days since the last ride
- Last-ride load classification
- Strava Relative Effort, with estimated power load as a fallback
- Actual current and prior seven-day load windows

The training engine owns the workout arithmetic, target power, and explanation.
Every plan is validated before it is written, including an exact match between
the displayed duration and the sum of its sets.

## Time Window Toggle

Both workout cards (Growth + Stabilizer) have a 45/60/90 min toggle. When you select a different duration:
- Interval count scales to fit the window
- Warmup/cooldown scale proportionally (5-15 min range)
- Target power stays the same — only volume changes
- The .zwo download generates a file matching the selected duration

## .zwo Downloads

Download buttons generate Zwift workout files (.zwo) that can be imported into:
- Zwift (place in `Documents/Zwift/Workouts/YOUR_ZWIFT_ID/`)
- TrainerRoad (import as custom workout)
- Any training app that supports .zwo format

## Local Development

```bash
# Serve locally
python3 -m http.server 8000
# Open http://localhost:8000

# Test the fetch script (requires env vars)
export STRAVA_CLIENT_ID=xxx
export STRAVA_CLIENT_SECRET=xxx
export STRAVA_REFRESH_TOKEN=xxx
python3 scripts/fetch-strava.py

# Test the training engine
python3 -m unittest discover -s tests
python3 scripts/generate-recommendation.py
```

## Manual Sync

Trigger from the GitHub Actions tab: Actions > "Strava Sync & Training Plan" > "Run workflow"

## File Structure

```
index.html                          # Dashboard (single-page, all CSS/JS inline)
data/
  athlete.json                      # Athlete profile (FTP, weight, goals, YTD stats)
  rides.json                        # Recent rides from Strava
  recommendation.json               # Data-backed workout recommendation
scripts/
  fetch-strava.py                   # Strava API fetch (runs in GitHub Actions)
  generate-recommendation.py        # Training recommendation generator
training_plan.py                     # Pure session selection + validation logic
tests/
  test_training_plan.py             # Planner regression tests
.github/workflows/
  strava-sync.yml                   # GitHub Actions workflow (cron + manual)
```
