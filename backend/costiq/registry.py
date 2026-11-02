"""Use-case registry: the catalog use cases (FinOps Foundation, AI and utilities) as executable, versioned YAML entries."""
from __future__ import annotations
import datetime as dt, glob, os, re
import yaml

MODULES = {
    "command": "Command Center", "explorer": "Spend Explorer & Reporting", "ai": "AI Economics",
    "allocation": "Allocation & Chargeback", "rates": "Rate & Commitments", "budgets": "Budgets & Forecasting",
    "anomalies": "Anomalies & Optimization", "reconciliation": "Reconciliation & Close",
    "governance": "Governance & Evidence", "cloud": "Cloud Cost Monitor", "optimization": "Optimization Center",
    "prudence": "Prudence Radar", "regulatory": "Regulatory Accounting", "utilities": "Utility Compliance",
    "catalog": "Use Case Catalog", "ask": "Ask CostIQ",
}


def _clean_notes(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("- "):
        parts = [p.strip() for p in re.split(r"(?:^|\s)- (?=[A-Z\"`(])", text) if p.strip()]
        parts = [p for p in parts if not p.lower().startswith(("next use case", "previous use case"))]
        text = " ".join(p if p.endswith(".") else p + "." for p in parts)
    return text


class Registry:
    def __init__(self, directory: str):
        self.items: dict[str, dict] = {}
        for path in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
            with open(path, encoding="utf-8") as fh:
                uc = yaml.safe_load(fh)
            uc["notes"] = _clean_notes(uc.get("notes", ""))
            self.items[uc["id"]] = uc
        self.order = sorted(self.items, key=lambda k: (k[0] != "F", k.split("-")[0], int(k.split("-")[1])))

    def get(self, uid: str) -> dict:
        if uid not in self.items:
            raise KeyError(uid)
        return self.items[uid]

    def summary(self, uc: dict) -> dict:
        return {k: uc.get(k) for k in ("id", "title", "category", "module", "business_question", "source",
                                         "personas", "catalog_persona", "datasets", "products")} | {
            "params": uc.get("params", [])}

    def resolve_params(self, uc: dict, given: dict | None) -> dict:
        out = {}
        for p in uc.get("params", []):
            v = (given or {}).get(p["name"], p.get("default"))
            if p.get("type") == "date" and isinstance(v, str):
                v = dt.datetime.fromisoformat(v)
            out[p["name"]] = v
        return out
