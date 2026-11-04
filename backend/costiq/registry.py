"""Use-case registry: the catalog use cases (FinOps Foundation, AI and the tenant's own) as executable, versioned YAML
entries. The shared folder (registry/usecases) is read first, then the tenant's (tenants/<t>/usecases).

A YAML file may use {{name}} placeholders; they are filled from the tenant pack's `usecase_vars` before parsing, so a
shared use case can carry a tenant's value (GV-02's regulated data class, for example)."""
from __future__ import annotations
import datetime as dt, glob, os, re
import yaml


def _modules() -> dict:
    """Module key -> label, from the tenant's access.yaml (the module keys are the same for every tenant)."""
    try:
        from .auth import access
        return dict(access().get("modules", {}))
    except Exception:  # noqa: BLE001 - labels are informational
        return {}


MODULES = _modules()
VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _vars() -> dict:
    try:
        from .tenant import pack
        return dict(pack().get("usecase_vars") or {})
    except Exception:  # noqa: BLE001
        return {}


def _fill(text: str, values: dict, path: str) -> str:
    def sub(m):
        if m.group(1) not in values:
            raise KeyError(f"{path}: no usecase_vars entry for {{{{{m.group(1)}}}}} in the tenant pack")
        return str(values[m.group(1)])
    return VAR.sub(sub, text)


def _clean_notes(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("- "):
        parts = [p.strip() for p in re.split(r"(?:^|\s)- (?=[A-Z\"`(])", text) if p.strip()]
        parts = [p for p in parts if not p.lower().startswith(("next use case", "previous use case"))]
        text = " ".join(p if p.endswith(".") else p + "." for p in parts)
    return text


def directories(spec: str | list[str] | tuple) -> list[str]:
    """One folder, a list of folders, or folders joined with os.pathsep (settings.registry_dir)."""
    if isinstance(spec, (list, tuple)):
        return [d for d in spec if d]
    return [d for d in str(spec).split(os.pathsep) if d]


class Registry:
    def __init__(self, directory: str | list[str] | None = None):
        if directory is None:
            from .config import settings
            directory = settings.registry_dir
        self.directories = directories(directory)
        self.items: dict[str, dict] = {}
        values = _vars()
        for d in self.directories:
            for path in sorted(glob.glob(os.path.join(d, "*.yaml"))):
                with open(path, encoding="utf-8") as fh:
                    uc = yaml.safe_load(_fill(fh.read(), values, path))
                uc["notes"] = _clean_notes(uc.get("notes", ""))
                self.items[uc["id"]] = uc
        self.order = sorted(self.items, key=lambda k: (k[0] != "F", k.split("-")[0], int(k.split("-")[1])))

    def files(self) -> list[str]:
        return [p for d in self.directories for p in sorted(glob.glob(os.path.join(d, "*.yaml")))]

    def get(self, uid: str) -> dict:
        if uid not in self.items:
            raise KeyError(uid)
        return self.items[uid]

    def summary(self, uc: dict) -> dict:
        return {k: uc.get(k) for k in ("id", "title", "category", "module", "business_question", "source",
                                         "personas", "catalog_persona", "datasets", "products")} | {
            "params": uc.get("params", [])}

    def resolve_params(self, uc: dict, given: dict | None) -> dict:
        out = {}
        for p in uc.get("params", []):
            v = (given or {}).get(p["name"], p.get("default"))
            if p.get("type") == "date" and isinstance(v, str):
                v = dt.datetime.fromisoformat(v)
            out[p["name"]] = v
        return out
