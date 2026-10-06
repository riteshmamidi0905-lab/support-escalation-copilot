"""Print failed tests from a pytest junit XML as GitHub `::error` annotations, so a failure's cause is visible from the check-run annotations (no log access needed)."""
import sys
import xml.etree.ElementTree as ET  # noqa: S405  (our own CI artefact, not untrusted input)

root = ET.parse(sys.argv[1]).getroot()  # noqa: S314
n = 0
for case in root.iter("testcase"):
    for bad in list(case.findall("failure")) + list(case.findall("error")):
        msg = " ".join((bad.get("message") or "").split())[:300]
        tail = " ".join((bad.text or "").split())[-500:]
        print(f"::error title={case.get('classname')}::{case.get('name')}: {msg} || {tail}".replace("%", "%25").replace("\r", "").replace("\n", " "))
        n += 1
print(f"{n} failing test(s)")
