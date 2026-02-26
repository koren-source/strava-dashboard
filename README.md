# Strava Cycling Performance Dashboard

Live at: [koren-source.github.io/strava-dashboard](https://koren-source.github.io/strava-dashboard/)

Personal cycling performance dashboard powered by Strava API + AI workout recommendations.

## How It Works

```
Strava API  -->  GitHub Actions (every 6hrs)  -->  JSON data files  -->  Static site on GitHub Pages
                       |
               Claude API (Sonnet 4.6)  -->  AI workout recommendation
```

1. **GitHub Actions** runs `scripts/fetch-strava.py` on a cron schedule (every 6 hours)
2. The script refreshes the Strava OAuth token, fetches recent rides and athlete stats
3. Data is written to `data/rides.json` and `data/athlete.json`
4. A second script (`scripts/generate-recommendation.py`) calls Claude API to generate an AI-powered Growth Ride recommendation
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
| `ANTHROPIC_API_KEY` | Anthropic API key for AI recommendations (optional) |

### 4. Enable GitHub Pages

Settings > Pages > Source: Deploy from branch > Branch: `main`, folder: `/ (root)`

### 5. Trigger the First Sync

Go to Actions > "Strava Sync & AI Recommendations" > "Run workflow"

## AI Recommendations

The Growth Ride recommendation uses Claude Sonnet 4.6 to analyze:
- Last ride metrics (power, HR, suffer score, duration)
- Athlete profile (FTP, weight, goals)
- Training context (days since last ride, TSS trend, training load)

The AI returns a structured workout prescription: name, reasoning, target power, sets, and duration.

If the `ANTHROPIC_API_KEY` secret is not set, the system falls back to rule-based recommendations (Zone 2 → Threshold, Zone 3 → VO2 Max, etc.)

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

# Test AI recommendation (requires env var)
export ANTHROPIC_API_KEY=xxx
python3 scripts/generate-recommendation.py
```

## Manual Sync

Trigger from the GitHub Actions tab: Actions > "Strava Sync & AI Recommendations" > "Run workflow"

## File Structure

```
index.html                          # Dashboard (single-page, all CSS/JS inline)
data/
  athlete.json                      # Athlete profile (FTP, weight, goals, YTD stats)
  rides.json                        # Recent rides from Strava
  recommendation.json               # AI-generated workout recommendation
scripts/
  fetch-strava.py                   # Strava API fetch (runs in GitHub Actions)
  generate-recommendation.py        # AI recommendation generator
.github/workflows/
  strava-sync.yml                   # GitHub Actions workflow (cron + manual)
```
