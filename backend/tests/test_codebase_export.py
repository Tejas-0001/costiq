"""Native Snowflake Pipeline and Client-Owned Codebase: Snowflake translation of the catalog and the dbt project export.

Tenant-neutral: runs against whichever tenant is active (COSTIQ_TENANT)."""
import csv, hashlib, io, json, re, zipfile
import pytest
import sqlglot
import yaml
from sqlglot import exp
from fastapi.testclient import TestClient

from costiq import dbt_export, dialect
from costiq.context import db, reg
from costiq.main import app

client = TestClient(app)
ROOT = dbt_export.ROOT


def _sf(sql: str) -> str:
    return dialect.translate(sql, "snowflake")[0]


# ---------------------------------------------------------------------------------------- dialect: the whole catalog
@pytest.mark.parametrize("scope", [(0, 0), (2, 0), (0, 2)])
def test_every_use_case_translates_to_snowflake_and_reparses(scope):
    failed = []
    for uid in reg.order:
        try:
            out = dialect.translate(reg.get(uid)["sql"], "snowflake", *scope)[0]
            assert isinstance(sqlglot.parse_one(out, read="snowflake"), exp.Query)
        except Exception as e:  # noqa: BLE001
            failed.append((uid, str(e)[:120]))
    assert not failed, failed


def test_no_untranslatable_patterns_remain_in_snowflake_sql():
    json_cols = "|".join(sorted(dialect.JSON_COLUMNS))
    bad = {
        "json_each": re.compile(r"\bJSON_EACH\s*\(", re.I),
        "interval arithmetic": re.compile(r"\bINTERVAL\b", re.I),
        "PARSE_JSON on a VARIANT column": re.compile(rf"PARSE_JSON\(\s*(?:\w+\.)?(?:{json_cols})\s*\)", re.I),
        "text JSON function on a VARIANT column": re.compile(rf"JSON_EXTRACT_PATH_TEXT\(\s*(?:\w+\.)?(?:{json_cols})\b", re.I),
    }
    hits = {}
    for uid in reg.order:
        out = _sf(reg.get(uid)["sql"])
        for name, rx in bad.items():
            if rx.search(out):
                hits.setdefault(name, []).append(uid)
        tree = sqlglot.parse_one(out, read="snowflake")
        for tc in tree.find_all(exp.TryCast):  # TRY_CAST only ever receives text
            assert isinstance(tc.this, exp.Cast) or (isinstance(tc.this, exp.Literal) and tc.this.is_string), uid
    assert not hits, hits


def test_json_each_becomes_flatten_and_reads_variant_without_parse_json():
    out = _sf("SELECT json_extract_string(CA.value, '$.ContractCommitmentID') AS Id, "
              "SUM(CAST(json_extract_string(CA.value, '$.ContractCommitmentAppliedCost') AS DOUBLE)) AS Applied "
              "FROM focus_data_table CU CROSS JOIN json_each(CU.ContractApplied -> 'Elements') AS CA GROUP BY 1")
    assert "TABLE(FLATTEN(INPUT => GET_PATH(CU.ContractApplied, 'Elements'))) AS CA" in out
    assert "CAST(GET_PATH(CA.Value, 'ContractCommitmentID') AS VARCHAR)" in out
    assert "CAST(GET_PATH(CA.Value, 'ContractCommitmentAppliedCost') AS DOUBLE)" in out  # the JSON number, no text detour
    assert "PARSE_JSON" not in out and "JSON_EACH" not in out.upper()


def test_exists_over_json_each_becomes_filter():
    out = _sf("SELECT 1 FROM CostAndUsage CU WHERE EXISTS (SELECT 1 FROM json_each(CU.CommitmentProgramEligibilityDetails "
              "-> 'CommitmentPrograms') AS CP WHERE json_extract_string(CP.value, '$.ProgramType') IN ('ResourceReservation'))")
    assert "ARRAY_SIZE(FILTER(CAST(GET_PATH(CU.CommitmentProgramEligibilityDetails, 'CommitmentPrograms') AS ARRAY), " \
           "CP -> CAST(GET_PATH(CP, 'ProgramType') AS VARCHAR) IN ('ResourceReservation'))) > 0" in out


def test_json_keys_paths_and_parameters_on_variant():
    out = _sf("SELECT json_extract_string(Tags, '$.\"user:CostCenter\"') AS a, Tags ->> :tag_key AS b, "
              "json_extract_string(ContractApplied, '$.Elements[0].ContractCommitmentID') AS c FROM CostAndUsage")
    assert "CAST(GET(Tags, 'user:CostCenter') AS VARCHAR) AS a" in out
    assert "CAST(GET(Tags, :tag_key) AS VARCHAR) AS b" in out
    assert "CAST(GET_PATH(ContractApplied, 'Elements[0].ContractCommitmentID') AS VARCHAR) AS c" in out


def test_interval_arithmetic_becomes_dateadd():
    out = _sf("SELECT d - INTERVAL 7 DAY AS a, CAST(:as_of AS TIMESTAMP) - INTERVAL '30' DAY AS b, "
              "DATE '2026-01-01' + INTERVAL (:n) MONTH AS c, date_add(d, INTERVAL 1 MONTH) AS e, "
              "(d - INTERVAL 1 DAY) - INTERVAL 2 MONTH AS f FROM t")
    assert "DATEADD(DAY, -7, d) AS a" in out
    assert "DATEADD(DAY, -30, CAST(:as_of AS TIMESTAMPNTZ)) AS b" in out
    assert "DATEADD(MONTH, :n, CAST('2026-01-01' AS DATE)) AS c" in out
    assert "DATEADD(MONTH, 1, d) AS e" in out
    assert "DATEADD(MONTH, -2, (DATEADD(DAY, -1, d))) AS f" in out
    assert "INTERVAL" not in out


def test_try_cast_only_on_strings_and_timestamps_have_a_fixed_text_form():
    out = _sf("SELECT TRY_CAST(EffectiveCost AS DOUBLE) AS a, TRY_CAST('1.5' AS DOUBLE) AS b, "
              "md5(CAST(ChargePeriodStart AS VARCHAR)) AS k FROM CostAndUsage")
    assert "TRY_CAST(CAST(EffectiveCost AS VARCHAR) AS DOUBLE) AS a" in out
    assert "TRY_CAST('1.5' AS DOUBLE) AS b" in out
    assert "MD5(TO_CHAR(ChargePeriodStart, 'yyyy-mm-DD hh24:mi:ss')) AS k" in out  # matches DuckDB's text form


def test_order_by_is_null_keeps_the_input_column():
    sql = ("SELECT COALESCE(GlAccount, '(unmapped)') AS GlAccount, SUM(AllocatedCost) AS AllocatedCost FROM allocated_cost "
           "GROUP BY GlAccount ORDER BY CASE WHEN GlAccount IS NULL THEN 0 ELSE 1 END, AllocatedCost DESC")
    out = _sf(sql)
    assert "ORDER BY CASE WHEN allocated_cost.GlAccount IS NULL THEN 0 ELSE 1 END" in out
    assert "COALESCE(GlAccount, '(unmapped)') IS NULL" not in out
    # an alias that is not also an input column is still inlined (PostgreSQL behaviour unchanged)
    assert "ORDER BY ABS(SUM(x))" in dialect.translate("SELECT SUM(x) AS v FROM t ORDER BY ABS(v)", "postgres")[0].replace('"', "")


def test_group_by_all_is_kept_on_snowflake():
    assert "GROUP BY ALL" in _sf("SELECT ServiceName, SUM(EffectiveCost) FROM CostAndUsage GROUP BY ALL")


def test_databricks_parameter_key_gets_a_json_path():
    out = dialect.translate("SELECT Tags ->> :tag_key FROM CostAndUsage", "databricks")[0]
    assert "GET_JSON_OBJECT(Tags, '$.' || :tag_key)" in out


def test_duckdb_and_postgres_output_unchanged_by_snowflake_rules():
    sql = "SELECT CA.value FROM CostAndUsage CU CROSS JOIN json_each(CU.ContractApplied -> 'Elements') AS CA WHERE d > now() - INTERVAL 7 DAY"
    duck = dialect.translate(sql, "duckdb")[0]
    assert "JSON_EACH" in duck.upper() and "INTERVAL" in duck and "FLATTEN" not in duck
    pg = dialect.translate(sql, "postgres")[0]
    assert "JSON_ARRAY_ELEMENTS" in pg and "FLATTEN" not in pg and "DATEADD" not in pg


# ---------------------------------------------------------------------------------------- the dbt project
@pytest.fixture(scope="module")
def files():
    return dbt_export.build("snowflake")


def _db_tables():
    rows = db.query("""SELECT table_name, column_name, data_type FROM information_schema.columns
                       WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                       ORDER BY table_name, ordinal_position""", {}, None, limit=None)["rows"]
    out = {}
    for t, c, typ in rows:
        out.setdefault(t, []).append((c, typ))
    return out


def test_export_is_deterministic(files):
    again = dbt_export.build("snowflake")
    assert again == files
    assert dbt_export.to_zip(again) == dbt_export.to_zip(files)


def test_export_tree(files):
    expected = [
        "dbt_project.yml", "profiles.yml", "README.md", "packages/README.md", "models/sources.yml",
        "models/staging/aws/stg_aws__focus.sql", "models/staging/azure/stg_azure__focus.sql",
        "models/staging/snowflake/stg_snowflake__focus.sql",
        "models/intermediate/int_focus__v14.sql", "models/intermediate/int_tags__resolved.sql",
        "models/marts/fct_cloud_ledger.sql", "models/marts/fct_tag_compliance.sql",
        "models/marts/fct_service_company_billing.sql", "models/marts/schema.yml",
        "seeds/properties.yml", "seeds/ledger_period_close.csv", "seeds/allocation_rule.csv",
        "tests/assert_every_dollar_mapped.sql", "tests/assert_ledger_ties_to_source.sql",
        "tests/assert_shares_sum_to_one.sql",
        "snowflake/00_account_objects.sql", "snowflake/01_storage_integrations_and_stages.sql",
        "snowflake/02_raw_tables.sql", "snowflake/03_pipes.sql", "snowflake/04_reload_period.sql",
        "snowflake/05_dbt_project_and_task.sql",
        "governance/01_roles.sql", "governance/02_row_access.sql", "governance/03_masking.sql", "governance/04_tags.sql",
        "provider-policy/aws/tag-policy.json", "provider-policy/aws/scp-require-tags.json",
        "provider-policy/azure/policy-assignments.json", "provider-policy/README.md",
        ".github/workflows/dbt-pull-request.yml", ".github/workflows/dbt-deploy.yml", "docs/rule-register.md",
    ]
    # the 920 guard belongs to a FERC chart of accounts: only tenants that seed ref_ferc_account get it
    ferc = f"{ROOT}/seeds/ref_ferc_account.csv" in files
    assert (f"{ROOT}/tests/assert_no_non_labor_in_920.sql" in files) == ferc
    missing = [p for p in expected if f"{ROOT}/{p}" not in files]
    assert not missing, missing
    assert all(p.startswith(ROOT + "/") for p in files)
    analyses = [p for p in files if p.startswith(f"{ROOT}/analyses/")]
    names = {p.rsplit("/", 1)[1][:-4] for p in analyses}
    assert names == {u.lower().replace("-", "_") for u in reg.order}  # every catalog use case


def test_seeds_match_the_reference_tables(files):
    tables = _db_tables()
    props = yaml.safe_load(files[f"{ROOT}/seeds/properties.yml"])
    types = {s["name"]: s["config"]["column_types"] for s in props["seeds"]}
    refs = sorted(t for t in tables if t.startswith("ref_"))
    assert refs, "no reference tables in the active database"
    for t in refs:
        name = t if f"{ROOT}/seeds/{t}.csv" in files else f"{t}__text"
        text = files[f"{ROOT}/seeds/{name}.csv"]
        rows = list(csv.reader(io.StringIO(text)))
        assert rows[0] == [c for c, _ in tables[t]], t
        n = db.query(f"SELECT COUNT(*) FROM {t}", {}, None)["rows"][0][0]
        assert len(rows) - 1 == n, t
        for c, typ in tables[t]:  # account numbers (and every other text column) stay text
            if typ.upper().startswith("VARCHAR"):
                assert types[name][c] == "varchar", (t, c)
    for t in ("ref_ferc_account", "ref_ferc_rule", "ref_required_tag"):
        if t in tables:
            assert f"{ROOT}/seeds/{t}.csv" in files or f"{ROOT}/seeds/{t}__text.csv" in files
    if "ref_ferc_rule" in tables:
        assert "Mapping rules applied by the ledger" in files[f"{ROOT}/docs/rule-register.md"]


def test_every_sql_file_parses_with_snowflake_dialect(files):
    rep = dbt_export.validate(files)
    assert rep["errors"] == []
    assert rep["files"] == sum(p.endswith(".sql") for p in files)
    assert rep["queries"] >= len(reg.order) + 8  # analyses, models and tests are real query trees


def test_wording_names_and_secrets(files):
    blob = "\n".join(files.values())
    assert not re.search(r"\b(demo|sample|samples|synthetic|prototype|preview)\b", blob, re.I)
    prospect = re.compile(r"\bAEP\b|American Electric|Appalachian Power|Indiana Michigan|Kentucky Power|Kingsport Power|"
                          r"Wheeling Power|Ohio Power|Public Service Company of Oklahoma|Southwestern Electric|Transource", re.I)
    assert not prospect.search(blob)
    assert not re.search(r"(password|private_key|secret_access_key|api[_-]?key)\s*[:=]\s*['\"]?\w", blob, re.I)
    assert not re.search(r"sk-ant-|AKIA[0-9A-Z]{12}", blob)


def test_pipeline_patterns(files):
    f = lambda p: files[f"{ROOT}/{p}"]
    ledger = f("models/marts/fct_cloud_ledger.sql")
    assert "incremental_strategy='delete+insert'" in ledger and "unique_key='PeriodStart'" in ledger
    assert "transient=false" in ledger and "full_refresh=false" in ledger
    assert re.search(r"CAST\(.+ AS DECIMAL\(38, 10\)\) AS AllocatedCost", ledger)
    assert "{{ ref('int_tags__resolved') }}" in ledger
    assert not any("incremental_strategy='insert_overwrite'" in t or "DOUBLE" in t
                   for p, t in files.items() if p.startswith(f"{ROOT}/models/"))
    project = yaml.safe_load(f("dbt_project.yml"))
    assert project["require-dbt-version"] == dbt_export.DBT_VERSION == "1.12.3"
    assert project["models"]["client_cloud_ledger"]["+transient"] is False
    raw = f("snowflake/02_raw_tables.sql")
    assert "_FILE_NAME VARCHAR" in raw and "_FILE_ROW_NUMBER NUMBER(38, 0)" in raw
    assert "BilledCost DECIMAL(38, 10)" in raw
    pipes = f("snowflake/03_pipes.sql")
    assert "METADATA$FILENAME" in pipes and "METADATA$FILE_ROW_NUMBER" in pipes and "AUTO_INGEST = TRUE" in pipes
    task = f("snowflake/05_dbt_project_and_task.sql")
    assert "EXECUTE DBT PROJECT" in task and f"WAREHOUSE = {dbt_export.WAREHOUSE}" in task
    assert f"DBT_VERSION = '{dbt_export.DBT_VERSION}'" in task
    ci = f(".github/workflows/dbt-pull-request.yml") + f(".github/workflows/dbt-deploy.yml")
    assert "id-token: write" in ci and "use-oidc: true" in ci
    assert not [line for line in ci.splitlines() if line.strip().startswith("run:") and "--force" in line]
    assert "packages.yml" not in "".join(p for p in files)  # no remote packages


def test_governance_follows_the_access_model(files):
    from costiq.auth import access
    acc = access()
    rap = files[f"{ROOT}/governance/02_row_access.sql"]
    for group, values in {**(acc.get("scopeGroups") or {}), **(acc.get("entityScopeGroups") or {})}.items():
        assert f"'{group}'" in rap
        for v in values:
            assert "'" + v.replace("'", "''") + "'" in rap
    roles = files[f"{ROOT}/governance/01_roles.sql"]
    for key, p in acc["personas"].items():
        assert f"COSTIQ_{key.upper()}" in roles
        assert (f"GRANT ROLE COSTIQ_ENTERPRISE TO ROLE COSTIQ_{key.upper()};" in roles) == bool(p.get("enterprise"))


def test_provider_policies_require_the_tenants_tags(files):
    keys = dbt_export.Export().required_tag_keys()
    tag_policy = json.loads(files[f"{ROOT}/provider-policy/aws/tag-policy.json"])
    assert sorted(tag_policy["tags"]) == sorted(keys)
    scp = json.loads(files[f"{ROOT}/provider-policy/aws/scp-require-tags.json"])
    assert sorted(next(iter(s["Condition"]["Null"])).split("/", 1)[1] for s in scp["Statement"]) == sorted(keys)
    az = json.loads(files[f"{ROOT}/provider-policy/azure/policy-assignments.json"])
    ids = {a["policyDefinitionId"].rsplit("/", 1)[1] for a in az["assignments"]}
    assert ids == {dbt_export.AZ_REQUIRE_TAG, dbt_export.AZ_INHERIT_SUB_TAG}
    assert len(az["assignments"]) == 2 * len(keys)


# ---------------------------------------------------------------------------------------- API
def test_api_snowflake_sql_for_a_use_case():
    r = client.get("/api/codebase/usecases/F-22/sql?target=snowflake", headers={"x-costiq-persona": "cfo"})
    assert r.status_code == 200
    body = r.json()
    assert body["target"] == "snowflake" and "FLATTEN" in body["sql"] and "\n" in body["sql"]
    assert set(body["params"]) == {p["name"] for p in reg.get("F-22").get("params", [])}
    assert client.get("/api/codebase/usecases/F-05/sql?target=databricks", headers={"x-costiq-persona": "cfo"}).status_code == 200
    assert client.get("/api/codebase/usecases/F-05/sql?target=oracle", headers={"x-costiq-persona": "cfo"}).status_code == 400
    assert client.get("/api/codebase/usecases/NOPE/sql", headers={"x-costiq-persona": "cfo"}).status_code == 404


def test_api_dbt_zip_downloads(files):
    r = client.get("/api/codebase/dbt.zip", headers={"x-costiq-persona": "cfo"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f'filename="{ROOT}.zip"' in r.headers["content-disposition"]
    assert hashlib.sha256(r.content).hexdigest() == r.headers["x-costiq-sha256"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == sorted(files)
    assert z.read(f"{ROOT}/dbt_project.yml").decode() == files[f"{ROOT}/dbt_project.yml"]


# ---------------------------------------------------------------------------------------- tenant ledger columns
def test_ledger_carries_the_tenants_extra_columns(files):
    """The columns a tenant adds to allocated_cost (allocation.extra_columns(), the FERC ledger's rule, function, invoice
    and billing-class stamps) flow into fct_cloud_ledger and its schema.yml; the rule register gets dollars by rule and the
    service company bill groups by BillingClass. Columns the database does not hold yet are not required."""
    from costiq.allocation import extra_columns
    f = lambda p: files.get(f"{ROOT}/{p}", "")
    ledger, yml = f("models/marts/fct_cloud_ledger.sql"), yaml.safe_load(f("models/marts/schema.yml"))
    held = {c for c, _ in _db_tables().get("allocated_cost", [])}
    expected = [c for c in extra_columns() if c in held]
    documented = {c["name"] for m in yml["models"] if m["name"] == "fct_cloud_ledger" for c in m.get("columns", [])}
    for c in expected:
        assert re.search(rf"\bAS {c}\b", ledger), c
        assert c in documented, c
    if "RuleId" in expected and "ref_ferc_rule" in _db_tables():
        assert f("models/marts/fct_rule_register.sql") and f("tests/assert_rule_register_ties_to_ledger.sql")
        assert "Dollars" in f("docs/rule-register.md") or "AllocatedCost" in f("docs/rule-register.md")
    if "BillingClass" in expected:
        assert "BillingClass" in f("models/marts/fct_service_company_billing.sql")
    if not extra_columns():  # pharma: the ledger and its docs are unchanged
        assert not f("models/marts/fct_rule_register.sql")
        assert "BillingClass" not in f("models/marts/fct_service_company_billing.sql")


def test_ferc_content_only_for_ferc_tenants(files):
    """The 920 guard and FERC wording ship only with a tenant that seeds the FERC chart of accounts."""
    ferc = "ref_ferc_account" in _db_tables()
    assert (f"{ROOT}/tests/assert_no_non_labor_in_920.sql" in files) == ferc
    if not ferc:
        blob = "\n".join(files.values())
        assert not re.search(r"\bFERC\b|\bNERC\b|\b920\b|\bA&G\b", blob), re.search(r".{40}(\bFERC\b|\bNERC\b|\b920\b|\bA&G\b).{40}", blob)
