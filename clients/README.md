# clients/

One folder per client, created by `python bouncer.py setup <client> --site <domain>`.

```
clients/
├── README.md          (this file)
├── example/
│   └── gate.json      a made-up gate showing the format
└── <your-client>/
    └── gate.json      created by setup, private (gitignored)
```

Real client folders are gitignored, so ICPs and client names never reach GitHub. Only this README and `example/` are published.

## gate.json

| Field | What it is |
|---|---|
| `icp_summary` | The client's ICP, sent to Jev with every company |
| `question.instructions` | The Yes/No question Jev answers |
| `question.true_when` / `false_when` | What counts as a fit and what doesn't, exclusions included |
| `thresholds.yes` / `thresholds.no` | Probability at or above `yes` is `Y`, at or below `no` is `N`, in between is `Review` |
| `model` | The Jev model used |

Edit any field by hand and re-run. Changing only the thresholds is free, because every answer is cached.
