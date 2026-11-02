// Delivery, quality and defense content for the planner: quality gates, standards, code custody, environments, every
// migration route, cutover strategies, the exact configuration for a client's own F5 and Cisco devices, and the cyber
// defense layers with the attacks they stop. Product names, versions and control IDs were checked in November 2026
// against vendor docs (F5 techdocs and clouddocs, Cisco Secure Firewall docs), NIST, CISA, OWASP, SLSA and MITRE ATT&CK.

export type Gate = { name: string; tool: string; rule: string; std: string };
export type Stage = { id: string; name: string; where: string; gates: Gate[] };
export type Standard = { id: string; name: string; version: string; what: string; how: string; url: string };
export type Lane = { id: "dev" | "qa" | "prod"; name: string; who: string; access: string; promote: string };
export type Step = { label: string; live: number; next: number; check: string };

/* ------------------------------------------------------------------ quality gates */

export const STAGES: Stage[] = [
  { id: "commit", name: "Commit", where: "Developer desktop in your VDI", gates: [
    { name: "Signed commit", tool: "Git commit signing (SSH or gitsign)", rule: "Unsigned commits are rejected by your Git server", std: "SSDF PS.1 · SLSA Source" },
    { name: "Secrets check", tool: "Betterleaks (Gitleaks successor), pre-commit", rule: "No credentials, keys or tokens in the change", std: "SSDF PS.1 · ISO A.8.28" },
    { name: "Format and lint", tool: "Ruff, ESLint, Hadolint", rule: "Zero errors", std: "SSDF PW.5" },
  ] },
  { id: "review", name: "Pull request", where: "Your Git server", gates: [
    { name: "Two-person review", tool: "Branch protection, CODEOWNERS", rule: "Two approvals, one from a code owner; the author cannot approve", std: "SLSA Source L4 · SSDF PW.7" },
    { name: "Static analysis", tool: "Opengrep, CodeQL or SonarQube", rule: "No new critical or high findings; quality gate passed", std: "SSDF PW.7 · ASVS L2" },
    { name: "Types", tool: "mypy strict, TypeScript strict", rule: "Zero type errors", std: "ISO 25010 maintainability" },
    { name: "Unit tests", tool: "pytest, Vitest", rule: "All pass; at least 80% coverage on new code", std: "SSDF PW.8 · ISO A.8.29" },
    { name: "Infrastructure checks", tool: "Checkov, kube-linter", rule: "No high misconfigurations in Helm, Compose or IaC", std: "NIST SP 800-190" },
  ] },
  { id: "build", name: "Build", where: "Ephemeral runner in your CI", gates: [
    { name: "Pinned toolchain", tool: "Your registry mirror", rule: "Scanners and CI actions mirrored and pinned by digest, never by tag", std: "SSDF PO.3" },
    { name: "Hermetic build", tool: "Lockfiles with hashes", rule: "Every dependency pinned by version and hash; no network during build", std: "SLSA Build L3" },
    { name: "SBOM", tool: "Syft or cdxgen (CycloneDX 1.7, SPDX 3.0.1)", rule: "All 17 CISA 2026 minimum fields present", std: "CISA SBOM 2026 · SSDF PS.3" },
    { name: "Provenance", tool: "in-toto attestation", rule: "Records the source commit, builder and every input", std: "SLSA Build L3" },
  ] },
  { id: "test", name: "Test", where: "Ephemeral test environment", gates: [
    { name: "Integration tests", tool: "pytest against PostgreSQL 18", rule: "All pass on a freshly migrated database", std: "SSDF PW.8" },
    { name: "API contract", tool: "Schemathesis on the OpenAPI spec", rule: "No schema violations or server errors", std: "OWASP API Top 10" },
    { name: "Use-case conformance", tool: "CostIQ conformance suite", rule: "All 145 use cases return expected results", std: "ISO 25010 suitability" },
    { name: "Accessibility", tool: "axe-core", rule: "No WCAG 2.2 AA violations", std: "WCAG 2.2 AA" },
  ] },
  { id: "scan", name: "Scan", where: "Your CI", gates: [
    { name: "Dependency vulnerabilities", tool: "OSV-Scanner, Grype", rule: "No critical or high vulnerability with a fix available", std: "SSDF RV.1 · OWASP A03" },
    { name: "Container image", tool: "Trivy or Grype, pinned by digest", rule: "No critical or high with a fix; non-root; minimal base", std: "NIST SP 800-190" },
    { name: "Licences", tool: "ORT or ScanCode", rule: "Only licences on your approved list", std: "SSDF PW.4" },
    { name: "Dynamic scan", tool: "ZAP baseline and API scan", rule: "No high alerts; full scan in QA", std: "ASVS L2 · OWASP Top 10" },
  ] },
  { id: "sign", name: "Sign and release", where: "Your CI, with your key", gates: [
    { name: "Signature", tool: "Sigstore cosign 3, key in your vault or HSM", rule: "Image, SBOM and provenance signed with your key", std: "SSDF PS.2 · CIP-013 R1.2.5" },
    { name: "Version and notes", tool: "Semantic Versioning 2.0", rule: "Tagged release with notes and change list", std: "ISO A.8.32" },
    { name: "Stored by digest", tool: "Your registry", rule: "Pushed and referenced by digest, never by tag", std: "SSDF PS.3" },
  ] },
  { id: "admit", name: "Admit", where: "QA and production", gates: [
    { name: "Signature verified", tool: "Kyverno, Sigstore policy-controller or OpenShift image policy", rule: "Unsigned or unknown images are refused", std: "NIST CM-14 · SI-7(15)" },
    { name: "Approved change", tool: "Your ITSM (ServiceNow or similar)", rule: "Production needs an approved change linked to this build", std: "ISO A.8.32" },
  ] },
];

export const FAIL_AT = {
  stage: "scan", gate: 0,
  message: "A newly added library carries a critical vulnerability (CVSS 9.8) and a fixed version exists. Policy allows none, so the merge is blocked and a fix ticket is opened.",
};

export const DIGEST = "sha256:7f3c…e91a";

export const STANDARDS: Standard[] = [
  { id: "ssdf", name: "NIST Secure Software Development Framework", version: "SP 800-218 v1.1", what: "The secure-development practices US federal software attestation was built on.",
    how: "Every practice group (PO, PS, PW, RV) maps to a gate; the mapping ships with each release. Tracking the v1.2 draft.", url: "https://csrc.nist.gov/projects/ssdf" },
  { id: "slsa", name: "SLSA", version: "v1.2 · Build L3", what: "Supply-chain levels for software artifacts, build and source tracks.",
    how: "Ephemeral, isolated builders and unforgeable provenance; two-person review meets the source track.", url: "https://slsa.dev/spec/v1.2/" },
  { id: "asvs", name: "OWASP ASVS", version: "5.0 · Level 2", what: "Testable security requirements; Level 2 is the target for most applications.",
    how: "Level 2 requirements are backlog items with tests, verified again in the pen test.", url: "https://asvs.owasp.org/" },
  { id: "top10", name: "OWASP Top 10 and API Top 10", version: "2025 · 2023", what: "The most critical web and API risks.",
    how: "Static analysis, contract tests, dynamic scanning and the pen test cover every category.", url: "https://owasp.org/Top10/2025/" },
  { id: "iso27001", name: "ISO/IEC 27001 Annex A", version: "2022 · A.8.25–8.33", what: "Secure development, outsourced development, separated environments, change, test data.",
    how: "Separate environments; your change records; synthetic or masked test data only.", url: "https://www.iso.org/standard/27001" },
  { id: "sp80053", name: "NIST SP 800-53", version: "Rev. 5", what: "SA-3(2) live data in test, CM-4(1) separate test environments, SA-11 developer testing.",
    how: "Production data never enters development; a QA holding masked data is protected at production level.", url: "https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final" },
  { id: "sbom", name: "CISA minimum elements for an SBOM", version: "2026 · 17 fields", what: "Author signature, hashes, licences, tool and generation context.",
    how: "SPDX and CycloneDX SBOMs for every image, signed and delivered with each release.", url: "https://www.cisa.gov/resources-tools/resources/2026-minimum-elements-software-bill-materials-sbom" },
  { id: "sp800190", name: "NIST Application Container Security", version: "SP 800-190", what: "Image, registry, orchestrator and host risks for containers.",
    how: "Minimal non-root images on CIS-hardened hosts, scanned, signed and admitted only by digest.", url: "https://csrc.nist.gov/pubs/sp/800/190/final" },
  { id: "cip013", name: "NERC CIP-013-2", version: "R1.2.5", what: "Verifying software integrity and authenticity, the utility supply-chain standard.",
    how: "CostIQ is not a BES Cyber System, but every release can be verified the way CIP-013 asks.", url: "https://www.nerc.com/pa/Stand/Reliability%20Standards/CIP-013-2.pdf" },
  { id: "iso25010", name: "ISO/IEC 25010", version: "2023", what: "Product quality: suitability, reliability, security, maintainability and more.",
    how: "Each quality characteristic has an automated measure in the pipeline.", url: "https://www.iso.org/standard/78176.html" },
  { id: "pentest", name: "Independent penetration test", version: "NIST SP 800-115", what: "Technical security testing method.",
    how: "Run by CREST-accredited testers; the cadence is the one you chose.", url: "https://csrc.nist.gov/pubs/sp/800/115/final" },
  { id: "wcag", name: "WCAG", version: "2.2 AA", what: "Accessibility; a superset of the Section 508 and ADA Title II baselines.",
    how: "Automated checks on every change plus a manual audit before go-live.", url: "https://www.w3.org/TR/WCAG22/" },
];

/* ------------------------------------------------------------------ code custody */

export const CUSTODY: Record<string, { name: string; short: string; flow: string[]; get: string[]; pros: string[]; cons: string[] }> = {
  "client-repo": { name: "Built in your repositories from day one",
    short: "Our engineers use your identities and commit straight to your Git. There is never a copy to hand over.",
    flow: ["You issue identities and virtual desktops", "We commit to your Git server", "Your CI builds and signs with your key", "Your registry holds every release"],
    get: ["Complete history: every commit, review and pipeline run", "Pipeline definitions and infrastructure as code", "Decision records, threat model and runbooks"],
    pros: ["Nothing to transfer, so nothing lost in transfer", "Full audit trail from line one", "No copy of the code exists on our side"],
    cons: ["Accounts and desktops are on the critical path in week one", "Our engineers depend on your tooling being up"] },
  drops: { name: "Signed code drops at each milestone",
    short: "We work in an isolated repository with no client data and hand over a signed Git bundle at every milestone.",
    flow: ["We develop on synthetic data only", "Each milestone is tagged and signed", "Git bundle and checksums sent through your approved channel", "You run git bundle verify, then import"],
    get: ["Signed Git bundle with full history", "Checksums and our signing public key", "Release notes per milestone"],
    pros: ["Work can start before your access is ready", "Clear acceptance point at each milestone", "Crosses an air gap"],
    cons: ["The master copy sits with us between drops", "Each drop must be verified and merged"] },
  escrow: { name: "Source code escrow",
    short: "An escrow agent (for example Escode or EscrowTech) holds a verified copy and releases it if agreed conditions are met.",
    flow: ["We deposit the source with each release", "The agent verifies the build and compares binaries", "Release conditions written into the contract", "You receive the deposit if a condition triggers"],
    get: ["Verified deposit at each release", "The agent's verification report"],
    pros: ["Familiar to procurement and legal", "Neutral third party"],
    cons: ["Largely redundant when the code is already yours", "Ongoing fees; deposits can go stale"] },
  rebuild: { name: "Independent rebuild check",
    short: "You rebuild each release from source yourselves and compare digests bit for bit; any mismatch blocks promotion.",
    flow: ["We publish source, lockfiles and the build recipe", "You rebuild in your own CI", "Digests are compared bit for bit", "Only a match is promoted"],
    get: ["Build recipe and pinned toolchain", "Expected digest for every image"],
    pros: ["Proves the binary is exactly the reviewed source", "Gives you what escrow build verification would, without escrow"],
    cons: ["Needs a second build path", "Some toolchains need tuning to build deterministically"] },
};

/* ------------------------------------------------------------------ environments and test data */

export const LANES: Lane[] = [
  { id: "dev", name: "Development", who: "Engineers", access: "Engineers, through the pipeline only", promote: "Merge to main with every gate passed" },
  { id: "qa", name: "Test", who: "Testers and business users", access: "Nobody by hand; the pipeline only", promote: "Signed release candidate; test deployment approved" },
  { id: "prod", name: "Production", who: "Your operations team", access: "The pipeline with a change record; emergency access through your PAM", promote: "Acceptance signed, change board approval, two-person release" },
];

export const TESTDATA: Record<string, { name: string; detail: string; lane: string; tone: "synthetic" | "masked" }> = {
  synthetic: { name: "Synthetic data only", lane: "Synthetic", tone: "synthetic",
    detail: "Generated billing data at production scale with the edge cases planted on purpose. No real data ever exists outside production." },
  masked: { name: "Masked copy made inside production", lane: "Masked copy", tone: "masked",
    detail: "A job inside the production boundary replaces names, accounts and cost centres with keyed pseudonyms before anything reaches test. Your data owner approves the rules, and test is then protected at production level (NIST SA-3(2))." },
  "prod-uat": { name: "Final acceptance in production", lane: "Synthetic", tone: "synthetic",
    detail: "Test stays synthetic. The last acceptance pass runs on the new version in production, before users are switched, by authorised users only." },
};

/* ------------------------------------------------------------------ migration routes */

export type Route = { name: string; short: string; flow: string[]; airgap: "yes" | "partial" | "no"; needs: string[]; security: string[]; cons: string[]; fit: string;
  scores: { assurance: number; speed: number; effort: number } };
export const ROUTES: Record<string, Route> = {
  gitops: { name: "GitOps from your Git", airgap: "yes", fit: "Kubernetes or OpenShift",
    short: "Argo CD or Flux inside each environment pulls the approved, signed release from your Git and registry. Nothing pushes into production.",
    flow: ["Your Git", "Your CI", "Your registry", "Argo CD in test", "Argo CD in production"],
    needs: ["Kubernetes or OpenShift (OpenShift GitOps is Argo CD)", "Your Git and registry reachable from the clusters", "A protected branch or folder per environment"],
    security: ["Pull model: no inbound credentials into production", "Every change is a reviewed commit; the audit trail is the Git history", "Flux refuses artifacts whose cosign signature fails; drift is reverted"],
    cons: ["Kubernetes only", "Your platform team owns the GitOps controller"], scores: { assurance: 5, speed: 5, effort: 2 } },
  pipeline: { name: "Your CI/CD builds and deploys", airgap: "yes", fit: "Any platform",
    short: "Your pipeline (GitLab Self-Managed, Azure DevOps Server, Jenkins, GitHub Enterprise Server) builds from source and deploys with approvals per environment.",
    flow: ["Your Git", "Your CI builds", "Your registry", "Approval", "Test", "Approval", "Production"],
    needs: ["A CI/CD platform you already run", "Mirrors for Python, npm and base images inside your network", "Deployment credentials held in your vault"],
    security: ["Built entirely inside your boundary, signed with your key", "Same pipeline, same gates as the rest of your estate", "Approvals and logs stay in your tool"],
    cons: ["The pipeline holds deploy rights into production", "Builds can drift unless you add the rebuild check"], scores: { assurance: 5, speed: 5, effort: 3 } },
  registry: { name: "Signed releases into your registry", airgap: "partial", fit: "Networks with one outbound rule",
    short: "We build and sign; your registry pulls each release through replication or a proxy cache. Your admission policy refuses anything not signed by our key.",
    flow: ["Our build service", "Signed image and SBOM", "|", "Your registry (replication)", "Signature check", "Test", "Production"],
    needs: ["Harbor, Artifactory or Quay with replication or proxy cache", "One outbound rule from your registry to ours", "An admission policy with our public key"],
    security: ["Harbor replication carries the cosign signatures with the image", "Only signed, rescanned images are admitted", "One narrow outbound rule, no inbound access"],
    cons: ["Builds happen outside your boundary (code only, never data)", "Needs that one outbound rule"], scores: { assurance: 4, speed: 4, effort: 1 } },
  bundle: { name: "Signed offline bundle", airgap: "yes", fit: "Isolated networks",
    short: "One signed bundle (Zarf, Hauler or oc-mirror) with every image, chart, signature and SBOM crosses through your transfer server, data diode or scanned media.",
    flow: ["Our build service", "Signed offline bundle", "|", "Your transfer path", "Scan and verify", "Your registry", "Test", "Production"],
    needs: ["A transfer server, one-way diode or media kiosk", "Malware scanning on arrival", "Our public key to verify signatures"],
    security: ["No network path between us and you", "Signatures and SBOMs checked before and after transfer (NIST AC-4)", "Removable media handled per NIST SP 1334"],
    cons: ["A day or two per release", "Your team runs the import"], scores: { assurance: 5, speed: 2, effort: 3 } },
  ansible: { name: "Ansible to hardened VMs", airgap: "yes", fit: "Virtual machines",
    short: "Your Ansible Automation Platform or Satellite verifies each signed image with cosign, then rolls the release out with Compose, host by host.",
    flow: ["Your Git", "Your CI", "Your registry", "Ansible: cosign verify", "Test VMs", "Production VMs"],
    needs: ["Ansible Automation Platform, Satellite or similar", "RHEL hosts on your hardening baseline", "Approval workflow in your automation tool"],
    security: ["Repeatable, reviewed playbooks", "Signature checked on every host before start", "Runs from your automation, with its logs"],
    cons: ["Less self-healing than Kubernetes", "Host-by-host rollout takes longer"], scores: { assurance: 4, speed: 3, effort: 2 } },
  pam: { name: "We deploy through your PAM", airgap: "no", fit: "Networks that allow vendor sessions",
    short: "Our engineer runs the release from your jump host in a time-boxed, recorded session you approve and can end at any time, following the CIP-005-7 R2 vendor-access pattern.",
    flow: ["Access request", "Your approval", "Your PAM jump host with MFA", "Recorded session", "Test", "Production"],
    needs: ["On-prem privileged access management (for example a BeyondTrust appliance)", "Just-in-time accounts with MFA", "Session recording and a kill switch"],
    security: ["Approved per session and time-boxed", "Every session recorded", "You can see and end active sessions (CIP-005-7 R2.4, R2.5)"],
    cons: ["A person holds production rights during the session", "Cloud-hosted vendor PAM would route through a vendor cloud"], scores: { assurance: 3, speed: 3, effort: 2 } },
};

/* ------------------------------------------------------------------ cutover */

export type Cutover = { name: string; short: string; downtime: string; mechanism: (f5: boolean) => string; from: string; to: string; steps: Step[]; rollback: (f5: boolean) => string };
export const CUTOVERS: Record<string, Cutover> = {
  bluegreen: { name: "Blue/green", downtime: "No downtime", from: "Blue · 1.3 (live)", to: "Green · 1.4 (new)",
    short: "The new version runs beside the old one. When it passes, the load balancer points users at it in one change.",
    mechanism: (f5) => (f5 ? "Default pool switched on the virtual server" : "Backend switched in one change"),
    steps: [
      { label: "Deploy green beside blue", live: 100, next: 0, check: "Green passes the health monitor on /api/health" },
      { label: "Test green privately", live: 100, next: 0, check: "Testers use a separate test address; users stay on blue" },
      { label: "Switch the pool", live: 0, next: 100, check: "One change on the load balancer; blue connections drain" },
      { label: "Keep blue warm", live: 0, next: 100, check: "Blue keeps running for 24 to 72 hours as the rollback" },
    ],
    rollback: (f5) => (f5 ? "point the virtual server back at the blue pool; takes seconds." : "switch the backend back to blue; takes seconds.") },
  canary: { name: "Canary", downtime: "No downtime", from: "Stable · 1.3", to: "Canary · 1.4",
    short: "A small share of users goes to the new version first. The share grows only while error rates and response times hold.",
    mechanism: (f5) => (f5 ? "Ratio weights between old and new pool members" : "Weighted routing between versions"),
    steps: [
      { label: "5% of users", live: 95, next: 5, check: "Error rate under 1% and response time within 10% of baseline" },
      { label: "25% of users", live: 75, next: 25, check: "The same checks hold for an hour" },
      { label: "50% of users", live: 50, next: 50, check: "Business owner checks the key dashboards" },
      { label: "Everyone", live: 0, next: 100, check: "Old members drained and kept for rollback" },
    ],
    rollback: (f5) => (f5 ? "set the ratio weights back to 100/0 on the F5." : "set the weights back to 100/0.") },
  rolling: { name: "Rolling update", downtime: "No downtime", from: "Pods on 1.3", to: "Pods on 1.4",
    short: "Kubernetes replaces instances one at a time; each must pass its readiness check before the next is replaced.",
    mechanism: () => "maxUnavailable 0: never below full capacity",
    steps: [
      { label: "First pod replaced", live: 67, next: 33, check: "Readiness check passes before it takes traffic" },
      { label: "Second pod replaced", live: 33, next: 67, check: "Capacity never drops below the full count" },
      { label: "Last pod replaced", live: 0, next: 100, check: "Rollout marked complete" },
    ],
    rollback: () => "helm rollback to the previous revision; the database is untouched thanks to expand and contract." },
  window: { name: "Maintenance window", downtime: "Planned downtime, under an hour", from: "1.3", to: "1.4",
    short: "The simplest route: users see a maintenance page while the release is installed and checked.",
    mechanism: (f5) => (f5 ? "Maintenance page served by the F5 during the window" : "Maintenance page during the window"),
    steps: [
      { label: "Window approved", live: 100, next: 0, check: "Change board approval and user notice" },
      { label: "Maintenance page on", live: 0, next: 0, check: "Fresh base backup and a named restore point" },
      { label: "Install and verify", live: 0, next: 0, check: "Smoke tests on the new version" },
      { label: "Reopen", live: 0, next: 100, check: "Maintenance page removed" },
    ],
    rollback: () => "point-in-time restore to the named restore point." },
};

export const DB_STEPS = [
  "Fresh base backup and a named restore point (PostgreSQL point-in-time recovery)",
  "Expand: add new tables and columns; nothing removed",
  "Release the new version; old and new both work",
  "Backfill and verify",
  "Contract: remove what the old version used, one release later",
];

/* ------------------------------------------------------------------ the client's own F5 and Cisco */

export type DeviceConfig = { id: string; product: string; role: string; items: string[]; url: string };
export const F5_LTM: DeviceConfig = { id: "f5-ltm", product: "F5 BIG-IP LTM", url: "https://clouddocs.f5.com/cli/tmsh-reference/latest/modules/ltm/ltm_pool.html",
  role: "Publishes costiq.<your domain> with your certificate and makes every release a pool switch. Any supported LTS line (17.1, 17.5 or 21.1).",
  items: [
    "HTTPS virtual server on 443: client SSL profile (TLS 1.2 and 1.3) with your certificate, server SSL to re-encrypt to the backend",
    "Pools costiq-web (3000) and costiq-api (8080), or one pool to the cluster ingress",
    "A traffic policy sending /api/* to the API pool; X-Forwarded-For inserted",
    "HTTPS monitor: GET /api/health, expecting 200",
    "Blue and green pools (or ratio weights for canary), changed through your AS3, Terraform or Ansible",
  ] };
export const F5_WAF: DeviceConfig = { id: "f5-waf", product: "F5 Advanced WAF", url: "https://techdocs.f5.com/en-us/bigip-21-1-0/big-ip-release-notes/big-ip-new-features.html",
  role: "A positive security policy built from our OpenAPI file, so only documented calls get through.",
  items: [
    "Policy imported from the CostIQ OpenAPI specification (BIG-IP 21.1 imports OpenAPI 3.1)",
    "Transparent mode in test, blocking mode in production",
    "Behavioural DoS and bot defense on the virtual server",
    "Upload size limits for invoice and billing files",
    "Remote logging of violations to your SIEM",
  ] };
export const F5_CIS: DeviceConfig = { id: "f5-cis", product: "F5 Container Ingress Services (optional)", url: "https://clouddocs.f5.com/containers/latest/reference/release-notes.html",
  role: "Keeps your BIG-IP pools in step with the cluster as pods move (OpenShift 4.19 to 4.21).",
  items: ["CIS watching the CostIQ namespace through a VirtualServer resource or an AS3 config map", "Pool members updated automatically; no manual pool edits after a release"] };
export const CISCO_FW: DeviceConfig = { id: "cisco-fw", product: "Cisco Secure Firewall (FTD or ASA)",
  url: "https://www.cisco.com/c/en/us/td/docs/security/secure-firewall/roadmap/management-center-new-features-by-release.html",
  role: "Threat Defense managed by your Management Center, or ASA. Default deny and no internet egress; nothing depends on Cisco's cloud management.",
  items: [
    "Access rules for the flows in the table above, nothing else",
    "FQDN objects only if your identity provider is in the cloud (no wildcards; needs DNS server groups on FTD)",
    "Snort 3 intrusion policy attached to the CostIQ allow rules",
    "Do Not Decrypt for CostIQ traffic: TLS ends on the load balancer, where the WAF inspects it",
    "Connection and intrusion events to your SIEM by syslog",
    "Changes through the Management Center REST API, Ansible or Terraform from your pipeline, with a least-privilege API user",
  ] };

/* ------------------------------------------------------------------ cyber defense */

export type LayerId = "firewall" | "waf" | "identity" | "access" | "pam" | "supply" | "segment" | "runtime" | "secrets" | "detect" | "backup";
export type Outcome = "block" | "contain" | "detect" | "recover";
export type Threat = { id: string; name: string; attack: string; technique: string; url: string; steps: { layer: LayerId; outcome: Outcome; text: string }[] };

export const THREATS: Threat[] = [
  { id: "web", name: "Exploit a web flaw", technique: "T1190 Exploit Public-Facing Application", url: "https://attack.mitre.org/techniques/T1190/",
    attack: "Crafted requests to the CostIQ address, looking for injection or broken access control.",
    steps: [
      { layer: "firewall", outcome: "contain", text: "Only HTTPS to the published address is reachable; every other port is closed." },
      { layer: "waf", outcome: "block", text: "Calls outside the API specification, and known attack signatures, are blocked." },
      { layer: "supply", outcome: "block", text: "The code passed static analysis, dynamic scanning and an ASVS Level 2 pen test before release." },
      { layer: "detect", outcome: "detect", text: "A spike in WAF violations raises an alert." },
    ] },
  { id: "password", name: "Use a stolen password", technique: "T1078 Valid Accounts", url: "https://attack.mitre.org/techniques/T1078/",
    attack: "A phished employee password is tried against CostIQ.",
    steps: [
      { layer: "identity", outcome: "block", text: "Sign-in goes through your identity provider; MFA and conditional access stop a password on its own." },
      { layer: "access", outcome: "contain", text: "Even a signed-in account sees only its persona and cost-center scope." },
      { layer: "detect", outcome: "detect", text: "Bursts of failed sign-ins and impossible travel raise an alert." },
    ] },
  { id: "supply", name: "Slip in a poisoned dependency", technique: "T1195.002 Compromise Software Supply Chain", url: "https://attack.mitre.org/techniques/T1195/002/",
    attack: "A compromised open-source package or build tool tries to ride into a release.",
    steps: [
      { layer: "supply", outcome: "block", text: "Dependency and image scans block known-bad versions; scanners themselves are pinned by digest." },
      { layer: "supply", outcome: "block", text: "Admission refuses any image not signed with your key, so a tampered build never runs." },
      { layer: "detect", outcome: "detect", text: "SBOMs let you find every affected release within minutes when a new CVE lands." },
    ] },
  { id: "ransom", name: "Ransomware spreads in", technique: "T1486 Data Encrypted for Impact", url: "https://attack.mitre.org/techniques/T1486/",
    attack: "Ransomware on a nearby server tries to move across and encrypt CostIQ's data.",
    steps: [
      { layer: "segment", outcome: "contain", text: "Default-deny rules on the firewall and in the cluster stop lateral movement into CostIQ." },
      { layer: "runtime", outcome: "detect", text: "Mass file encryption is spotted on the host and the host is isolated." },
      { layer: "backup", outcome: "recover", text: "Immutable, offsite backups restore the database to a point in time." },
    ] },
  { id: "exfil", name: "An insider takes the data", technique: "T1567 Exfiltration Over Web Service", url: "https://attack.mitre.org/techniques/T1567/",
    attack: "Someone with access tries to bulk-export cost data and upload it outside.",
    steps: [
      { layer: "access", outcome: "contain", text: "Each persona sees only its scope; OpCo users can't see other OpCos." },
      { layer: "firewall", outcome: "block", text: "CostIQ has no route to the internet, so uploads to file-sharing sites fail." },
      { layer: "detect", outcome: "detect", text: "Every export is written to the hash-chained ledger; out-of-hours bulk exports raise an alert." },
    ] },
  { id: "escape", name: "Break out of a container", technique: "T1611 Escape to Host", url: "https://attack.mitre.org/techniques/T1611/",
    attack: "An attacker who reached the app tries to break out to the host underneath.",
    steps: [
      { layer: "supply", outcome: "block", text: "Images run non-root with a read-only file system under restricted pod security." },
      { layer: "runtime", outcome: "detect", text: "A shell spawning in a container or a privilege escalation is flagged at once." },
      { layer: "secrets", outcome: "contain", text: "No secrets sit on disk or in images; they are fetched from the vault at start." },
    ] },
];

export const OUTCOME_LABEL: Record<Outcome, string> = { block: "Blocked", contain: "Contained", detect: "Detected", recover: "Recovered" };

export const DEFENSE_SOURCES: [string, string][] = [
  ["MITRE ATT&CK Enterprise", "https://attack.mitre.org/"],
  ["NIST Cybersecurity Framework 2.0", "https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf"],
  ["CISA #StopRansomware guide", "https://www.cisa.gov/stopransomware/ransomware-guide"],
  ["Falco runtime security (CNCF)", "https://falco.org/"],
];

/** Release-related sources for the Markdown export (the page links each one where it is used). */
export const RELEASE_SOURCES: [string, string][] = [
  ["NIST SSDF project (SP 800-218 v1.1; Rev. 1 draft)", "https://csrc.nist.gov/projects/ssdf"],
  ["SLSA v1.2 specification", "https://slsa.dev/spec/v1.2/"],
  ["OWASP ASVS 5.0", "https://asvs.owasp.org/"],
  ["NSA/CISA/ODNI: securing the software supply chain for customers", "https://www.cisa.gov/sites/default/files/2024-08/ESF_SECURING_THE_SOFTWARE_SUPPLY_CHAIN_CUSTOMER_508.pdf"],
  ["NIST SP 1334, portable storage media in OT", "https://csrc.nist.gov/news/2025/cyber-risks-of-portable-storage-media-in-ot"],
  ["NERC CIP-005-7 (vendor remote access, R2)", "https://www.nerc.com/pa/Stand/Reliability%20Standards/CIP-005-7.pdf"],
  ["Reproducible builds: definition", "https://reproducible-builds.org/docs/definition/"],
  ["F5 BIG-IP LTM pool reference", "https://clouddocs.f5.com/cli/tmsh-reference/latest/modules/ltm/ltm_pool.html"],
  ["F5 BIG-IP Zero Trust Access (formerly APM)", "https://www.f5.com/company/blog/hello-f5-big-ip-zero-trust-access-long-live-f5-big-ip-apm"],
  ["Cisco FTD FQDN objects", "https://www.cisco.com/c/en/us/support/docs/security/firepower-ngfw/214698-understand-fqdn-feature-on-firepower-thr.html"],
  ["Cisco Secure Firewall REST API", "https://www.cisco.com/c/en/us/support/docs/security/secure-firewall-management-center/221075-secure-firewall-rest-api-introduction.html"],
  ["Flux: signature verification", "https://fluxcd.io/flux/flux-e2e/"],
  ["Harbor replication", "https://goharbor.io/docs/main/administration/configuring-replication/"],
  ["Martin Fowler: Blue/green deployment", "https://martinfowler.com/bliki/BlueGreenDeployment.html"],
  ["Martin Fowler: Parallel change", "https://martinfowler.com/bliki/ParallelChange.html"],
  ["PostgreSQL point-in-time recovery", "https://www.postgresql.org/docs/current/continuous-archiving.html"],
  ["Trivy supply-chain incident, March 2026", "https://www.aquasec.com/blog/trivy-supply-chain-attack-what-you-need-to-know"],
];
