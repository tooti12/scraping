from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session
from werkzeug.security import check_password_hash
from models import get_admin_by_username

auth_bp = Blueprint("auth", __name__)


def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


@auth_bp.route("/admin/login", methods=["GET", "POST"])
def login():
    if "admin_id" in session:
        return redirect(url_for("admin_panel.dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_admin_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            session.permanent = True
            session["admin_id"] = user["id"]
            session["admin_username"] = user["username"]
            return redirect(url_for("admin_panel.dashboard"))
        error = "Invalid username or password."
    return render_template("admin/login.html", error=error)


@auth_bp.route("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
