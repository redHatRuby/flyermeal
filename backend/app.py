"""
FlyerMeal - Backend API
Flask application for grocery flyer scanning and meal planning.

Run locally:  python app.py
Deploy to:    Render.com (see docs/deployment.md)
"""

from flask import Flask, request, jsonify, session, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
import re
import base64
import json
import tempfile
from io import BytesIO
from datetime import datetime, timedelta
from dotenv import load_dotenv
import anthropic as _anthropic
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

load_dotenv()

# --- App Setup ---
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
app.permanent_session_lifetime = timedelta(days=7)

# Allow requests from your GoDaddy frontend
# In production, replace * with your actual domain e.g. "https://yourdomain.com"
CORS(app, supports_credentials=True, origins=os.environ.get("ALLOWED_ORIGIN", "*"))

# --- Database Setup ---
# Uses SQLite locally, easy to swap for PostgreSQL on Render later
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///flyermeal.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# --- Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class FlyerScan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    store = db.Column(db.String(100), nullable=False)
    scanned_at = db.Column(db.DateTime, server_default=db.func.now())
    flyer_date = db.Column(db.String(50), nullable=True)
    item_count = db.Column(db.Integer, default=0)
    bulk_deal_count = db.Column(db.Integer, default=0)
    items_json = db.Column(db.Text, default="[]")       # full item list
    bulk_deals_json = db.Column(db.Text, default="[]")  # full bulk deal list
    source_url = db.Column(db.Text, nullable=True)       # URL where flyer was found (web-fetched scans)


class MealPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    generated_at = db.Column(db.DateTime, server_default=db.func.now())
    week_of = db.Column(db.String(20), nullable=True)
    plan_json = db.Column(db.Text, nullable=False)
    stores_used = db.Column(db.Text, default="[]")  # JSON array of store names


class UserProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True)
    household_size = db.Column(db.Integer, default=1)
    dietary_restrictions = db.Column(db.Text, default="[]")   # JSON string
    disliked_foods = db.Column(db.Text, default="[]")          # JSON string
    preferred_stores = db.Column(db.Text, default="[]")        # JSON string
    excluded_stores = db.Column(db.Text, default="[]")         # JSON string
    shop_online = db.Column(db.Boolean, default=True)
    max_walk_km = db.Column(db.Float, default=2.0)
    budget_per_week = db.Column(db.Float, nullable=True)
    cooking_skill = db.Column(db.String(20), default="intermediate")
    leftover_friendly = db.Column(db.Boolean, default=True)
    postal_code = db.Column(db.String(10), nullable=True)
    province = db.Column(db.String(50), nullable=True)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


# --- Postal Code Helpers ---

_POSTAL_PREFIX_TO_PROVINCE = {
    'A': 'Newfoundland and Labrador',
    'B': 'Nova Scotia',
    'C': 'Prince Edward Island',
    'E': 'New Brunswick',
    'G': 'Quebec', 'H': 'Quebec', 'J': 'Quebec',
    'K': 'Ontario', 'L': 'Ontario', 'M': 'Ontario', 'N': 'Ontario', 'P': 'Ontario',
    'R': 'Manitoba',
    'S': 'Saskatchewan',
    'T': 'Alberta',
    'V': 'British Columbia',
    'X': 'Northwest Territories',
    'Y': 'Yukon',
}

_ATLANTIC_PROVINCES = {'Nova Scotia', 'Newfoundland and Labrador', 'New Brunswick', 'Prince Edward Island'}


def _postal_code_to_province(postal_code):
    if not postal_code:
        return None
    return _POSTAL_PREFIX_TO_PROVINCE.get(postal_code.strip().upper()[0])


def _suggested_stores_for_province(province):
    if province in _ATLANTIC_PROVINCES:
        return ['Sobeys', 'Atlantic Superstore', 'Lawtons Drugs', 'Walmart', 'Costco']
    return ['Sobeys', 'Walmart', 'Costco', 'No Frills', 'Food Basics']


# --- Auth Routes ---
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data or not data.get("email") or not data.get("password"):
        return jsonify({"error": "Email and password required"}), 400

    if User.query.filter_by(email=data["email"]).first():
        return jsonify({"error": "Email already registered"}), 409

    user = User(email=data["email"], name=data.get("name", ""))
    user.set_password(data["password"])
    db.session.add(user)
    db.session.commit()

    session.permanent = True
    session["user_id"] = user.id
    return jsonify({"message": "Account created", "user": {"id": user.id, "email": user.email}}), 201


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json()
    user = User.query.filter_by(email=data.get("email")).first()

    if not user or not user.check_password(data.get("password", "")):
        return jsonify({"error": "Invalid email or password"}), 401

    session.permanent = True
    session["user_id"] = user.id
    return jsonify({"message": "Logged in", "user": {"id": user.id, "email": user.email, "name": user.name}})


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out"})


@app.route("/api/auth/me", methods=["GET"])
def me():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"id": user.id, "email": user.email, "name": user.name})


# --- Profile Routes ---
@app.route("/api/profile", methods=["GET"])
def get_profile():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401

    profile = UserProfile.query.filter_by(user_id=user_id).first()
    if not profile:
        return jsonify({"error": "No profile yet"}), 404

    import json
    suggested = _suggested_stores_for_province(profile.province) if profile.province else []
    return jsonify({
        "household_size": profile.household_size,
        "dietary_restrictions": json.loads(profile.dietary_restrictions),
        "disliked_foods": json.loads(profile.disliked_foods),
        "preferred_stores": json.loads(profile.preferred_stores),
        "excluded_stores": json.loads(profile.excluded_stores),
        "shop_online": profile.shop_online,
        "max_walk_km": profile.max_walk_km,
        "budget_per_week": profile.budget_per_week,
        "cooking_skill": profile.cooking_skill,
        "leftover_friendly": profile.leftover_friendly,
        "postal_code": profile.postal_code,
        "province": profile.province,
        "suggested_stores": suggested,
    })


@app.route("/api/profile", methods=["POST", "PUT"])
def save_profile():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json()
    import json

    profile = UserProfile.query.filter_by(user_id=user_id).first()
    if not profile:
        profile = UserProfile(user_id=user_id)
        db.session.add(profile)

    profile.household_size = data.get("household_size", profile.household_size)
    profile.dietary_restrictions = json.dumps(data.get("dietary_restrictions", []))
    profile.disliked_foods = json.dumps(data.get("disliked_foods", []))
    profile.preferred_stores = json.dumps(data.get("preferred_stores", []))
    profile.excluded_stores = json.dumps(data.get("excluded_stores", []))
    profile.shop_online = data.get("shop_online", profile.shop_online)
    profile.max_walk_km = data.get("max_walk_km", profile.max_walk_km)
    profile.budget_per_week = data.get("budget_per_week", profile.budget_per_week)
    profile.cooking_skill = data.get("cooking_skill", profile.cooking_skill)
    profile.leftover_friendly = data.get("leftover_friendly", profile.leftover_friendly)

    if "postal_code" in data:
        raw_postal = (data["postal_code"] or "").strip().upper() or None
        profile.postal_code = raw_postal
        profile.province = _postal_code_to_province(raw_postal) if raw_postal else None

    db.session.commit()

    suggested = _suggested_stores_for_province(profile.province) if profile.province else []
    return jsonify({"message": "Profile saved", "suggested_stores": suggested, "province": profile.province})


# --- Flyer Scanner Helpers ---

_SCANNER_SYSTEM = """\
You are a grocery flyer scanner. Extract all sale items from the flyer provided.

Store-specific rules:
- Lawtons Drugs is a Canadian drugstore chain. For Lawtons flyers, extract ONLY food \
and beverage items. You MUST exclude the following — do not include them even if on sale: \
vitamins, supplements, protein powders, health aids, cold & flu products, pain relievers, \
allergy medicine, pharmacy items, cosmetics, skincare, haircare, personal care, deodorant, \
toothpaste, household cleaning products, laundry, paper products, and pet supplies. \
If you are unsure whether an item is food/beverage, exclude it.
- Walmart and Costco: extract food items only, ignore general merchandise.
- All other stores: extract all food and beverage items on sale.

Mark bulk_buy as true when any of these apply:
- Price is ≥30% below typical grocery store price
- Deal is a multi-unit offer (e.g. "3 for $10", "buy 2 get 1 free", "4/$5")
- Item has long shelf life and per-unit cost is unusually low

Return ONLY a valid JSON object — no explanation, no markdown fences, just raw JSON:
{
  "store": "store name",
  "flyer_date": "date string or null",
  "items": [
    {
      "name": "item name",
      "category": "meat|produce|dairy|bakery|frozen|pantry|beverage|deli|seafood|other",
      "price": 3.49,
      "unit": "per kg",
      "original_price": null,
      "deal_type": "sale",
      "bulk_buy": false
    }
  ],
  "bulk_deals": [
    {
      "name": "item name",
      "price": 0.99,
      "unit": "per can",
      "note": "Exceptional value — stock up"
    }
  ],
  "summary": {
    "total_items": 42,
    "total_bulk_deals": 3
  }
}"""


def _pdf_to_content_blocks(file_path):
    """Return Claude content blocks for a PDF — text if extractable, images otherwise."""
    with pdfplumber.open(file_path) as pdf:
        pages_text = [page.extract_text() or "" for page in pdf.pages]
    extracted = "\n\n".join(pages_text).strip()

    if len(extracted) > 300:
        return [{"type": "text", "text": f"[Flyer text]\n\n{extracted}"}]

    # Image-based PDF — render each page as JPEG
    doc = pdfium.PdfDocument(file_path)
    blocks = []
    for i in range(min(len(doc), 8)):
        bitmap = doc[i].render(scale=1.5)
        buf = BytesIO()
        bitmap.to_pil().convert("RGB").save(buf, format="JPEG", quality=82)
        buf.seek(0)
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(buf.read()).decode("utf-8"),
            },
        })
    return blocks


def _image_to_content_block(file_path):
    """Return a Claude image content block for a photo."""
    img = Image.open(file_path).convert("RGB")
    if max(img.size) > 1600:
        img.thumbnail((1600, 1600), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=82)
    buf.seek(0)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(buf.read()).decode("utf-8"),
        },
    }


def _call_claude_scanner(content_blocks, store_hint=""):
    """Send flyer content to Claude and return structured scan results."""
    client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    user_content = []
    if store_hint:
        user_content.append({"type": "text", "text": f"The store name is: {store_hint}"})
    user_content.extend(content_blocks)

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=8192,
        system=_SCANNER_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        response = stream.get_final_message()

    app.logger.info(f"Claude blocks: {[(b.type, getattr(b,'text','')[:100]) for b in response.content]}")
    raw = next((b.text for b in response.content if b.type == "text"), "")

    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)

    # Extract outermost JSON object
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end + 1]

    data = json.loads(raw)
    return {
        "store": data.get("store") or store_hint or "Unknown Store",
        "items": data.get("summary", {}).get("total_items", len(data.get("items", []))),
        "bulk_deals": data.get("summary", {}).get("total_bulk_deals", len(data.get("bulk_deals", []))),
        "flyer_date": data.get("flyer_date"),
        "item_list": data.get("items", []),
        "bulk_deal_list": data.get("bulk_deals", []),
    }


# --- Meal Plan Helpers ---

_FLYER_FETCH_SYSTEM = """\
You are a grocery flyer data fetcher for DorothyAnn, a meal planning app serving Atlantic Canada.

Search for this week's sale items at the requested store. The user's province will be provided — use it in your query, e.g. "[store name] [province] flyer this week" or "[store name] [province] weekly deals". Fetch the most relevant result — prefer the store's own site, then flipp.com, reebee.com, or flyerify.ca.

Store-specific filtering:
- Lawtons Drugs: extract ONLY food and beverage items. You MUST exclude: vitamins, supplements, health aids, cold & flu, pain relievers, allergy medicine, pharmacy items, cosmetics, skincare, haircare, personal care, deodorant, toothpaste, household cleaning, laundry, paper products, pet supplies. When in doubt, exclude it.
- Walmart/Costco: food items only, ignore general merchandise.
- All other stores: all food and beverage items on sale.

Mark bulk_buy as true when:
- Price is ≥30% below typical grocery store price
- Deal is a multi-unit offer (e.g. "3 for $10", "buy 2 get 1 free", "4/$5")
- Item has long shelf life and per-unit cost is unusually low

Return ONLY raw JSON, no markdown fences, no explanation:
{
  "store": "store name",
  "flyer_date": "week of YYYY-MM-DD or null",
  "source": "url fetched",
  "items": [
    {
      "name": "item name",
      "category": "meat|produce|dairy|bakery|frozen|pantry|beverage|deli|seafood|other",
      "price": 3.49,
      "unit": "per kg",
      "original_price": null,
      "deal_type": "sale",
      "bulk_buy": false
    }
  ],
  "bulk_deals": [
    {"name": "item name", "price": 0.99, "unit": "per can", "note": "reason to stock up"}
  ],
  "summary": {"total_items": 42, "total_bulk_deals": 3}
}

If you cannot find current data, return:
{"store": "[name]", "error": "not found", "items": [], "bulk_deals": [], "summary": {"total_items": 0, "total_bulk_deals": 0}}"""


_MEAL_BUILDER_SYSTEM = """\
You are DorothyAnn's meal planning engine for Atlantic Canada households.

Given a list of grocery sale items (with store field on each) and a user profile, build a practical 7-day meal plan.

Rules:
- Plan breakfast, lunch, and dinner for all 7 days (21 meals total)
- Every meal must use at least one sale item as a primary ingredient
- Respect all dietary_restrictions — never include excluded ingredients
- Never suggest foods listed in disliked_foods
- If leftover_friendly is true, plan at least 3 leftover meals (leftover: true, leftover_of: "original meal name"); leftovers appear the day after the source meal
- Bulk buy items should feature in multiple meals to justify stocking up
- Match recipe complexity to cooking_skill: beginner=simple one-pot, intermediate=moderate, advanced=multi-step
- Do not suggest meals that require shopping only at excluded_stores
- Shopping list grouped by store; preferred_stores listed first
- If budget_per_week is set, try to stay under it and note if exceeded

Return ONLY raw JSON, no markdown fences, no explanation:
{
  "week_of": "YYYY-MM-DD",
  "household_size": 2,
  "estimated_cost": 87.50,
  "bulk_buy_alerts": [
    {"item": "Campbell's Soup", "store": "Lawtons Drugs", "price": "$1.00/can", "note": "Exceptional value — stock up"}
  ],
  "days": [
    {
      "day": "Monday",
      "meals": {
        "breakfast": {"name": "meal name", "sale_items": ["Item (Store)"], "leftover": false, "leftover_of": null},
        "lunch":     {"name": "meal name", "sale_items": ["Item (Store)"], "leftover": false, "leftover_of": null},
        "dinner":    {"name": "meal name", "sale_items": ["Item (Store)"], "leftover": false, "leftover_of": null}
      }
    }
  ],
  "shopping_list": [
    {
      "store": "store name",
      "items": [{"name": "item", "price": 3.49, "unit": "per kg", "bulk_buy": false}]
    }
  ]
}"""


def _fetch_flyer_via_web(store_name, province=None):
    """Use Claude with web search to find current sale items for a store."""
    client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    location = province or "Atlantic Canada"
    try:
        response = client.beta.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            betas=["web-search-2025-03-05"],
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            system=_FLYER_FETCH_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Find this week's grocery sale items at {store_name} in {location}. Search for '{store_name} {location} flyer this week'. Return structured JSON."
            }],
        )
        raw = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        data = json.loads(raw[start:end + 1])
        items = data.get("items", [])
        for item in items:
            item["store"] = data.get("store", store_name)
        return {
            "store": data.get("store", store_name),
            "flyer_date": data.get("flyer_date"),
            "source": data.get("source", "web"),
            "items": items,
            "bulk_deals": data.get("bulk_deals", []),
        }
    except Exception as e:
        app.logger.error(f"Web flyer fetch failed for {store_name}: {e}")
        return None


def _build_meal_plan_with_claude(all_items, profile_data):
    """Call Claude to generate a 7-day meal plan from sale items and user profile."""
    client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=8192,
        system=_MEAL_BUILDER_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Build a 7-day meal plan.\n\n"
                f"User profile:\n{json.dumps(profile_data, indent=2)}\n\n"
                f"Sale items ({len(all_items)} total):\n{json.dumps(all_items, indent=2)}"
            )
        }],
    ) as stream:
        response = stream.get_final_message()
    raw = next((b.text for b in response.content if b.type == "text"), "")
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in meal plan response")
    return json.loads(raw[start:end + 1])


# --- Meal Plan Helpers ---

def _sse(data):
    return f"data: {json.dumps(data)}\n\n"


def _pipeline_stream(user_id):
    """Generator that runs the full pipeline and yields SSE progress events."""
    try:
        yield _sse({"type": "progress", "step": 1, "message": "Loading your profile and flyer data…"})

        profile = UserProfile.query.filter_by(user_id=user_id).first()
        preferred = json.loads(profile.preferred_stores)     if profile else []
        excluded  = json.loads(profile.excluded_stores)      if profile else []
        dietary   = json.loads(profile.dietary_restrictions) if profile else []
        dislikes  = json.loads(profile.disliked_foods)       if profile else []
        province  = profile.province                         if profile else None

        profile_data = {
            "household_size":       profile.household_size      if profile else 2,
            "cooking_skill":        profile.cooking_skill       if profile else "intermediate",
            "dietary_restrictions": dietary,
            "disliked_foods":       dislikes,
            "preferred_stores":     preferred,
            "excluded_stores":      excluded,
            "leftover_friendly":    profile.leftover_friendly   if profile else True,
            "budget_per_week":      profile.budget_per_week     if profile else None,
        }

        # Fresh scanned flyers (within 7 days), one per store
        cutoff = datetime.utcnow() - timedelta(days=7)
        fresh_scans = (
            FlyerScan.query
            .filter_by(user_id=user_id)
            .filter(FlyerScan.scanned_at >= cutoff)
            .order_by(FlyerScan.scanned_at.desc())
            .all()
        )
        seen, deduped = set(), []
        for scan in fresh_scans:
            if scan.store not in seen:
                deduped.append(scan)
                seen.add(scan.store)

        scanned_stores = {s.store for s in deduped}
        all_items = []
        for scan in deduped:
            items = json.loads(scan.items_json)
            for item in items:
                item["store"] = scan.store
            all_items.extend(items)

        default_stores = ["Sobeys", "Atlantic Superstore", "Lawtons Drugs"]
        target_stores  = [s for s in (preferred or default_stores) if s not in excluded]
        missing_stores = [s for s in target_stores if s not in scanned_stores]

        if missing_stores:
            yield _sse({"type": "progress", "step": 2,
                        "message": f"Fetching flyers for {', '.join(missing_stores)}…"})
        else:
            yield _sse({"type": "progress", "step": 2, "message": "Using your scanned flyer data…"})

        web_fetched, stores_with_no_data = [], []
        for store in missing_stores:
            result = _fetch_flyer_via_web(store, province)
            if result and result.get("items"):
                all_items.extend(result["items"])
                web_fetched.append(store)
                # Save web-fetched flyer to the database the same way uploaded flyers are saved
                bulk_deals = result.get("bulk_deals", [])
                scan = FlyerScan(
                    user_id=user_id,
                    store=result["store"],
                    flyer_date=result.get("flyer_date"),
                    item_count=len(result["items"]),
                    bulk_deal_count=len(bulk_deals),
                    items_json=json.dumps(result["items"]),
                    bulk_deals_json=json.dumps(bulk_deals),
                    source_url=result.get("source"),
                )
                db.session.add(scan)
            else:
                stores_with_no_data.append(store)
        db.session.commit()

        if not all_items:
            yield _sse({"type": "error",
                        "message": "No flyer data available. Please scan a flyer or try again later."})
            return

        yield _sse({"type": "progress", "step": 3,
                    "message": f"Selecting meals from {len(all_items)} sale items…"})

        try:
            plan = _build_meal_plan_with_claude(all_items, profile_data)
        except (json.JSONDecodeError, ValueError) as e:
            app.logger.error(f"Meal plan build failed: {e}")
            yield _sse({"type": "error", "message": "Could not generate meal plan. Please try again."})
            return

        yield _sse({"type": "progress", "step": 4, "message": "Planning leftovers and shopping list…"})

        plan["pipeline_notes"] = {
            "scanned_flyers_used": list(scanned_stores),
            "web_fetched_flyers":  web_fetched,
            "stale_flyers":        [],
            "stores_with_no_data": stores_with_no_data,
            "total_sale_items":    len(all_items),
        }

        # Save to database
        saved = MealPlan(
            user_id=user_id,
            week_of=plan.get("week_of"),
            plan_json=json.dumps(plan),
            stores_used=json.dumps(list(scanned_stores) + web_fetched),
        )
        db.session.add(saved)
        db.session.commit()

        yield _sse({"type": "progress", "step": 5, "message": "Saving your meal plan…"})
        yield _sse({"type": "complete", "plan": plan})

    except Exception as e:
        app.logger.error(f"Pipeline stream error: {e}")
        yield _sse({"type": "error", "message": "An unexpected error occurred. Please try again."})


# --- Meal Plan Routes ---

@app.route("/api/meal-plan", methods=["GET"])
def get_meal_plan():
    """Return the most recently saved meal plan without regenerating."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401

    saved = (
        MealPlan.query
        .filter_by(user_id=user_id)
        .order_by(MealPlan.generated_at.desc())
        .first()
    )
    if not saved:
        return jsonify({"error": "No meal plan yet"}), 404

    return jsonify(json.loads(saved.plan_json))


@app.route("/api/meal-plan/generate", methods=["GET"])
def generate_meal_plan():
    """Stream SSE progress events while running the full pipeline."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 500

    return Response(
        stream_with_context(_pipeline_stream(user_id)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/flyers/scan", methods=["POST"])
def scan_flyer():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401

    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"error": "No file uploaded"}), 400

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 500

    file = request.files["file"]
    store_hint = request.form.get("store_name", "").strip()
    suffix = os.path.splitext(file.filename.lower())[1]

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        if suffix == ".pdf":
            content_blocks = _pdf_to_content_blocks(tmp_path)
        elif suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"):
            content_blocks = [_image_to_content_block(tmp_path)]
        else:
            return jsonify({"error": f"Unsupported file type: {suffix}"}), 400

        result = _call_claude_scanner(content_blocks, store_hint)

        scan = FlyerScan(
            user_id=user_id,
            store=result["store"],
            flyer_date=result.get("flyer_date"),
            item_count=result["items"],
            bulk_deal_count=result["bulk_deals"],
            items_json=json.dumps(result["item_list"]),
            bulk_deals_json=json.dumps(result["bulk_deal_list"]),
        )
        db.session.add(scan)
        db.session.commit()
        result["scan_id"] = scan.id

        return jsonify(result)

    except json.JSONDecodeError:
        app.logger.error("Could not parse Claude response as JSON")
        return jsonify({"error": "Could not parse scan results"}), 500
    except Exception as e:
        app.logger.error(f"Flyer scan error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        os.unlink(tmp_path)


# --- Frontend ---
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    if path and os.path.exists(os.path.join(FRONTEND_DIR, path)):
        return send_from_directory(FRONTEND_DIR, path)
    return send_from_directory(FRONTEND_DIR, "index.html")


# --- Flyer Scan History ---
@app.route("/api/flyers", methods=["GET"])
def get_flyer_scans():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401

    scans = FlyerScan.query.filter_by(user_id=user_id).order_by(FlyerScan.scanned_at.desc()).all()
    return jsonify([{
        "id": s.id,
        "store": s.store,
        "scanned_at": s.scanned_at.isoformat() if s.scanned_at else None,
        "flyer_date": s.flyer_date,
        "item_count": s.item_count,
        "bulk_deal_count": s.bulk_deal_count,
        "items": json.loads(s.items_json),
        "bulk_deals": json.loads(s.bulk_deals_json),
        "source_url": s.source_url,
    } for s in scans])


@app.route("/api/flyers/<int:scan_id>", methods=["GET"])
def get_flyer_scan(scan_id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401

    s = FlyerScan.query.filter_by(id=scan_id, user_id=user_id).first()
    if not s:
        return jsonify({"error": "Scan not found"}), 404

    return jsonify({
        "id": s.id,
        "store": s.store,
        "scanned_at": s.scanned_at.isoformat() if s.scanned_at else None,
        "flyer_date": s.flyer_date,
        "item_count": s.item_count,
        "bulk_deal_count": s.bulk_deal_count,
        "items": json.loads(s.items_json),
        "bulk_deals": json.loads(s.bulk_deals_json),
        "source_url": s.source_url,
    })


# --- Health Check (Render uses this) ---
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "app": "FlyerMeal"})


# --- Init DB and Run ---
with app.app_context():
    db.create_all()

    # Add new columns to existing tables if they're missing (SQLite doesn't support ALTER TABLE ADD COLUMN IF NOT EXISTS)
    with db.engine.connect() as conn:
        existing = {row[1] for row in conn.execute(db.text("PRAGMA table_info(user_profile)"))}
        for col, definition in [
            ("postal_code", "VARCHAR(10)"),
            ("province",    "VARCHAR(50)"),
        ]:
            if col not in existing:
                conn.execute(db.text(f"ALTER TABLE user_profile ADD COLUMN {col} {definition}"))

        flyer_cols = {row[1] for row in conn.execute(db.text("PRAGMA table_info(flyer_scan)"))}
        if "source_url" not in flyer_cols:
            conn.execute(db.text("ALTER TABLE flyer_scan ADD COLUMN source_url TEXT"))

        conn.commit()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
