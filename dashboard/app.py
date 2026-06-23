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


def create_app(bridge):
    app = Flask(__name__)
    app.config["bridge"] = bridge

    # check_id -> queue.Queue(), one per in-flight /api/check call. Lets the
    # frontend open an SSE stream scoped to *its own* check instead of a
    # global broadcast — important since the slot-checker (unlike /booking)
    # is meant to be multi-visitor, so two people checking at once must
    # never see each other's progress events.
    check_queues = {}
    check_queues_lock = threading.Lock()

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
        status_queue = queue.Queue()
        with check_queues_lock:
            check_queues[check_id] = status_queue

        def on_status(event):
            status_queue.put({"type": "status", "event": event})

        def worker():
            try:
                result = check_country_slot(country, on_status=on_status)
            except Exception:
                result = {"status": "error", "message": "Something went wrong while checking. Please try again."}
            status_queue.put({"type": "result", "data": result})

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"check_id": check_id})

    @app.route("/api/check-stream/<check_id>")
    def check_stream(check_id):
        status_queue = check_queues.get(check_id)
        if status_queue is None:
            return Response(status=404)

        def stream():
            try:
                while True:
                    event = status_queue.get()
                    yield f"data: {json.dumps(event)}\n\n"
                    if event["type"] == "result":
                        break
            finally:
                with check_queues_lock:
                    check_queues.pop(check_id, None)

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
