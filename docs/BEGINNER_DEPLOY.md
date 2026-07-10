# Deploy PBICompass — beginner step-by-step

This gets your app live on the internet so anyone can open a web page, upload a
Power BI file, and download documentation. No servers to manage. We'll use
**Render** (free to start). Total time: ~20–30 minutes.

We do it in stages. **Stage 1 just gets it live.** Stages 2–4 are optional
upgrades you can do later.

---

## What these words mean (30-second primer)

- **Git** — a tool that tracks your code. You already have it (Git Bash).
- **GitHub** — a website that stores your code online. Render reads your code from here.
- **Render** — a service that takes your code and runs it on the internet for you.
- **Dockerfile** — a recipe (already in your project) that tells Render how to
  build and start the app. You don't edit it for Stage 1.

The plan: **your code → GitHub → Render → a public web address.**

---

## Before you start: create two free accounts

1. **GitHub:** go to https://github.com → **Sign up** → follow the prompts.
2. **Render:** go to https://render.com → **Get Started** → **"Sign in with GitHub"**
   (this links the two automatically — easiest option).

---

## STAGE 1 — Get your app live (no auth, no API key, free)

### Step 1.1 — Open a terminal in your project folder

1. Open the **Start menu**, type **"Git Bash"**, open it.
2. Go to your project folder by typing (then press Enter):
   ```bash
   cd "/c/Users/resod/OneDrive/Desktop/pbidocumenntation"
   ```
   Tip: you can also right-click the folder in File Explorer → **"Open Git Bash here"**.

### Step 1.2 — Tell Git who you are (first time only)

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

### Step 1.3 — Turn the folder into a Git repository and save a snapshot

```bash
git init
git add .
git commit -m "pbicompass initial version"
```
- `git init` starts tracking this folder.
- `git add .` stages every file (your `.gitignore` automatically skips the big
  `.venv` folder and other junk — good).
- `git commit` saves a snapshot called "pbicompass initial version".

### Step 1.4 — Create an empty repository on GitHub

1. Go to https://github.com/new
2. **Repository name:** `pbicompass` (or any name).
3. Choose **Private** (recommended) or Public.
4. **Do NOT** tick "Add a README", ".gitignore", or "license" — leave them off.
5. Click **Create repository**.
6. GitHub shows you a page with a URL like
   `https://github.com/yourname/pbicompass.git`. Copy it.

### Step 1.5 — Push your code to GitHub

Back in Git Bash (replace the URL with yours):
```bash
git remote add origin https://github.com/yourname/pbicompass.git
git branch -M main
git push -u origin main
```
- The first push opens a **browser window asking you to log in to GitHub** —
  approve it. (Windows remembers it after that.)
- When it finishes, refresh your GitHub repo page — you'll see all your files.

### Step 1.6 — Create the web service on Render

1. Go to your **Render dashboard** (https://dashboard.render.com).
2. Click **New +** (top right) → **Web Service**.
3. Render lists your GitHub repos. Find **pbicompass** → click **Connect**.
   - If you don't see it: click **"Configure account"** / **"Configure GitHub"**
     and grant Render access to the repo, then come back.
4. Render reads your project and should show **Language: Docker** (it found the
   Dockerfile). If it asks, choose **Docker**.
5. Fill in:
   - **Name:** `pbicompass` (this becomes part of your URL).
   - **Region:** pick the one nearest you.
   - **Branch:** `main`.
   - **Instance Type:** **Free** (fine for testing).
6. Leave everything else default. Scroll down, click **Create Web Service**.

### Step 1.7 — Wait for the build, then open it

- Render now builds the Docker image and starts it. Watch the **Logs** tab —
  it takes about **3–6 minutes**. You're looking for a line like
  `Uvicorn running on http://0.0.0.0:...` and the status turning **"Live"** (green).
- At the top you'll see your public URL, e.g. **`https://pbicompass.onrender.com`**.
- Click it. You should see your dark PBICompass homepage. 🎉

> **Free-tier note:** free services "go to sleep" after ~15 minutes of no use.
> The next visit takes ~30–60 seconds to wake up. That's normal. Upgrade to the
> **Starter** plan (~$7/month) to remove sleeping (and to enable a disk later).

### Step 1.8 — Test it with a real file

The app accepts a **`.pbix`** file, or a **`.zip` of a `.pbip` project**. The
`.pbip` route is the one that's guaranteed to produce a *complete* document, so
let's use that.

**How to get a `.pbip` zip from Power BI Desktop:**
1. Open your report in **Power BI Desktop**.
2. **File → Save as** → change the type to **"Power BI project files (*.pbip)"**
   → save it into an empty folder.
3. That creates a folder containing your report's pieces (e.g.
   `MyReport.pbip`, `MyReport.Report/`, `MyReport.SemanticModel/`).
4. In File Explorer, select **all** those items → right-click → **Send to →
   Compressed (zipped) folder**. You now have `MyReport.zip`.

**Now:**
5. Open your Render URL, drag `MyReport.zip` onto the upload box.
6. Leave the engine on **"Offline"** (works with no API key).
7. Click **Generate documentation**, wait a few seconds, then **download HTML**
   (or Word/PDF/Markdown). That's your documentation. ✅

> **About `.pbix`:** a raw `.pbix` will upload, but with the default build the
> *model* sections (measures, DAX, data dictionary) come out empty — only the
> report pages are read. To fully support `.pbix`, see Stage 4. The simplest
> reliable path is the `.pbip` zip above.

**You're live.** Everything below is optional.

---

## STAGE 2 — Turn on the Claude / Gemini / Cohere AI engines (optional)

By default the **Offline** engine writes the whole document with templated,
deterministic prose (no API key, free). The Docker image already ships with
the Claude, Gemini, and Cohere client libraries installed — you only need to
add an API key for whichever engine you want:

1. **Get an API key:**
   - Claude: https://console.anthropic.com → sign up → add billing →
     **API Keys** → create one (looks like `sk-ant-...`).
   - Gemini: https://aistudio.google.com/apikey → create one (looks like
     `AQ....`).
   - Cohere: https://dashboard.cohere.com/api-keys → create one.
   All are paid services billed per use.
2. **Add it to Render:** dashboard → your service → **Environment** tab →
   **Add Environment Variable** → Key: `ANTHROPIC_API_KEY` (for Claude),
   `GEMINI_API_KEY` (for Gemini), or `COHERE_API_KEY` (for Cohere), Value:
   your key → **Save Changes** (Render redeploys).
3. In the web UI, choose the matching engine from the **Documentation
   Engine** dropdown. (If a key is missing or wrong, the job falls back to
   Offline and tells you why in the "fallback notes" — it can't break the
   whole job.)

You can set both keys at once to let every user pick either engine, or paste
a personal key into the UI's "Engine API Key" field instead of setting one
server-side (BYOK, used for that job only, never stored).

---

## STAGE 3 — Turn on accounts & API keys (optional, for a real SaaS)

This makes the service require a key, isolates each customer's jobs, and enforces
daily limits per plan. It needs a small **persistent disk** (to store accounts),
which on Render requires a **paid instance** (Starter, ~$7/mo).

1. **Upgrade the instance:** dashboard → your service → **Settings** → change
   **Instance Type** to **Starter**.
2. **Add a disk:** **Settings → Disks → Add Disk** → Name: `data`, **Mount Path:
   `/data`**, Size: `1 GB` → Save.
3. **Add environment variables** (Environment tab):
   - `PBICOMPASS_DB` = `/data/pbicompass.db`
   - `PBICOMPASS_ADMIN_TOKEN` = a long random string (e.g. generate one at
     https://www.uuidgenerator.net/ or run `python -c "import secrets; print(secrets.token_urlsafe(32))"`
     on your own machine). This is your admin password — save it somewhere safe.
   - `PBICOMPASS_REQUIRE_AUTH` = `1`
   - `PBICOMPASS_SANDBOX_ROOT` = `/tmp/pbicompass`
   Save (Render redeploys).
4. **Create your first account — no shell needed.** Open
   `https://your-app.onrender.com/admin`, paste the admin token from step 3
   into **Admin token** → **Unlock**. Fill in **Tenant** (e.g. `acme`),
   **Name** (e.g. `Acme BI`), pick a **Plan**, click **Create account**.
   It shows an **API key** like `pbicompass_sk_...` **once** — copy it now
   (there's a **Copy** button).
5. Give that key to a user. In the main web UI they expand **"Add report
   details"**, paste the key into the **Account API Key** field, and generate
   as normal. Without a valid key, requests are rejected.
6. To revoke a key later (e.g. it leaked), go back to `/admin` and click
   **Revoke** next to that account — it stops working immediately.

Plans/limits: `free` 10/day, `pro` 200/day, `enterprise` 100,000/day. The
`/admin` page also lists every account with today's usage against its limit.
(The `pbicompass account create` / `list` / `revoke` CLI commands still work
too, via the Shell tab, if you prefer.)

---

## STAGE 4 — Support raw `.pbix` files (optional, may not build)

To read the model out of a raw `.pbix`, the build needs the `pbixray` library.
Edit `Dockerfile`:
```dockerfile
RUN pip install ".[service,pbix]"
```
(or `".[service,agents,pbix]"` if you also did Stage 2), commit and push.

**Honest caveats:** `pbixray` is finicky to install and may fail the build; if it
does, revert that line. Even when it works, **RLS/security can't be read from a
`.pbix`** (only from `.pbip`). The reliable path remains the **`.pbip` zip**.

---

## STAGE 5 — Use your own domain (optional)

1. Render → your service → **Settings → Custom Domains → Add Custom Domain** →
   enter e.g. `docs.yourdomain.com`.
2. Render shows a **CNAME** record. Go to your domain registrar (GoDaddy,
   Namecheap, Cloudflare, …), open DNS settings, and add that CNAME.
3. Wait a few minutes — Render issues a free HTTPS certificate automatically.

---

## Making changes later

Edit files locally, then:
```bash
git add .
git commit -m "what I changed"
git push
```
Render automatically rebuilds and redeploys. That's the whole loop.

---

## Troubleshooting

- **Build failed:** open the **Logs** tab and read the last red lines. Most often
  it's a typo in the `Dockerfile`. Undo your last change, commit, push.
- **"Application failed to respond" / blank page:** usually the port. The
  Dockerfile already binds to Render's `$PORT`, so make sure you pushed the
  latest version (`git push`).
- **First load is slow:** free instances sleep; the first request wakes it
  (~30–60s). Upgrade to Starter to stop this.
- **Uploaded a `.pbix`, model sections are empty:** expected on the default
  build — use a `.pbip` zip, or try Stage 4.
- **"Job not found" right after upload:** wait a moment and it appears; the
  status updates every ~1 second.
- **`git push` asks for a password and rejects it:** GitHub no longer accepts
  account passwords on the command line. Let the browser pop-up log you in, or
  install **GitHub CLI** (`gh auth login`).

---

## Cost summary

- **Free:** Stage 1 (with sleeping). Good for testing and demos.
- **~$7/month:** Starter instance — no sleeping, and unlocks the disk needed for
  accounts (Stage 3).
- **Pay-per-use:** only if you enable Claude (Stage 2) — your Anthropic bill.
- Everything else (the offline engine, all document formats) is free.
