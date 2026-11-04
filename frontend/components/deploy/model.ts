// On-prem deployment planner: the questions, and a pure plan(answers) that turns them into a full deployment spec.
// Facts come from the CostIQ Helm chart (replicas, resources, ports, network policy), the MVP containers (web :3000,
// API :8080, /api/health) and the Client Deployment Playbook (prerequisites P1-P14, roles, FOCUS export steps).
// Component choices were checked against project and vendor sources in Nov 2026 (ingress-nginx retired Mar 2026, MinIO
// community edition archived Apr 2026, PostgreSQL 14 end of life Nov 12 2026, Entra/Okta signing keys rotate without notice).
// Compliance text follows 21 CFR Part 11, EU GMP Annex 11, GAMP 5 (2nd ed.), the HIPAA Security Rule, GDPR, NIST OSCAL
// baselines, CISA (CPG 2.0, 2026 SBOM minimum elements) and NIST CMVP (FIPS 140-2 historical after Sep 21 2026).
// Sizing is a starting point to confirm in a load test, and the UI says so.

import { CISCO_FW, F5_CIS, F5_LTM, F5_WAF, STAGES, type DeviceConfig, type LayerId } from "./release";

export type DefenseLayer = { id: LayerId; label: string; owner: "yours" | "provided"; product: string; does: string };

export type Answers = {
  users: "pilot" | "dept" | "ent" | "large";
  spend: "s" | "m" | "l" | "xl";
  entities: "one" | "few" | "many" | "lots";
  hosting: "dc" | "vpc" | "isolated";
  platform: "k8s" | "vms" | "none";
  distro: "openshift" | "aks" | "eks" | "gke" | "rke2" | "other";
  database: "pg" | "other" | "provide" | "embedded";
  have: string[];
  identity: "entra" | "okta" | "adfs" | "keycloak" | "local";
  compliance: string[];
  placement: "dedicated" | "shared-corp";
  phi: "no" | "yes" | "unsure";
  ledger: "gl" | "mgmt";
  fips: "required" | "preferred" | "no";
  retention: "90d" | "1y" | "3y" | "7y";
  egress: "none" | "proxy" | "direct";
  transfer: "server" | "diode" | "media";
  edge: "f5-cisco" | "f5" | "cisco" | "other";
  soc: "24x7" | "hours" | "none";
  testing: "golive" | "yearly" | "release";
  custody: "client-repo" | "drops" | "escrow" | "rebuild";
  testdata: "synthetic" | "masked" | "prod-uat";
  route: "gitops" | "pipeline" | "registry" | "bundle" | "ansible" | "pam";
  cutover: "bluegreen" | "canary" | "rolling" | "window";
  sources: string[];
  availability: "standard" | "ha" | "dr";
  environments: "prod" | "prod-test" | "prod-test-dev";
  operations: "client" | "managed" | "shared";
};
export type Key = keyof Answers;

export const DEFAULTS: Answers = {
  users: "ent", spend: "l", entities: "few", hosting: "dc", platform: "k8s", distro: "openshift", database: "pg", have: [], transfer: "server",
  identity: "entra", compliance: ["gxp", "sox"], placement: "dedicated", phi: "no", ledger: "gl", fips: "preferred", retention: "3y", egress: "proxy", sources: ["aws", "azure", "saas", "m365"],
  availability: "ha", environments: "prod-test", operations: "client",
  edge: "f5-cisco", soc: "24x7", testing: "yearly", custody: "client-repo", testdata: "synthetic", route: "gitops", cutover: "bluegreen",
};

export type Option = { value: string; label: string; detail: string; recommended?: boolean };
export type Question = {
  key: Key; chapter: string; ask: string; why: string; multi?: boolean; options: Option[];
  /** reason an option is unavailable for the current answers */
  blocked?: (value: string, a: Answers) => string | null;
  /** skipped questions get this answer forced, optionally with a note from the guide */
  skip?: (a: Answers) => { value: Answers[Key]; note?: string } | null;
};

// ------------------------------------------------------------------------------------------------ building blocks
export type Block = { id: string; label: string; yours: string; provided: string; product?: string; version?: string; layer: "edge" | "platform" | "data" | "operate" };
export const BLOCKS: Block[] = [
  { id: "firewall", layer: "edge", label: "Perimeter firewall", yours: "We add the rules in this blueprint to your firewall; nothing new to buy.",
    provided: "Host firewalls on every node (firewalld on RHEL, ufw on Ubuntu, both on nftables) plus a default-deny network policy." },
  { id: "waf", layer: "edge", label: "WAF", yours: "Publish costiq.<your domain> through your WAF (F5, Imperva, Akamai or similar).",
    provided: "The Coraza WAF running the OWASP Core Rule Set 4.25 LTS in front of the web app.", product: "Coraza WAF with OWASP CRS", version: "CRS 4.25 LTS" },
  { id: "lb", layer: "edge", label: "Load balancer", yours: "Use a VIP on your load balancer (F5, NetScaler or your cloud load balancer).",
    provided: "MetalLB on bare-metal Kubernetes, or an HAProxy LTS pair with keepalived on VMs." },
  { id: "gateway", layer: "edge", label: "Ingress or Gateway API", yours: "Attach CostIQ to your routes or gateway (OpenShift Routes, AKS App Routing, GKE Gateway, Traefik).",
    provided: "Envoy Gateway, a Gateway API implementation. Kubernetes ingress-nginx was retired in March 2026, so we don't use it.", product: "Envoy Gateway", version: "Gateway API v1" },
  { id: "pki", layer: "edge", label: "Internal certificate authority", yours: "Issue one TLS certificate for costiq.<your domain>.",
    provided: "cert-manager with a private CA; you distribute its root certificate to browsers.", product: "cert-manager", version: "1.x" },
  { id: "registry", layer: "operate", label: "Container registry", yours: "Push the signed images, signatures and SBOMs into your registry (Harbor, Quay, Artifactory, ACR or ECR).",
    provided: "A Harbor registry for the signed images, their signatures and SBOMs.", product: "Harbor", version: "2.15" },
  { id: "vault", layer: "operate", label: "Secrets vault or KMS", yours: "Keep database credentials, the pseudonym key and the signing key in your vault (CyberArk, Vault or a cloud KMS).",
    provided: "OpenBao, the open-source fork of Vault (Vault itself moved to the BSL licence in 2023).", product: "OpenBao", version: "2.x" },
  { id: "siem", layer: "operate", label: "SIEM", yours: "Forward application and audit logs to Splunk, Sentinel or QRadar.",
    provided: "Grafana Loki with Grafana Alloy collectors, 90-day retention.", product: "Grafana Loki and Alloy", version: "Loki 3" },
  { id: "monitoring", layer: "operate", label: "Monitoring", yours: "Add /api/health and container metrics to your monitoring.",
    provided: "Prometheus, Grafana and Alertmanager with CostIQ dashboards.", product: "Prometheus, Grafana, Alertmanager", version: "Prometheus 3" },
  { id: "backup", layer: "data", label: "Enterprise backup", yours: "Back up the database and landing zone with Veeam, Commvault or Rubrik.",
    provided: "Point-in-time database backups (Barman Cloud plugin on Kubernetes, pgBackRest on VMs) and Velero for the platform.",
    product: "Barman Cloud plugin or pgBackRest, Velero", version: "pgBackRest 2.60, Velero 1.18" },
  { id: "storage", layer: "data", label: "File or object storage", yours: "Share an NFS or SMB path, or an S3-compatible bucket (NetApp, Dell ECS, Ceph), as the landing zone.",
    provided: "A SeaweedFS S3-compatible store. We don't ship MinIO: its open-source edition is no longer maintained.", product: "SeaweedFS", version: "4.x" },
  { id: "proxy", layer: "edge", label: "Outbound web proxy", yours: "Allow-list the provider endpoints on your proxy.",
    provided: "A Squid allow-list proxy (CONNECT to named domains only).", product: "Squid", version: "7" },
  { id: "cicd", layer: "operate", label: "CI/CD pipeline", yours: "Ship upgrades through your pipeline (Azure DevOps, GitHub, GitLab).",
    provided: "Signed upgrades applied from an admin jump host with Helm or Compose." },
  { id: "edr", layer: "operate", label: "Endpoint detection (EDR)", yours: "Your EDR agent (CrowdStrike, Defender for Endpoint, SentinelOne) covers CostIQ's hosts and nodes.",
    provided: "Falco runtime threat detection (a CNCF graduated project) on every host or node, alerting to the SIEM.", product: "Falco", version: "current" },
  { id: "pam", layer: "operate", label: "Privileged access (PAM)", yours: "Admin and vendor sessions go through your PAM (CyberArk, BeyondTrust, Delinea): just-in-time, MFA, recorded.",
    provided: "Break-glass admin accounts kept in the vault, retrieved by two people and logged." },
  { id: "itsm", layer: "operate", label: "Change management (ITSM)", yours: "Releases arrive in ServiceNow or Jira Service Management as standard changes, with test evidence attached by the pipeline.",
    provided: "Release records in CostIQ's hash-chained ledger, exportable for audit." },
];

export const SOURCES: { id: string; label: string; kind: "cloud" | "saas" | "ai"; endpoint?: string; how: string; cadence: string }[] = [
  { id: "aws", label: "AWS", kind: "cloud", endpoint: "s3.<region>.amazonaws.com (your export bucket)",
    how: "Billing and Cost Management / Data Exports: 'FOCUS with AWS columns' from the payer account to your S3 bucket, Parquet, overwrite.", cadence: "Daily" },
  { id: "azure", label: "Microsoft Azure", kind: "cloud", endpoint: "<account>.blob.core.windows.net",
    how: "Cost Management / Exports at billing-account scope, dataset 'Cost and usage details (FOCUS)', daily month-to-date, Parquet.", cadence: "Daily" },
  { id: "gcp", label: "Google Cloud", kind: "cloud", endpoint: "storage.googleapis.com",
    how: "Detailed billing export to BigQuery, a FOCUS view on top, and a scheduled query that writes Parquet to Cloud Storage.", cadence: "Daily" },
  { id: "oci", label: "Oracle Cloud", kind: "cloud", endpoint: "objectstorage.<region>.oraclecloud.com",
    how: "Copy the FOCUS cost reports from the Oracle reporting bucket with a cross-tenancy policy or scheduled job.", cadence: "Daily" },
  { id: "saas", label: "SaaS invoices (SAP, Veeva, ServiceNow, Oracle)", kind: "saas",
    how: "One CSV per invoice period using the saas_invoice template, dropped in the landing zone.", cadence: "Monthly" },
  { id: "anthropic", label: "Anthropic (Claude)", kind: "ai", endpoint: "api.anthropic.com",
    how: "An organization admin creates an Admin API key used only for the usage and cost reports.", cadence: "Daily" },
  { id: "openai", label: "OpenAI / Azure OpenAI", kind: "ai", endpoint: "api.openai.com",
    how: "An organization owner creates an admin key for the usage and costs endpoints.", cadence: "Daily" },
  { id: "github", label: "GitHub Copilot", kind: "ai", endpoint: "api.github.com",
    how: "A GitHub App (preferred) with read-only access to Copilot metrics, seats and billing.", cadence: "Daily" },
  { id: "m365", label: "Microsoft 365 Copilot", kind: "ai", endpoint: "graph.microsoft.com",
    how: "An app registration with Microsoft Graph reports permission and admin consent.", cadence: "Daily" },
  { id: "gateway", label: "LLM gateway logs", kind: "ai",
    how: "Daily CSV from your gateway or proxy using the llm_gateway template, dropped in the landing zone.", cadence: "Daily" },
];

export const FRAMEWORKS: { id: string; label: string; detail: string }[] = [
  { id: "gxp", label: "GxP (21 CFR Part 11, EU Annex 11)", detail: "Validation scope, audit trails, supplier quality" },
  { id: "hipaa-gdpr", label: "HIPAA and GDPR", detail: "Patient data protection and residency" },
  { id: "sox", label: "SOX ITGC", detail: "Change, access and operations controls over financial reporting" },
  { id: "nist-csf", label: "NIST CSF 2.0", detail: "Govern, Identify, Protect, Detect, Respond, Recover" },
  { id: "nist-800-53", label: "NIST SP 800-53 (Moderate)", detail: "Federal-grade control baseline" },
  { id: "iso-27001", label: "ISO/IEC 27001:2022", detail: "Annex A controls" },
  { id: "cisa-cpg", label: "CISA CPG 2.0", detail: "Cross-sector cybersecurity performance goals" },
  { id: "privacy", label: "Employee privacy", detail: "Pseudonymized people data, retention" },
];

// ------------------------------------------------------------------------------------------------ questions
export const CHAPTERS = ["Your organization", "Where it runs", "What you already have", "Security and compliance", "Cyber defense", "Data and operations", "Delivery and release"];

export const QUESTIONS: Question[] = [
  { key: "users", chapter: CHAPTERS[0], ask: "How many people will use CostIQ?",
    why: "This sets how many copies of the app we run and how much headroom the database needs.",
    options: [
      { value: "pilot", label: "Pilot", detail: "Up to 50 people" },
      { value: "dept", label: "Department", detail: "50 to 250 people" },
      { value: "ent", label: "Enterprise", detail: "250 to 1,000 people", recommended: true },
      { value: "large", label: "Large enterprise", detail: "More than 1,000 people" },
    ] },
  { key: "spend", chapter: CHAPTERS[0], ask: "How much cloud, SaaS and AI spend will flow through it each year?",
    why: "Spend drives billing rows per month, which sizes the database, the landing zone and the nightly ingest job.",
    options: [
      { value: "s", label: "Under $10M", detail: "About 0.4M billing rows a month" },
      { value: "m", label: "$10M to $50M", detail: "About 2M rows a month" },
      { value: "l", label: "$50M to $200M", detail: "About 8M rows a month", recommended: true },
      { value: "xl", label: "Over $200M", detail: "About 25M rows a month" },
    ] },
  { key: "entities", chapter: CHAPTERS[0], ask: "How many legal entities share these costs?",
    why: "Every entity adds allocation policies, drivers and an intercompany recharge schedule to compute each night.",
    options: [
      { value: "one", label: "One company", detail: "No affiliate allocation" },
      { value: "few", label: "2 to 5", detail: "A shared-services entity and a few country affiliates", recommended: true },
      { value: "many", label: "6 to 15", detail: "Regional affiliates" },
      { value: "lots", label: "16 or more", detail: "Global affiliate network" },
    ] },
  { key: "hosting", chapter: CHAPTERS[1], ask: "Where should CostIQ run?",
    why: "CostIQ is self-hosted. Your billing data never leaves the boundary you choose.",
    options: [
      { value: "dc", label: "Your data center", detail: "Corporate IT network", recommended: true },
      { value: "vpc", label: "Your private cloud", detail: "Your own AWS, Azure or Google Cloud tenancy" },
      { value: "isolated", label: "Isolated network", detail: "No internet path at all" },
    ] },
  { key: "platform", chapter: CHAPTERS[1], ask: "What do you already run containers on?",
    why: "If you have Kubernetes we deploy into a namespace. If not, we bring a hardened, Compose-based setup.",
    options: [
      { value: "k8s", label: "Kubernetes", detail: "OpenShift, AKS, EKS, GKE, Rancher or similar", recommended: true },
      { value: "vms", label: "Virtual machines", detail: "VMware, Hyper-V or Nutanix, no Kubernetes" },
      { value: "none", label: "Nothing yet", detail: "We'll specify the servers to buy or allocate" },
    ] },
  { key: "distro", chapter: CHAPTERS[1], ask: "Which Kubernetes do you run?",
    why: "It decides how traffic reaches CostIQ, where the images live and how we enforce signed images.",
    options: [
      { value: "openshift", label: "Red Hat OpenShift", detail: "Routes or Gateway API; restricted-v2 security", recommended: true },
      { value: "aks", label: "Azure Kubernetes Service", detail: "App Routing on Gateway API" },
      { value: "eks", label: "Amazon EKS", detail: "AWS Load Balancer Controller" },
      { value: "gke", label: "Google Kubernetes Engine", detail: "GKE Gateway" },
      { value: "rke2", label: "Rancher RKE2", detail: "Traefik ingress by default" },
      { value: "other", label: "Another conformant cluster", detail: "Kubernetes 1.35 or 1.36" },
    ],
    skip: (a) => (a.platform !== "k8s" ? { value: "other" } : null) },
  { key: "database", chapter: CHAPTERS[1], ask: "Which database should CostIQ use?",
    why: "Your own managed PostgreSQL is the easiest to run. If you don't have one, we bring it.",
    options: [
      { value: "pg", label: "Our PostgreSQL", detail: "Version 16 or later, run by your DBAs", recommended: true },
      { value: "other", label: "Our SQL Server or ClickHouse", detail: "Also supported" },
      { value: "provide", label: "Bring one for us", detail: "PostgreSQL 18, deployed and tuned for you" },
    ], },
  { key: "have", chapter: CHAPTERS[2], ask: "What do you already have in place?", multi: true,
    why: "Anything you already run, we plug into instead of adding. Your firewall stays your firewall.",
    options: BLOCKS.map((b) => ({ value: b.id, label: b.label, detail: b.yours })) },
  { key: "edge", chapter: CHAPTERS[2], ask: "Which products are your firewall and load balancer?",
    why: "If they're F5 BIG-IP and Cisco Secure Firewall, the blueprint carries the exact virtual servers, pools, health monitor, WAF policy and firewall rules your teams set up.",
    options: [
      { value: "f5-cisco", label: "F5 BIG-IP and Cisco Secure Firewall", detail: "LTM and Advanced WAF; Threat Defense or ASA", recommended: true },
      { value: "f5", label: "F5 BIG-IP, another firewall", detail: "LTM and Advanced WAF configuration included" },
      { value: "cisco", label: "Cisco Secure Firewall, another load balancer", detail: "Threat Defense or ASA configuration included" },
      { value: "other", label: "Other vendors", detail: "Generic rules you translate to your devices" },
    ],
    blocked: (v, a) => {
      const fw = a.have.includes("firewall"), f5 = a.have.includes("lb") || a.have.includes("waf");
      if (v === "f5-cisco" && !(fw && f5)) return "Needs both a firewall and a load balancer or WAF";
      if (v === "cisco" && !fw) return "Needs a firewall";
      if (v === "f5" && !f5) return "Needs a load balancer or WAF";
      return null;
    },
    skip: (a) => (!["firewall", "lb", "waf"].some((x) => a.have.includes(x)) ? { value: "other" } : null) },
  { key: "identity", chapter: CHAPTERS[3], ask: "How do people sign in at your company?",
    why: "CostIQ uses your single sign-on. App roles become personas; security groups become cost-center scope.",
    options: [
      { value: "entra", label: "Microsoft Entra ID", detail: "OIDC with app roles and groups", recommended: true },
      { value: "okta", label: "Okta", detail: "OIDC with groups claim" },
      { value: "adfs", label: "AD FS or Ping", detail: "OIDC on your federation server" },
      { value: "keycloak", label: "Keycloak", detail: "Self-hosted OIDC" },
    ], },
  { key: "compliance", chapter: CHAPTERS[3], ask: "Which frameworks must the deployment satisfy?", multi: true,
    why: "Each one adds specific controls to the blueprint, and evidence you can hand to auditors.",
    options: FRAMEWORKS.map((f) => ({ value: f.id, label: f.label, detail: f.detail })) },
  { key: "placement", chapter: CHAPTERS[3], ask: "Where will CostIQ's hosts sit relative to your validated GxP systems?",
    why: "CostIQ is not a GxP system. Keeping it off validated infrastructure leaves that infrastructure's validation untouched.",
    options: [
      { value: "dedicated", label: "Dedicated corporate infrastructure", detail: "Its own cluster or VMs in the corporate zone", recommended: true },
      { value: "shared-corp", label: "Shared corporate cluster", detail: "Shared only with non-GxP workloads" },
    ],
    skip: (a) => (!a.compliance.includes("gxp") ? { value: "dedicated" } : null) },
  { key: "phi", chapter: CHAPTERS[3], ask: "Could billing or tag data include patient or trial-subject identifiers?",
    why: "Cost data should name workloads, not people. Any patient identifier switches on HIPAA and GDPR handling.",
    options: [
      { value: "no", label: "No", detail: "Tags name workloads and studies at most", recommended: true },
      { value: "yes", label: "Yes", detail: "Turn on patient-data handling" },
      { value: "unsure", label: "Not sure", detail: "Plan for it until a data review says otherwise" },
    ],
    skip: (a) => (!a.compliance.includes("hipaa-gdpr") ? { value: "no" } : null) },
  { key: "ledger", chapter: CHAPTERS[3], ask: "Does CostIQ output post to your general ledger?",
    why: "If chargebacks or accruals flow into the GL, auditors treat CostIQ as part of financial reporting and test its IT general controls.",
    options: [
      { value: "gl", label: "Yes, it feeds the GL", detail: "Chargebacks, allocations or accruals", recommended: true },
      { value: "mgmt", label: "No, management reporting only", detail: "Usually outside ITGC scope" },
    ],
    skip: (a) => (!a.compliance.includes("sox") ? { value: "mgmt" } : null) },
  { key: "fips", chapter: CHAPTERS[3], ask: "Do you require FIPS 140-3 validated cryptography?",
    why: "FIPS 140-2 certificates moved to the historical list after September 21, 2026, so new deployments that need FIPS claim 140-3 modules.",
    options: [
      { value: "required", label: "Required", detail: "Hosts in FIPS mode, validated modules only" },
      { value: "preferred", label: "Preferred", detail: "Use validated modules where available", recommended: true },
      { value: "no", label: "Not required", detail: "Standard TLS 1.2+ and AES-256" },
    ] },
  { key: "retention", chapter: CHAPTERS[3], ask: "How long must audit logs be kept?",
    why: "Sizes log storage in your SIEM (or ours). GxP and SOX programs commonly keep audit evidence for years.",
    options: [
      { value: "90d", label: "90 days", detail: "Short operational window" },
      { value: "1y", label: "1 year", detail: "Common IT default" },
      { value: "3y", label: "3 years", detail: "Common audit evidence retention", recommended: true },
      { value: "7y", label: "7 years", detail: "Financial-records style" },
    ] },
  { key: "egress", chapter: CHAPTERS[3], ask: "Can CostIQ reach the internet?",
    why: "Cloud billing exports can arrive as file drops. AI-vendor usage needs an API call or a monthly export.",
    options: [
      { value: "none", label: "No outbound access", detail: "Everything arrives as file drops" },
      { value: "proxy", label: "Through our proxy", detail: "Allow-listed endpoints only", recommended: true },
      { value: "direct", label: "Direct HTTPS", detail: "Outbound 443 to named endpoints" },
    ],
    skip: (a) => a.hosting === "isolated" ? { value: "none", note: "Isolated networks get file drops only, so I've set outbound access to none." } : null },
  { key: "soc", chapter: CHAPTERS[4], ask: "Who watches CostIQ for attacks?",
    why: "Defenses only matter if someone acts on what they catch. This decides where alerts go, how fast they're triaged, and the joint incident playbook.",
    options: [
      { value: "24x7", label: "Your SOC, around the clock", detail: "Alerts land in your SIEM and its playbooks", recommended: true },
      { value: "hours", label: "Your SOC, business hours", detail: "Critical alerts page out of hours" },
      { value: "none", label: "No SOC yet", detail: "Alerts page your platform team; we supply the rules" },
    ] },
  { key: "testing", chapter: CHAPTERS[4], ask: "How often should CostIQ be attacked on purpose?",
    why: "Independent penetration tests prove the defenses work, not just that they exist. Each test follows NIST SP 800-115 and is run by CREST-accredited testers.",
    options: [
      { value: "golive", label: "Before go-live", detail: "One independent test" },
      { value: "yearly", label: "Before go-live, then yearly", detail: "Plus a retest after every fix", recommended: true },
      { value: "release", label: "Every major release and yearly", detail: "Plus a purple-team exercise with your SOC" },
    ] },
  { key: "sources", chapter: CHAPTERS[5], ask: "Which spend should CostIQ bring in?", multi: true,
    why: "Each source adds one connector, a firewall rule or file-drop job, and a prerequisite for its owner.",
    options: SOURCES.map((s) => ({ value: s.id, label: s.label, detail: s.kind === "cloud" ? "FOCUS billing export" : s.kind === "saas" ? "Invoice file drop" : "Usage and cost reports" })) },
  { key: "availability", chapter: CHAPTERS[5], ask: "How critical is CostIQ to your teams?",
    why: "This decides replicas, database standby, backups and whether we plan a second site.",
    options: [
      { value: "standard", label: "Business hours", detail: "Nightly backups; back the next business day" },
      { value: "ha", label: "Business critical", detail: "No single point of failure in one site", recommended: true },
      { value: "dr", label: "Critical, with DR", detail: "A warm standby in a second site" },
    ], },
  { key: "environments", chapter: CHAPTERS[5], ask: "Which environments do you need?",
    why: "Test and development are sized smaller and never hold production data unless you say so.",
    options: [
      { value: "prod", label: "Production only", detail: "Smallest footprint" },
      { value: "prod-test", label: "Production and test", detail: "Upgrades are tested first", recommended: true },
      { value: "prod-test-dev", label: "Production, test and development", detail: "For your own extensions" },
    ] },
  { key: "operations", chapter: CHAPTERS[5], ask: "Who runs CostIQ day to day?",
    why: "We hand over everything either way. This sets runbooks, access and who gets paged.",
    options: [
      { value: "client", label: "Our IT team", detail: "Runbooks and training included", recommended: true },
      { value: "managed", label: "Miracle Minds", detail: "Through your jump host, time-bound access" },
      { value: "shared", label: "Shared", detail: "You operate; we handle upgrades and tuning" },
    ] },
  { key: "custody", chapter: CHAPTERS[6], ask: "How should the code become yours?",
    why: "The code belongs to you from the first commit. This is how you hold it; options can be combined later.",
    options: [
      { value: "client-repo", label: "Built in your repositories", detail: "We commit to your Git with your identities", recommended: true },
      { value: "drops", label: "Signed code drops", detail: "A signed Git bundle at every milestone" },
      { value: "escrow", label: "Source code escrow", detail: "A verified copy held by an escrow agent" },
      { value: "rebuild", label: "Independent rebuild check", detail: "You rebuild each release and compare digests" },
    ] },
  { key: "testdata", chapter: CHAPTERS[6], ask: "What data may test environments hold?",
    why: "Production data never leaves production unless you decide otherwise. Each choice keeps it inside your boundary.",
    options: [
      { value: "synthetic", label: "Synthetic data only", detail: "Production scale, no real data", recommended: true },
      { value: "masked", label: "A masked copy made in production", detail: "Keyed pseudonyms; data-owner approval" },
      { value: "prod-uat", label: "Final acceptance in production", detail: "Test stays synthetic; last check on real data" },
    ],
    skip: (a) => (a.environments === "prod" ? { value: "synthetic", note: "With production only, the pipeline tests each release in a short-lived environment on synthetic data." } : null) },
  { key: "route", chapter: CHAPTERS[6], ask: "How should each release reach test and production?",
    why: "Every route keeps your data where it is and verifies signatures before anything runs. They differ in who pushes, what crosses your boundary and how fast a fix ships.",
    options: [
      { value: "gitops", label: "GitOps from your Git", detail: "Argo CD or Flux pulls signed releases", recommended: true },
      { value: "pipeline", label: "Your CI/CD builds and deploys", detail: "Built from source inside your boundary" },
      { value: "registry", label: "Signed releases into your registry", detail: "Replication through one outbound rule" },
      { value: "bundle", label: "Signed offline bundle", detail: "Transfer server, data diode or media" },
      { value: "ansible", label: "Ansible to hardened VMs", detail: "Your automation verifies, then rolls out" },
      { value: "pam", label: "We deploy through your PAM", detail: "Time-boxed, recorded vendor sessions" },
    ],
    blocked: (v, a) => {
      if (v === "gitops" && a.platform !== "k8s") return "Needs Kubernetes";
      if (v === "ansible" && a.platform === "k8s") return "For VM installs";
      if ((v === "registry" || v === "pam") && a.hosting === "isolated") return "Needs a network path";
      return null;
    } },
  { key: "transfer", chapter: CHAPTERS[6], ask: "How do approved files cross into your network?",
    why: "Each release arrives as one signed offline bundle. This sets how it travels and how we verify it on the inside.",
    options: [
      { value: "server", label: "An approved transfer server", detail: "Scanned and logged, the usual path", recommended: true },
      { value: "diode", label: "A data diode", detail: "One-way, inbound only" },
      { value: "media", label: "Scanned removable media", detail: "Through a media kiosk (NIST SP 1334)" },
    ],
    skip: (a) => (a.hosting !== "isolated" && a.route !== "bundle" ? { value: "server" } : null) },
  { key: "cutover", chapter: CHAPTERS[6], ask: "How should users move to each new version?",
    why: "Database changes are split into expand and contract steps, so old and new versions run side by side and rollback is a traffic switch, not a restore.",
    options: [
      { value: "bluegreen", label: "Blue/green", detail: "New beside old; one switch on the load balancer", recommended: true },
      { value: "canary", label: "Canary", detail: "5%, 25%, 50%, then everyone" },
      { value: "rolling", label: "Rolling update", detail: "One pod at a time, never below capacity" },
      { value: "window", label: "Maintenance window", detail: "A short planned outage" },
    ],
    blocked: (v, a) => (v === "rolling" && a.platform !== "k8s" ? "Needs Kubernetes" : null) },
];

// ------------------------------------------------------------------------------------------------ the plan
export type Owner = "yours" | "provided";
export type Node = { id: string; lane: "people" | "edge" | "platform" | "data" | "operate" | "sources"; label: string; sub: string; owner: Owner; count?: number };
export type Server = { role: string; count: number; vcpu: number; ram: number; disk: number; note?: string };
export type Env = { name: string; servers: Server[]; quota?: { vcpu: number; ram: number; storage: number } };
export type Rule = { from: string; to: string; port: string; purpose: string; when?: string };
export type Plan = {
  answers: Answers;
  topology: string; runtime: string;
  nodes: Node[];
  envs: Env[];
  totals: { vcpu: number; ram: number; storage: number; servers: number };
  reuse: { have: number; total: number; yours: Block[]; provided: Block[] };
  db: { engine: string; vcpu: number; ram: number; storage: number; rowsPerMonth: number; layout: string; owner: Owner };
  app: { apiReplicas: number; webReplicas: number; ingest: string };
  rules: Rule[];
  software: { name: string; version: string; note: string }[];
  identity: string[];
  connectors: { label: string; method: string; cadence: string; owner: string }[];
  controls: { framework: string; items: string[] }[];
  operations: { label: string; value: string }[];
  team: { role: string; side: "Client" | "Miracle Minds"; days: number; does: string }[];
  /** the development plan: phases closed by measurable exit gates ("gates, not dates") */
  timeline: { phase: string; start: number; weeks: number; detail: string; work: string[]; exit: string[]; signoff: string }[];
  weeks: number;
  /** exact configuration for the client's own F5 and Cisco devices (de facto: we use what they run) */
  edgeConfig: DeviceConfig[];
  defense: { layers: DefenseLayer[]; soc: string; testing: string; detections: { alert: string; source: string; severity: "Critical" | "High" | "Medium" }[] };
  /** how the client's load balancer carries a cutover */
  lb: { via: string; f5: boolean };
  prerequisites: { id: string; item: string; owner: string; status: "reuse" | "todo"; week: number }[];
  assurance: string[];
  logs: { retention: string; perDayGb: number; storedGb: number };
  notes: string[];
};

const SHAPES: [number, number][] = [[2, 8], [4, 16], [8, 32], [16, 64], [32, 128], [48, 192], [64, 256]];
/** smallest standard server shape with at least 30% headroom */
function shape(vcpu: number, ram: number): [number, number] {
  return SHAPES.find(([c, r]) => c >= vcpu * 1.3 && r >= ram * 1.3) ?? SHAPES[SHAPES.length - 1];
}
const roundUp = (n: number, step: number) => Math.max(step, Math.ceil(n / step) * step);

const USERS = { pilot: { api: 1, web: 1, cpu: 1, mem: 2 }, dept: { api: 2, web: 2, cpu: 1, mem: 2 }, ent: { api: 3, web: 2, cpu: 1, mem: 2 }, large: { api: 4, web: 3, cpu: 2, mem: 4 } };
const ROWS = { s: 0.4, m: 2, l: 8, xl: 25 }; // million FOCUS rows a month
const DBSIZE = { s: [2, 8], m: [4, 16], l: [8, 32], xl: [16, 64] } as const;
const INGEST = { s: [1, 2], m: [2, 4], l: [4, 8], xl: [8, 16] } as const;
const ENTITY_EXTRA = { one: 0, few: 0, many: 1, lots: 2 };

export function plan(a: Answers): Plan {
  const has = (id: string) => a.have.includes(id);
  const ha = a.availability !== "standard";
  const dr = a.availability === "dr";
  const k8s = a.platform === "k8s";
  const blocksInScope = BLOCKS.filter((b) => (b.id !== "proxy" || a.egress === "proxy") && (b.id !== "gateway" || k8s));
  const yours = blocksInScope.filter((b) => has(b.id));
  const provided = blocksInScope.filter((b) => !has(b.id));
  const notes: string[] = [];
  // de facto: the client's own F5 and Cisco devices, when they have them
  const f5 = (a.edge === "f5-cisco" || a.edge === "f5") && (has("lb") || has("waf"));
  const cisco = (a.edge === "f5-cisco" || a.edge === "cisco") && has("firewall");
  const productFor = (id: string): string | null =>
    id === "firewall" && cisco ? "Cisco Secure Firewall" : id === "waf" && f5 ? "F5 Advanced WAF" : id === "lb" && f5 ? "F5 BIG-IP LTM" : id === "gateway" && f5 ? "F5 CIS or your routes" : null;

  // ---- application tier (Helm: API 2 replicas, HPA 2-6; requests 250m/512Mi, limits 1 vCPU/2Gi; web 250m/128Mi limits)
  const u = USERS[a.users];
  const apiReplicas = Math.max(u.api, ha ? 2 : 1);
  const webReplicas = Math.max(u.web, ha ? 2 : 1);
  const [ingCpu, ingMem] = INGEST[a.spend];
  const extra = ENTITY_EXTRA[a.entities];
  const appCpu = apiReplicas * u.cpu + webReplicas * 0.25 + ingCpu + extra;
  const appMem = apiReplicas * u.mem + webReplicas * 0.25 + ingMem + extra * 2;

  // ---- database
  const rows = ROWS[a.spend];
  const dbStorage = roundUp(rows * 36 * 2 * 1.5, 50); // 36 months, ~2 KB a row with indexes, 1.5x headroom
  let [dbCpu, dbRam] = DBSIZE[a.spend];
  if (a.users === "ent") { dbCpu += 2; dbRam += 8; }
  if (a.users === "large") { dbCpu += 4; dbRam += 16; }
  const engine = a.database === "other" ? "SQL Server or ClickHouse (yours)" : a.database === "embedded" ? "DuckDB (embedded)" : a.database === "provide" ? "PostgreSQL 18" : "PostgreSQL 16+ (yours)";
  const dbOwner: Owner = a.database === "pg" || a.database === "other" ? "yours" : "provided";
  const dbLayout = a.database === "embedded" ? "Single file on the application host"
    : `${ha ? "Primary with a synchronous standby" : "Single primary"}${dr ? ", plus an asynchronous replica in the DR site" : ""}`;
  if (a.database === "embedded") notes.push("Embedded DuckDB is right for a pilot. Plan the move to PostgreSQL before you scale past one department.");

  // ---- landing zone and backups
  const landing = roundUp(rows * 13 * 1.2, 25); // 13 months of raw exports
  const backups = roundUp(dbStorage * 3, 50); // 30 daily + 12 monthly, compressed

  // ---- platform, servers per environment
  const runtime = a.platform === "k8s" ? "Kubernetes namespace (Helm chart)" : a.platform === "vms" ? "Docker Compose on hardened Linux VMs" : "Hardened Linux servers with Docker Compose";
  const topology = `${a.hosting === "vpc" ? "Your private cloud" : a.hosting === "isolated" ? "Isolated network" : "Your data center"} · ${runtime} · ${dr ? "two sites" : "one site"}`;
  const opsCpu = (has("monitoring") ? 0 : 4) + (has("siem") ? 0 : 1) + (has("registry") ? 0 : 2) + (has("storage") ? 0 : 2) + (has("backup") ? 0 : 1) + (!has("proxy") && a.egress === "proxy" ? 1 : 0);
  const opsRam = (has("monitoring") ? 0 : 16) + (has("siem") ? 0 : 4) + (has("registry") ? 0 : 8) + (has("storage") ? 0 : 8) + (has("backup") ? 0 : 2) + (!has("proxy") && a.egress === "proxy" ? 1 : 0);
  const retentionDays = { "90d": 90, "1y": 365, "3y": 1095, "7y": 2555 }[a.retention];
  const retentionLabel = { "90d": "90 days", "1y": "1 year", "3y": "3 years", "7y": "7 years" }[a.retention];
  const logsPerDay = { pilot: 0.2, dept: 0.5, ent: 1.5, large: 4 }[a.users];
  const logsStored = roundUp((logsPerDay * retentionDays) / 10, 10);
  const opsDisk = (has("monitoring") ? 0 : 250) + (has("siem") ? 0 : logsStored + 50) + (has("registry") ? 0 : 200) + (has("storage") ? 0 : landing + backups) + 100;
  const edgeCpu = (has("waf") ? 0 : 1) + (has("lb") ? 0 : 1);
  const edgeRam = (has("waf") ? 0 : 1) + (has("lb") ? 0 : 1);

  const envDefs: { name: string; scale: number; ha: boolean; storage: number }[] = [{ name: "Production", scale: 1, ha, storage: 1 }];
  if (a.environments !== "prod") envDefs.push({ name: "Test", scale: 0.5, ha: false, storage: 0.4 });
  if (a.environments === "prod-test-dev") envDefs.push({ name: "Development", scale: 0.25, ha: false, storage: 0.15 });
  if (dr) envDefs.push({ name: "DR site (warm standby)", scale: 0.6, ha: false, storage: 1 });

  const envs: Env[] = envDefs.map((e) => {
    const cpu = Math.max(2, appCpu * e.scale), ram = Math.max(4, appMem * e.scale);
    const servers: Server[] = [];
    let quota: Env["quota"];
    const dbHere = a.database === "provide";
    const dbCount = dbHere ? (e.ha ? 2 : 1) : 0;
    const dbDisk = roundUp(dbStorage * e.storage * 1.5, 100);
    if (a.platform === "k8s") {
      const qCpu = Math.ceil((cpu + (dbHere ? dbCpu * e.scale * dbCount : 0) + (e.name === "Production" ? edgeCpu : 0)) * 1.25);
      const qRam = Math.ceil((ram + (dbHere ? dbRam * e.scale * dbCount : 0) + (e.name === "Production" ? edgeRam : 0)) * 1.25);
      quota = { vcpu: qCpu, ram: qRam, storage: (dbHere ? dbDisk * dbCount : 0) + roundUp(landing * e.storage, 25) };
      servers.push({ role: "Namespace in your cluster", count: 0, vcpu: qCpu, ram: qRam, disk: quota.storage, note: "Resource quota (limits) and persistent volumes; no new servers" });
    } else {
      const hosts = e.ha ? 2 : 1;
      const [c, r] = shape(cpu / hosts + (e.name === "Production" ? edgeCpu : 0), ram / hosts + (e.name === "Production" ? edgeRam : 0));
      servers.push({ role: "Application host", count: hosts, vcpu: c, ram: r, disk: a.database === "embedded" ? roundUp(dbStorage * e.storage + 100, 100) : 150,
        note: `Web, API and nightly ingest${e.ha ? ", active/active behind the load balancer" : ""}` });
      if (dbHere) {
        const [dc, dRam] = shape(dbCpu * e.scale, dbRam * e.scale);
        servers.push({ role: "PostgreSQL 18 host (Patroni)", count: dbCount, vcpu: dc, ram: dRam, disk: dbDisk, note: e.ha ? "Primary and synchronous standby, NVMe, RAID 1" : "NVMe, RAID 1" });
      }
    }
    if (a.database === "provide" && a.platform === "k8s") {
      servers.push({ role: "PostgreSQL 18 (CloudNativePG)", count: dbCount, vcpu: Math.ceil(dbCpu * e.scale), ram: Math.ceil(dbRam * e.scale), disk: dbDisk, note: "Runs inside the namespace quota above" });
    }
    if (e.name === "Production" && opsCpu > 0) {
      const [oc, or] = shape(opsCpu, opsRam);
      servers.push({ role: "Operations host", count: 1, vcpu: oc, ram: or, disk: roundUp(opsDisk, 100),
        note: provided.filter((b) => ["monitoring", "siem", "registry", "storage", "backup", "proxy"].includes(b.id)).map((b) => b.label).join(", ") });
    }
    return { name: e.name, servers, quota };
  });

  const physical = envs.flatMap((e) => e.servers.filter((s) => s.count > 0 && !s.role.includes("CloudNativePG")));
  const totals = {
    vcpu: envs.reduce((n, e) => n + (e.quota ? e.quota.vcpu : 0), 0) + physical.reduce((n, s) => n + s.count * s.vcpu, 0),
    ram: envs.reduce((n, e) => n + (e.quota ? e.quota.ram : 0), 0) + physical.reduce((n, s) => n + s.count * s.ram, 0),
    storage: envs.reduce((n, e) => n + (e.quota ? e.quota.storage : 0), 0) + physical.reduce((n, s) => n + s.count * s.disk, 0)
      + (dbOwner === "yours" ? dbStorage : 0) + (has("storage") ? landing : 0),
    servers: physical.reduce((n, s) => n + s.count, 0),
  };

  // ---- diagram nodes
  const idpLabel = { entra: "Microsoft Entra ID", okta: "Okta", adfs: "AD FS / Ping", keycloak: "Keycloak", local: "Demo sign-in" }[a.identity];
  const nodes: Node[] = [
    { id: "users", lane: "people", label: `${{ pilot: "≤50", dept: "50–250", ent: "250–1,000", large: "1,000+" }[a.users]} people`, sub: "16 personas", owner: "yours" },
    { id: "idp", lane: "people", label: idpLabel, sub: a.identity === "local" ? "Pilot only" : "Single sign-on", owner: a.identity === "local" ? "provided" : "yours" },
    ...["firewall", "waf", "lb", ...(k8s ? ["gateway"] : []), "pki"].map((id) => {
      const b = BLOCKS.find((x) => x.id === id)!;
      const product = has(id) ? productFor(id) : null;
      return { id, lane: "edge" as const, label: product ?? b.label.replace("Internal certificate authority", "Certificates"), sub: has(id) ? "Yours, reused" : "We provide", owner: (has(id) ? "yours" : "provided") as Owner };
    }),
    { id: "web", lane: "platform", label: "CostIQ web", sub: "Next.js · :3000", owner: "provided", count: webReplicas },
    { id: "api", lane: "platform", label: "CostIQ API", sub: "FastAPI · :8080", owner: "provided", count: apiReplicas },
    { id: "ingest", lane: "platform", label: "Ingest and allocation", sub: "Nightly, 06:00", owner: "provided" },
    { id: "db", lane: "data", label: engine.replace(" (yours)", ""), sub: dbLayout, owner: dbOwner, count: a.database === "embedded" ? 1 : (ha ? 2 : 1) + (dr ? 1 : 0) },
    { id: "storage", lane: "data", label: "Landing zone", sub: `${landing} GB · ${has("storage") ? "your storage" : "SeaweedFS"}`, owner: has("storage") ? "yours" : "provided" },
    { id: "backup", lane: "data", label: "Backups", sub: `${backups} GB · ${has("backup") ? "your tool" : k8s ? "Barman Cloud + Velero" : "pgBackRest + Velero"}`, owner: has("backup") ? "yours" : "provided" },
    ...["monitoring", "siem", "registry", "vault", "cicd", "edr", "pam", "itsm"].map((id) => {
      const b = BLOCKS.find((x) => x.id === id)!;
      return { id, lane: "operate" as const, label: b.label, sub: has(id) ? "Yours, reused" : "We provide", owner: (has(id) ? "yours" : "provided") as Owner };
    }),
  ];
  if (a.egress === "proxy") nodes.push({ id: "proxy", lane: "edge", label: "Outbound proxy", sub: has("proxy") ? "Yours, reused" : "We provide", owner: has("proxy") ? "yours" : "provided" });
  for (const s of SOURCES.filter((x) => a.sources.includes(x.id))) nodes.push({ id: `src-${s.id}`, lane: "sources", label: s.label.split(" (")[0], sub: s.cadence, owner: "yours" });

  // ---- network rules (MVP ports: web 3000, API 8080; Helm network policy: DNS, database, allow-listed 443)
  const dbPort = a.database === "other" ? "1433 (SQL Server) or 9440 (ClickHouse, TLS)" : a.database === "embedded" ? "none (local file)" : "5432 (TLS required)";
  const storagePort = "2049 NFS, 445 SMB or 443 S3";
  const rules: Rule[] = [
    { from: "Corporate users", to: has("waf") ? "Your WAF" : "CostIQ edge (Coraza WAF)", port: "443", purpose: "HTTPS to costiq.<your domain>" },
    { from: k8s ? (has("gateway") ? "Your gateway or routes" : "Envoy Gateway") : "Load balancer", to: "CostIQ web", port: "3000", purpose: "Web app (internal)" },
    { from: "CostIQ web", to: "CostIQ API", port: "8080", purpose: "API calls through the web app; the browser never calls the API" },
    { from: "CostIQ API and ingest", to: a.database === "provide" ? "PostgreSQL 18 (ours)" : "Your database", port: dbPort, purpose: "Application data" },
    { from: "CostIQ ingest", to: has("storage") ? "Your landing zone" : "SeaweedFS landing zone", port: storagePort, purpose: "Read billing exports (read-only identity)" },
  ];
  if (a.database === "provide" && ha) {
    rules.push({ from: "PostgreSQL primary", to: "PostgreSQL standby", port: k8s ? "5432" : "5432, and 8008 for the Patroni API", purpose: "Synchronous replication and failover" });
  }
  if (dr) rules.push({ from: "Production site", to: "DR site", port: "5432 or 443 to the WAL archive", purpose: "Asynchronous replication or WAL shipping" });
  if (!k8s && !has("lb") && ha) rules.push({ from: "Load balancer pair", to: "Load balancer pair", port: "VRRP (IP protocol 112)", purpose: "keepalived failover of the virtual IP" });
  if (a.identity !== "local") {
    const idpHost = { entra: "login.microsoftonline.com (or login.microsoftonline.us for US Government)", okta: "<your-org>.okta.com", adfs: "your AD FS / Ping host", keycloak: "your Keycloak host", local: "" }[a.identity];
    rules.push({ from: "CostIQ API", to: idpHost, port: "443", purpose: "OpenID configuration and signing keys (JWKS) to validate sign-ins",
      when: a.hosting === "isolated" && (a.identity === "entra" || a.identity === "okta")
        ? "Isolated network: the keys rotate without notice, so allow this one endpoint through an approved path or federate an on-prem IdP (AD FS or Keycloak)."
        : a.egress !== "direct" && (a.identity === "entra" || a.identity === "okta") ? "Through your outbound proxy" : undefined });
  }
  if (a.egress !== "none") {
    for (const src of SOURCES.filter((x) => a.sources.includes(x.id) && x.endpoint)) {
      rules.push({ from: a.egress === "proxy" ? "CostIQ ingest via proxy" : "CostIQ ingest", to: src.endpoint!, port: "443", purpose: `${src.label} ${src.kind === "ai" ? "usage and cost reports" : "billing export"}` });
    }
  }
  rules.push({ from: k8s ? "Cluster nodes" : "CostIQ hosts", to: has("registry") ? "Your registry" : "Harbor (ours)", port: "443", purpose: "Pull signed images" });
  rules.push({ from: "CostIQ hosts", to: "Your DNS and NTP servers", port: "53 UDP/TCP, 123 UDP", purpose: "Name resolution and time sync (timestamps in the audit ledger)" });
  rules.push({ from: "CostIQ hosts", to: has("siem") ? "Your SIEM collector" : "Loki (ours)", port: has("siem") ? "6514 syslog over TLS, or your HTTPS collector" : "3100", purpose: "Application and audit logs" });
  rules.push({ from: has("monitoring") ? "Your monitoring" : "Prometheus (ours)", to: "CostIQ API", port: "8080", purpose: "Health checks (/api/health)" });
  rules.push({ from: a.operations === "client" ? "Your admin jump host" : "Your jump host (vendor sessions, time-bound)", to: k8s ? "Cluster API" : "CostIQ hosts", port: k8s ? "6443" : "22", purpose: "Administration and upgrades" });

  // ---- software (versions current as of Nov 2026; each release pins exact versions)
  const distroNote = { openshift: "Routes or Gateway API; images run under restricted-v2 with an arbitrary UID", aks: "App Routing on Gateway API",
    eks: "AWS Load Balancer Controller", gke: "GKE Gateway", rke2: "Traefik ingress (default from RKE2 1.36)", other: "Any CNCF-conformant cluster" }[a.distro];
  const software: Plan["software"] = [
    { name: "CostIQ API image", version: "1.x", note: "Python 3.12, FastAPI; signed with cosign 3; SBOM attached as a signed attestation" },
    { name: "CostIQ web image", version: "1.x", note: "Node 24, Next.js 16 standalone server; signed with cosign 3" },
    k8s ? { name: "CostIQ Helm chart", version: "1.x", note: "Restricted pod security, non-root, read-only root filesystem, default-deny network policy" }
      : { name: "CostIQ Compose bundle", version: "1.x", note: "Systemd-managed Compose project with health checks" },
  ];
  if (k8s) {
    software.push({ name: "Kubernetes", version: "1.35 or 1.36", note: distroNote });
    software.push({ name: "Signed-image admission policy", version: "Kyverno 1.19", note: "Refuses unsigned CostIQ images; or your existing policy engine" });
  } else {
    software.push({ name: "Operating system", version: "RHEL 10 or Ubuntu 24.04 LTS", note: "CIS Benchmark hardened; SELinux or AppArmor enforcing" });
    software.push({ name: "Docker Engine with the Compose plugin", version: "current", note: "Supported on RHEL and Ubuntu; Podman if your standard requires it" });
  }
  if (a.database === "provide") software.push({ name: "PostgreSQL", version: "18", note: k8s ? "CloudNativePG 1.30 operator; Barman Cloud plugin for WAL archiving and point-in-time recovery" : "Patroni 4 for automatic failover; pgBackRest 2.60 for backups and point-in-time recovery" });
  if (a.database === "pg") software.push({ name: "PostgreSQL (yours)", version: "16 or later, 18 recommended", note: "PostgreSQL 14 reached end of life on Nov 12, 2026; 15 ends in Nov 2027" });
  if (a.database === "other") software.push({ name: "SQL Server or ClickHouse (yours)", version: "your supported version", note: "One database, a schema-owner role and an application role" });
  if (a.database === "embedded") software.push({ name: "DuckDB (embedded)", version: "bundled", note: "Single file on the application host; pilots only" });
  for (const blk of provided) {
    if (blk.id === "lb") software.push(k8s ? { name: "MetalLB", version: "0.16", note: "Only on bare-metal clusters without a load balancer" } : { name: "HAProxy with keepalived", version: "HAProxy 3.2 LTS", note: "Active/passive pair with a virtual IP" });
    else if (blk.product) software.push({ name: blk.product, version: blk.version ?? "current", note: blk.provided });
  }
  if (a.hosting === "isolated") {
    software.push({ name: "Offline install bundle", version: "per release",
      note: `${a.distro === "openshift" && k8s ? "Mirrored with oc-mirror v2" : "A Zarf or Hauler package"} with images, chart, signatures and SBOMs; verified offline with a key-based cosign signature` });
  }

  // ---- identity (Playbook P5/P6)
  const identity = a.identity === "local" ? ["Demo sign-in (persona picker) for the pilot only; it has no passwords. Move to your SSO before wider use."] : [
    `Two app registrations in ${idpLabel}: "CostIQ API" (audience) and "CostIQ" (web, single-page app with your redirect URI).`,
    "One app role per persona (16: CFO, controller, tax analyst, GxP compliance, FinOps analyst, audit committee and the rest); users and groups assigned to roles.",
    "One security group per cost center that needs scoped access; their object IDs map to cost centers. Entity-scoped groups for each country affiliate's finance team.",
    "Assignment required, so only assigned users can sign in; tokens carry a roles claim and a groups claim.",
    "Hand-over to the delivery team: tenant or issuer, API client ID, web client ID. No secrets.",
  ];

  // ---- data connectors (Playbook P7/P8)
  const connectors = SOURCES.filter((s) => a.sources.includes(s.id)).map((s) => ({
    label: s.label,
    method: s.kind === "ai" && a.egress === "none" ? "Monthly usage export file, dropped in the landing zone by the vendor admin" : s.how,
    cadence: s.kind === "ai" && a.egress === "none" ? "Monthly" : s.cadence,
    owner: s.kind === "cloud" ? "Cloud account owner" : s.kind === "saas" ? "Accounts payable" : "AI vendor admin",
  }));
  if (a.egress === "none" && a.sources.some((s) => SOURCES.find((x) => x.id === s)?.kind === "cloud")) {
    notes.push("With no outbound access, a scheduled copy job you already run (AzCopy, rclone or a data-factory pipeline) moves the provider files into the landing zone each day.");
  }

  // ---- compliance controls (requirement numbers checked against the primary texts, Nov 2026)
  const phiMode = a.compliance.includes("hipaa-gdpr") && a.phi !== "no";
  const C: Record<string, string[]> = {
    gxp: [
      "Scope: CostIQ is not a GxP computerized system. It reads billing data, not clinical, manufacturing or safety records, so it stays outside your validated estate.",
      "If your quality unit classifies it anyway, a risk-based approach (GAMP 5 Second Edition, FDA Computer Software Assurance) keeps testing proportionate; we supply requirements, risk assessment and test evidence.",
      "Audit trail: approvals and exports are time-stamped, attributable and kept in a hash-chained ledger, in line with 21 CFR 11.10(e) and EU GMP Annex 11 section 9.",
      "Supplier quality: a quality agreement with change notification, audit rights and data-integrity (ALCOA+) commitments.",
      `Audit logs of sign-ins, approvals and exports go to ${has("siem") ? "your SIEM" : "Loki"} and are kept ${retentionLabel}.`,
    ],
    "hipaa-gdpr": phiMode ? [
      "Patient-data handling on: identifiers are tokenized at ingest with keys held in your HSM or KMS; CostIQ never stores them in clear.",
      "HIPAA Security Rule (45 CFR 164.312): unique user IDs, audit controls, integrity and transmission security through SSO, the ledger and TLS.",
      "GDPR: EU personal data stays in the EU region you choose; a record of processing (Article 30) and a data processing agreement come with the deployment.",
    ] : [
      "Cost data names workloads and studies, not patients; tags are checked for identifiers in the data review.",
      "No clinical or safety records are ever ingested; only billing and usage data.",
    ],
    sox: a.ledger === "gl" ? [
      "IT general controls evidence across the commonly used domains: access to programs and data, program changes and computer operations (audited under PCAOB AS 2201).",
      "Access: SSO roles per persona, a quarterly user-access review export, and separation of duties enforced in the app (a submitter can't approve).",
      "Change: every release is signed and promoted through your pipeline with approvals; the change log ties to release digests.",
      "Operations: job, backup and restore-test evidence, plus completeness checks on the reports that post to the GL.",
      "Audit trail: approvals, exports and evidence packs are written to a hash-chained ledger anyone can verify.",
    ] : [
      "CostIQ output doesn't post to your general ledger, so it's usually outside ITGC scope. Access, change and backup evidence is still available on request.",
    ],
    "nist-csf": [
      "Govern (GV.SC): supplier risk managed through the vendor pack, SBOM and contract terms.",
      "Identify: data inventory and data-flow diagram from this blueprint.",
      "Protect: SSO with MFA, least-privilege roles, TLS in transit and encryption at rest.",
      "Detect (DE.CM): logs to your SIEM and health alerts to your monitoring.",
      "Respond and Recover (RC.RP): runbooks, 3-2-1 backups and tested restores.",
    ],
    "nist-800-53": [
      "AC-2, AC-6 and IA-2(1)/(2): accounts, least privilege and multi-factor sign-in through your IdP.",
      `AU-2, AU-9 and AU-11: event logging, audit information protected by the hash-chained ledger, and ${retentionLabel} retention.`,
      "CM-3 and CM-8: controlled changes and a component inventory (the SBOM).",
      "SI-7(1), SR-3 and SR-11: integrity checks and component authenticity through signed images and SBOMs. SR-4 Provenance sits outside the Moderate baseline; the provenance attestations cover it anyway.",
      "SC-8, SC-12, SC-13 and SC-28: TLS, key management, approved cryptography and encryption at rest.",
      "CP-9: system backup with tested restores.",
    ],
    "iso-27001": [
      "5.15 Access control: personas mapped to IdP roles and groups.",
      "5.19 to 5.22 Supplier relationships: vendor pack, SBOM and change notices.",
      "5.34 Privacy and protection of PII: pseudonymized employee data.",
      "8.8 Management of technical vulnerabilities: disclosure policy and patched releases.",
      "8.13 Information backup, 8.15 Logging, 8.24 Use of cryptography, 8.25 Secure development life cycle, 8.32 Change management.",
      "Certificates must be on the 2022 edition; the 2013 transition ended Oct 31, 2025.",
    ],
    "cisa-cpg": [
      "1.D Supply-chain incident reporting and vulnerability disclosure: published disclosure policy and CVE practice.",
      "3.D Revoke credentials for departing staff and 3.F MFA: both enforced by your IdP; CostIQ has no local accounts in production.",
      "3.K Strong encryption: TLS 1.2+ and AES-256 at rest" + (a.fips !== "no" ? ", with FIPS 140-3 validated modules." : "."),
      "3.N Change management, 3.O Backups and restoration, 3.Q Log collection and storage: covered by the release, backup and logging plans in this blueprint.",
    ],
    privacy: [
      "Employee identifiers are pseudonymized with an HMAC key you hold; treat them as personal information all the same.",
      "California employees are covered by the CCPA (the employment exemption expired Dec 31, 2022), so access and deletion requests apply to their data.",
      "Raw exports in the landing zone expire after 13 months by default; retention is configurable per data set.",
    ],
  };
  const controls = FRAMEWORKS.filter((f) => a.compliance.includes(f.id)).map((f) => ({ framework: f.label, items: C[f.id] }));

  // ---- assurance pack (what pharma supplier and quality reviews ask for)
  const assurance = [
    "SBOM for every release meeting CISA's 2026 minimum elements: signed, with component hashes, licences and generation context.",
    "SLSA v1.2 build provenance and a cosign signature for every image; verification steps and SHA-256 digests in the release notes.",
    "Answers to your supplier quality questionnaire and a standard security questionnaire (SIG or CAIQ).",
    "A quality agreement covering change notification, audit rights, incident notice, vulnerability documentation within 30 days, SBOM and file hashes.",
    "Published vulnerability disclosure policy and CVE practice (CISA CPG 2.0 goal 1.D).",
    a.fips === "required" ? "FIPS 140-3: hosts and nodes run in FIPS mode and use validated cryptographic modules; FIPS 140-2 modules are historical after Sep 21, 2026."
      : a.fips === "preferred" ? "FIPS 140-3 validated modules wherever your OS provides them; FIPS mode can be turned on later without a reinstall."
      : "Standard cryptography: TLS 1.2 or later in transit and AES-256 at rest.",
  ];
  if (a.operations !== "client") assurance.push("Support staff access is time-bound through your jump host; named staff in your region on request.");

  // ---- operations
  const operations = [
    { label: "Node failure", value: ha ? "Automatic failover to the synchronous standby; no committed data is lost (RPO 0)" : "Restore on a new host from the last backup" },
    { label: "Site failure", value: dr ? "Fail over to the DR site; at most about 5 minutes of data at risk (WAL archiving)" : "Restore from backup in the same or another site" },
    { label: "Recovery point (RPO)", value: dr ? "0 for a node failure; 5 minutes or less for a site loss" : ha ? "0 for a node failure; point-in-time recovery to any moment from the WAL archive" : "24 hours (nightly backup)" },
    { label: "Recovery time (RTO) target", value: `${dr ? "Under 4 hours for a site failover" : ha ? "Under 1 hour" : "Next business day"}, confirmed in a failover drill before go-live` },
    { label: "Backups", value: `3-2-1: 30 daily and 12 monthly copies, one offsite and immutable, restores tested quarterly; ${has("backup") ? "in your backup platform" : "Barman Cloud or pgBackRest, and Velero"} (${backups} GB)` },
    { label: "Monitoring", value: has("monitoring") ? "Your monitoring checks /api/health and container metrics" : "Prometheus, Grafana and Alertmanager (provided)" },
    { label: "Audit logs", value: `Kept ${retentionLabel}: about ${logsPerDay} GB a day, ${logsStored} GB stored, ${has("siem") ? "in your SIEM" : "in Grafana Loki with Alloy collectors (provided)"}` },
    { label: "Upgrades", value: `Monthly signed releases, applied ${has("cicd") ? "through your pipeline" : "from the admin jump host"} after the test environment` },
    { label: "Who runs it", value: { client: "Your IT team, with runbooks and training", managed: "Miracle Minds, through your jump host with time-bound access", shared: "Your team operates; Miracle Minds upgrades and tunes" }[a.operations] },
  ];

  // ---- team (client effort in person-days; Playbook roles)
  const gxp = a.compliance.includes("gxp"), strict = gxp || a.compliance.includes("nist-800-53") || a.fips === "required";
  const team: Plan["team"] = [
    { role: "Platform team", side: "Client", days: a.platform === "k8s" ? 3 : a.platform === "vms" ? 5 : 8, does: a.platform === "k8s" ? "Namespace, quota, ingress, registry access" : a.platform === "vms" ? "Provision and patch the VMs" : "Rack, cable and image the servers" },
    { role: "Network and security", side: "Client", days: 2 + (strict ? 3 : 0) + (has("firewall") ? 1 : 0), does: `${rules.length} firewall rules, security review${strict ? ", control evidence" : ""}` },
    { role: "Identity team", side: "Client", days: a.identity === "local" ? 0 : a.identity === "entra" ? 1.5 : 2.5, does: "App registrations, 16 app roles, cost-center groups" },
    { role: "Database team", side: "Client", days: a.database === "pg" ? 1 : a.database === "other" ? 2 : 0.5, does: a.database === "provide" || a.database === "embedded" ? "Review backup and access design" : "Database, two roles, TLS, backups" },
    { role: "Cloud, SaaS and AI owners", side: "Client", days: Math.max(0.5, a.sources.length * 0.5), does: `${a.sources.length} billing and usage exports` },
    { role: "Finance (FP&A, controller)", side: "Client", days: 3 + ENTITY_EXTRA[a.entities] * 2 + (a.entities === "few" ? 1 : 0), does: "Cost-center hierarchy, allocation policies, reconciliation sign-off" },
    ...(a.compliance.length ? [{ role: "Compliance and audit", side: "Client" as const, days: a.compliance.length * 1.5, does: `Control evidence for ${a.compliance.length} framework${a.compliance.length > 1 ? "s" : ""}` }] : []),
    { role: "Solution architect", side: "Miracle Minds", days: 6 + (strict ? 3 : 0), does: "Architecture, security pack, sizing confirmation" },
    { role: "DevOps engineer", side: "Miracle Minds", days: (a.platform === "k8s" ? 6 : 9) + provided.length, does: `Install, ${provided.length} provided components, monitoring hooks` },
    { role: "Data engineer and FinOps analyst", side: "Miracle Minds", days: 5 + a.sources.length * 1.5 + ENTITY_EXTRA[a.entities] * 2, does: "Connectors, allocation policies, conformance tests" },
  ];

  // ---- development plan (weeks): six phases, each closed by a signed exit gate. Onboarding inside the client's estate is
  // usually the critical path (Playbook), so accounts and access come first.
  const missing = provided.length;
  const offline = a.hosting === "isolated" || a.route === "bundle";
  const many = a.entities === "many" || a.entities === "lots";
  const phases: Omit<Plan["timeline"][number], "start">[] = [
    { phase: "Mobilize inside your estate", weeks: 2, detail: "Identities, desktops, repositories and pipeline in your tenancy",
      work: ["Client-issued identities, MFA and virtual desktops for every engineer", a.custody === "drops" ? "An isolated repository on our side, synthetic data only" : "Repositories, pipeline, registry and secrets created in your tenancy",
        "Branch rules: two reviewers, code owners, signed commits", "Owners named for every prerequisite below"],
      exit: ["Every engineer signs in with your identity and MFA; no shared accounts", a.custody === "drops" ? "Isolated repository and drop procedure approved by your security team" : "Zero artifacts hosted on our systems",
        "A skeleton pipeline passes every quality gate", "Security contact and change process confirmed"],
      signoff: "Your delivery lead and security delegate" },
    { phase: "Discovery and secure design", weeks: 2 + (strict ? 1 : 0) + (a.hosting === "isolated" ? 1 : 0), detail: "Architecture, threat model, data flows, your security questionnaire",
      work: ["Architecture decision records and data-flow diagram", "Threat model with every mitigation tracked as a backlog item", "OWASP ASVS 5.0 Level 2 requirements chosen for the backlog",
        f5 || cisco ? `${[f5 && "F5", cisco && "Cisco"].filter(Boolean).join(" and ")} change requests raised with your network team` : "Firewall, DNS and certificate requests raised with your teams"],
      exit: ["Architecture review board approval", "Threat model reviewed by your security team; no unowned high risks", "Data classification approved by each data owner", "Recovery, performance and accessibility targets agreed in writing"],
      signoff: "Your enterprise and security architects" },
    { phase: "Foundations in non-production", weeks: (k8s ? 2 : a.platform === "vms" ? 3 : 4) + (missing >= 8 ? 1 : 0) + (offline && a.transfer !== "server" ? 0.5 : 0) + (a.custody === "rebuild" ? 0.5 : 0),
      detail: `${runtime}; the pipeline and every gate first`,
      work: ["Pipeline with every quality gate switched on", "Infrastructure as code for every environment", "Signature verification at admission", a.testdata === "masked" ? "Masking job built inside production, rules approved by the data owner" : "Synthetic data set at production scale"],
      exit: ["Non-production is deployed only by the pipeline", "Every image signed, with provenance and an SBOM carrying all 17 CISA 2026 fields", "No critical or high vulnerability with a fix available", "A restore from backup tested"],
      signoff: "Your platform owner" },
    { phase: "Build, connect and configure", weeks: 4 + Math.max(0, a.sources.length - 2) * 0.5 + (many ? 1 : 0), detail: `${a.sources.length} sources connected, allocation policies, extensions in sprints`,
      work: [`${a.sources.length} data sources, one month each, totals matched to provider consoles`, "Allocation policies, tax-risk tests and dashboards configured", "Two-week sprints with a demo to finance, tax, quality and IT", "Runbooks written alongside the code"],
      exit: ["Every increment accepted by your product owner", "At least 80% test coverage on new code; every API endpoint contract-tested", "No high dynamic-scan findings", "Use-case conformance and reconciliation signed off by finance"],
      signoff: "Your product owner and controller" },
    { phase: "Hardening and independent assurance", weeks: 3 + (strict ? 1 : 0) + (dr ? 0.5 : 0), detail: "Load test, penetration test, recovery drill",
      work: ["User acceptance testing", "Load test at twice the expected volume", "Independent penetration test (NIST SP 800-115, CREST-accredited testers)", `${dr ? "Site failover" : "Backup restore"} drill`],
      exit: ["No open critical or high penetration-test findings after retest", "ASVS Level 2 verification report delivered", "Recovery point and time objectives met in the drill", "Change advisory board approves the production change"],
      signoff: "Business owners, CISO delegate and change board" },
    { phase: "Go-live and handover", weeks: 1 + (dr ? 1 : 0) + (a.environments === "prod" ? 0 : 0.5), detail: `${CUTOVER_NAME[a.cutover]} cutover, then 30 days of hypercare`,
      work: [`${CUTOVER_NAME[a.cutover]} cutover through your change management`, "Thirty days of hypercare with daily checks", "Shadow, then reverse-shadow, on-call with your team", "Handover pack: runbooks, decision records, threat model, SBOMs"],
      exit: ["First month-end close reconciled in CostIQ", "Your team has run every runbook at least once", a.operations === "client" ? "Our access revoked on the agreed date" : "Ongoing access limited to your PAM, time-bound", "Handover acceptance signed; warranty starts"],
      signoff: "Your service owner and operations lead" },
  ];
  let t = 0;
  const timeline = phases.map((p, i) => {
    const start = Math.max(0, t - (i === 3 ? 1 : 0)); // building starts in the last week of foundations
    t = start + p.weeks;
    return { ...p, start };
  });
  const weeks = Math.ceil(t);

  // ---- prerequisites (Playbook P1-P14, adjusted to the answers)
  const wk = (phase: number) => Math.max(1, Math.round(timeline[phase].start) + 1);
  const pre: Plan["prerequisites"] = [
    { id: "P1", item: "Named accounts with MFA (VDI or managed laptop) for each engineer", owner: "IT identity", status: "todo", week: 1 },
    { id: "P2", item: a.platform === "k8s" ? "Non-production namespace with restricted pod security, ingress and TLS" : `${a.platform === "vms" ? "VMs" : "Servers"} for non-production, CIS-hardened`, owner: "Platform", status: "todo", week: wk(2) },
    { id: "P3", item: has("registry") ? "Repository path in your registry (with OCI referrers for signatures and SBOMs), push and pull rights" : "Space for the Harbor registry on the operations host", owner: "Platform", status: has("registry") ? "reuse" : "todo", week: wk(1) },
    { id: "P4", item: has("vault") ? "Image-signing key and secrets in your vault" : "Signing key for cosign (we set up encrypted secrets)", owner: "Security", status: has("vault") ? "reuse" : "todo", week: wk(1) },
    { id: "P5", item: a.database === "pg" || a.database === "other" ? "Database with TLS, a schema-owner role and an application role" : "Storage for the database we provide", owner: "Database", status: "todo", week: wk(1) },
    { id: "P6", item: a.identity === "local" ? "Pilot users list (demo sign-in)" : `Two app registrations and 16 app roles in ${idpLabel}`, owner: "Identity", status: "todo", week: wk(1) },
    { id: "P7", item: "Security groups for cost-center and entity scope", owner: "Identity and finance", status: "todo", week: wk(2) },
    { id: "P8", item: `FOCUS exports for ${a.sources.filter((s) => SOURCES.find((x) => x.id === s)?.kind === "cloud").length || "no"} cloud provider(s) into the landing zone`, owner: "Cloud owners", status: "todo", week: wk(3) },
    { id: "P9", item: "Read-only reporting access to AI vendors", owner: "SaaS owners", status: "todo", week: wk(3) },
    { id: "P10", item: a.egress === "none" ? "Approval for file-drop exports instead of API calls" : `Outbound ${a.egress === "proxy" ? "proxy" : "firewall"} rules to provider endpoints`, owner: "Network", status: a.egress === "proxy" && has("proxy") ? "reuse" : "todo", week: wk(3) },
    { id: "P11", item: "Cost-center hierarchy, budgets and three months of invoices", owner: "Finance", status: "todo", week: wk(3) },
    { id: "P12", item: has("firewall") ? "Firewall change for the rules in this blueprint" : "Approval for host firewalls and network policy", owner: "Network and security", status: has("firewall") ? "reuse" : "todo", week: wk(1) },
    { id: "P13", item: `Production ${a.platform === "k8s" ? "namespace" : "hosts"}, database and DNS name${dr ? " in both sites" : ""}`, owner: "Platform and database", status: "todo", week: wk(4) },
    { id: "P14", item: has("itsm") ? "A standard-change template for CostIQ releases in your ITSM" : "Change-management slot and support model for go-live", owner: "IT operations", status: has("itsm") ? "reuse" : "todo", week: wk(4) },
  ];
  if (offline) pre.push({ id: "P15", item: `Transfer path for the signed offline bundle (${{ server: "approved transfer server", diode: "data diode", media: "media kiosk" }[a.transfer]})`, owner: "Security", status: "todo", week: wk(2) });
  if (f5) pre.push({ id: "P17", item: "F5 change: virtual server, pools, health monitor and WAF policy for CostIQ (configuration in this blueprint)", owner: "Network (F5)", status: "reuse", week: wk(2) });
  if (cisco) pre.push({ id: "P18", item: "Cisco change: access rules, FQDN objects if needed, Snort 3 policy and syslog for CostIQ", owner: "Network security (Cisco)", status: "reuse", week: wk(2) });
  if (a.soc !== "none") pre.push({ id: "P19", item: "SOC onboarding: CostIQ log source, detection rules and the joint incident playbook", owner: "Security operations", status: "todo", week: wk(3) });
  if (a.hosting === "isolated" && (a.identity === "entra" || a.identity === "okta")) {
    notes.push("Your sign-in provider is a cloud service and its signing keys rotate without notice, so an isolated network needs one approved path to it, or an on-prem IdP (AD FS or Keycloak) federated to it.");
  }
  if (k8s && a.database === "provide") notes.push("CloudNativePG 1.30 supports Kubernetes 1.34 to 1.36; we match the operator to your cluster version.");
  if (!has("storage")) notes.push("Your own NFS, SMB or S3-compatible storage is the better landing zone; the bundled SeaweedFS store is a fallback.");
  if (a.placement === "shared-corp") notes.push("A shared corporate cluster is fine, as long as it never also hosts validated GxP systems.");
  if (phiMode) pre.push({ id: "P16", item: "Key custody: your HSM or KMS holds the tokenization and encryption keys for patient identifiers", owner: "Security and data privacy", status: has("vault") ? "reuse" : "todo", week: wk(1) });
  if (phiMode) notes.push(a.phi === "yes" ? "Patient-data handling is on: identifiers tokenized at ingest with keys you hold, EU data kept in the EU region." : "Patient-data handling stays on until a data review confirms cost tags hold no patient identifiers.");
  if (gxp) notes.push("Keep CostIQ outside your validated GxP estate; it reads billing data, not GxP records.");
  if (!has("firewall")) notes.push("We strongly recommend placing CostIQ behind a perimeter firewall; host firewalls and network policy are the minimum we deploy.");
  if (a.testdata === "prod-uat" && (a.cutover === "rolling" || a.cutover === "window")) {
    notes.push("Final acceptance in production needs the new version running beside the old one; that comes with a blue/green or canary cutover.");
  }

  // ---- the client's own F5 and Cisco devices (de facto)
  const edgeConfig: DeviceConfig[] = [
    ...(cisco ? [CISCO_FW] : []),
    ...(f5 && has("waf") ? [F5_WAF] : []),
    ...(f5 && has("lb") ? [F5_LTM] : []),
    ...(f5 && k8s && has("gateway") ? [F5_CIS] : []),
  ];
  const lb = { f5: f5 && has("lb"), via: f5 && has("lb") ? "F5 BIG-IP LTM" : has("lb") ? "Your load balancer" : k8s ? "Envoy Gateway" : "HAProxy pair" };

  // ---- cyber defense: every layer between an attacker and the cost data, and whose it is
  const own = (id: string): Owner => (has(id) ? "yours" : "provided");
  const layers: DefenseLayer[] = [
    { id: "firewall", label: "Perimeter firewall and IPS", owner: own("firewall"), product: cisco ? "Cisco Secure Firewall with Snort 3" : has("firewall") ? "Your firewall" : "Host firewalls (nftables)",
      does: "Only the flows in this blueprint; default deny; no internet egress" },
    { id: "waf", label: "Web application firewall", owner: own("waf"), product: f5 && has("waf") ? "F5 Advanced WAF" : has("waf") ? "Your WAF" : "Coraza with OWASP CRS",
      does: "Positive security from the API specification, attack signatures, DoS and bot defense" },
    { id: "identity", label: "Sign-in", owner: a.identity === "local" ? "provided" : "yours", product: `${idpLabel}${f5 ? ", optionally behind F5 Zero Trust Access" : ""}`,
      does: "Single sign-on with MFA and conditional access; no local passwords in production" },
    { id: "access", label: "Roles and scope", owner: "provided", product: "CostIQ personas and scope",
      does: "16 personas, cost-center and entity scope, separation of duties on approvals" },
    { id: "pam", label: "Privileged access", owner: own("pam"), product: has("pam") ? "Your PAM" : "Break-glass accounts in the vault",
      does: "Just-in-time admin and vendor sessions, recorded" },
    { id: "supply", label: "Signed, scanned releases", owner: has("cicd") ? "yours" : "provided", product: has("cicd") ? "Your pipeline with our gates" : "Our gates, signed with your key",
      does: `${STAGES.reduce((n, s) => n + s.gates.length, 0)} quality gates; unsigned images refused at admission` },
    { id: "segment", label: "Segmentation", owner: "provided", product: k8s ? "Default-deny network policy" : "Host firewalls per role",
      does: "Each component reaches only what it needs; stops lateral movement" },
    { id: "runtime", label: "Runtime detection", owner: own("edr"), product: has("edr") ? "Your EDR" : "Falco",
      does: "Flags shells in containers, privilege escalation and mass encryption" },
    { id: "secrets", label: "Secrets and keys", owner: own("vault"), product: has("vault") ? "Your vault or HSM" : "OpenBao",
      does: `Credentials, pseudonym and signing keys never on disk${a.fips !== "no" ? "; FIPS 140-3 modules" : ""}` },
    { id: "detect", label: "Detection and response", owner: own("siem"), product: `${has("siem") ? "Your SIEM" : "Grafana Loki"}, ${SOC_LABEL[a.soc]}`,
      does: "Detection rules below; joint incident playbook" },
    { id: "backup", label: "Recovery", owner: own("backup"), product: has("backup") ? "Your backup platform, immutable copy" : "Barman or pgBackRest, immutable copy",
      does: "3-2-1 backups with one immutable offsite copy; restores tested quarterly" },
  ];
  const siemName = has("siem") ? "your SIEM" : "Loki";
  const detections: Plan["defense"]["detections"] = [
    { alert: "Ledger hash chain broken", source: "CostIQ audit ledger", severity: "Critical" },
    { alert: "Shell or privilege escalation inside a container", source: has("edr") ? "Your EDR" : "Falco", severity: "Critical" },
    { alert: "Burst of failed sign-ins or impossible travel", source: idpLabel, severity: "High" },
    { alert: "Role or scope granted outside an approved change", source: "CostIQ audit ledger", severity: "High" },
    { alert: "Unsigned or unknown image refused at admission", source: k8s ? "Admission controller" : "Ansible or host verification", severity: "High" },
    { alert: "Spike in WAF violations", source: f5 && has("waf") ? "F5 Advanced WAF" : "WAF", severity: "Medium" },
    { alert: "Outbound connection denied from CostIQ", source: cisco ? "Cisco Secure Firewall" : "Firewall", severity: "Medium" },
    { alert: "Bulk export or evidence pack outside business hours", source: "CostIQ audit ledger", severity: "Medium" },
  ];
  const testingLabel = { golive: "an independent penetration test before go-live", yearly: "an independent penetration test before go-live and every year", release: "a penetration test every major release and every year, plus a purple-team exercise" }[a.testing];
  notes.push(`${detections.length} detection rules feed ${siemName}, watched by ${SOC_LABEL[a.soc]}; CostIQ gets ${testingLabel}.`);

  return {
    answers: a, topology, runtime, nodes, envs, totals,
    reuse: { have: yours.length, total: blocksInScope.length, yours, provided },
    db: { engine, vcpu: dbCpu, ram: dbRam, storage: dbStorage, rowsPerMonth: rows, layout: dbLayout, owner: dbOwner },
    app: { apiReplicas, webReplicas, ingest: `Nightly at 06:00, ${ingCpu} vCPU / ${ingMem} GB while it runs` },
    rules, software, identity, connectors, controls, operations, team, timeline, weeks, prerequisites: pre, assurance,
    logs: { retention: retentionLabel, perDayGb: logsPerDay, storedGb: logsStored }, notes,
    edgeConfig, lb, defense: { layers, soc: SOC_LABEL[a.soc], testing: testingLabel, detections },
  };
}

const CUTOVER_NAME: Record<Answers["cutover"], string> = { bluegreen: "Blue/green", canary: "Canary", rolling: "Rolling", window: "Maintenance-window" };
const SOC_LABEL: Record<Answers["soc"], string> = { "24x7": "your SOC around the clock", hours: "your SOC in business hours", none: "your platform team" };

/** Apply skip rules so derived answers stay consistent. */
export function normalize(a: Answers): Answers {
  const out = { ...a };
  for (const q of QUESTIONS) {
    const s = q.skip?.(out);
    if (s) (out as Record<Key, unknown>)[q.key] = s.value;
    if (q.blocked && !q.multi) {
      const v = out[q.key] as string;
      if (q.blocked(v, out)) {
        const ok = q.options.find((o) => !q.blocked!(o.value, out));
        if (ok) (out as Record<Key, unknown>)[q.key] = ok.value;
      }
    }
  }
  return out;
}
