// Public sources behind the claims on the home page. Collected for the "MM CostIQ for a Large US Electric Utility"
// research (Nov 2026); numbering here is the numbering shown on the page.
export type Source = { n: number; publisher: string; title: string; date?: string; url: string };

export const SOURCES: Source[] = [
  { n: 1, publisher: "Southern California Edison", title: "2025 General Rate Case, Exhibit SCE-06 Vol. 01, Enterprise Technology (CPUC A.23-05-010)", date: "2023",
    url: "https://docs.cpuc.ca.gov/PublishedDocs/SupDoc/A2305010/6064/508571157.pdf" },
  { n: 2, publisher: "PowerLines", title: "Utilities request $18.6 billion in rate increases in first half of 2026", date: "2026",
    url: "https://powerlines.org/utilities-request-18-6-billion-in-rate-increases-in-first-half-of-2026/" },
  { n: 3, publisher: "E&E News", title: "US utilities planning record-breaking capital spending", date: "Apr 2026",
    url: "https://www.eenews.net/articles/us-utilities-planning-record-breaking-capital-spending/" },
  { n: 4, publisher: "AEP Service Corporation", title: "2023 FERC Form No. 60 (Schedule XXI, Methods of Allocation)",
    url: "https://docs.aep.com/docs/requiredpostings/TariffFilings/ER18-194-000/2024/2023TrueUp/2023%20AEPSC%20FERC%20Form%2060-compressed.pdf" },
  { n: 5, publisher: "Idaho Public Utilities Commission", title: "Final Order No. 34707 (Idaho Power, Case IPC-E-20-11)", date: "Jun 25, 2020",
    url: "https://puc.idaho.gov/Fileroom/PublicFiles/ELEC/IPC/IPCE2011/OrdNotc/20200625Final_Order_No_34707.pdf" },
  { n: 6, publisher: "chigov.com (news report)", title: "Illinois Commerce Commission drops cloud computing rule", date: "2020",
    url: "https://www.chigov.com/illinois/general-assembly/icc-kills-cloud-computing" },
  { n: 7, publisher: "FinOps Foundation / Linux Foundation", title: "State of FinOps 2026", date: "2026",
    url: "https://www.linuxfoundation.org/press/state-of-finops-survey-ai-value-and-skills-top-priorities-as-finops-matures-across-technology-value-98-manage-ai-90-saas-64-licensing-48-data-center-1" },
  { n: 8, publisher: "Federal Energy Regulatory Commission", title: "Order No. 898 (Federal Register)", date: "Oct 5, 2023",
    url: "https://www.govinfo.gov/content/pkg/FR-2023-10-05/html/2023-14994.htm" },
  { n: 9, publisher: "Troutman Pepper", title: "FERC approves virtualization CIP reliability standards and definitions (Order No. 919)", date: "Apr 2026",
    url: "https://www.troutmanenergyreport.com/2026/04/ferc-approves-virtualization-cip-reliability-standards-and-definitions/" },
  { n: 10, publisher: "UC Today", title: "Microsoft's Copilot rollout yields 480% ROI and 96% adoption at UK Power Networks", date: "Dec 2025",
    url: "https://www.uctoday.com/unified-communications/microsofts-copilot-rollout-yields-480-roi-and-96-adoption-at-uk-power-networks/" },
  { n: 11, publisher: "Cornell Legal Information Institute", title: "18 CFR 369.1, FERC Form No. 60, annual report for centralized service companies",
    url: "https://www.law.cornell.edu/cfr/text/18/369.1" },
  { n: 12, publisher: "AESI", title: "BES Cyber System Information in the cloud: clearer skies ahead (CIP-004-7, CIP-011-3)",
    url: "https://blog.aesi-inc.com/bes-cyber-system-information-in-the-cloud-clearer-skies-ahead/" },
  { n: 13, publisher: "Federal Energy Regulatory Commission", title: "AI20-1-000, accounting guidance for cloud computing arrangements", date: "Dec 2019",
    url: "https://WWW.FERC.GOV/sites/default/files/2020-05/AI20-1-000.pdf" },
  { n: 14, publisher: "Morgan Lewis", title: "Virtualization in the CIP environment: preparing for compliance after FERC Order No. 919", date: "Jul 2026",
    url: "https://www.morganlewis.com/pubs/2026/07/virtualization-in-the-cip-environment-preparing-for-compliance-after-ferc-order-no-919" },
  { n: 15, publisher: "Federal Register", title: "Order No. 919, Virtualization Reliability Standards (91 FR 13957, Docket RM24-8-000)", date: "Mar 24, 2026",
    url: "https://www.federalregister.gov/documents/2026/03/24/2026-05716/order-no-919-virtualization-reliability-standards" },
  { n: 16, publisher: "Texas Reliability Entity", title: "NERC standards effective and enforcement dates", date: "Aug 2026",
    url: "https://www.texasre.org/Documents/Standards/NSRF%20Uploads/August%202026/Standards%20Dates.pdf" },
];

export const source = (n: number) => SOURCES.find((s) => s.n === n)!;
