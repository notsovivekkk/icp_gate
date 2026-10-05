"""`bouncer.py run`: check every company on a CSV against a client's approved ICP gate."""

import csv
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jev
import web

DECISION_CACHE = Path("cache/decisions")
OUTPUT_DIR = Path("output")
CONFIRM_ABOVE_USD = 1.00

DOMAIN_NAMES = ["website", "domain", "url", "companywebsite", "companydomain", "websiteurl",
                "companyurl", "homepage", "site", "web"]
COMPANY_NAMES = ["company", "companyname", "name", "organization", "organisation",
                 "account", "accountname", "business"]
DESCRIPTION_WORDS = ["description", "about", "summary", "overview", "bio"]


def run_list(client, csv_path, limit=None, domain_col=None, name_col=None, workers=8):
    gate = load_gate(client)
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is missing. Add OPENROUTER_API_KEY=... to your .env file.")

    headers, rows = read_csv(csv_path)
    if limit:
        rows = rows[:limit]
    domain_col = domain_col or detect_column(headers, DOMAIN_NAMES, ["website", "domain", "url"])
    name_col = name_col or detect_column(headers, COMPANY_NAMES, ["company", "name"])
    desc_col = detect_column(headers, [], DESCRIPTION_WORDS)
    for flag, col in (("--domain-col", domain_col), ("--name-col", name_col)):
        if col and col not in headers:
            raise SystemExit("%s '%s' is not a column. Columns: %s" % (flag, col, ", ".join(headers)))
    if not domain_col:
        raise SystemExit("Couldn't find a website/domain column. Pass --domain-col. Columns: %s"
                         % ", ".join(headers))
    print("%d companies · domain column: '%s' · name column: '%s'%s" % (
        len(rows), domain_col, name_col or "none",
        " · fallback description column: '%s'" % desc_col if desc_col else ""))

    # 1. Scrape each unique domain once (free, cached on disk).
    domains = sorted({web.normalize_domain(r.get(domain_col)) for r in rows} - {""})
    sites = parallel_map(web.scrape_company, domains, workers=16, label="Reading websites")

    # 2. Build the Jev state for every row. Rows with no usable text are Unscanned.
    jobs = {}  # cache key -> state, so duplicate companies cost one call
    row_keys = []
    for row in rows:
        domain = web.normalize_domain(row.get(domain_col))
        state = build_state(gate, row, domain, sites.get(domain), name_col, desc_col)
        key = decision_key(gate, state) if state else None
        if key:
            jobs[key] = state
        row_keys.append(key)

    cache_dir = DECISION_CACHE / client
    pending = {k: s for k, s in jobs.items() if not (cache_dir / (k + ".json")).exists()}

    # 3. Show the cost before spending anything.
    estimate = sum(jev.estimate_cost(len(json.dumps(s)), gate) for s in pending.values())
    print("Jev calls needed: %d new, %d already cached (free). Estimated cost: $%.5f"
          % (len(pending), len(jobs) - len(pending), estimate))
    if estimate > CONFIRM_ABOVE_USD:
        if input("That's over $%.2f. Continue? [y/N] " % CONFIRM_ABOVE_USD).strip().lower() != "y":
            raise SystemExit("Stopped before calling Jev. Nothing was spent.")

    # 4. Ask Jev, caching each answer the moment it arrives (this is what makes resume work).
    cache_dir.mkdir(parents=True, exist_ok=True)
    spent = [0.0]
    stop = threading.Event()
    lock = threading.Lock()

    def decide(key):
        if stop.is_set():
            return None
        try:
            prob, cost = jev.ask(pending[key], gate, api_key)
        except jev.JevFatalError as err:
            if not stop.is_set():
                print("\n%s" % err)
            stop.set()
            return None
        except jev.JevRetryableError:
            return None  # gave up after retries; left uncached so a re-run tries again
        (cache_dir / (key + ".json")).write_text(json.dumps({"probability": prob, "cost": cost}))
        with lock:
            spent[0] += cost
        return prob

    if pending:
        parallel_map(decide, list(pending), workers=workers, label="Asking Jev")
    if stop.is_set():
        raise SystemExit("Run stopped. Every answer so far is cached; fix the issue and re-run to resume.")

    # 5. Write the original CSV plus two columns, and print the summary.
    thresholds = gate["thresholds"]
    out_headers = [h for h in headers if h not in ("icp_match", "icp_probability")]
    out_headers += ["icp_match", "icp_probability"]
    for row, key in zip(rows, row_keys):
        prob = cached_probability(cache_dir, key)
        row["icp_match"] = verdict(prob, key, thresholds)
        row["icp_probability"] = "" if prob is None else "%.3f" % prob

    out_path = OUTPUT_DIR / ("%s_%s_bouncer.csv" % (Path(csv_path).stem, client))
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print_summary(rows, spent[0], len(pending), out_path, thresholds)


def build_state(gate, row, domain, site, name_col, desc_col):
    """What Jev sees: the client's ICP and the prospect's own words. None = Unscanned."""
    if site and site["ok"]:
        source, text = "company website (homepage + about page)", site["text"]
    elif desc_col and len((row.get(desc_col) or "").strip()) > 20:
        source, text = "description from the prospect list", row[desc_col].strip()[:6000]
    else:
        return None
    return {
        "client_icp": gate["icp_summary"],
        "prospect": {
            "company": (row.get(name_col) or "").strip() if name_col else "",
            "domain": domain,
            "source": source,
            "text": text,
        },
    }


def decision_key(gate, state):
    # A new question, ICP, website text or model means a new answer. Thresholds aren't
    # part of the key, so you can retune them and re-run for free.
    return web.text_hash(gate.get("model", jev.MODEL), json.dumps(gate["question"], sort_keys=True),
                         json.dumps(state, sort_keys=True))


def cached_probability(cache_dir, key):
    path = cache_dir / ((key or "") + ".json")
    if key and path.exists():
        return json.loads(path.read_text())["probability"]
    return None


def verdict(prob, key, thresholds):
    if key is None:
        return "Unscanned"
    if prob is None:
        return "Error"  # Jev kept failing for this row; re-run to retry it
    if prob >= thresholds["yes"]:
        return "Y"
    if prob <= thresholds["no"]:
        return "N"
    return "Review"


def print_summary(rows, spent, calls, out_path, thresholds):
    counts = {}
    for row in rows:
        counts[row["icp_match"]] = counts.get(row["icp_match"], 0) + 1
    probs = [float(r["icp_probability"]) for r in rows if r["icp_probability"]]

    print("\nDone. %d companies checked." % len(rows))
    labels = [("Y", "Y        (≥ %s)" % thresholds["yes"]),
              ("Review", "Review   (in between)"),
              ("N", "N        (≤ %s)" % thresholds["no"]),
              ("Unscanned", "Unscanned (no website text)"),
              ("Error", "Error    (Jev failed, re-run to retry)")]
    for key, label in labels:
        if counts.get(key) or key in ("Y", "Review", "N"):
            print("  %-38s %d" % (label, counts.get(key, 0)))
    if probs:
        print("  Average probability: %.2f" % (sum(probs) / len(probs)))
    print("  Jev cost this run: $%.5f (%d new calls; cached rows are free)" % (spent, calls))
    print("  Saved: %s" % out_path)


def load_gate(client):
    path = Path("clients") / client / "gate.json"
    if not path.exists():
        raise SystemExit("No gate for '%s'. Run first: python bouncer.py setup %s --site <domain>"
                         % (client, client))
    return json.loads(path.read_text())


def read_csv(path):
    if not Path(path).exists():
        raise SystemExit("CSV not found: %s" % path)
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def detect_column(headers, exact, contains):
    """Exact match on a normalized header first, then 'header contains word'.

    Exports often namespace columns ('basic_info.name'), so the exact pass also
    checks the part after the last dot. That way 'basic_info.name' wins over
    'basic_info.company_type' instead of losing to the looser 'contains' pass.
    """
    norm = {h: re.sub(r"[^a-z0-9]", "", h.lower()) for h in headers}
    tail = {h: re.sub(r"[^a-z0-9]", "", h.lower().split(".")[-1]) for h in headers}
    for want in exact:
        for h in headers:
            if want in (norm[h], tail[h]):
                return h
    for want in contains:
        for h in headers:
            if want in norm[h] and "linkedin" not in norm[h]:
                return h
    return None


def parallel_map(fn, items, workers, label):
    """Run fn over items in a thread pool with a one-line progress counter."""
    results, done = {}, [0]
    lock = threading.Lock()

    def task(item):
        result = fn(item)
        with lock:
            results[item] = result
            done[0] += 1
            sys.stdout.write("\r  %s: %d/%d" % (label, done[0], len(items)))
            sys.stdout.flush()

    if items:
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            list(pool.map(task, items))
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)  # drop queued work on Ctrl+C
            raise
        pool.shutdown()
        print()
    return results
