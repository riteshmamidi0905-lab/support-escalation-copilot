"""Retrieval parameters. Every value here that is not a fixed a-priori choice was chosen on the DEV set (generator tickets, never the hand set);
see docs/eval-protocol.md and scripts/tune_dev.py. `thresholds.json` is written by the tuning script and committed BEFORE the held-out set is scored."""
import json
from pathlib import Path

CANDIDATES = 30            # chunk hits fetched from each ranked list
RERANK_DEPTH = 20          # fixed a priori (protocol)
TOP_K = 5                  # evidence items returned
CONFLICT_TOPN = 3          # a conflict set is raised when one of the top-3 governed items belongs to it (fixed a priori)
MAX_QUERY_CHARS = 2000     # the fixed query catalogue caps text parameters
CONTROL_ORDER = "conflict_first"   # M3 fix of the M2 finding; "abstain_first" reproduces the published M2 results only
STRATEGIES = ("lexical", "vector", "hybrid", "rerank")
_P = Path(__file__).with_name("thresholds.json")


def load_tuned() -> dict:
    return json.loads(_P.read_text()) if _P.exists() else {"query_prefix": False, "thresholds": {}}


def thresholds() -> dict:
    return load_tuned().get("thresholds", {})


def use_query_prefix() -> bool:
    return bool(load_tuned().get("query_prefix", False))


def rrf_k() -> int:
    return int(load_tuned().get("rrf_k", 60))      # published default 60; changed only if dev shows a clear gain (scripts/tune_dev.py)
