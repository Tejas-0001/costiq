"""Tenant packs: every pack offers the same interface (copy keys, hooks, name tables, pack keys), so the shared engines and
the web app read one key for any tenant. Runs for whichever tenant is active; checks every pack on disk."""
import ast, glob, json, os, re
import pytest
import yaml
from costiq.tenant import load_copy

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TENANTS = sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(ROOT, "tenants", "*", "pack.yaml")))
HOOKS = {"findings": ["eng", "scope", "add"], "unlock": ["prudence", "scope", "filing"], "board_ai_metrics": ["bp"],
         "board_sections": [], "summary_compliance": ["summ", "b"]}
PACK_KEYS = {"name", "addons", "schema", "register_tables", "default_rules", "allocation", "usecases", "usecase_vars",
             "data_requests", "ask_synonyms"}


def tenant_file(t, name):
    return os.path.join(ROOT, "tenants", t, name)


def keys(node, prefix=""):
    """Leaf key paths of a JSON object (lists are leaves)."""
    if isinstance(node, dict):
        out = {prefix.rstrip(".")} if not node and prefix else set()
        for k, v in node.items():
            out |= keys(v, f"{prefix}{k}.")
        return out
    return {prefix.rstrip(".")}


def placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text)) if isinstance(text, str) else set()


def test_pharma_and_utility_packs_exist():
    assert {"pharma", "utility"} <= set(TENANTS)


def copy_files(t):
    """copy.json and the copy/*.json fragments of a tenant, in merge order."""
    return [tenant_file(t, "copy.json")] + sorted(glob.glob(tenant_file(t, os.path.join("copy", "*.json"))))


def merged_copies():
    return {t: load_copy(t) for t in TENANTS}


def test_copy_fragments_merge_and_reject_duplicate_leaves(tmp_path, monkeypatch):
    from costiq import tenant
    for t in TENANTS:  # every fragment has the documented shape
        for f in copy_files(t)[1:]:
            frag = json.load(open(f, encoding="utf-8"))
            assert isinstance(frag, dict) and set(frag) <= {"backend", "frontend"}, f
    (tmp_path / "x" / "copy").mkdir(parents=True)
    (tmp_path / "x" / "copy.json").write_text(json.dumps({"frontend": {"a": {"b": "1"}}}), encoding="utf-8")
    (tmp_path / "x" / "copy" / "1.json").write_text(json.dumps({"frontend": {"a": {"c": ["2"]}}, "backend": {"d": "3"}}), encoding="utf-8")
    monkeypatch.setattr(tenant, "tenant_dir", lambda t: str(tmp_path / t))
    tenant.load_copy.cache_clear()
    try:
        assert tenant.load_copy("x") == {"frontend": {"a": {"b": "1", "c": ["2"]}}, "backend": {"d": "3"}}
        (tmp_path / "x" / "copy" / "2.json").write_text(json.dumps({"frontend": {"a": {"b": "again"}}}), encoding="utf-8")
        tenant.load_copy.cache_clear()
        with pytest.raises(ValueError, match=r"2\.json.*'frontend\.a\.b'"):
            tenant.load_copy("x")
    finally:
        tenant.load_copy.cache_clear()


def test_copy_files_have_the_same_keys():
    copies = merged_copies()
    base = keys(copies["pharma"])
    assert {"backend", "frontend"} == set(copies["pharma"])
    for t, c in copies.items():
        k = keys(c)
        assert k == base, f"{t} merged copy: missing {sorted(base - k)[:10]}, extra {sorted(k - base)[:10]}"


def test_copy_templates_take_the_same_placeholders():
    """A template is filled by shared code, so every tenant's version must use the same {names}."""
    copies = merged_copies()

    def leaves(node, prefix=""):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from leaves(v, f"{prefix}{k}.")
        else:
            yield prefix.rstrip("."), "\n".join(node) if isinstance(node, list) and all(isinstance(x, str) for x in node) else node
    base = dict(leaves(copies["pharma"]))
    for t, c in copies.items():
        for k, v in leaves(c):
            assert placeholders(v) == placeholders(base[k]), (t, k)


def test_hooks_define_the_same_functions():
    for t in TENANTS:
        tree = ast.parse(open(tenant_file(t, "hooks.py"), encoding="utf-8").read())
        fns = {n.name: [a.arg for a in n.args.args] for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name, args in HOOKS.items():
            assert name in fns, f"{t}/hooks.py lacks {name}"
            assert fns[name] == args, f"{t}/hooks.py {name}{tuple(fns[name])} should take {tuple(args)}"


def test_name_tables_define_the_same_variables():
    def names(t):
        tree = ast.parse(open(tenant_file(t, "names.py"), encoding="utf-8").read())
        out = set()
        for n in tree.body:
            if isinstance(n, ast.Assign):
                for tgt in n.targets:
                    out |= {e.id for e in (tgt.elts if isinstance(tgt, ast.Tuple) else [tgt]) if isinstance(e, ast.Name)}
        return out
    base = names("pharma")
    for t in TENANTS:
        assert names(t) == base, (t, sorted(base ^ names(t)))


def test_packs_and_access_models_are_complete():
    shared = {f"{{{{{v}}}}}" for p in glob.glob(os.path.join(ROOT, "registry", "usecases", "*.yaml"))
              for v in re.findall(r"\{\{\s*(\w+)\s*\}\}", open(p, encoding="utf-8").read())}
    pharma_modules = set(yaml.safe_load(open(tenant_file("pharma", "access.yaml"), encoding="utf-8"))["modules"])
    for t in TENANTS:
        pack = yaml.safe_load(open(tenant_file(t, "pack.yaml"), encoding="utf-8"))
        assert PACK_KEYS <= set(pack), (t, PACK_KEYS - set(pack))
        assert pack["name"] == t
        assert {"bridge", "recharge", "filing_exhibits"} <= set(pack["usecases"])
        assert all(len(r) == 6 for r in pack["default_rules"])
        assert {f"{{{{{k}}}}}" for k in pack["usecase_vars"]} >= shared, (t, "usecase_vars missing a registry placeholder")
        for rel in pack["addons"] + pack["schema"]:
            assert os.path.exists(os.path.join(ROOT, rel)), (t, rel)
        acc = yaml.safe_load(open(tenant_file(t, "access.yaml"), encoding="utf-8"))
        assert set(acc["modules"]) == pharma_modules, t  # module keys are shared; only labels differ
        assert all(set(p["modules"]) <= pharma_modules for p in acc["personas"].values()), t
        assert all(v in acc["personas"] for v in acc["roleMap"].values()), t


def test_utility_copy_labels():
    acc = yaml.safe_load(open(tenant_file("utility", "access.yaml"), encoding="utf-8"))
    assert (acc["modules"]["risk"], acc["modules"]["tax"], acc["modules"]["compliance"]) == (
        "Prudence Radar", "Regulatory Accounting & Rates", "NERC CIP & Data")
    nav = load_copy("utility")["frontend"]["nav"]["items"]
    assert (nav["risk"], nav["tax"], nav["compliance"]) == ("Prudence Radar", "Regulatory Accounting & Rates", "NERC CIP & Data")
    assert (nav["rates"], load_copy("pharma")["frontend"]["nav"]["items"]["rates"]) == ("Commitments & Discounts", "Rates & Commitments")


# words that must never appear in the other tenant's copy, access model or pack
PHARMA_TERMS = re.compile(r"pharma|gxp|patient|clinical|genomic|r&d credit|qualified research|part 11|trial|examiner|"
                          r"transfer[ -]pricing|molecule|\bqre\b|enrolled", re.I)
UTILITY_TERMS = re.compile(r"\bferc\b|nerc|\bcip\b|docket|rate case|prudence|commission|operating compan|ceii|work order|"
                           r"transmission|distribution", re.I)


@pytest.mark.parametrize("t,banned", [("pharma", UTILITY_TERMS), ("utility", PHARMA_TERMS)])
def test_tenant_text_does_not_leak_the_other_tenant(t, banned):
    files = copy_files(t) + [tenant_file(t, n) for n in ("access.yaml", "pack.yaml", "names.py")]
    for f in files:
        text = open(f, encoding="utf-8").read()
        hits = sorted({m.group(0) for m in banned.finditer(text)})
        assert not hits, f"{os.path.relpath(f, ROOT)} mentions {hits}"
    merged = json.dumps(load_copy(t), ensure_ascii=False)
    assert not banned.search(merged), (t, sorted({m.group(0) for m in banned.finditer(merged)}))


def test_utility_copy_respects_the_accounting_rules():
    pack = yaml.safe_load(open(tenant_file("utility", "pack.yaml"), encoding="utf-8"))  # values only: comments are notes for developers
    text = json.dumps(load_copy("utility"), ensure_ascii=False) + json.dumps(pack, ensure_ascii=False, default=str)
    assert "CIP compliant" not in text
    assert not re.search(r"\b920\b(?! is A&G salaries)", text), "920 is A&G salaries only; cloud invoices never book there"
    assert not re.search(r"Snowflake Native App|dbt Cloud", text)
    for word in ("demo", "sample", "synthetic", "prototype", "preview"):  # working product: no such word in a visible label
        assert not re.search(rf"\b{word}\b", json.dumps(load_copy("utility")["frontend"]), re.I), word
