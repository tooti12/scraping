# dashboard/app.py
"""Flask app serving three independent surfaces:

- "/"             — marketing/showcase homepage (home.html). Just lists the
                    countries the bot covers; no live or cached check data.
- "/availability" — the comprehensive results page (availability.html).
                    Shows the latest London-centre/Tourism-category result
                    for every country, kept fresh by slot_status_cache.py's
                    background loop — but that loop doesn't run on its own;
                    it only starts once a visitor clicks "Start Bot" here
                    (/api/start-bot). Before that, and on every other
                    request once it's running, visitors only ever read the
                    cache — no request triggers a live Selenium run, so
                    there's no risk of two visitors' checks ever running
                    Chrome at the same time.
- "/booking"      — the existing internal booking console (booking.html).
                    Talks to the booking automation exclusively through a
                    FrontendBridge instance — never touches Selenium/VFS
                    directly. This is the human-in-the-loop UI main.py's
                    BrowserClient drives via `bridge`.

Binds to 127.0.0.1 only for now. There is no authentication layer, which is
acceptable solely because this is loopback-only and single-user; the
/booking payment step briefly puts real card data into a request body here.
Before this is ever deployed publicly, "/booking", "/api/answer", and
"/events" must be locked down (auth, or removed from the public process
entirely) — only "/", "/availability", and "/api/status" are meant to be
internet-facing.
"""
import json
import logging

from flask import Flask, Response, jsonify, render_template, request

import slot_status_cache
from config import COUNTRIES


def create_app(bridge):
    app = Flask(__name__)
    app.config["bridge"] = bridge

    @app.route("/")
    def home():
        return render_template("home.html", countries=COUNTRIES)

    @app.route("/availability")
    def availability():
        return render_template(
            "availability.html",
            countries=COUNTRIES,
            status=slot_status_cache.get_all_status(),
            bot_running=slot_status_cache.is_running(),
        )

    @app.route("/api/status")
    def api_status():
        return jsonify(slot_status_cache.get_all_status())

    @app.route("/api/bot-status")
    def api_bot_status():
        return jsonify({"running": slot_status_cache.is_running()})

    @app.route("/api/start-bot", methods=["POST"])
    def api_start_bot():
        slot_status_cache.start()
        return jsonify({"running": True})

    @app.route("/booking")
    def booking():
        return render_template("booking.html")

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
