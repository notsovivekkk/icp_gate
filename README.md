# Bouncer

**An ICP gate for prospect lists.** Give Bouncer a client and a CSV of companies. It reads each company's own website and decides whether that company actually matches the client's ideal customer profile, then adds the answer as two new columns.

Works for any client, any industry, any list. Nothing client-specific is hardcoded.

## Why

Database filters tell you who *could* fit: industry = SaaS, 50–500 employees, US. That's how a list gets built, and it's why lists are full of near misses: the agency that *serves* SaaS companies, the 40-person startup tagged as 200, the competitor, the consumer app filed under "software".

Bouncer checks who *actually* fits. It reads what each company says about itself on its own website and asks one precise yes/no question: *is this a direct fit for this client's ideal customer?* You get a probability back, not a guess buried in prose.

## How it works

1. **`setup`** (once per client). Claude reads the client's website (product, case studies, customer logos) and your ICP doc if you have one. It drafts a short ICP summary and a single yes/no question with exact "true when" and "false when" conditions, exclusions included. You approve or edit it. It's saved to `clients/<client>/gate.json`.
2. **`run`** (every list). For each company, Bouncer fetches the homepage and about page and strips them to plain text (up to about 6,000 characters). It sends that text plus the client's ICP to [Jev](https://openrouter.ai/docs/guides/community/jev) (`typesafe/jev-1.13` on OpenRouter), a decision model that returns the probability that the answer is yes. One call per company.

Claude is used only for the one-time setup. Every per-company decision goes to Jev, which costs a fraction of a cent.

## Install (2 minutes)

You need Python 3.9 or newer.

```bash
git clone https://github.com/notsovivekkk/icp_gate.git
cd icp_gate
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create a file named `.env` in the project folder with your two keys:

```bash
OPENROUTER_API_KEY=sk-or-...
ANTHROPIC_API_KEY=sk-ant-...
```

| Key | Used for | Get it |
|---|---|---|
| `OPENROUTER_API_KEY` | Jev decisions in `run` | [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys) |
| `ANTHROPIC_API_KEY` | One-time ICP drafting in `setup` | [platform.claude.com](https://platform.claude.com/) |

`.env` is in `.gitignore`. Bouncer never prints your keys.

## Command 1: set up a client

```bash
python bouncer.py setup acme --site acme.com
python bouncer.py setup acme --site acme.com --icp docs/acme_icp.md   # if you have an ICP doc (.md, .txt, .pdf)
```

Bouncer shows you the draft:

```
────────────────────────────────────────────────────────────────
ICP SUMMARY  (Acme)
────────────────────────────────────────────────────────────────
B2B software companies selling to finance teams, 50-1,000 employees ...
Not a fit: accounting firms, consulting agencies, consumer apps, ...

────────────────────────────────────────────────────────────────
JEV QUESTION
────────────────────────────────────────────────────────────────
Is this prospect a direct fit for Acme's ideal customer?

  TRUE when:  The prospect sells its own software product to businesses ...
  FALSE when: The prospect is a services firm, agency, reseller, ...

  Thresholds: Y ≥ 0.8 · N ≤ 0.3 · Review in between

[a]pprove and save · [e]dit in your editor · [r]egenerate with feedback · [q]uit:
```

- **a** saves it to `clients/acme/gate.json`.
- **e** opens it as JSON in `$EDITOR` so you can change any wording or the thresholds.
- **r** asks for feedback, such as "exclude agencies" or "healthcare only", and Claude redrafts.

You can edit `clients/acme/gate.json` by hand later. The `thresholds` block sets where Y and N start for this client.

## Command 2: gate a list

```bash
python bouncer.py run acme leads.csv --limit 20        # try 20 rows first
python bouncer.py run acme leads.csv                   # the whole list
python bouncer.py run acme leads.csv --domain-col "Company Website" --name-col "Account"
```

- **Columns are auto-detected.** Bouncer looks for headers like `website`, `domain` and `url`, and `company` and `name`. Use the flags if it picks the wrong one.
- **No website?** If a site won't load, Bouncer uses a `description` column when the list has one. Otherwise the row is marked `Unscanned`.
- **Cost check first.** Bouncer always prints the estimated cost before calling Jev, and asks you to confirm when it's over $1.
- **Re-runs are free, and runs resume.** Websites and Jev answers are cached in `cache/`. If a run is interrupted, run the same command again and it picks up where it left off. Changing only the thresholds costs nothing to re-run.
- **Fast.** Sites are fetched in parallel and Jev runs 8 requests at a time (`--workers` to change), backing off automatically on rate limits.

Try it on the bundled sample:

```bash
python bouncer.py run acme examples/sample_companies.csv
```

The sample uses made-up `.example` domains that don't resolve, so it also shows the fallbacks: four rows are judged from their description and one becomes `Unscanned`.

## Output

Results are saved to `output/<list>_<client>_bouncer.csv`. Every original column is kept, and two are added:

| Column | Values |
|---|---|
| `icp_match` | `Y` (probability ≥ 0.8), `N` (≤ 0.3), `Review` (in between), `Unscanned` (no text to judge). `Error` only if Jev kept failing for that row; re-run to retry it. |
| `icp_probability` | 0 to 1: Jev's probability that the company is a direct fit |

```csv
Company Name,Website,Industry,Employees,Description,icp_match,icp_probability
Northwind Analytics,northwind-analytics.example,Software,120,Dashboards and ...,Y,0.912
Bluefin Logistics,bluefin-logistics.example,Transportation,450,Freight brokerage ...,N,0.041
Cedar & Pine Dental,cedarpinedental.example,Healthcare,15,,Unscanned,
Quantum Ledger,quantumledger.example,Financial Services,60,Accounting automation ...,Review,0.534
Maple Street Bakery,maplestreetbakery.example,Food & Beverage,8,Family-owned ...,N,0.012
```

(These numbers are illustrative.) The terminal summary:

```
Done. 5 companies checked.
  Y        (≥ 0.8)                       1
  Review   (in between)                  1
  N        (≤ 0.3)                       2
  Unscanned (no website text)            1
  Average probability: 0.37
  Jev cost this run: $0.00021 (4 new calls; cached rows are free)
  Saved: output/sample_companies_acme_bouncer.csv
```

Send the `Review` rows to a person. Start outreach with the `Y` rows.

## Costs

| Step | Model | Typical cost |
|---|---|---|
| `setup` (once per client) | Claude Opus 5.5 | about $0.10–$0.30 |
| `run` (per company) | Jev `typesafe/jev-1.13` | about $0.00008 (≈ 2,000 input tokens at $0.042 per million; output is free) |
| `run` (1,000 companies) | | about **$0.08** |
| Re-runs | | $0, everything is cached |

The per-company figure comes from Jev's [listed price](https://openrouter.ai/typesafe/jev-1.13). The run summary reports the actual amount from each response's `usage.cost`. If the price changes, set `JEV_PRICE_PER_M` in `.env` so the pre-run estimate stays accurate.

## Files

```
bouncer.py        CLI entry point (setup / run)
setup_client.py   Client research and ICP drafting with Claude, plus the approval loop
run_list.py       CSV in, Jev decisions, CSV out, caching, resume, summary
jev.py            Small OpenRouter Decisions API client (retries, backoff)
web.py            Website fetching and HTML-to-text, cached
examples/         A 5-row made-up sample list
clients/  cache/  output/   created at runtime, gitignored
```

## Tips

- Run `--limit 20` on a new client first and skim the `Review` and `N` rows. If the question is too strict or too loose, edit `true_when` and `false_when` in `gate.json`. The cache is keyed on the question, so the next run re-asks only what changed.
- To retry websites that failed, delete `cache/sites/<domain>.json` (or the whole `cache/sites/` folder).
- Bouncer reads public homepages and about pages only, at a modest pace. Respect sites' terms when running large lists.

## License

MIT
