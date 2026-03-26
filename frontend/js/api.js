/**
 * FlyerMeal — API Helper
 * All calls to your Flask backend go through here.
 * Change API_BASE to your Render URL when deploying.
 */

// In development: Flask runs on port 5000
// In production: change to your Render URL e.g. "https://flyermeal-api.onrender.com"
const API_BASE = window.location.hostname === "localhost"
  ? "http://localhost:5000/api"
  : "https://your-app-name.onrender.com/api";  // ← update this when you deploy to Render

const api = {
  async post(path, body) {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(body),
    });
    return res.json();
  },

  async get(path) {
    const res = await fetch(`${API_BASE}${path}`, {
      credentials: "include",
    });
    return res.json();
  },

  async put(path, body) {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(body),
    });
    return res.json();
  },
};
