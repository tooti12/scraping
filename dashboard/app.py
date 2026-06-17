# dashboard/app.py
"""Local-only Flask dashboard. Talks to the booking automation exclusively
through a FrontendBridge instance - never touches Selenium/VFS directly.

Binds to 127.0.0.1 only. There is no authentication layer, which is
acceptable solely because this is loopback-only and single-user; the
payment step briefly puts real card data into a request body here, so this
must never be exposed beyond localhost without adding auth first.
"""
import json
import logging

from flask import Flask, Response, jsonify, render_template, request


def create_app(bridge):
    app = Flask(__name__)
    app.config["bridge"] = bridge

    @app.route("/")
    def index():
        return render_template("index.html")

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
