"""Minimal client for the OpenRouter Decisions API (Jev).

Docs: https://openrouter.ai/docs/guides/community/jev
One Noul (yes/no) question per call; the answer is the probability of "yes".
"""

import os
import random
import threading
import time

import requests

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
# USD per million input tokens (output tokens are free). Check the model page:
# https://openrouter.ai/typesafe/jev-1.13 and override with JEV_PRICE_PER_M if it changes.
PRICE_PER_M_INPUT = float(os.getenv("JEV_PRICE_PER_M", "0.042"))
QUESTION_ID = "icp_fit"


class JevFatalError(Exception):
    """Out of credits, bad key, malformed request: stop the run, don't retry."""


class JevRetryableError(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


# When one worker hits a rate limit, every worker pauses until this time.
_pause_until = 0.0
_pause_lock = threading.Lock()


def build_question(gate):
    q = gate["question"]
    return {
        "type": "noul",
        "instructions": q["instructions"],
        "criteria": {"true": q["true_when"], "false": q["false_when"]},
    }


def ask(state, gate, api_key):
    """Send one Decisions request. Returns (probability_of_yes, cost_usd)."""
    body = {
        "model": gate.get("model", MODEL),
        "state": state,
        "questions": {QUESTION_ID: build_question(gate)},
    }
    for attempt in range(6):
        _wait_for_pause()
        try:
            return _post(body, api_key)
        except JevRetryableError as err:
            if attempt == 5:
                raise
            delay = err.retry_after or min(30, 2 ** attempt) + random.random()
            _pause_all(delay)


def _post(body, api_key):
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    try:
        resp = requests.post(URL, json=body, headers=headers, timeout=60)
    except requests.RequestException as err:
        raise JevRetryableError("network error: %s" % type(err).__name__)

    if resp.status_code != 200:
        text = resp.text[:300]
        retry_after = _retry_after(resp)
        in_flight_budget = resp.status_code == 402 and "openrouter_in_flight_budget" in text
        if resp.status_code == 429 or resp.status_code >= 500 or in_flight_budget:
            raise JevRetryableError("HTTP %s" % resp.status_code, retry_after)
        raise JevFatalError("Jev returned HTTP %s: %s" % (resp.status_code, text))

    data = resp.json()
    answer = data.get("answers", {}).get(QUESTION_ID, {})
    prob = answer.get("noul")
    if not isinstance(prob, (int, float)) or not 0 <= prob <= 1:
        raise JevRetryableError("unexpected response shape")
    cost = (data.get("usage") or {}).get("cost") or 0.0
    return float(prob), float(cost)


def estimate_cost(state_chars, gate):
    """Rough pre-run estimate: ~4 characters per token, plus question overhead."""
    q = gate["question"]
    overhead_chars = len(q["instructions"]) + len(q["true_when"]) + len(q["false_when"]) + 600
    tokens = (state_chars + overhead_chars) / 4
    return tokens * PRICE_PER_M_INPUT / 1_000_000


def _retry_after(resp):
    try:
        return float(resp.headers.get("Retry-After", ""))
    except ValueError:
        return None


def _pause_all(seconds):
    global _pause_until
    with _pause_lock:
        _pause_until = max(_pause_until, time.time() + seconds)


def _wait_for_pause():
    delay = _pause_until - time.time()
    if delay > 0:
        time.sleep(delay)
