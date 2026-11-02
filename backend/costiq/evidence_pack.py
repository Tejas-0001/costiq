"""Rate-case Evidence Pack: one deterministic ZIP per docket, sealed in the hash-chained ledger.

Contents
  exhibit_bridge.csv        UT-05 test-year bridge (the exhibit)
  workpapers/<vendor>.csv   every base-period line: allocation step by step, invoice line and tie-out, FERC account,
                            evidence flags and risk
  approvals.csv             ledger entries for the policies, optimizations and exhibits behind the numbers
  optimization_actions.csv  savings actions and their verification
  prudence_summary.csv      disallowance risk by reason and vendor
  ledger_certificate.json   chain verification result and the hash of the last decision entry
  methodology.md            how every figure is computed

The ZIP is byte-identical for the same data (fixed timestamps, sorted entries, no wall-clock values inside), so its
SHA-256 identifies the evidence. Exporting appends `evidence_pack:<docket>` to the ledger with that hash; anyone can
later prove a filed pack was not altered by checking the hash against the ledger.
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

    def build(self, docket):
        """(zip bytes, sha256, summary). Deterministic for the same data and ledger decisions."""
        rc = self.q("SELECT * FROM ut_rate_case WHERE Docket = :d", {"d": docket})
        if not rc:
            raise KeyError(docket)
        rc = rc[0]
        uc = self.reg.get("UT-05")
        bridge = rows(self.db.query(uc["sql"], self.reg.resolve_params(uc, {"docket": docket}), None, limit=None))
        lines = self.q(f"""
            WITH c AS (
              SELECT {CHARGE_KEY} AS ChargeKey, InvoiceId, InvoiceDetailId, ResourceName AS Resource
              FROM CostAndUsage WHERE BillingPeriodStart >= :b0 AND BillingPeriodStart < :b1
            )
            SELECT e.ServiceProviderName AS Vendor, CAST(e.ChargeDay AS VARCHAR) AS ChargeDay, e.ServiceName, c.Resource,
                   e.Application, e.CostCenter, a.PoolMethod, a.OpcoPolicy, a.OpcoPolicyVersion, a.DriverName,
                   ROUND(a.OpcoShare, 6) AS OpcoShare, e.LegalEntity, e.Jurisdiction, ROUND(a.JurisdictionFactor, 4) AS JurisdictionFactor,
                   e.FercAccount, e.CostClass, ROUND(a.SourceCost, 4) AS SourceCost, ROUND(e.AllocatedCost, 4) AS AllocatedCost,
                   c.InvoiceId, c.InvoiceDetailId, e.Reconciled, e.PolicyApproved, e.FercMapped, e.UsedAndUseful, e.Remediated,
                   e.SavingsActioned, e.RiskReason, ROUND(e.RiskAmount, 4) AS RiskAmount, e.ChargeKey
            FROM prudence_evidence e
            JOIN ut_allocated_cost a ON a.ChargeKey = e.ChargeKey AND a.LegalEntity = e.LegalEntity AND a.Jurisdiction = e.Jurisdiction
            LEFT JOIN c ON c.ChargeKey = e.ChargeKey
            WHERE e.Docket = :d
            ORDER BY Vendor, ChargeDay, ServiceName, e.ChargeKey""", {"d": docket, "b0": rc["BasePeriodStart"], "b1": rc["BasePeriodEnd"]})
        by_vendor = {}
        for l in lines:
            by_vendor.setdefault(l["Vendor"], []).append(l)
        decisions = [e for e in self.ledger.entries()
                     if e["Target"].split(":")[0] in ("policy", "optimization", "finding", "data_request", "exhibit")]
        approvals = [{k: e[k] for k in ("Seq", "EntryAt", "Actor", "Persona", "Action", "Target", "Detail", "Hash")}
                     for e in decisions]
        actions = self.q("""SELECT OptimizationId, Lever, Title, ScopeType, ScopeKey, CostCenter, Status, EstimatedMonthlySavings,
                                   BaselineMonthlyCost, VerifiedMonthlySavings, VerificationBasis, IdentifiedOn, ApprovedOn,
                                   ImplementedOn, VerifiedOn FROM optimization_action ORDER BY OptimizationId""")
        summary = self.q("""SELECT COALESCE(RiskReason, 'evidenced') AS RiskReason, ServiceProviderName AS Vendor, COUNT(*) AS Lines,
                                   ROUND(SUM(AllocatedCost), 2) AS AllocatedCost, ROUND(SUM(RiskAmount), 2) AS RiskAmount
                            FROM prudence_evidence WHERE Docket = :d GROUP BY 1, 2 ORDER BY 1, 2""", {"d": docket})
        verify = self.ledger.verify()
        last = decisions[-1] if decisions else None
        certificate = {
            "docket": docket, "legal_entity": rc["LegalEntity"], "jurisdiction": rc["Jurisdiction"],
            "base_period": [str(rc["BasePeriodStart"])[:10], str(rc["BasePeriodEnd"])[:10]],
            "test_year": [str(rc["TestYearStart"])[:10], str(rc["TestYearEnd"])[:10]],
            "data_as_of": self.eng.as_of().isoformat(),
            "ledger_chain_verified": bool(verify.get("verified")),
            "last_decision_entry": {"seq": last["Seq"], "hash": last["Hash"]} if last else None,
            "lines": len(lines), "base_period_spend": round(sum(float(l["AllocatedCost"] or 0) for l in lines), 2),
            "disallowance_risk": round(sum(float(l["RiskAmount"] or 0) for l in lines), 2),
        }
        files = {
            "exhibit_bridge.csv": to_csv(bridge),
            "approvals.csv": to_csv(approvals),
            "optimization_actions.csv": to_csv(actions),
            "prudence_summary.csv": to_csv(summary),
            "ledger_certificate.json": json.dumps(certificate, indent=2, sort_keys=True, default=str) + "\n",
            "methodology.md": self.methodology(docket, rc),
        }
        for vendor, vl in by_vendor.items():
            files[f"workpapers/{safe(vendor)}.csv"] = to_csv(vl)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(files):
                info = zipfile.ZipInfo(f"{safe(docket)}/{name}", date_time=FIXED)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, files[name])
        data = buf.getvalue()
        return data, hashlib.sha256(data).hexdigest(), certificate

    def methodology(self, docket, rc):
        return f"""# Technology O&M evidence: {docket}

{rc['LegalEntity']} ({rc['Jurisdiction']}). Base period {str(rc['BasePeriodStart'])[:10]} to {str(rc['BasePeriodEnd'])[:10]}
(actuals); forecast test year {str(rc['TestYearStart'])[:10]} to {str(rc['TestYearEnd'])[:10]}. Prepared with MM CostIQ.

## Sources
Provider billing exports in FOCUS format (cloud and AI), SaaS vendor invoices (file drop), invoice detail, and the
utility's reference registers (legal entities, allocation drivers and policies, FERC account map, jurisdiction factors).

## Allocation (workpapers, one row per allocated charge)
1. Pool: cost center from split-cost allocation tags, resource tags or approved rules.
2. Operating company: first approved policy by priority; shares from the policy's statistical driver for the month, or
   100% to a fixed entity.
3. Jurisdiction: the entity's jurisdictional factors.
4. Cost class: capitalizable implementation (ASU 2018-15, FERC AI20-1-000), deferral where the jurisdiction allows
   regulatory-asset treatment, else O&M.
5. FERC account: the client's map (18 CFR Part 101; Order No. 898 computer accounts).
Allocated totals tie to source totals for every period (control C-10).

## Exhibit (exhibit_bridge.csv)
Base-period O&M and deferred subscription fees by vendor, annualized; test year adds contract escalators from each
renewal date (known and measurable). Invoice tie-out compares each vendor's invoices with its charges.

## Evidence tests (workpapers and prudence_summary.csv)
Reconciled to the invoice line (within $1); allocated under an approved, effective-dated policy; mapped to a FERC
account; used and useful (no idle day, unused commitment or unused seat share) unless remediated by an implemented
optimization; no known savings left unactioned for 60+ days.

## Integrity
approvals.csv lists the ledger entries for every policy, optimization and exhibit decision with their hashes.
ledger_certificate.json records that the hash chain verified and the last decision entry. The SHA-256 of this ZIP is
written to the ledger on export as `evidence_pack:{docket}`.

Decision support for the regulatory team; not a legal opinion or a prediction of any commission's ruling.
"""

    def export(self, docket, user, persona):
        data, sha, cert = self.build(docket)
        entry = self.ledger.append(user, persona, "export_pack", f"evidence_pack:{docket}", f"sha256:{sha}")
        return data, sha, cert, entry

    def packs(self):
        return [{"Seq": e["Seq"], "EntryAt": e["EntryAt"], "Actor": e["Actor"], "Docket": e["Target"].split(":", 1)[1],
                 "Sha256": e["Detail"].split(":", 1)[1], "LedgerHash": e["Hash"]}
                for e in self.ledger.entries() if e["Target"].startswith("evidence_pack:")]

    def verify(self, data: bytes):
        sha = hashlib.sha256(data).hexdigest()
        match = [p for p in self.packs() if p["Sha256"] == sha]
        chain = self.ledger.verify()
        return {"sha256": sha, "recorded": bool(match), "entries": match, "ledger_chain_verified": bool(chain.get("verified")),
                "verdict": ("Authentic: this pack matches ledger entry " + str(match[0]["Seq"]) if match and chain.get("verified")
                            else "Not found in the ledger: the file differs from every exported pack")}
