"""Checks every relative link in the repository's Markdown files: the target file exists and, for `#fragment` links, the heading exists in it (GitHub's slug rules). External links are not fetched
(CI must not depend on the network); they are listed so a human can see what the documents rely on. Exit status 1 on any broken link. Run: python scripts/check_links.py"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "node_modules", "support_escalation_copilot.egg-info"}
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|(?<=\!)\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def slug(text: str) -> str:
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text.strip().lower())      # [text](url) -> text
    t = re.sub(r"`|\*", "", t)
    t = re.sub(r"[^\w\- ]", "", t, flags=re.UNICODE)
    return t.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen, out, fence = {}, set(), False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            fence = not fence
            continue
        m = None if fence else HEADING.match(line)
        if m:
            s = slug(m.group(2))
            n = seen.get(s, 0)
            seen[s] = n + 1
            out.add(s if n == 0 else f"{s}-{n}")
    return out


def main() -> int:
    bad, external, checked = [], set(), 0
    files = [p for p in ROOT.rglob("*.md") if not (set(p.relative_to(ROOT).parts) & SKIP_DIRS)]
    cache: dict[Path, set[str]] = {}
    for md in sorted(files):
        fence = False
        for n, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            if FENCE.match(line):
                fence = not fence
            if fence:
                continue
            for m in LINK.finditer(line):
                target = m.group(1) or m.group(2)
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                    external.add(target) if target.startswith("http") else None
                    continue
                checked += 1
                path_part, _, frag = target.partition("#")
                dest = (md.parent / unquote(path_part)).resolve() if path_part else md
                if not dest.exists():
                    bad.append(f"{md.relative_to(ROOT)}:{n}: missing file {target}")
                    continue
                if frag and dest.suffix == ".md":
                    cache.setdefault(dest, anchors(dest))
                    if frag.lower() not in cache[dest]:
                        bad.append(f"{md.relative_to(ROOT)}:{n}: no heading '#{frag}' in {dest.relative_to(ROOT)}")
    print(f"{checked} relative links checked in {len(files)} Markdown files; {len(external)} distinct external links (not fetched)")
    for b in bad:
        print("BROKEN", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
