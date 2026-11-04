"""SaaS invoice file-drop adapter: vendor invoices (SAP, Veeva, ServiceNow, Oracle ...) -> FOCUS rows.

Enterprise SaaS rarely publishes FOCUS billing data, so the client drops one CSV per invoice period using
data/templates/saas_invoice_template.csv. This adapter maps it to FOCUS-shaped rows, loads them through the standard
ingestion path (manifest, schema checks, idempotent replay) and records the invoice lines in InvoiceDetail so
reconciliation ties charges to invoices.

  python -m costiq.adapters.saas_invoice --period 2026-11 --path /landing/saas/2026-11/*.csv

Template columns: vendor, invoice_id, invoice_line_id, billing_period (YYYY-MM), product, category, charge_type
(subscription | implementation | one_time | usage), amount, currency, cost_center, application, invoice_date.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, glob, json, os, sys, tempfile
from ..config import settings
from ..db import connect
from ..ingest import ingest

SOURCE = "saas_invoice_filedrop"
FREQUENCY = {"subscription": "Recurring", "implementation": "One-Time", "one_time": "One-Time", "usage": "Usage-Based"}


def month_bounds(period: str):
    start = dt.datetime.strptime(period, "%Y-%m")
    end = dt.datetime(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end


def to_focus(line: dict) -> dict:
    start, end = month_bounds(line["billing_period"])
    kind = line.get("charge_type", "subscription").strip().lower()
    amount = float(line["amount"])
    tags = {"cost_center": line.get("cost_center") or None, "application": line.get("application") or None,
            "environment": "prod"}
    if kind == "implementation":
        tags["implementation"] = "true"
    acct = f"{line['vendor'].lower().replace(' ', '-')}-invoices"
    return {
        "BillingAccountId": acct, "BillingAccountName": f"{line['vendor']} (invoice file drop)", "BillingAccountType": "Billing Account",
        "BillingCurrency": line.get("currency") or "USD", "BillingPeriodStart": start.isoformat(sep=" "),
        "BillingPeriodEnd": end.isoformat(sep=" "), "ChargePeriodStart": start.isoformat(sep=" "),
        "ChargePeriodEnd": end.isoformat(sep=" "), "ChargeCategory": "Usage" if kind == "usage" else "Purchase",
        "ChargeFrequency": FREQUENCY.get(kind, "Recurring"), "ChargeDescription": line["product"],
        "BilledCost": amount, "EffectiveCost": amount, "ListCost": amount, "ContractedCost": amount,
        "PricingCurrency": line.get("currency") or "USD", "PricingCategory": "Standard",
        "ServiceName": line["product"], "ServiceCategory": line.get("category") or "Business Applications",
        "ServiceProviderName": line["vendor"], "HostProviderName": line["vendor"], "InvoiceIssuerName": line["vendor"],
        "SubAccountId": acct, "SubAccountName": f"{line['vendor']} subscription",
        "ResourceId": f"{acct}:{line.get('application') or line['product']}", "ResourceType": "SaaS subscription",
        "InvoiceId": line["invoice_id"], "InvoiceDetailId": line["invoice_line_id"],
        "ConsumedQuantity": 1, "ConsumedUnit": "Subscription", "PricingQuantity": 1, "PricingUnit": "Subscription",
        "Tags": json.dumps({k: v for k, v in tags.items() if v}), "x_CostSource": "invoiced",
    }


def load(db, period: str, paths: list[str]):
    lines = []
    for p in paths:
        with open(p, newline="", encoding="utf-8-sig") as fh:
            lines += [r for r in csv.DictReader(fh) if r.get("billing_period") == period]
    if not lines:
        raise SystemExit(f"no invoice lines for {period}")
    focus = [to_focus(l) for l in lines]
    tmp = os.path.join(tempfile.mkdtemp(prefix="costiq-saas-"), f"saas_{period}.csv")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(focus[0]))
        w.writeheader()
        w.writerows(focus)
    result = ingest(db, SOURCE, period, tmp)
    if result["status"] == "promoted":
        start, end = month_bounds(period)
        for l in lines:
            db.query("DELETE FROM InvoiceDetail WHERE InvoiceDetailId = :d", {"d": l["invoice_line_id"]}, limit=None)
            issued = dt.datetime.fromisoformat(l.get("invoice_date") or start.isoformat()[:10])
            rec = {"BilledCost": float(l["amount"]), "BillingAccountId": f"{l['vendor'].lower().replace(' ', '-')}-invoices",
                   "BillingCurrency": l.get("currency") or "USD",
                   "BillingPeriodEnd": end, "BillingPeriodStart": start, "ChargeCategory": "Purchase",
                   "InvoiceDetailCreated": issued, "InvoiceDetailDescription": l["product"],
                   "InvoiceDetailGrain": json.dumps({"InvoiceLine": l["invoice_line_id"]}), "InvoiceDetailId": l["invoice_line_id"],
                   "InvoiceDetailLastUpdated": issued, "InvoiceId": l["invoice_id"], "InvoiceIssueDate": issued,
                   "InvoiceIssueStatus": "Issued", "InvoiceIssuerName": l["vendor"], "PaymentCurrency": l.get("currency") or "USD",
                   "PaymentCurrencyBilledCost": float(l["amount"]), "PaymentCurrencyInvoiceDetailId": "PAY-" + l["invoice_line_id"],
                   "PaymentDueDate": issued + dt.timedelta(days=30), "PaymentTerms": "Net 30", "PurchaseOrderNumber": None,
                   "ReferenceInvoiceId": None}
            cols = db.query("SELECT * FROM InvoiceDetail WHERE 1 = 0")["columns"]
            db.query(f"INSERT INTO InvoiceDetail ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})",
                     {c: rec.get(c) for c in cols}, limit=None)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(prog="costiq.adapters.saas_invoice")
    ap.add_argument("--period", required=True, help="YYYY-MM")
    ap.add_argument("--path", required=True, help="glob of invoice CSV files (template columns)")
    a = ap.parse_args(argv)
    out = load(connect(settings.db_url), a.period, sorted(glob.glob(a.path)))
    print(json.dumps(out, indent=2))
    return 0 if out["status"] == "promoted" else 2


if __name__ == "__main__":
    sys.exit(main())
