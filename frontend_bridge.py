# frontend_bridge.py
"""Thread-safe bridge between the booking automation (main thread) and the
Flask dashboard (background thread, see dashboard/app.py).

- push_status() is fire-and-forget: a status/log event broadcast to every
  connected dashboard tab.
- ask() blocks the calling thread until a human answers a prompt from the
  dashboard (date, time, review confirmation, card details).

Both sides only ever touch this object's queues, never each other's
internals, so plain queue.Queue gives all the locking we need.
"""
import queue
import threading
import time
import uuid


class FrontendBridge:
    def __init__(self):
        self._lock = threading.Lock()
        self._status_subscribers = []  # one queue.Queue per connected SSE client
        self._pending_prompt = None
        self._answer_queues = {}  # prompt_id -> queue.Queue(maxsize=1)

    # --- automation / booking_flow side ---

    def push_status(self, event_type, payload=None):
        self._broadcast(
            {"type": "status", "event": event_type, "payload": payload or {}, "ts": time.time()}
        )

    def ask(self, prompt_type, payload, timeout=None):
        """Block until the dashboard answers this prompt. Returns whatever
        JSON value the dashboard posted back."""
        prompt_id = str(uuid.uuid4())
        answer_queue = queue.Queue(maxsize=1)
        with self._lock:
            self._pending_prompt = {
                "prompt_id": prompt_id,
                "prompt_type": prompt_type,
                "payload": payload,
            }
            self._answer_queues[prompt_id] = answer_queue
        self._broadcast(
            {"type": "prompt", "prompt_id": prompt_id, "prompt_type": prompt_type, "payload": payload}
        )
        try:
            return answer_queue.get(timeout=timeout)
        finally:
            with self._lock:
                self._answer_queues.pop(prompt_id, None)
                if self._pending_prompt and self._pending_prompt["prompt_id"] == prompt_id:
                    self._pending_prompt = None

    # --- Flask app side ---

    def submit_answer(self, prompt_id, value):
        with self._lock:
            answer_queue = self._answer_queues.get(prompt_id)
        if not answer_queue:
            return False
        answer_queue.put(value)
        self._broadcast({"type": "prompt_resolved", "prompt_id": prompt_id})
        return True

    def current_prompt(self):
        with self._lock:
            return self._pending_prompt

    def subscribe(self):
        """New queue.Queue receiving every future broadcast; used by the SSE
        endpoint, one per connected browser tab. Replays the currently
        pending prompt (if any) so a late-joining tab doesn't miss it."""
        new_queue = queue.Queue()
        with self._lock:
            self._status_subscribers.append(new_queue)
        prompt = self.current_prompt()
        if prompt:
            new_queue.put({"type": "prompt", **prompt})
        return new_queue

    def unsubscribe(self, subscriber_queue):
        with self._lock:
            if subscriber_queue in self._status_subscribers:
                self._status_subscribers.remove(subscriber_queue)

    def _broadcast(self, event):
        with self._lock:
            subscribers = list(self._status_subscribers)
        for subscriber_queue in subscribers:
            subscriber_queue.put(event)
