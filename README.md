# 🛒 DorothyAnn

> Weekly meal plans built around this week's best grocery deals.

DorothyAnn scans local grocery flyers, extracts sale items, and builds a personalized weekly meal plan — complete with leftover meals, bulk buy alerts, and a store-by-store shopping list.

---

## Features

- 📋 **Flyer Scanning** — upload flyer PDFs or images, extract all sale items
- 🍽️ **Meal Planning** — 7-day plans built around sales and your preferences
- ♻️ **Leftover Planning** — every plan includes planned leftover meals to cut waste
- ⭐ **Bulk Buy Alerts** — flags exceptional deals worth stocking up on
- 👤 **User Profiles** — stores, dislikes, dietary needs, online vs in-store
- 🛍️ **Shopping Lists** — grouped by store, adjusted for leftovers

---

## Project Structure

```
flyermeal/
├── frontend/                  → GoDaddy (HTML/CSS/JS)
│   ├── index.html             → Login & dashboard
│   ├── css/style.css
│   ├── js/api.js              → Backend API calls
│   ├── js/app.js              → Auth & routing
│   └── pages/                 → Meal plan, flyers, profile pages
│
├── backend/                   → Render.com (Python/Flask)
│   ├── app.py                 → Main Flask app + API routes
│   ├── requirements.txt
│   ├── .env.example
│   └── data/
│       ├── flyers/            → Scanned flyer JSON files
│       └── profiles/          → User profile JSON files
│
├── .claude/skills/            → Claude Code agent skills
│   ├── flyer-scanner/         → Extracts sale items from flyers
│   └── meal-planner/          → Builds weekly meal plans
│
└── docs/
    └── deployment.md          → How to deploy to GoDaddy + Render
```

---

## Capstone Build Plan (Weeks 4–8)

| Week | Focus | What Gets Built |
|------|-------|-----------------|
| ✅ Week 4 | Agent Skills | flyer-scanner + meal-planner skills |
| Week 5 | Sub-agents | Separate flyer agent + meal planner agent |
| Week 6 | Agent SDK | Backend routes call agents programmatically |
| Week 7 | Evals | Test suite for meal plan quality |
| Week 8 | Demo | Live demo with real flyers |

---

## Quick Start (Local)

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
python app.py
# Open frontend/index.html in browser
```

See `docs/deployment.md` for full deployment instructions.

---

## Tech Stack

- **Frontend**: HTML, CSS, JavaScript (hosted on GoDaddy)
- **Backend**: Python, Flask, SQLite (hosted on Render.com)
- **AI**: Claude Code + Anthropic Agent SDK
- **Flyer Parsing**: pdfplumber, Pillow
