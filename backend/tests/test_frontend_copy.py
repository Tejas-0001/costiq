"""The web app's sources are tenant-neutral: every word a user sees that belongs to one tenant (pharma or utility) comes
from the tenant copy (/api/config, /api/me), never from a literal in frontend/components, frontend/lib or frontend/app.
The deployment planner (components/deploy/) is exempt: it holds both tenants' frameworks and picks one from /api/config.

String literals, template text and JSX text are read with the TypeScript compiler (frontend/node_modules/typescript), so
comments, identifiers, regular expressions and import paths are not scanned. Skipped when Node or the frontend's
node_modules are not present (for example in the backend image). Runs the same for every tenant."""
import json, os, re, shutil, subprocess
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FRONTEND = os.path.join(ROOT, "frontend")
TS = os.path.join(FRONTEND, "node_modules", "typescript")
SCAN = ["components", "lib", "app"]
EXEMPT = [os.path.join("components", "deploy") + os.sep]

PHARMA_TERMS = re.compile(r"pharma|gxp|patient|clinical|\btrials?\b|r&d credit|\bqre\b|examiner|transfer[ -]pricing|molgen|"
                          r"veeva|genomic|part 11|\bstud(?:y|ies)\b", re.I)
UTILITY_TERMS = re.compile(r"\bferc\b|\bnerc\b|\bcip\b|transmission|distribution|rate case|operating compan|work order", re.I)
# a bare lowercase key ("pharma", "cip_compliance", "transmission_finance") is an id the code switches on, not a label
KEY = re.compile(r"^[a-z0-9_]+$")

EXTRACT = r"""
const ts = require(process.argv[2]);
const fs = require("fs");
const out = {};
for (const file of JSON.parse(fs.readFileSync(0, "utf8"))) {
  const text = fs.readFileSync(file, "utf8");
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const found = [];
  const visit = (n) => {
    if (ts.isImportDeclaration(n) || ts.isExportDeclaration(n) || ts.isExternalModuleReference(n)) return;
    if (ts.isCallExpression(n) && n.expression.kind === ts.SyntaxKind.ImportKeyword) return;
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isTemplateHead(n) || ts.isTemplateMiddle(n) ||
        ts.isTemplateTail(n) || ts.isJsxText(n)) {
      const v = (n.text || "").trim();
      if (v) found.push([sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1, v]);
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  out[file] = found;
}
process.stdout.write(JSON.stringify(out));
"""


def sources() -> list[str]:
    out = []
    for d in SCAN:
        for base, _, files in os.walk(os.path.join(FRONTEND, d)):
            for f in files:
                if f.endswith((".ts", ".tsx")) and not f.endswith(".d.ts"):
                    p = os.path.join(base, f)
                    rel = os.path.relpath(p, FRONTEND)
                    if not any(rel.startswith(e) for e in EXEMPT):
                        out.append(p)
    return sorted(out)


@pytest.fixture(scope="module")
def literals(tmp_path_factory):
    node = shutil.which("node")
    if not node or not os.path.isdir(TS):
        pytest.skip("Node or frontend/node_modules/typescript not present")
    script = tmp_path_factory.mktemp("frontend-copy") / "extract.js"
    script.write_text(EXTRACT, encoding="utf-8")
    files = sources()
    assert files, "no frontend sources found"
    r = subprocess.run([node, str(script), TS], input=json.dumps(files), capture_output=True, text=True, encoding="utf-8",
                       timeout=180)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def hits(literals: dict, terms: re.Pattern) -> dict[str, list[str]]:
    out = {}
    for file, found in literals.items():
        rel = os.path.relpath(file, FRONTEND).replace("\\", "/")
        for line, text in found:
            if KEY.match(text):
                continue
            for m in terms.finditer(text):
                out.setdefault(rel, []).append(f"{line}: ...{text[max(0, m.start() - 40):m.end() + 30]}...")
    return out


@pytest.mark.parametrize("words", ["pharma", "utility"])
def test_frontend_sources_carry_no_tenant_wording(literals, words):
    found = hits(literals, PHARMA_TERMS if words == "pharma" else UTILITY_TERMS)
    assert not found, json.dumps({k: v[:4] for k, v in found.items()}, indent=1)
