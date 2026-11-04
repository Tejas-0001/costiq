"""Tenant packs: one folder per sample tenant, selected with COSTIQ_TENANT (default "pharma"). One tenant per deployment.

The engines, routes and module keys (risk, tax, compliance, ...) are shared. A pack varies data, copy and five hooks:

  tenants/<t>/
    pack.yaml       read by costiq/tenant.py pack()
        name              the folder name
        addons            sample add-on scripts run after data/generate_sample.py, in order (paths relative to backend/)
        schema            tenant schema files, loaded after schema/costiq_regulatory_schema.sql
        register_tables   the tenant's register tables copied into the served database
        default_rules     allocation rules seeded by `costiq seed`: [id, name, priority, column, value, cost center]
        allocation        classes [{class, when (SQL), function, account, title}], default_class, unmapped_exception,
                          expense_classes (what the evidence tests and the unlock read). Optional (utility):
                          engine: ferc (costiq/allocation.py Allocator.ferc_sql: direct charge first, rule register,
                          work orders, the 920 guard), classes' valid / held / rule, ledger_columns (allocated_cost
                          columns the tenant schema adds; costiq.allocation.extra_columns(), empty when absent) and
                          ferc {direct_policy, shared_tag_value, multi_function, nonutility, commitment, rules,
                          exceptions, exception_order, tables}
        usecases          ids the shared code calls by role: bridge, recharge, filing_exhibits; optional (read with
                          pack()["usecases"].get(role)): capital_path, exception_queue, tag_compliance, invoice_tieout,
                          seat_reharvest, token_check, rule_register, account_listing
        reconciliation    optional: items_table (labeled reconciling items, matched on InvoiceDetailId)
        tags              optional: required_table, value_map_table, account_table, suspense_exception, suspense_owner
        controls          optional: cross_subsidy_accounts (C-09 also flags regulated lines from accounts a
                          non-regulated entity owns)
                          A missing optional key means the feature is off (pharma has none of them).
        usecase_vars      values for {{name}} placeholders in registry YAML (residency_data_class, residency_data_label,
                          extension_source, storage_tier_note, default_application)
        data_requests     id_prefix, ledger_target
        ask_synonyms      word -> words, added to Ask CostIQ's shared list
    access.yaml     modules (labels for the shared keys), personas, roleMap, scopeGroups, entityScopeGroups, demoScope
    copy.json       {"backend": {...}, "frontend": {...}}; every tenant has the same keys (tests/test_tenants.py)
    copy/*.json     fragments in the same shape, owned per feature; tenants/<t>/copy/*.json are deep-merged after
                    copy.json in filename order (a leaf defined twice is an error)
        backend     read with costiq.tenant.text("summary.risk.lead", amount=...) / lookup(key): findings, controls,
                    disclaimers, risk, tax, requests, evidence, board, summary
        frontend    served as "copy" by /api/config and /api/me; read in React with useCopy().t("nav.items.risk")
                    (frontend/components/copy.tsx): nav, periodNotes, then one namespace per page (landing, glossary,
                    agent, command, tax, risk, catalog, format)
    hooks.py        findings(eng, scope, add); unlock(prudence, scope, filing=None) -> dict;
                    board_ai_metrics(bp) -> [metric]; board_sections() -> [(key, kind, title, why, fn(bp))];
                    summary_compliance(summ, b) -> [sentence]
    names.py        name tables for the shared generators (DEPTS, WORKSPACES, SUBS, AI_BUDGET, OPT_*, ...); same
                    variables in every pack
    usecases/       the tenant's own use cases (YAML), added to registry/usecases
    sample.py       the tenant's add-on generator (listed in pack.yaml addons)
    schema.sql      the tenant's registers
    summary_responses.json   saved AI summary prose (optional; tools/summary_responses.py)

Build and test one tenant:   python data/build_sample.py --tenant utility
                             COSTIQ_TENANT=utility python -m pytest -q tests
"""
