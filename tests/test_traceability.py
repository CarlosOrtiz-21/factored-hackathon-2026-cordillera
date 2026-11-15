"""A5: the requirement matrix as a failing test, not a hand-kept table.

Requirements: DLV-01, DLV-02, DLV-03, DLV-04, DIM-01, DIM-02, DIM-03, DIM-04, DIM-05

Every ID in docs/requirements.md must be cited by at least one test module or doc, and
every citation in the repository must resolve to a defined ID. Citations rot when kept by
hand (Faro cites by hand); here a missing or stale one fails CI.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "docs" / "requirements.md"
ID = re.compile(r"\b(DIM|SCP|PRB|AI|CTL|SEC|DML|EVL|OPS|DSN|DLV)-\d{2}\b")
SCAN = [ROOT / "tests", ROOT / "docs", ROOT / "src", ROOT / "eval", ROOT / "README.md"]
SKIP_PARTS = {"__pycache__", "results", "data"}


def defined_ids() -> set[str]:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    return {
        m.group(0)
        for line in text.splitlines()
        if line.startswith("| ")
        for m in [ID.search(line)]
        if m
    }


def citations() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for base in SCAN:
        files = [base] if base.is_file() else base.rglob("*")
        for f in files:
            if not f.is_file() or f.suffix not in {".py", ".md"} or f == REQUIREMENTS:
                continue
            if SKIP_PARTS & set(f.parts):
                continue
            ids = {m.group(0) for m in ID.finditer(f.read_text(encoding="utf-8", errors="ignore"))}
            if ids:
                found[str(f.relative_to(ROOT))] = ids
    return found


def test_every_requirement_is_cited_somewhere():
    defined = defined_ids()
    cited = set().union(*citations().values())
    assert defined, "no requirement IDs parsed"
    missing = sorted(defined - cited)
    assert not missing, f"requirements nobody cites: {missing}"


def test_every_citation_resolves_to_a_defined_requirement():
    defined = defined_ids()
    stale = {f: sorted(ids - defined) for f, ids in citations().items() if ids - defined}
    assert not stale, f"citations of undefined requirements: {stale}"


def test_every_test_module_cites_at_least_one_requirement():
    uncited = [
        p.name
        for p in (ROOT / "tests").glob("test_*.py")
        if "Requirements:" not in p.read_text(encoding="utf-8")
    ]
    assert not uncited, f"test modules without a Requirements line: {uncited}"
