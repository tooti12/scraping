import json
from datetime import datetime, timezone
from flask import Blueprint, render_template, redirect, url_for, session
import slot_status_cache
from dashboard.auth import require_admin
from models import get_recent_cycles
from config import COUNTRIES

admin_panel_bp = Blueprint("admin_panel", __name__)


def _fmt_dt(iso_str: str | None) -> str | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d %b %Y, %H:%M UTC")
    except Exception:
        return iso_str


def _fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h {m}m"
    return f"{m}m {s}s" if m else f"{s}s"


def _summarize(results: dict) -> str:
    if not results:
        return "No data"
    total = len(results)
    available = sum(1 for v in results.values() if v.get("status") == "slots_available")
    errors = sum(1 for v in results.values() if v.get("status") == "error")
    parts = [f"{total} countries"]
    if available:
        parts.append(f"{available} slot{'s' if available != 1 else ''} found")
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    return " · ".join(parts)


@admin_panel_bp.route("/admin")
@admin_panel_bp.route("/admin/")
@require_admin
def dashboard():
    cycles_raw = get_recent_cycles(100)
    cycles = []
    for c in cycles_raw:
        try:
            results = json.loads(c["results_json"]) if c["results_json"] else {}
        except Exception:
            results = {}
        cycles.append({
            **c,
            "results": results,
            "summary": _summarize(results),
            "duration_fmt": _fmt_duration(c.get("duration_seconds")),
            "started_at_fmt": _fmt_dt(c.get("started_at")),
            "ended_at_fmt": _fmt_dt(c.get("ended_at")),
        })

    cycle_state = slot_status_cache.get_cycle_state()
    return render_template(
        "admin/dashboard.html",
        running=slot_status_cache.is_running(),
        checking=cycle_state.get("checking", False),
        next_check_in=cycle_state.get("next_check_in_seconds"),
        status=slot_status_cache.get_all_status(),
        cycles=cycles,
        countries=COUNTRIES,
        admin_username=session.get("admin_username", "Admin"),
        total_cycles=len(cycles),
    )


@admin_panel_bp.route("/admin/start-bot", methods=["POST"])
@require_admin
def start_bot():
    slot_status_cache.start()
    return redirect(url_for("admin_panel.dashboard"))


@admin_panel_bp.route("/admin/stop-bot", methods=["POST"])
@require_admin
def stop_bot():
    slot_status_cache.stop()
    return redirect(url_for("admin_panel.dashboard"))
