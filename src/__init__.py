"""Bouncer's code. Run it through bouncer.py at the project root."""

from pathlib import Path

# Project root, so clients/, cache/ and output/ land in the same place
# no matter which folder you run the command from.
ROOT = Path(__file__).resolve().parent.parent
