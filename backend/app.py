"""
FlyerMeal - Backend API
Flask application for grocery flyer scanning and meal planning.

Run locally:  python app.py
Deploy to:    Render.com (see docs/deployment.md)
"""

from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
import re
import base64
import json
import tempfile
from io import BytesIO
from datetime import timedelta
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
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


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

    db.session.commit()
    return jsonify({"message": "Profile saved"})


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


# --- Meal Plan Routes (stubs — filled in Week 5/6) ---
@app.route("/api/meal-plan", methods=["GET"])
def get_meal_plan():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401
    # TODO Week 6: call meal planner agent and return plan
    return jsonify({"message": "Meal plan generation coming in Week 6"}), 501


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
    })


# --- Health Check (Render uses this) ---
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "app": "FlyerMeal"})


# --- Init DB and Run ---
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
