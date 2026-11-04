"""Manage the saved AI summary responses in data/summary_responses.json (see costiq/summary.py, "Writers").

  python tools/summary_responses.py dump  OUT.json [--range mtd] [--persona cfo ...]
        the costiq summary and its fact list for each role, to write prose from
  python tools/summary_responses.py save  DRAFTS.json
        add or replace saved prose; DRAFTS.json maps "persona:range" to {"headline": "...", "sections": {"spend": [...]}}.
        Each draft is checked against the current data (citations only, every figure cited) before it is saved.
  python tools/summary_responses.py check
        exit 1 unless every saved response applies in full to the current data

Run against the built-in sample (the default database), so saved prose matches what a fresh deployment serves.
"""
from __future__ import annotations
import argparse, copy, json, os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402
from costiq import summary  # noqa: E402
from costiq.auth import access  # noqa: E402
from costiq.main import app  # noqa: E402

client = TestClient(app)


def costiq_text(key: str) -> dict:
    """The summary as the costiq writer produces it for `persona:range`."""
    persona, rng = key.split(":")
    saved, summary._responses = summary._responses, {}
    try:
        r = client.get("/api/summary", params={"range": rng, "fresh": "true"}, headers={"X-CostIQ-Persona": persona})
        r.raise_for_status()
        return r.json()
    finally:
        summary._responses = saved


def dump(a):
    personas = a.persona or list(access()["personas"])
    out = {}
    for p in personas:
        d = costiq_text(f"{p}:{a.range}")
        out[f"{p}:{a.range}"] = {
            "role": access()["personas"][p]["label"], "period": d["period"]["label"],
            "facts": {k: {"figure": v["text"], "means": v["label"]} for k, v in d["facts"].items()},
            "headline": d["headline"],
            "sections": [{"key": s["key"], "title": s["title"], "state": s["state"], "sentences": s["sentences"]} for s in d["sections"]],
            "actions": [x["title"] for x in d["actions"]],
        }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"{len(out)} summaries -> {a.out}")


def entry(key: str, draft: dict) -> dict:
    d = costiq_text(key)
    texts = [draft["headline"]] + [t for v in draft["sections"].values() for t in v]
    cited = {fid for t in texts for fid in summary.CITE.findall(t)}
    unknown = cited - set(d["facts"])
    if unknown:
        raise SystemExit(f"{key}: unknown citations {sorted(unknown)}")
    missing = set(draft["sections"]) - {s["key"] for s in d["sections"]}
    if missing:
        raise SystemExit(f"{key}: sections not in this summary: {sorted(missing)}")
    b = summary.basis(d)
    e = {"key": key, "basis": {k: b[k] for k in ["headline", *draft["sections"]]},
         "facts": {fid: summary.fact_key(d["facts"][fid]) for fid in sorted(cited, key=lambda f: int(f[1:]))},
         "headline": draft["headline"], "sections": draft["sections"]}
    used = summary.apply_saved(copy.deepcopy(d), e)
    if sorted(used) != sorted(e["basis"]):
        problem = summary.Summarizer._check({"headline": e["headline"],
                                             "sections": [{"key": k, "sentences": v} for k, v in e["sections"].items()]}, d)
        raise SystemExit(f"{key}: draft rejected ({problem or 'parts not applied: ' + str(sorted(set(e['basis']) - set(used)))})")
    return e


def save(a):
    with open(a.drafts, encoding="utf-8") as fh:
        drafts = json.load(fh)
    try:
        with open(summary.RESPONSES, encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError:
        doc = {"about": "AI summary prose saved per role and period; see costiq/summary.py and tools/summary_responses.py.",
               "responses": {}}
    for key, draft in drafts.items():
        doc["responses"][key] = entry(key, draft)
        print(f"saved {key}: headline + {len(draft['sections'])} sections")
    doc["responses"] = dict(sorted(doc["responses"].items()))
    with open(summary.RESPONSES, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def check(_):
    bad = 0
    for key, e in summary.saved_responses().items():
        used = summary.apply_saved(costiq_text(key), e)
        missing = sorted(set(e["basis"]) - set(used))
        bad += bool(missing)
        print(f"{key}: {'ok' if not missing else 'not applied: ' + ', '.join(missing)}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump"); d.add_argument("out"); d.add_argument("--range", default="mtd"); d.add_argument("--persona", nargs="*")
    s = sub.add_parser("save"); s.add_argument("drafts")
    sub.add_parser("check")
    a = ap.parse_args()
    {"dump": dump, "save": save, "check": check}[a.cmd](a)
