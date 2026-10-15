# inputs/

Drop your prospect lists and ICP documents here. Everything in this folder except this README is gitignored, so client data never reaches GitHub.

```
inputs/
├── acme_icp.md          ICP doc for: python bouncer.py setup acme --site acme.com --icp inputs/acme_icp.md
└── acme_leads.csv       list for:    python bouncer.py run acme inputs/acme_leads.csv
```

Any CSV works: Bouncer auto-detects the website and company name columns.
