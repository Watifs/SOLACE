# Deploying Solace Web (free, auto-deploy on every push)

The desktop app (`solace.py`) is **unchanged**. The web layer lives in new files:
`webapp.py`, `templates/index.html`, `Dockerfile`, `render.yaml`,
`requirements.txt`, and `sample_music/`. It reuses solace.py's emotion model and
playlist generator by importing it as a library.

It works on **any device** — iPad, iPhone, Android, laptop — through a browser.
The UI is responsive with large touch targets, iOS-safe-area padding, and HTTP
range streaming so audio seeking works in Safari.

---

## Recommended host: Render (easiest free Docker + GitHub auto-deploy)

### 1. Add a few playable songs (optional but recommended)
Copy 5–15 audio files into `sample_music/` using their **original filenames**
(see `sample_music/README.md`). Songs without a file still appear and work in
playlists/lyrics — they just won't stream audio.

### 2. Push everything to GitHub
```powershell
git add .
git commit -m "Add Solace web front-end + deploy config"
git push origin main
```

### 3. Create the service on Render
1. Go to https://render.com and sign in with GitHub (free).
2. **New ▸ Blueprint**.
3. Select your repo **github.com/Watifs/SOLACE**.
4. Render reads `render.yaml`, shows a service named **solace** on the **free**
   plan. Click **Apply**.
5. First build takes a few minutes (it builds the Docker image). When it's
   **Live**, you get a public URL like `https://solace.onrender.com`.

### 4. Open it on your iPad / iPhone
Visit the URL in Safari. Tap the **Share ▸ Add to Home Screen** to get an
app-like icon (full-screen, no browser chrome).

### Auto-deploy
`autoDeploy: true` in `render.yaml` means **every `git push` to `main`
redeploys automatically.** Just commit and push — Render rebuilds and goes live.

> **Free-tier note:** the service sleeps after ~15 min idle; the first request
> after sleeping takes ~30–50 s to wake (cold start), then it's fast. The disk
> is ephemeral, so anything written at runtime resets on redeploy — fine here,
> since the library is read-only and shipped in the image.

---

## Alternative host: Railway
1. https://railway.app → **New Project ▸ Deploy from GitHub repo** → pick
   `Watifs/SOLACE`.
2. Railway auto-detects the `Dockerfile` and builds it. No extra config needed.
3. Under the service ▸ **Settings ▸ Networking ▸ Generate Domain** for a public
   URL. Pushes to `main` redeploy automatically.

Railway's free usage is trial-credit based (can run out); Render has a standing
free web tier, which is why Render is the recommendation.

---

## Run it locally first (to preview before deploying)
```powershell
pip install flask gunicorn numpy
python webapp.py
# open http://127.0.0.1:8000
```

## What's where
| File | Purpose |
|------|---------|
| `webapp.py` | Flask backend; imports solace.py, serves API + audio (range) |
| `templates/index.html` | Responsive touch UI (mood→playlist, library, player, lyrics) |
| `requirements.txt` | flask, gunicorn, numpy |
| `Dockerfile` | Container build (python:3.12-slim + gunicorn) |
| `render.yaml` | Render blueprint, auto-deploy on push |
| `sample_music/` | Drop audio files here to make songs playable |

Your data files (`solace_library.json`, `solace_lyrics.json`,
`solace_feedback.json`) are shipped read-only so the cloud app shows the same
emotion tags, lyrics and learned playlist preferences as your desktop app.
