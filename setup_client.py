"""`bouncer.py setup`: research a client once and save its approved ICP gate.

Claude is used only here. It reads the client's ICP doc (if given) and website,
then drafts an ICP summary and one Yes/No question for Jev. A human approves it.
"""

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import anthropic

import jev
import web

CLAUDE_MODEL = "claude-opus-5-5"

SYSTEM = """You set up an ICP (ideal customer profile) gate for a B2B go-to-market agency.

The gate is used later by a small yes/no decision model. For each prospect company on a list it sees exactly two things:
1. `client_icp`: the ICP summary you write now.
2. `prospect`: the prospect's company name, domain, and up to ~6,000 characters of plain text scraped from the prospect's own homepage and about page (or a short description from the list when the site could not be loaded).

It then answers your question with a probability of "yes". Write for that reader:
- The ICP summary must be concrete and self-contained: who buys (industry, business model, company type, size or stage if it matters, geography if it matters), what problem they have that the client solves, and the signals that show it on a website. End with an explicit "Not a fit:" list of exclusions (for example competitors, wrong segment, too small or too large, consumer-facing, agencies or resellers, students or job seekers) drawn from the ICP doc and the client's site. Keep it under 200 words.
- `true_when` and `false_when` must be judgeable from a prospect's own website text alone. Do not depend on data a website rarely states (exact revenue, headcount, tech stack) unless the ICP truly hinges on it, and if it does, say what visible evidence counts.
- `true_when` describes a direct fit: the prospect clearly is the kind of company in the ICP.
- `false_when` covers every exclusion, plus prospects that only loosely relate (they sell to the ICP, serve it as a vendor, or are adjacent but not the buyer), plus the client's competitors.
- Be precise and short. One or two sentences each. No hedging words like "may" or "possibly".
- Base everything on evidence from the provided material. If the material is thin, keep the ICP narrow rather than inventing segments."""

SCHEMA = {
    "type": "object",
    "properties": {
        "client_name": {"type": "string", "description": "The client's company name as it brands itself."},
        "icp_summary": {"type": "string"},
        "true_when": {"type": "string"},
        "false_when": {"type": "string"},
    },
    "required": ["client_name", "icp_summary", "true_when", "false_when"],
    "additionalProperties": False,
}


def run_setup(client, site, icp_path=None):
    domain = web.normalize_domain(site)
    if not domain:
        raise SystemExit("--site must be a domain like acme.com")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is missing. Add ANTHROPIC_API_KEY=... to your .env file.")

    icp_doc = read_icp_doc(icp_path) if icp_path else None
    print("Researching %s ..." % domain)
    site_text = web.research_site(domain)
    print("Read %d characters from the website.%s" % (
        len(site_text), " Plus your ICP doc." if icp_doc else ""))

    claude = anthropic.Anthropic(api_key=api_key)
    feedback = None
    while True:
        print("Asking Claude to draft the ICP gate ...")
        draft = draft_gate(claude, client, domain, site_text, icp_doc, feedback)
        gate = to_gate(client, domain, draft, icp_from_doc=bool(icp_doc))

        while True:
            show(gate)
            choice = input("\n[a]pprove and save · [e]dit in your editor · [r]egenerate with feedback · [q]uit: ").strip().lower()
            if choice == "a":
                save(client, gate)
                return
            if choice == "e":
                gate = edit_in_editor(gate)
            elif choice == "r":
                feedback = input("What should change? ").strip()
                break
            elif choice == "q":
                print("Nothing saved.")
                return


def draft_gate(claude, client, domain, site_text, icp_doc, feedback):
    parts = ["Client slug: %s\nClient website: %s" % (client, domain)]
    if icp_doc:
        parts.append("<icp_document>\n%s\n</icp_document>\n"
                     "The ICP document is the source of truth. Use the website to fill gaps "
                     "and to learn the client's vocabulary." % icp_doc)
    else:
        parts.append("There is no ICP document. Infer the ICP from the website: what the "
                     "product does, who the case studies and customer logos are, which "
                     "industries and roles the pages speak to.")
    parts.append("<client_website>\n%s\n</client_website>" % site_text)
    if feedback:
        parts.append("The user reviewed a previous draft and asked for this change: %s" % feedback)

    response = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": "\n\n".join(parts)}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        # Server-side fallback: if a safety classifier declines, the API retries
        # on another model inside the same call instead of failing.
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    if response.stop_reason == "refusal":
        raise SystemExit("Claude declined to draft this ICP. Try again with an ICP doc (--icp).")
    text = next(block.text for block in response.content if block.type == "text")
    return json.loads(text)


def to_gate(client, domain, draft, icp_from_doc):
    return {
        "client": client,
        "client_name": draft["client_name"],
        "site": domain,
        "icp_source": "icp_doc+website" if icp_from_doc else "website",
        "icp_summary": draft["icp_summary"],
        "question": {
            "instructions": "Is this prospect a direct fit for %s's ideal customer?" % draft["client_name"],
            "true_when": draft["true_when"],
            "false_when": draft["false_when"],
        },
        # Probability >= yes -> "Y", <= no -> "N", anything between -> "Review".
        "thresholds": {"yes": 0.8, "no": 0.3},
        "model": jev.MODEL,
    }


def show(gate):
    q = gate["question"]
    line = "─" * 64
    print("\n%s\nICP SUMMARY  (%s)\n%s\n%s" % (line, gate["client_name"], line, gate["icp_summary"]))
    print("\n%s\nJEV QUESTION\n%s\n%s" % (line, line, q["instructions"]))
    print("\n  TRUE when:  %s" % q["true_when"])
    print("\n  FALSE when: %s" % q["false_when"])
    t = gate["thresholds"]
    print("\n  Thresholds: Y ≥ %s · N ≤ %s · Review in between" % (t["yes"], t["no"]))


def edit_in_editor(gate):
    editor = os.getenv("EDITOR") or ("nano" if shutil.which("nano") else "vi")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(gate, f, indent=2, ensure_ascii=False)
        path = f.name
    subprocess.call([editor, path])
    try:
        edited = json.loads(Path(path).read_text())
        for key in ("instructions", "true_when", "false_when"):
            assert edited["question"][key].strip()
        assert edited["icp_summary"].strip()
        return edited
    except (ValueError, KeyError, AssertionError, AttributeError):
        print("That edit isn't valid JSON with all fields filled in. Keeping the previous version.")
        return gate
    finally:
        os.unlink(path)


def save(client, gate):
    gate["approved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = Path("clients") / client / "gate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gate, indent=2, ensure_ascii=False) + "\n")
    print("\nSaved %s. Next: python bouncer.py run %s <list.csv>" % (path, client))


def read_icp_doc(path):
    path = Path(path)
    if not path.exists():
        raise SystemExit("ICP file not found: %s" % path)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8", errors="replace")
