"""Filing audit file: one deterministic ZIP per tax filing, sealed in the hash-chained ledger.

Contents
  forecast_bridge.csv       the tenant's bridge use case (pharma PH-05): review period to forecast year
  workpapers/<vendor>.csv   every review-period line: allocation step by step, invoice line and tie-out, GL account,
                            evidence flags and risk
  approvals.csv             ledger entries for the policies, optimizations and exhibits behind the numbers
  optimization_actions.csv  savings actions and their verification
  risk_summary.csv          deduction at risk by reason and vendor
  ledger_certificate.json   chain verification result and the hash of the last decision entry
  methodology.md            how every figure is computed
  listing_by_account.csv    tenants whose ledger carries rule ids: every line for the filing's entity, jurisdiction
                            and review period, by account, with vendor, description, amount and invoice (pack.yaml
                            `usecases.account_listing`)
  rule_register.csv         the same tenants: each mapping rule, its citation and sign-off, and the dollars it placed
                            for the filing's entity, jurisdiction and review period

The ZIP is byte-identical for the same data (fixed timestamps, sorted entries, no wall-clock values inside), so its
SHA-256 identifies the evidence. Exporting appends `audit_file:<filing>` to the ledger with that hash; anyone can later
prove a submitted file was not altered by checking the hash against the ledger.
"""
from __future__ import annotations
import csv, hashlib, io, json, zipfile
from . import tenant
from .data_requests import ledger_target
from .engines import CHARGE_KEY

FIXED = (2026, 1, 1, 0, 0, 0)
# ledger columns copied into the workpapers when the tenant's ledger carries them (tenants/<t>/pack.yaml allocation.ledger_columns)
WORKPAPER_COLUMNS = ("FunctionCode", "RuleId", "Citation", "PolicyFlag", "BillingClass", "WorkOrderId")


def say(key, **values):
    """Backend copy, or "" when the tenant's copy has no such key."""
    try:
        return tenant.text(key, **values)
    except KeyError:
        return ""


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
        cols = self.ledger_columns()
        extra = "".join(f", a.{c}" for c in WORKPAPER_COLUMNS if c in cols)
        uc = self.reg.get(tenant.usecase("bridge"))
        bridge = rows(self.db.query(uc["sql"], self.reg.resolve_params(uc, {"filing": filing}), None, limit=None))
        lines = self.q(f"""
            WITH c AS (
              SELECT {CHARGE_KEY} AS ChargeKey, InvoiceId, InvoiceDetailId, ResourceName AS Resource
              FROM CostAndUsage WHERE BillingPeriodStart >= :b0 AND BillingPeriodStart < :b1
            )
            SELECT e.ServiceProviderName AS Vendor, CAST(e.ChargeDay AS VARCHAR) AS ChargeDay, e.ServiceName, c.Resource,
                   e.Application, e.CostCenter, a.PoolMethod, a.OpcoPolicy, a.OpcoPolicyVersion, a.DriverName,
                   ROUND(a.OpcoShare, 6) AS OpcoShare, e.LegalEntity, e.Jurisdiction, ROUND(a.JurisdictionFactor, 4) AS JurisdictionFactor,
                   e.GlAccount, e.CostClass{extra}, ROUND(a.SourceCost, 4) AS SourceCost, ROUND(e.AllocatedCost, 4) AS AllocatedCost,
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
                     if e["Target"].split(":")[0] in ("policy", "optimization", "finding", ledger_target(), "exhibit", "listing")]
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
        if "RuleId" in cols:
            files.update(self.ledger_files(rc))
            note = say("tax.methodology_ledger", filing=filing)
            if note:
                files["methodology.md"] = files["methodology.md"].rstrip("\n") + "\n\n" + note + "\n"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(files):
                info = zipfile.ZipInfo(f"{safe(filing)}/{name}", date_time=FIXED)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, files[name])
        data = buf.getvalue()
        return data, hashlib.sha256(data).hexdigest(), certificate

    def ledger_columns(self):
        from .regulatory import Regulatory
        return Regulatory(self.db, self.eng).ledger_columns()

    def ledger_files(self, rc):
        """listing_by_account.csv (the account-listing use case, run per account) and rule_register.csv for the filing's
        entity, jurisdiction and review period. Rows are sorted on every key so the ZIP stays byte-identical."""
        p = {"e": rc["LegalEntity"], "j": rc["Jurisdiction"], "b0": rc["ReviewPeriodStart"], "b1": rc["ReviewPeriodEnd"]}
        scope = "LegalEntity = :e AND Jurisdiction = :j AND PeriodStart >= :b0 AND PeriodStart < :b1"
        files = {}
        uid = (tenant.pack().get("usecases") or {}).get("account_listing")
        try:
            uc = self.reg.get(uid) if uid else None
        except KeyError:
            uc = None
        if uc:
            names = {x["name"] for x in uc.get("params", [])}
            accounts = [r["GlAccount"] for r in self.q(f"SELECT DISTINCT GlAccount FROM allocated_cost WHERE {scope} "
                                                         "AND GlAccount IS NOT NULL ORDER BY 1", p)]
            listing, columns = [], None
            for a in accounts:
                params = {"account": a, "period_start": str(rc["ReviewPeriodStart"])[:10], "period_end": str(rc["ReviewPeriodEnd"])[:10],
                          "entity": rc["LegalEntity"]}
                res = self.db.query(uc["sql"], self.reg.resolve_params(uc, {k: v for k, v in params.items() if k in names}),
                                    None, limit=None)
                columns = columns or res["columns"]
                listing += [r for r in rows(res) if r.get("LegalEntity") == rc["LegalEntity"] and r.get("Jurisdiction") == rc["Jurisdiction"]]
            first = [c for c in ("GlAccount", "ChargeDay", "ChargeKey", "InvoiceDetailId") if c in (columns or [])]
            order = first + [c for c in (columns or []) if c not in first]
            listing.sort(key=lambda r: [str(r.get(c)) for c in order])
            files["listing_by_account.csv"] = to_csv(listing, columns) if listing else ""
        placed = {r["RuleId"]: r for r in self.q(f"""SELECT RuleId, COUNT(*) AS Lines, ROUND(SUM(AllocatedCost), 2) AS AllocatedCost
                                                     FROM allocated_cost WHERE {scope} GROUP BY 1""", p)}
        register = []
        for r in self.q("SELECT * FROM ref_ferc_rule ORDER BY EvaluationStep, RuleId"):
            d = placed.get(r["RuleId"], {})
            register.append({**r, "Lines": d.get("Lines", 0), "AllocatedCost": d.get("AllocatedCost", 0.0)})
        files["rule_register.csv"] = to_csv(register)
        return files

    def methodology(self, filing, rc):
        """methodology.md, from the tenant's copy (backend.evidence.methodology)."""
        return tenant.text("evidence.methodology", filing=filing, entity=rc["LegalEntity"], jurisdiction=rc["Jurisdiction"],
                           authority=rc["Authority"], review_start=str(rc["ReviewPeriodStart"])[:10],
                           review_end=str(rc["ReviewPeriodEnd"])[:10], forecast_start=str(rc["ForecastYearStart"])[:10],
                           forecast_end=str(rc["ForecastYearEnd"])[:10])

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
