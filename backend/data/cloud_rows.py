"""Synthetic cloud FOCUS 1.4 rows (AWS, Azure, GCP, Snowflake, marketplace) for the sample.

Called by generate_sample.py before invoices, tax and billing periods are derived, so cloud rows
flow through the same invoice / reconciliation logic as the AI rows.

Every scenario that a FinOps Foundation FOCUS use case needs is seeded deliberately:
  - Compute Savings Plan (used + unused), RDS Reserved Instance, Azure Reservation, GCP CUD
  - capacity reservations (used and unused), commitment program eligibility on uncovered usage
  - marketplace software (Fortinet hosted on AWS) and SaaS (Datadog) invoiced by AWS
  - multi-currency: an EUR-billed Azure billing account with USD payment currency
  - virtual currency: Snowflake credits purchased and consumed (PricingCurrency = credits)
  - EKS split-cost allocation (AllocatedMethodId / AllocatedResourceId / AllocatedTags)
  - availability zones, SkuPriceDetails core counts, data transfer in GB
  - an EC2 spend spike in the aws-dev account, us-west-2, on 2026-10-09 (anomaly use cases)
  - a September AWS correction posted after invoicing, an Azure correction, AWS promotional credits
All prices are illustrative, not vendor list prices. Account, bucket, namespace and cost-center names come from the
active tenant (tenants/<tenant>/names.py); the scenarios are shared.
"""
import json, random, datetime as dt
from sample_common import names

N = names()

ELIG_AWS = {"CommitmentPrograms": [
    {"ProgramType": "FlexibleSpendPlan", "ProgramName": "Compute Savings Plans"},
    {"ProgramType": "ResourceReservation", "ProgramName": "EC2 Reserved Instances"},
    {"ProgramType": "AdvanceResourceCommitment", "ProgramName": "On-Demand Capacity Reservations"}]}
ELIG_RDS = {"CommitmentPrograms": [{"ProgramType": "ResourceReservation", "ProgramName": "RDS Reserved Instances"}]}
ELIG_AZ = {"CommitmentPrograms": [
    {"ProgramType": "ResourceReservation", "ProgramName": "Azure Reservations"},
    {"ProgramType": "FlexibleSpendPlan", "ProgramName": "Azure Savings Plan for Compute"}]}
ELIG_GCP = {"CommitmentPrograms": [
    {"ProgramType": "FlexibleSpendPlan", "ProgramName": "Flexible CUDs"},
    {"ProgramType": "ZonalResourceCommitment", "ProgramName": "Zonal reservations"}]}

ACCTS = {
    "AWS": ("aws-payer-2002", "AWS Payer", "USD"),
    "Microsoft": ("ms-mca-1001", "MCA", "USD"),
    "MicrosoftEU": ("ms-mca-eu-1002", "MCA Europe", "EUR"),
    "Google Cloud": ("gcp-billing-3003", "GCP Billing", "USD"),
    "Snowflake": ("snow-org-6006", "Snowflake", "USD"),
}
# sub account id, name, cost center, application, application id, issuer key (keys are fixed; names are the tenant's)
SUBS = N.SUBS
GCP_MKTG, GCP_HR = SUBS["gcp-mktg"][0], SUBS["gcp-hr"][0]   # GCP project ids
SNOW_ACCOUNT = SUBS["snow"][0]
PROVIDER = {"AWS": "AWS", "Microsoft": "Microsoft", "MicrosoftEU": "Microsoft", "Google Cloud": "Google Cloud",
            "Snowflake": "Snowflake"}
ISSUER = {"AWS": "AWS", "Microsoft": "Microsoft", "MicrosoftEU": "Microsoft", "Google Cloud": "Google Cloud",
          "Snowflake": "Snowflake"}
SRC = {"AWS": "aws_focus_export", "Microsoft": "azure_focus_export", "MicrosoftEU": "azure_focus_export",
       "Google Cloud": "gcp_focus_export", "Snowflake": "snowflake_focus_export"}

# EC2-style instances: id, name, sub, region, az, type, series, cores, list $/hr, hours/day, commitment
INSTANCES = [
    ("i-0a11prodweb01", "web-store-web-01", "aws-prod", "us-east-1", "us-east-1a", "m7i.2xlarge", "m7i", 8, 0.4032, 24, "sp+cr"),
    ("i-0a12prodweb02", "web-store-web-02", "aws-prod", "us-east-1", "us-east-1b", "m7i.2xlarge", "m7i", 8, 0.4032, 24, "sp"),
    ("i-0a13prodapi01", "web-store-api-01", "aws-prod", "us-east-1", "us-east-1a", "c7i.4xlarge", "c7i", 16, 0.7140, 24, "sp+cr"),
    ("i-0a14prodapi02", "web-store-api-02", "aws-prod", "us-east-1", "us-east-1c", "c7i.4xlarge", "c7i", 16, 0.7140, 24, "sp"),
    ("i-0a15prodwork1", "web-store-worker", "aws-prod", "us-west-2", "us-west-2a", "m7i.xlarge", "m7i", 4, 0.2016, 24, None),
    ("i-0b21dataspark", "analytics-spark-01", "aws-data", "us-east-1", "us-east-1b", "r7i.4xlarge", "r7i", 16, 1.0584, 24, "sp"),
    ("i-0b22dataspark", "analytics-spark-02", "aws-data", "us-east-1", "us-east-1b", "r7i.4xlarge", "r7i", 16, 1.0584, 20, None),
    ("i-0b23datagpu01", "ml-training-gpu", "aws-data", "us-west-2", "us-west-2b", "g6.4xlarge", "g6", 16, 1.3232, 12, None),
    ("i-0c31devbox001", "dev-sandbox-01", "aws-dev", "us-west-2", "us-west-2a", "m7i.large", "m7i", 2, 0.1008, 10, None),
    ("i-0c32devbox002", "dev-sandbox-02", "aws-dev", "us-west-2", "us-west-2c", "m7i.xlarge", "m7i", 4, 0.2016, 10, None),
]
AZ_VMS = [  # id, name, sub, region, zone, size, series, cores, list/hr, hours, reserved?
    ("/subscriptions/sub-0a1b-crm/vm/crm-app-01", "crm-app-01", "az-crm", "eastus", "1", "D8s_v5", "Dsv5", 8, 0.384, 24, True),
    ("/subscriptions/sub-0a1b-crm/vm/crm-app-02", "crm-app-02", "az-crm", "eastus", "2", "D8s_v5", "Dsv5", 8, 0.384, 24, True),
    ("/subscriptions/sub-0c2d-erp/vm/erp-db-01", "erp-db-01", "az-erp", "eastus2", "1", "E16s_v5", "Esv5", 16, 1.008, 24, True),
    ("/subscriptions/sub-0c2d-erp/vm/erp-batch-01", "erp-batch-01", "az-erp", "eastus2", "2", "D4s_v5", "Dsv5", 4, 0.192, 14, False),
    ("/subscriptions/sub-0e3f-eu/vm/eu-web-01", "eu-web-01", "az-eu", "westeurope", "1", "D4s_v5", "Dsv5", 4, 0.176, 24, False),
    ("/subscriptions/sub-0e3f-eu/vm/eu-web-02", "eu-web-02", "az-eu", "westeurope", "3", "D4s_v5", "Dsv5", 4, 0.176, 24, False),
]
GCE = [
    (f"projects/{GCP_MKTG}/instances/mktg-etl-01", "mktg-etl-01", "gcp-mktg", "us-central1", "us-central1-a", "n2-standard-8", "n2", 8, 0.3885, 24, True),
    (f"projects/{GCP_MKTG}/instances/mktg-etl-02", "mktg-etl-02", "gcp-mktg", "us-central1", "us-central1-b", "n2-standard-8", "n2", 8, 0.3885, 16, False),
    (f"projects/{GCP_HR}/instances/hr-portal-01", "hr-portal-01", "gcp-hr", "us-east4", "us-east4-a", "e2-standard-4", "e2", 4, 0.1508, 24, False),
]
SP_ARN = "arn:aws:savingsplans::111111111111:savingsplan/sp-0compute2026"
CR_USED = "arn:aws:ec2:us-east-1:111111111111:capacity-reservation/cr-0a1used"
CR_IDLE = "arn:aws:ec2:us-east-1:111111111111:capacity-reservation/cr-0b2idle"
RI_RDS = "arn:aws:rds:us-east-1:111111111111:ri:ri-rds-erp-2026"
AZ_RES = "/providers/Microsoft.Capacity/reservationOrders/ro-crm-erp/reservations/res-dsv5-esv5"
GCP_CUD = f"projects/{GCP_MKTG}/regions/us-central1/commitments/cud-n2-2026"


def generate(ctx):
    ROWS, add_cc, DAYS, ts, month_start, next_month = (ctx[k] for k in
        ("ROWS", "add_cc", "DAYS", "ts", "month_start", "next_month"))
    rnd = random.Random(7)
    r6 = lambda x: round(x, 6)

    def growth(d): return 1 + 0.0015 * (d - DAYS[0]).days
    def wkf(d, prod=True): return 1.0 if prod or d.weekday() < 5 else 0.15

    def tags_for(sub_key, extra=None):
        sid, sname, cc, app, appid, _ = SUBS[sub_key]
        t = {"cost_center": cc, "application": app, "Application": app, "ApplicationId": appid,
             "environment": "dev" if "dev" in sub_key else "prod"}
        if extra: t.update(extra)
        return t

    def crow(d, sub_key, service, category, subcat, desc, *, charge_cat="Usage", charge_class=None,
             freq="Usage-Based", consumed=None, cunit=None, pqty=None, punit=None, list_up=None, contr_up=None,
             list_cost=0.0, contr_cost=0.0, eff_cost=0.0, billed=0.0, pricing_cat="Standard", region=None,
             az=None, res_id=None, res_name=None, res_type=None, sku=None, sku_details=None, tags=None,
             provider=None, host=None, issuer_key=None, bp=None, contract=None, pricing_currency=None,
             pc_list=None, pc_contr=None, pc_eff=None, period_end=None, **extra):
        sid, sname, cc, app, appid, ikey = SUBS[sub_key]
        ikey = issuer_key or ikey
        ba, ban, cur = ACCTS[ikey]
        bps = bp or month_start(d)
        r = dict(BillingAccountId=ba, BillingAccountName=ban, BillingAccountType="Billing Account",
                 BillingCurrency=cur, PricingCurrency=pricing_currency or cur,
                 BillingPeriodStart=ts(bps), BillingPeriodEnd=ts(next_month(bps)),
                 ChargePeriodStart=ts(d), ChargePeriodEnd=ts(period_end or d + dt.timedelta(1)),
                 ChargeCategory=charge_cat, ChargeClass=charge_class, ChargeFrequency=freq, ChargeDescription=desc,
                 ServiceProviderName=provider or PROVIDER[ikey], HostProviderName=host or provider or PROVIDER[ikey],
                 InvoiceIssuerName=ISSUER[ikey], ServiceName=service, ServiceCategory=category,
                 ServiceSubcategory=subcat, SubAccountId=sid, SubAccountName=sname,
                 SubAccountType={"AWS": "AWS Account", "Microsoft": "Subscription", "MicrosoftEU": "Subscription",
                                 "Google Cloud": "Project", "Snowflake": "Account"}[ikey],
                 RegionId=region, RegionName=region, AvailabilityZone=az, ResourceId=res_id, ResourceName=res_name,
                 ResourceType=res_type, SkuId=sku, SkuPriceId=f"{sku}-{pricing_cat.lower()}" if sku else None,
                 SkuMeter=cunit, SkuPriceDetails=json.dumps(sku_details) if sku_details else None,
                 ConsumedQuantity=consumed, ConsumedUnit=cunit,
                 PricingQuantity=pqty if pqty is not None else consumed, PricingUnit=punit or cunit,
                 ListUnitPrice=list_up, ContractedUnitPrice=contr_up,
                 PricingCurrencyListUnitPrice=pc_list if pc_list is not None else list_up,
                 PricingCurrencyContractedUnitPrice=pc_contr if pc_contr is not None else contr_up,
                 ListCost=r6(list_cost), ContractedCost=r6(contr_cost), EffectiveCost=r6(eff_cost), BilledCost=r6(billed),
                 PricingCurrencyEffectiveCost=r6(pc_eff if pc_eff is not None else eff_cost),
                 PricingCategory=pricing_cat, Tags=json.dumps(tags if tags is not None else tags_for(sub_key)),
                 ContractApplied=json.dumps({"Elements": [dict(contract, ContractCommitmentAppliedCost=r6(eff_cost))]})
                     if contract else None,
                 x_SourceSystem=SRC[ikey], x_CostSource="invoiced")
        r.update(extra)
        ROWS.append(r)

    EDP = {"ContractID": "aws-edp-2025", "ContractCommitmentID": "cc-aws-edp", "ContractCommitmentAppliedUnit": "USD"}
    MACC = {"ContractID": "ms-macc-2025", "ContractCommitmentID": "cc-ms-macc", "ContractCommitmentAppliedUnit": "USD"}
    EDP_DISC, MACC_DISC = 0.08, 0.05

    # ------------------------------------------------------------------ AWS EC2 + Savings Plan + capacity reservations
    sp_daily_commit = 0.0
    for inst in INSTANCES:
        if inst[10] and "sp" in inst[10]:
            sp_daily_commit += inst[8] * 0.72 * (1 - EDP_DISC) * inst[9]
    sp_daily_commit = round(sp_daily_commit * 1.04, 2)  # ~4% headroom -> small Unused amounts
    for d in DAYS:
        sp_used = 0.0
        for iid, name, sub, region, az, itype, series, cores, lp, hrs, cov in INSTANCES:
            prod = sub != "aws-dev"
            h = hrs * wkf(d, prod)
            if sub == "aws-dev" and d == dt.date(2026, 10, 9):
                h = 24 * 9  # seeded anomaly: dev fleet scaled out by an unattended load test
            if sub == "aws-data" and "gpu" in name:
                h = h * (1.0 + 0.6 * (d.month - 7))  # GPU training growing month over month
            h = round(max(1, h * rnd.uniform(0.92, 1.0)), 2)
            cp = lp * (1 - EDP_DISC)
            list_cost, contr = h * lp, h * cp
            details = {"InstanceType": itype, "InstanceSeries": series, "CoreCount": cores, "OperatingSystem": "Linux"}
            common = dict(consumed=h, cunit="Hours", punit="Hours", list_up=lp, contr_up=cp, region=region, az=az,
                          res_id=f"arn:aws:ec2:{region}:{SUBS[sub][0]}:instance/{iid}", res_name=name,
                          res_type="Virtual Machine", sku=f"EC2-{itype}-Linux", sku_details=details, contract=EDP,
                          provider="AWS")
            cr = {}
            if cov and "cr" in cov:
                cr = dict(CapacityReservationId=CR_USED, CapacityReservationStatus="Used")
            if cov and "sp" in cov:
                eff = contr * 0.72
                sp_used += eff
                crow(d, sub, "Amazon EC2", "Compute", "Virtual Machines", f"{itype} Linux on-demand covered by Compute Savings Plan",
                     list_cost=list_cost, contr_cost=contr, eff_cost=eff, billed=0, pricing_cat="Committed",
                     CommitmentDiscountId=SP_ARN, CommitmentDiscountName="Compute Savings Plan 1yr No Upfront",
                     CommitmentDiscountType="Compute Savings Plan", CommitmentDiscountCategory="Spend",
                     CommitmentDiscountStatus="Used", CommitmentDiscountQuantity=r6(eff), CommitmentDiscountUnit="USD",
                     **cr, **common)
            else:
                crow(d, sub, "Amazon EC2", "Compute", "Virtual Machines", f"{itype} Linux on-demand",
                     list_cost=list_cost, contr_cost=contr, eff_cost=contr, billed=contr,
                     CommitmentProgramEligibilityDetails=json.dumps(ELIG_AWS), **cr, **common)
        # Savings Plan: recurring fee (billed) + unused commitment (effective)
        crow(d, "aws-prod", "Savings Plans for AWS Compute usage", "Compute", "Virtual Machines",
             "Compute Savings Plan recurring fee", charge_cat="Purchase", freq="Recurring", consumed=None,
             pqty=round(sp_daily_commit, 2), punit="USD", list_cost=sp_daily_commit, contr_cost=sp_daily_commit,
             eff_cost=0, billed=sp_daily_commit, pricing_cat="Committed", region=None, res_id=SP_ARN,
             res_name="Compute Savings Plan", res_type="Savings Plan", sku="SavingsPlanRecurringFee",
             tags={"cost_center": N.IT_CC, "application": "shared-commitments", "Application": "shared-commitments"},
             provider="AWS", CommitmentDiscountId=SP_ARN, CommitmentDiscountName="Compute Savings Plan 1yr No Upfront",
             CommitmentDiscountType="Compute Savings Plan", CommitmentDiscountCategory="Spend",
             CommitmentDiscountQuantity=round(sp_daily_commit, 2), CommitmentDiscountUnit="USD")
        unused = max(0.0, sp_daily_commit - sp_used)
        if unused > 0.01:
            crow(d, "aws-prod", "Savings Plans for AWS Compute usage", "Compute", "Virtual Machines",
                 "Compute Savings Plan unused commitment", consumed=None, pqty=round(unused, 4), punit="USD",
                 list_cost=0, contr_cost=0, eff_cost=unused, billed=0, pricing_cat="Committed", res_id=SP_ARN,
                 res_name="Compute Savings Plan", res_type="Savings Plan", sku="SavingsPlanUnused",
                 tags={"cost_center": N.IT_CC, "application": "shared-commitments", "Application": "shared-commitments"},
                 provider="AWS", CommitmentDiscountId=SP_ARN, CommitmentDiscountName="Compute Savings Plan 1yr No Upfront",
                 CommitmentDiscountType="Compute Savings Plan", CommitmentDiscountCategory="Spend",
                 CommitmentDiscountStatus="Unused", CommitmentDiscountQuantity=round(unused, 4), CommitmentDiscountUnit="USD")
        # Idle capacity reservation: reserved but no instance running in it
        crow(d, "aws-prod", "Amazon EC2", "Compute", "Virtual Machines", "Unused On-Demand Capacity Reservation c7i.4xlarge x2",
             consumed=48, cunit="Hours", list_up=0.714, contr_up=0.714 * (1 - EDP_DISC), list_cost=48 * 0.714,
             contr_cost=48 * 0.714 * (1 - EDP_DISC), eff_cost=48 * 0.714 * (1 - EDP_DISC), billed=48 * 0.714 * (1 - EDP_DISC),
             region="us-east-1", az="us-east-1c", res_id=CR_IDLE, res_name="cr-checkout-peak", res_type="Capacity Reservation",
             sku="EC2-c7i.4xlarge-CapacityReservation", contract=EDP, provider="AWS",
             CapacityReservationId=CR_IDLE, CapacityReservationStatus="Unused")

        # -------------------------------------------------------------- RDS with Reserved Instance (used + unused)
        for sub, name, cls, lp, ri in (("aws-prod", "web-store-db", "db.r6g.xlarge", 0.48, True),
                                       ("aws-data", "lake-metadata-db", "db.r6g.large", 0.24, False)):
            cp = lp * (1 - EDP_DISC)
            common = dict(consumed=24, cunit="Hours", list_up=lp, contr_up=cp, region="us-east-1", az="us-east-1a",
                          res_id=f"arn:aws:rds:us-east-1:{SUBS[sub][0]}:db:{name}", res_name=name, res_type="Database",
                          sku=f"RDS-{cls}-PostgreSQL", sku_details={"InstanceType": cls, "CoreCount": 4 if "xlarge" in cls else 2,
                                                                    "InstanceSeries": "r6g"}, provider="AWS", contract=EDP)
            if ri:
                crow(d, sub, "Amazon RDS", "Databases", "Relational Databases", f"{cls} PostgreSQL covered by Reserved Instance",
                     list_cost=24 * lp, contr_cost=24 * cp, eff_cost=24 * cp * 0.6, billed=0, pricing_cat="Committed",
                     CommitmentDiscountId=RI_RDS, CommitmentDiscountName="RDS RI db.r6g.xlarge 1yr Partial Upfront",
                     CommitmentDiscountType="Reserved Instance", CommitmentDiscountCategory="Usage",
                     CommitmentDiscountStatus="Used", CommitmentDiscountQuantity=24, CommitmentDiscountUnit="Hours", **common)
            else:
                crow(d, sub, "Amazon RDS", "Databases", "Relational Databases", f"{cls} PostgreSQL on-demand",
                     list_cost=24 * lp, contr_cost=24 * cp, eff_cost=24 * cp, billed=24 * cp,
                     CommitmentProgramEligibilityDetails=json.dumps(ELIG_RDS), **common)
        # RI amortized fee (Purchase, recurring) for the RDS reservation
        crow(d, "aws-prod", "Amazon RDS", "Databases", "Relational Databases", "RDS Reserved Instance recurring fee",
             charge_cat="Purchase", freq="Recurring", pqty=24, punit="Hours", list_cost=24 * 0.48 * 0.6 * (1 - EDP_DISC),
             contr_cost=24 * 0.48 * 0.6 * (1 - EDP_DISC), eff_cost=0, billed=24 * 0.48 * 0.6 * (1 - EDP_DISC),
             pricing_cat="Committed", res_id=RI_RDS, res_name="RDS RI db.r6g.xlarge", res_type="Reserved Instance",
             sku="RDS-RI-RecurringFee", provider="AWS", tags={"cost_center": N.IT_CC, "application": "shared-commitments",
                                                              "Application": "shared-commitments"},
             CommitmentDiscountId=RI_RDS, CommitmentDiscountName="RDS RI db.r6g.xlarge 1yr Partial Upfront",
             CommitmentDiscountType="Reserved Instance", CommitmentDiscountCategory="Usage",
             CommitmentDiscountQuantity=24, CommitmentDiscountUnit="Hours")

        # -------------------------------------------------------------- S3 storage, requests and data transfer
        for sub, bucket, gb in N.S3_BUCKETS:
            gbd = gb * growth(d) / 30.0  # GB-month accrued per day
            lp = 0.023
            crow(d, sub, "Amazon S3", "Storage", "Object Storage", "S3 Standard storage", consumed=round(gbd, 3),
                 cunit="GB-Month", list_up=lp, contr_up=lp * (1 - EDP_DISC), list_cost=gbd * lp,
                 contr_cost=gbd * lp * (1 - EDP_DISC), eff_cost=gbd * lp * (1 - EDP_DISC), billed=gbd * lp * (1 - EDP_DISC),
                 region="us-east-1", res_id=f"arn:aws:s3:::{bucket}", res_name=bucket, res_type="Bucket",
                 sku="S3-Standard-Storage", contract=EDP, provider="AWS")
            reqs = gb / 10 * rnd.uniform(0.8, 1.2)
            crow(d, sub, "Amazon S3", "Storage", "Object Storage", "S3 PUT, COPY, POST, LIST requests",
                 consumed=round(reqs), cunit="Requests", pqty=round(reqs / 1000, 3), punit="1K Requests",
                 list_up=0.005, contr_up=0.005 * (1 - EDP_DISC), list_cost=reqs / 1000 * 0.005,
                 contr_cost=reqs / 1000 * 0.005 * (1 - EDP_DISC), eff_cost=reqs / 1000 * 0.005 * (1 - EDP_DISC),
                 billed=reqs / 1000 * 0.005 * (1 - EDP_DISC), region="us-east-1", res_id=f"arn:aws:s3:::{bucket}",
                 res_name=bucket, res_type="Bucket", sku="S3-Requests-Tier1", contract=EDP, provider="AWS")
        for sub, gbo in (("aws-prod", 2600), ("aws-data", 900)):
            g = gbo * growth(d) * wkf(d, True) * rnd.uniform(0.85, 1.15)
            crow(d, sub, "AWS Data Transfer", "Networking", "Network Connectivity", "Data transfer out to internet",
                 consumed=round(g, 3), cunit="GB", list_up=0.09, contr_up=0.09 * (1 - EDP_DISC), list_cost=g * 0.09,
                 contr_cost=g * 0.09 * (1 - EDP_DISC), eff_cost=g * 0.09 * (1 - EDP_DISC), billed=g * 0.09 * (1 - EDP_DISC),
                 region="us-east-1", sku="DataTransfer-Out-Internet", contract=EDP, provider="AWS")

        # -------------------------------------------------------------- EKS shared cluster with split cost allocation
        cluster = "arn:aws:eks:us-east-1:111111111111:cluster/shared-eks"
        node_cost = 6 * 24 * 0.4032 * (1 - EDP_DISC) * growth(d)
        for ns, share, cc, app in N.EKS_NAMESPACES:
            amt = node_cost * share
            crow(d, "aws-prod", "Amazon Elastic Kubernetes Service", "Compute", "Containers",
                 f"EKS split cost: namespace {ns}", consumed=round(6 * 24 * share, 2), cunit="Hours",
                 list_up=0.4032, contr_up=0.4032 * (1 - EDP_DISC), list_cost=amt / (1 - EDP_DISC), contr_cost=amt,
                 eff_cost=amt, billed=amt, region="us-east-1", res_id=cluster, res_name="shared-eks",
                 res_type="Kubernetes Cluster", sku="EKS-SplitCost-m7i.2xlarge", contract=EDP, provider="AWS",
                 tags={"cost_center": N.IT_CC, "application": "shared-eks", "Application": "shared-eks"},
                 AllocatedMethodId="eks-split-cost-allocation", AllocatedResourceId=f"{cluster}/namespace/{ns}",
                 AllocatedResourceName=ns, AllocatedTags=json.dumps({"cost_center": cc, "application": app}),
                 AllocatedMethodDetails=json.dumps({"Elements": [{"AllocatedRatio": share, "UsageUnit": "vCPU-Hours"}]}))

        # -------------------------------------------------------------- Marketplace on AWS
        crow(d, "aws-prod", "FortiGate Next-Generation Firewall", "Security", "Network Security",
             "Fortinet FortiGate-VM BYOL hourly software", consumed=48, cunit="Hours", list_up=0.95, contr_up=0.95,
             list_cost=45.6, contr_cost=45.6, eff_cost=45.6, billed=45.6, region="us-east-1",
             res_id="arn:aws:ec2:us-east-1:111111111111:instance/i-0f99fortigate", res_name="edge-fw-pair",
             res_type="Virtual Machine", sku="FTNT-FGTVM-HOURLY", provider="Fortinet", host="AWS")
        if d.day == 1:
            crow(d, "aws-prod", "Datadog Pro (Annual, billed monthly)", "Management and Governance", "Observability",
                 "Datadog Pro infrastructure monitoring, 120 hosts", charge_cat="Purchase", freq="Recurring",
                 consumed=120, cunit="Hosts", list_up=23.0, contr_up=20.0, list_cost=2760, contr_cost=2400,
                 eff_cost=2400, billed=2400, sku="DDOG-PRO-HOST", provider="Datadog", host="Datadog",
                 period_end=next_month(d))

        # -------------------------------------------------------------- Azure VMs, storage, SQL (USD + EUR accounts)
        for rid, name, sub, region, zone, size, series, cores, lp, hrs, reserved in AZ_VMS:
            h = round(hrs * rnd.uniform(0.95, 1.0), 2)
            cp = lp * (1 - MACC_DISC)
            common = dict(consumed=h, cunit="Hours", list_up=lp, contr_up=cp, region=region, az=f"{region}-{zone}",
                          res_id=rid, res_name=name, res_type="Virtual Machine", sku=f"VM-{size}-Linux",
                          sku_details={"InstanceType": size, "InstanceSeries": series, "CoreCount": cores},
                          contract=MACC if sub != "az-eu" else None)
            if reserved:
                crow(d, sub, "Virtual Machines", "Compute", "Virtual Machines", f"{size} covered by reservation",
                     list_cost=h * lp, contr_cost=h * cp, eff_cost=h * cp * 0.62, billed=0, pricing_cat="Committed",
                     CommitmentDiscountId=AZ_RES, CommitmentDiscountName="Dsv5/Esv5 1yr reservation",
                     CommitmentDiscountType="Reservation", CommitmentDiscountCategory="Usage",
                     CommitmentDiscountStatus="Used", CommitmentDiscountQuantity=h, CommitmentDiscountUnit="Hours", **common)
            else:
                crow(d, sub, "Virtual Machines", "Compute", "Virtual Machines", f"{size} pay-as-you-go",
                     list_cost=h * lp, contr_cost=h * cp, eff_cost=h * cp, billed=h * cp,
                     CommitmentProgramEligibilityDetails=json.dumps(ELIG_AZ), **common)
        res_fee = 24 * (0.384 * 2 + 1.008) * (1 - MACC_DISC) * 0.62
        crow(d, "az-crm", "Virtual Machines", "Compute", "Virtual Machines", "Reservation recurring fee",
             charge_cat="Purchase", freq="Recurring", pqty=72, punit="Hours", list_cost=res_fee, contr_cost=res_fee,
             eff_cost=0, billed=res_fee, pricing_cat="Committed", res_id=AZ_RES, res_name="res-dsv5-esv5",
             res_type="Reservation", sku="Reservation-RecurringFee",
             tags={"cost_center": N.IT_CC, "application": "shared-commitments", "Application": "shared-commitments"},
             CommitmentDiscountId=AZ_RES, CommitmentDiscountName="Dsv5/Esv5 1yr reservation",
             CommitmentDiscountType="Reservation", CommitmentDiscountCategory="Usage", CommitmentDiscountQuantity=72,
             CommitmentDiscountUnit="Hours")
        for sub, svc, cat, subcat, desc, qty, unit, lp, region in (
                ("az-crm", "Storage", "Storage", "Object Storage", "Blob storage hot LRS", 52000, "GB-Month", 0.0184, "eastus"),
                ("az-erp", "Azure SQL Database", "Databases", "Relational Databases", "SQL Database vCore Gen5 8 vCores", 24, "Hours", 1.94, "eastus2"),
                ("az-erp", "Azure Synapse Analytics", "Analytics", "Data Warehouses", "Dedicated SQL pool DW500c", 14, "Hours", 6.0, "eastus2"),
                ("az-eu", "Storage", "Storage", "Object Storage", "Blob storage hot LRS", 21000, "GB-Month", 0.0196, "westeurope"),
                ("az-eu", "Azure Front Door", "Networking", "Content Delivery", "Front Door data transfer out", 900, "GB", 0.083, "westeurope")):
            q = qty / 30 if unit == "GB-Month" else qty * (wkf(d, True) if unit == "GB" else 1)
            q = q * growth(d) * rnd.uniform(0.95, 1.05)
            cp = lp * (1 - MACC_DISC) if sub != "az-eu" else lp
            crow(d, sub, svc, cat, subcat, desc, consumed=round(q, 3), cunit=unit, list_up=lp, contr_up=cp,
                 list_cost=q * lp, contr_cost=q * cp, eff_cost=q * cp, billed=q * cp, region=region,
                 res_id=f"/subscriptions/{SUBS[sub][0]}/{svc.replace(' ', '').lower()}/{SUBS[sub][3]}",
                 res_name=f"{SUBS[sub][3]}-{svc.split()[-1].lower()}", res_type=svc, sku=f"AZ-{svc.replace(' ', '')}",
                 contract=MACC if sub != "az-eu" else None)

        # -------------------------------------------------------------- GCP Compute Engine (CUD), BigQuery, GCS
        for rid, name, sub, region, zone, mtype, series, cores, lp, hrs, cud in GCE:
            h = round(hrs * rnd.uniform(0.95, 1.0), 2)
            common = dict(consumed=h, cunit="Hours", list_up=lp, contr_up=lp, region=region, az=zone, res_id=rid,
                          res_name=name, res_type="Virtual Machine", sku=f"GCE-{mtype}",
                          sku_details={"InstanceType": mtype, "InstanceSeries": series, "CoreCount": cores})
            if cud:
                crow(d, sub, "Compute Engine", "Compute", "Virtual Machines", f"{mtype} covered by committed use discount",
                     list_cost=h * lp, contr_cost=h * lp, eff_cost=h * lp * 0.63, billed=h * lp * 0.63, pricing_cat="Committed",
                     CommitmentDiscountId=GCP_CUD, CommitmentDiscountName="n2 resource-based CUD 1yr",
                     CommitmentDiscountType="Committed Use Discount", CommitmentDiscountCategory="Usage",
                     CommitmentDiscountStatus="Used", CommitmentDiscountQuantity=cores * h, CommitmentDiscountUnit="vCPU-Hours",
                     **common)
            else:
                crow(d, sub, "Compute Engine", "Compute", "Virtual Machines", f"{mtype} on-demand",
                     list_cost=h * lp, contr_cost=h * lp, eff_cost=h * lp, billed=h * lp,
                     CommitmentProgramEligibilityDetails=json.dumps(ELIG_GCP), **common)
        tb = 14 * growth(d) * wkf(d, True) * rnd.uniform(0.7, 1.3)
        crow(d, "gcp-mktg", "BigQuery", "Analytics", "Data Warehouses", "BigQuery on-demand analysis", consumed=round(tb, 3),
             cunit="TiB", list_up=6.25, contr_up=6.25, list_cost=tb * 6.25, contr_cost=tb * 6.25, eff_cost=tb * 6.25,
             billed=tb * 6.25, region="us", res_id=f"projects/{GCP_MKTG}/datasets/marketing",
             res_name="marketing", res_type="Dataset", sku="BQ-Analysis-OnDemand")
        for sub, gb in (("gcp-mktg", 64000), ("gcp-hr", 3500)):
            g = gb / 30 * growth(d)
            crow(d, sub, "Cloud Storage", "Storage", "Object Storage", "Standard storage US multi-region",
                 consumed=round(g, 3), cunit="GB-Month", list_up=0.026, contr_up=0.026, list_cost=g * 0.026,
                 contr_cost=g * 0.026, eff_cost=g * 0.026, billed=g * 0.026, region="us",
                 res_id=f"projects/{SUBS[sub][0]}/buckets/{SUBS[sub][3]}", res_name=SUBS[sub][3], res_type="Bucket",
                 sku="GCS-Standard-Multi")

        # -------------------------------------------------------------- Snowflake virtual currency (credits)
        cr_used = 210 * growth(d) * wkf(d, True) * rnd.uniform(0.85, 1.15)
        crow(d, "snow", "Snowflake Compute", "Analytics", "Data Warehouses", "Virtual warehouse compute (ANALYTICS_WH)",
             consumed=round(cr_used / 8, 3), cunit="Warehouse-Hours", pqty=round(cr_used / 8, 3), punit="Warehouse-Hours",
             list_up=8 * 2.60, contr_up=8 * 2.20, list_cost=cr_used * 2.60, contr_cost=cr_used * 2.20,
             eff_cost=cr_used * 2.20, billed=0, pricing_cat="Committed", region="aws_us_east_1",
             res_id=f"{SNOW_ACCOUNT}.ANALYTICS_WH", res_name="ANALYTICS_WH", res_type="Warehouse",
             sku="SNOW-COMPUTE-ENTERPRISE", provider="Snowflake", host="AWS", pricing_currency="Snowflake Credits",
             pc_list=8.0, pc_contr=7.2, pc_eff=r6(cr_used * 0.9))
        crow(d, "snow", "Snowflake Storage", "Storage", "Data Warehouses", "Database storage", consumed=round(85 / 30, 4),
             cunit="TB-Month", list_up=23.0, contr_up=23.0, list_cost=85 / 30 * 23, contr_cost=85 / 30 * 23,
             eff_cost=85 / 30 * 23, billed=85 / 30 * 23, region="aws_us_east_1", res_id=f"{SNOW_ACCOUNT}.STORAGE",
             res_name="STORAGE", res_type="Storage", sku="SNOW-STORAGE", provider="Snowflake", host="AWS")
        if d.day == 1 and d.month in (7, 10):
            crow(d, "snow", "Snowflake Capacity", "Analytics", "Data Warehouses", "Capacity purchase: 20,000 credits",
                 charge_cat="Purchase", freq="One-Time", consumed=20000, cunit="Credits", pqty=20000, punit="Credits",
                 list_up=2.60, contr_up=2.20, list_cost=52000, contr_cost=44000, eff_cost=0, billed=44000,
                 pricing_cat="Committed", provider="Snowflake", host="Snowflake", sku="SNOW-CAPACITY-CREDITS",
                 pricing_currency="USD", period_end=next_month(d))

    # ------------------------------------------------------------------ corrections and credits
    crow(dt.date(2026, 9, 18), "aws-prod", "Amazon EC2", "Compute", "Virtual Machines",
         "Correction: c7i.4xlarge hours double-metered 2026-09-14..16", charge_class="Correction",
         consumed=-96, cunit="Hours", list_up=0.714, contr_up=0.714 * (1 - EDP_DISC), list_cost=-96 * 0.714,
         contr_cost=-96 * 0.714 * (1 - EDP_DISC), eff_cost=-96 * 0.714 * (1 - EDP_DISC), billed=-96 * 0.714 * (1 - EDP_DISC),
         region="us-east-1", az="us-east-1c", res_id="arn:aws:ec2:us-east-1:111111111111:instance/i-0a14prodapi02",
         res_name="web-store-api-02", res_type="Virtual Machine", sku="EC2-c7i.4xlarge-Linux", provider="AWS",
         bp=dt.date(2026, 9, 1))
    crow(dt.date(2026, 8, 12), "az-erp", "Storage", "Storage", "Object Storage",
         "Correction: blob transactions under-billed in August", charge_class="Correction", consumed=None,
         list_cost=420, contr_cost=420, eff_cost=420, billed=420, region="eastus2", sku="AZ-Storage")
    for m in (8, 9):
        crow(dt.date(2026, m, 1), "aws-data", "AWS Promotional Credits", "Other", "Other",
             "AWS Activate / migration acceleration credit", charge_cat="Credit", freq="One-Time",
             list_cost=0, contr_cost=0, eff_cost=-2500, billed=-2500, provider="AWS", period_end=dt.date(2026, m + 1, 1))

    # ------------------------------------------------------------------ contract commitments for the cloud
    add_cc(ContractCommitmentId="cc-aws-sp-compute", ContractId="aws-sp-2026", ServiceProviderName="AWS",
           InvoiceIssuerName="AWS", ContractCommitmentType="Compute Savings Plan", ContractCommitmentCategory="Spend",
           ContractCommitmentQuantity=round(sp_daily_commit * 365, 2), ContractCommitmentUnit="USD",
           ContractCommitmentCost=round(sp_daily_commit * 365, 2), ContractCommitmentFulfillmentInterval="Hourly",
           ContractCommitmentModel="Continuous", ContractCommitmentBenefitCategory="Discount",
           ContractCommitmentOfferCategory="Public", ContractCommitmentPaymentModel="No Upfront",
           ContractCommitmentPaymentInterval="Monthly", ContractCommitmentDiscountPercentage=0.28,
           start=dt.date(2026, 1, 1), end=dt.date(2027, 1, 1),
           ContractCommitmentDescription=f"Compute Savings Plan, ${sp_daily_commit / 24:,.2f}/hour, 1 year, no upfront",
           ContractCommitmentApplicability={"ServiceName": ["Amazon EC2", "AWS Lambda", "AWS Fargate"]})
    add_cc(ContractCommitmentId="cc-azure-reservation", ContractId="ms-ea-2026", ServiceProviderName="Microsoft",
           InvoiceIssuerName="Microsoft", ContractCommitmentType="Reservation", ContractCommitmentCategory="Usage",
           ContractCommitmentQuantity=72 * 365, ContractCommitmentUnit="Hours", ContractCommitmentCost=round(res_fee * 365, 2),
           ContractCommitmentFulfillmentInterval="Hourly", ContractCommitmentModel="Continuous",
           ContractCommitmentBenefitCategory="Discount", ContractCommitmentOfferCategory="Public",
           ContractCommitmentPaymentModel="No Upfront", ContractCommitmentPaymentInterval="Monthly",
           ContractCommitmentDiscountPercentage=0.38, start=dt.date(2026, 3, 1), end=dt.date(2027, 3, 1),
           ContractCommitmentDescription="Dsv5 x2 + Esv5 x1 1-year reservation (instance size flexibility)",
           ContractCommitmentApplicability={"ServiceName": ["Virtual Machines"]})
    add_cc(ContractCommitmentId="cc-snowflake-capacity", ContractId="snow-cap-2026", ServiceProviderName="Snowflake",
           InvoiceIssuerName="Snowflake", ContractCommitmentType="Capacity", ContractCommitmentCategory="Usage",
           ContractCommitmentQuantity=80000, ContractCommitmentUnit="Credits", ContractCommitmentCost=176000,
           ContractCommitmentFulfillmentInterval="Full Period", ContractCommitmentModel="Discontinuous",
           ContractCommitmentBenefitCategory="Discount", ContractCommitmentOfferCategory="Negotiated",
           ContractCommitmentPaymentModel="Partial Upfront", ContractCommitmentPaymentInterval="Quarterly",
           ContractCommitmentDiscountPercentage=0.154, start=dt.date(2026, 7, 1), end=dt.date(2027, 7, 1),
           ContractCommitmentDescription="Snowflake capacity: 80,000 credits over 12 months at $2.20",
           ContractCommitmentApplicability={"ServiceName": ["Snowflake Compute"]})
    return sp_daily_commit
