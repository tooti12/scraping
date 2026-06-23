# dashboard/app.py
"""Flask app serving two independent surfaces:

- "/"        — public slot-checker homepage (home.html). Country cards
               trigger a one-off, booking-free VFS slot check via
               slot_check_service.py. No Selenium/browser state is shared
               with the booking pipeline below.
- "/booking" — the existing internal booking console (booking.html). Talks
               to the booking automation exclusively through a
               FrontendBridge instance — never touches Selenium/VFS
               directly. This is the human-in-the-loop UI main.py's
               BrowserClient drives via `bridge`.

Binds to 127.0.0.1 only for now. There is no authentication layer, which is
acceptable solely because this is loopback-only and single-user; the
/booking payment step briefly puts real card data into a request body here.
Before this is ever deployed publicly, "/booking" and "/api/answer" must be
locked down (auth, or removed from the public process entirely) — only the
"/" slot-checker surface is meant to be internet-facing.
"""
import json
import logging
import queue
import threading
import uuid

from flask import Flask, Response, jsonify, render_template, request

from config import COUNTRIES
from slot_check_service import check_country_slot

_COUNTRY_CODES = {c["code"] for c in COUNTRIES}

# Hard ceiling on a single check. A normal run (login + OTP wait + reading
# the form) takes a few minutes; this is a generous multiple of that. It
# exists purely as a watchdog — if something inside the automation blocks
# forever with no timeout of its own (an unsolved captcha modal, a wait
# that never resolves), this is what stops the frontend from being stuck on
# "Checking..." forever with no way to know the bot is actually wedged.
CHECK_TIMEOUT_SECONDS = 240


def create_app(bridge):
    app = Flask(__name__)
    app.config["bridge"] = bridge

    # check_id -> {"queue", "browser", "cancelled"}, one per in-flight
    # /api/check call. Lets the frontend open an SSE stream scoped to *its
    # own* check instead of a global broadcast — important since the
    # slot-checker (unlike /booking) is meant to be multi-visitor, so two
    # people checking at once must never see each other's progress events.
    # The "browser" handle is what makes /api/check-cancel able to actually
    # stop the bot mid-check instead of just hiding the frontend overlay.
    check_sessions = {}
    check_sessions_lock = threading.Lock()

    @app.route("/")
    def home():
        return render_template("home.html", countries=COUNTRIES)

    @app.route("/booking")
    def booking():
        return render_template("booking.html")

    @app.route("/api/check/<country>", methods=["POST"])
    def api_check(country):
        if country not in _COUNTRY_CODES:
            return jsonify({"status": "error", "message": "Unknown country."}), 404

        check_id = uuid.uuid4().hex
        session = {"queue": queue.Queue(), "browser": None, "cancelled": False}
        with check_sessions_lock:
            check_sessions[check_id] = session

        def on_status(event):
            session["queue"].put({"type": "status", "event": event})

        def on_browser(browser):
            session["browser"] = browser

        def worker():
            done = threading.Event()
            holder = {}

            def run_check():
                try:
                    holder["result"] = check_country_slot(country, on_status=on_status, on_browser=on_browser)
                except Exception:
                    holder["result"] = {"status": "error", "message": "Something went wrong while checking. Please try again."}
                done.set()

            # Run the actual check in its own thread so this one can give up
            # waiting after CHECK_TIMEOUT_SECONDS instead of blocking forever
            # on it — Python can't force-kill a thread, but force-quitting
            # the browser unblocks whatever Selenium call is stuck, so the
            # abandoned thread exits shortly after anyway.
            threading.Thread(target=run_check, daemon=True).start()
            finished = done.wait(timeout=CHECK_TIMEOUT_SECONDS)

            if not finished:
                browser = session.get("browser")
                if browser is not None:
                    try:
                        browser.sb.driver.quit()
                    except Exception:
                        pass
                result = {
                    "status": "error",
                    "message": "This check is taking unusually long (possibly a captcha that couldn't be solved) and was stopped. Please try again.",
                }
            elif session["cancelled"]:
                result = {"status": "cancelled", "message": "Check cancelled."}
            else:
                result = holder.get("result", {"status": "error", "message": "Something went wrong while checking. Please try again."})

            session["queue"].put({"type": "result", "data": result})

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"check_id": check_id})

    @app.route("/api/check-cancel/<check_id>", methods=["POST"])
    def api_check_cancel(check_id):
        session = check_sessions.get(check_id)
        if session is None:
            return jsonify({"ok": False}), 404

        session["cancelled"] = True
        browser = session.get("browser")
        if browser is not None:
            # Force-quitting the Chrome session is what actually stops the
            # bot — without this, "cancel" would only hide the frontend
            # overlay while the check kept running server-side.
            try:
                browser.sb.driver.quit()
            except Exception:
                pass
        return jsonify({"ok": True})

    @app.route("/api/check-stream/<check_id>")
    def check_stream(check_id):
        session = check_sessions.get(check_id)
        if session is None:
            return Response(status=404)
        status_queue = session["queue"]

        def stream():
            try:
                while True:
                    event = status_queue.get()
                    yield f"data: {json.dumps(event)}\n\n"
                    if event["type"] == "result":
                        break
            finally:
                with check_sessions_lock:
                    check_sessions.pop(check_id, None)

        return Response(stream(), mimetype="text/event-stream")

    @app.route("/events")
    def events():
        subscriber_queue = bridge.subscribe()

        def stream():
            try:
                while True:
                    event = subscriber_queue.get()
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                bridge.unsubscribe(subscriber_queue)

        return Response(stream(), mimetype="text/event-stream")

    @app.route("/api/answer", methods=["POST"])
    def answer():
        data = request.get_json(force=True) or {}
        prompt_id = data.get("prompt_id")
        value = data.get("value")
        ok = bridge.submit_answer(prompt_id, value)
        return jsonify({"ok": ok})

    return app


def run_dashboard(bridge, host="127.0.0.1", port=5050):
    app = create_app(bridge)
    # Single-user local tool: silence werkzeug's per-request access log so
    # the /api/answer body (which carries card details during payment)
    # never ends up in a log stream.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
