"""
FlyerMeal - Backend API
Flask application for grocery flyer scanning and meal planning.

Run locally:  python app.py
Deploy to:    Render.com (see docs/deployment.md)
"""

from flask import Flask, request, jsonify, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import timedelta

# --- App Setup ---
app = Flask(__name__)
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
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


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
    # TODO Week 5: call flyer scanner agent
    return jsonify({"message": "Flyer scanning coming in Week 5"}), 501


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
