"""The hand-labelled set, rubric and protocol were frozen before any retrieval strategy was scored; changing any of them must fail loudly."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_frozen_files_unchanged():
    frozen = json.loads((ROOT / "docs/eval-freeze.json").read_text())["files"]
    assert frozen, "freeze record is empty"
    for rel, digest in frozen.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, f"{rel} changed after the freeze; a protocol change needs a new version and a new freeze"


def test_freeze_covers_every_hand_set_file():
    frozen = set(json.loads((ROOT / "docs/eval-freeze.json").read_text())["files"])
    on_disk = {str(p.relative_to(ROOT)) for p in (ROOT / "data/hand-labelled-v1").iterdir()}
    assert on_disk <= frozen
    assert "docs/eval-protocol.md" in frozen
