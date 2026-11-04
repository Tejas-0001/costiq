"""Filing audit file: one deterministic ZIP per tax filing, sealed in the hash-chained ledger.

Contents
  forecast_bridge.csv       PH-05 review period to forecast year bridge
  workpapers/<vendor>.csv   every review-period line: allocation step by step, invoice line and tie-out, GL account,
                            evidence flags and risk
  approvals.csv             ledger entries for the policies, optimizations and exhibits behind the numbers
  optimization_actions.csv  savings actions and their verification
  risk_summary.csv          deduction at risk by reason and vendor
  ledger_certificate.json   chain verification result and the hash of the last decision entry
  methodology.md            how every figure is computed

The ZIP is byte-identical for the same data (fixed timestamps, sorted entries, no wall-clock values inside), so its
SHA-256 identifies the evidence. Exporting appends `audit_file:<filing>` to the ledger with that hash; anyone can later
prove a submitted file was not altered by checking the hash against the ledger.
"""
from __future__ import annotations
import csv, hashlib, io, json, zipfile
from .engines import CHARGE_KEY

FIXED = (2026, 1, 1, 0, 0, 0)


def rows(res):
    return [dict(zip(res["columns"], r)) for r in res["rows"]]


def to_csv(records, columns=None):
    buf = io.StringIO()
    if not records:
        return ""
    columns = columns or list(records[0])
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in records:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
    return buf.getvalue()


def safe(name):
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(name or "unknown"))


class EvidencePack:
    def __init__(self, db, engines, registry, ledger):
        self.db, self.eng, self.reg, self.ledger = db, engines, registry, ledger

    def q(self, sql, params=None):
        return rows(self.db.query(sql, params or {}, None, limit=None))

    def build(self, filing):
        """(zip bytes, sha256, summary). Deterministic for the same data and ledger decisions."""
        rc = self.q("SELECT * FROM ref_tax_filing WHERE FilingId = :d", {"d": filing})
        if not rc:
            raise KeyError(filing)
        rc = rc[0]
        uc = self.reg.get("PH-05")
        bridge = rows(self.db.query(uc["sql"], self.reg.resolve_params(uc, {"filing": filing}), None, limit=None))
        lines = self.q(f"""
            WITH c AS (
              SELECT {CHARGE_KEY} AS ChargeKey, InvoiceId, InvoiceDetailId, ResourceName AS Resource
              FROM CostAndUsage WHERE BillingPeriodStart >= :b0 AND BillingPeriodStart < :b1
            )
            SELECT e.ServiceProviderName AS Vendor, CAST(e.ChargeDay AS VARCHAR) AS ChargeDay, e.ServiceName, c.Resource,
                   e.Application, e.CostCenter, a.PoolMethod, a.OpcoPolicy, a.OpcoPolicyVersion, a.DriverName,
                   ROUND(a.OpcoShare, 6) AS OpcoShare, e.LegalEntity, e.Jurisdiction, ROUND(a.JurisdictionFactor, 4) AS JurisdictionFactor,
                   e.GlAccount, e.CostClass, ROUND(a.SourceCost, 4) AS SourceCost, ROUND(e.AllocatedCost, 4) AS AllocatedCost,
                   c.InvoiceId, c.InvoiceDetailId, e.Reconciled, e.PolicyApproved, e.GlMapped, e.BenefitShown, e.Remediated,
                   e.SavingsActioned, e.RiskReason, ROUND(e.RiskAmount, 4) AS RiskAmount, e.ChargeKey
            FROM filing_evidence e
            JOIN allocated_cost a ON a.ChargeKey = e.ChargeKey AND a.LegalEntity = e.LegalEntity AND a.Jurisdiction = e.Jurisdiction
            LEFT JOIN c ON c.ChargeKey = e.ChargeKey
            WHERE e.FilingId = :d
            ORDER BY Vendor, ChargeDay, ServiceName, e.ChargeKey""", {"d": filing, "b0": rc["ReviewPeriodStart"], "b1": rc["ReviewPeriodEnd"]})
        by_vendor = {}
        for l in lines:
            by_vendor.setdefault(l["Vendor"], []).append(l)
        decisions = [e for e in self.ledger.entries()
                     if e["Target"].split(":")[0] in ("policy", "optimization", "finding", "examiner_request", "exhibit")]
        approvals = [{k: e[k] for k in ("Seq", "EntryAt", "Actor", "Persona", "Action", "Target", "Detail", "Hash")}
                     for e in decisions]
        actions = self.q("""SELECT OptimizationId, Lever, Title, ScopeType, ScopeKey, CostCenter, Status, EstimatedMonthlySavings,
                                   BaselineMonthlyCost, VerifiedMonthlySavings, VerificationBasis, IdentifiedOn, ApprovedOn,
                                   ImplementedOn, VerifiedOn FROM optimization_action ORDER BY OptimizationId""")
        summary = self.q("""SELECT COALESCE(RiskReason, 'evidenced') AS RiskReason, ServiceProviderName AS Vendor, COUNT(*) AS Lines,
                                   ROUND(SUM(AllocatedCost), 2) AS AllocatedCost, ROUND(SUM(RiskAmount), 2) AS RiskAmount
                            FROM filing_evidence WHERE FilingId = :d GROUP BY 1, 2 ORDER BY 1, 2""", {"d": filing})
        verify = self.ledger.verify()
        last = decisions[-1] if decisions else None
        certificate = {
            "filing": filing, "legal_entity": rc["LegalEntity"], "jurisdiction": rc["Jurisdiction"],
            "review_period": [str(rc["ReviewPeriodStart"])[:10], str(rc["ReviewPeriodEnd"])[:10]],
            "forecast_year": [str(rc["ForecastYearStart"])[:10], str(rc["ForecastYearEnd"])[:10]],
            "data_as_of": self.eng.as_of().isoformat(),
            "ledger_chain_verified": bool(verify.get("verified")),
            "last_decision_entry": {"seq": last["Seq"], "hash": last["Hash"]} if last else None,
            "lines": len(lines), "review_period_spend": round(sum(float(l["AllocatedCost"] or 0) for l in lines), 2),
            "deduction_at_risk": round(sum(float(l["RiskAmount"] or 0) for l in lines), 2),
        }
        files = {
            "forecast_bridge.csv": to_csv(bridge),
            "approvals.csv": to_csv(approvals),
            "optimization_actions.csv": to_csv(actions),
            "risk_summary.csv": to_csv(summary),
            "ledger_certificate.json": json.dumps(certificate, indent=2, sort_keys=True, default=str) + "\n",
            "methodology.md": self.methodology(filing, rc),
        }
        for vendor, vl in by_vendor.items():
            files[f"workpapers/{safe(vendor)}.csv"] = to_csv(vl)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(files):
                info = zipfile.ZipInfo(f"{safe(filing)}/{name}", date_time=FIXED)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, files[name])
        data = buf.getvalue()
        return data, hashlib.sha256(data).hexdigest(), certificate

    def methodology(self, filing, rc):
        return f"""# Technology cost evidence: {filing}

{rc['LegalEntity']} ({rc['Jurisdiction']}), {rc['Authority']}. Review period {str(rc['ReviewPeriodStart'])[:10]} to
{str(rc['ReviewPeriodEnd'])[:10]} (actuals); forecast year {str(rc['ForecastYearStart'])[:10]} to
{str(rc['ForecastYearEnd'])[:10]}. Prepared with MM CostIQ.

## Sources
Provider billing exports in FOCUS format (cloud and AI), SaaS vendor invoices (file drop), invoice detail, and the
group's reference registers (legal entities, allocation keys and policies, GL account map, jurisdiction factors).

## Allocation (workpapers, one row per allocated charge)
1. Pool: cost center from split-cost allocation tags, resource tags or approved rules.
2. Entity: first approved policy by priority; shares from the policy's allocation key for the month, or 100% to a
   fixed entity.
3. Jurisdiction: the entity's jurisdiction factors.
4. Cost class: capitalizable implementation (ASC 350-40), qre where the jurisdiction claims cloud fees as qualifying
   research expenditure, else opex.
5. GL account: the group's map.
Allocated totals tie to source totals for every period (control C-10).

## Forecast bridge (forecast_bridge.csv)
Review-period cost by vendor, annualized; the forecast year adds contract escalators from each renewal date. Invoice
tie-out compares each vendor's invoices with its charges.

## Evidence tests (workpapers and risk_summary.csv)
Reconciled to the invoice line (within $1); allocated under an approved, effective-dated policy; mapped to a GL
account; benefit shown (no idle day, unused commitment or unused seat share) unless remediated by an implemented
optimization; no known savings left unactioned for 60+ days.

## Integrity
approvals.csv lists the ledger entries for every policy, optimization and exhibit decision with their hashes.
ledger_certificate.json records that the hash chain verified and the last decision entry. The SHA-256 of this ZIP is
written to the ledger on export as `audit_file:{filing}`.

Decision support for the tax team; not a legal opinion or a prediction of any tax authority's ruling.
"""

    def export(self, filing, user, persona):
        data, sha, cert = self.build(filing)
        entry = self.ledger.append(user, persona, "export_audit_file", f"audit_file:{filing}", f"sha256:{sha}")
        return data, sha, cert, entry

    def packs(self):
        return [{"Seq": e["Seq"], "EntryAt": e["EntryAt"], "Actor": e["Actor"], "FilingId": e["Target"].split(":", 1)[1],
                 "Sha256": e["Detail"].split(":", 1)[1], "LedgerHash": e["Hash"]}
                for e in self.ledger.entries() if e["Target"].startswith("audit_file:")]

    def verify(self, data: bytes):
        sha = hashlib.sha256(data).hexdigest()
        match = [p for p in self.packs() if p["Sha256"] == sha]
        chain = self.ledger.verify()
        return {"sha256": sha, "recorded": bool(match), "entries": match, "ledger_chain_verified": bool(chain.get("verified")),
                "verdict": ("Authentic: this file matches ledger entry " + str(match[0]["Seq"]) if match and chain.get("verified")
                            else "Not found in the ledger: the file differs from every exported audit file")}
