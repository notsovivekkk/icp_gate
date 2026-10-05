#!/usr/bin/env python3
"""Bouncer: an ICP gate for prospect lists.

  python bouncer.py setup <client> --site <domain> [--icp <path>]
  python bouncer.py run <client> <list.csv> [--limit N] [--domain-col X] [--name-col Y]
"""

import argparse
import re
import warnings

from dotenv import load_dotenv

# macOS's built-in Python uses LibreSSL, which makes urllib3 print a harmless warning.
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")


def client_slug(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise argparse.ArgumentTypeError("use letters, numbers, - or _ (e.g. acme or acme-corp)")
    return value.lower()


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(prog="bouncer", description="Check which companies on a list actually fit a client's ICP.")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Create a client's ICP gate (run once per client).")
    setup.add_argument("client", type=client_slug, help="Short name for the client, e.g. acme")
    setup.add_argument("--site", required=True, help="The client's website, e.g. acme.com")
    setup.add_argument("--icp", help="Optional ICP document (.md, .txt or .pdf)")

    run = sub.add_parser("run", help="Gate a CSV of companies against a client's ICP.")
    run.add_argument("client", type=client_slug)
    run.add_argument("csv", help="Path to the list, e.g. examples/sample_companies.csv")
    run.add_argument("--limit", type=int, help="Only process the first N rows (good for a test run)")
    run.add_argument("--domain-col", help="Column with the website/domain (auto-detected if omitted)")
    run.add_argument("--name-col", help="Column with the company name (auto-detected if omitted)")
    run.add_argument("--workers", type=int, default=8, help="Parallel Jev requests (default 8)")

    args = parser.parse_args()
    if args.command == "setup":
        from setup_client import run_setup
        run_setup(args.client, args.site, args.icp)
    else:
        from run_list import run_list
        run_list(args.client, args.csv, args.limit, args.domain_col, args.name_col, args.workers)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Finished answers are cached; run the same command again to resume.")
