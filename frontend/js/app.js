/**
 * FlyerMeal — Main App Logic
 * Handles login, register, logout, and screen switching.
 */

// Check if user is already logged in when page loads
window.addEventListener("DOMContentLoaded", async () => {
  const data = await api.get("/auth/me");
  if (data.id) {
    showDashboard(data);
  } else {
    showAuthScreen();
  }
});

function showAuthScreen() {
  document.getElementById("auth-screen").classList.remove("hidden");
  document.getElementById("dashboard-screen").classList.add("hidden");
}

function showDashboard(user) {
  document.getElementById("auth-screen").classList.add("hidden");
  document.getElementById("dashboard-screen").classList.remove("hidden");
  document.getElementById("user-name").textContent = user.name || user.email.split("@")[0];
}

function showTab(tab) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
  document.getElementById(`tab-${tab}`).classList.add("active");
  event.target.classList.add("active");
}

async function login() {
  const email = document.getElementById("login-email").value.trim();
  const password = document.getElementById("login-password").value;
  const errorEl = document.getElementById("login-error");

  if (!email || !password) {
    showError(errorEl, "Please enter your email and password.");
    return;
  }

  const data = await api.post("/auth/login", { email, password });

  if (data.error) {
    showError(errorEl, data.error);
  } else {
    showDashboard(data.user);
  }
}

async function register() {
  const name = document.getElementById("reg-name").value.trim();
  const email = document.getElementById("reg-email").value.trim();
  const password = document.getElementById("reg-password").value;
  const errorEl = document.getElementById("reg-error");

  if (!email || !password) {
    showError(errorEl, "Please fill in all fields.");
    return;
  }

  if (password.length < 8) {
    showError(errorEl, "Password must be at least 8 characters.");
    return;
  }

  const data = await api.post("/auth/register", { name, email, password });

  if (data.error) {
    showError(errorEl, data.error);
  } else {
    showDashboard(data.user);
  }
}

async function logout() {
  await api.post("/auth/logout", {});
  showAuthScreen();
}

function showError(el, message) {
  el.textContent = message;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 5000);
}

// Allow pressing Enter to submit forms
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const activeTab = document.querySelector(".tab-content.active");
    if (!activeTab) return;
    if (activeTab.id === "tab-login") login();
    if (activeTab.id === "tab-register") register();
  }
});
