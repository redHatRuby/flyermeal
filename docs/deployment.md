# Deployment Guide

## Architecture
- **Frontend** → GoDaddy (cPanel file manager or FTP)
- **Backend** → Render.com (free tier, Python/Flask)
- **Database** → SQLite (local dev) → upgrades automatically on Render

---

## Step 1: Deploy Backend to Render

1. Push your code to GitHub (Render deploys from GitHub)
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   # Create a repo on github.com, then:
   git remote add origin https://github.com/YOUR_USERNAME/flyermeal.git
   git push -u origin main
   ```

2. Go to [render.com](https://render.com) → Sign up (free)

3. Click **New → Web Service** → Connect your GitHub repo

4. Configure the service:
   - **Name**: flyermeal-api
   - **Root Directory**: backend
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Plan**: Free

5. Add Environment Variables in Render dashboard:
   - `SECRET_KEY` → generate a random string (e.g. use [randomkeygen.com](https://randomkeygen.com))
   - `FLASK_ENV` → `production`
   - `ALLOWED_ORIGIN` → `https://yourdomain.com`

6. Click **Deploy** — Render gives you a URL like `https://flyermeal-api.onrender.com`

7. Update `frontend/js/api.js` — replace the Render URL placeholder with your actual URL

---

## Step 2: Deploy Frontend to GoDaddy

1. In `frontend/js/api.js`, make sure the production URL points to your Render app

2. Log into GoDaddy → cPanel → File Manager

3. Navigate to `public_html` (or your domain's folder)

4. Upload the entire `frontend/` folder contents:
   - `index.html`
   - `css/style.css`
   - `js/api.js`
   - `js/app.js`
   - `pages/` folder (when you build those pages)

5. Visit your domain — you should see the FlyerMeal login screen!

---

## Local Development

```bash
# 1. Set up Python environment
cd backend
python -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env from example
cp .env.example .env
# Edit .env and add your SECRET_KEY

# 4. Run Flask
python app.py
# App runs at http://localhost:5000

# 5. Open frontend
# Open frontend/index.html in your browser
# Or use Live Server extension in VS Code
```

---

## Render Free Tier Notes

- App **spins down** after 15 minutes of inactivity (free tier)
- First request after spin-down takes ~30 seconds to wake up
- 750 hours/month free — enough for one always-on app
- Upgrade to $7/month Starter plan when you go live to avoid spin-down

---

## Updating the App

```bash
git add .
git commit -m "describe your change"
git push
```
Render auto-deploys every time you push to GitHub. GoDaddy frontend just needs a re-upload of changed files.
