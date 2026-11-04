"""The active tenant pack (backend/tenants/<COSTIQ_TENANT>/): data, copy and a few named hooks that vary per tenant.

The engines are shared; a tenant changes only what is listed in backend/tenants/__init__.py:
  pack()           pack.yaml: sample add-ons, schema, register tables, default rules, cost classes, use-case ids, ...
  copy()           copy.json merged with the fragments in copy/*.json (load_copy); a leaf defined twice is an error
  text(key, **v)   backend copy (merged "backend" section), formatted with str.format(**v)
  frontend_copy()  the merged "frontend" section, served by /api/config and /api/me
  hooks()          tenants/<t>/hooks.py (findings, unlock, Board Pack sections, summary sentences)
  names()          tenants/<t>/names.py (name tables for the shared sample generators)
  usecase(role)    a use-case id the shared routes call by role (pack.yaml `usecases`)
"""
from __future__ import annotations
import functools, glob, importlib, json, os, sys
import yaml
from .config import ROOT, settings, tenant_dir

if ROOT not in sys.path:  # tenants/ is a package next to costiq/
    sys.path.insert(0, ROOT)


def name() -> str:
    return settings.tenant


@functools.lru_cache(maxsize=None)
def pack() -> dict:
    with open(os.path.join(tenant_dir(settings.tenant), "pack.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def path(rel: str) -> str:
    """A path from pack.yaml (relative to backend/) as an absolute path."""
    return os.path.join(ROOT, *rel.replace("\\", "/").split("/"))


def _merge(into: dict, add: dict, source: str, prefix: str = "") -> None:
    """Deep-merge `add` into `into`: dicts merge key by key; a leaf (anything that is not a dict, lists included) defined
    twice raises ValueError naming the file and the dotted key."""
    for k, v in add.items():
        key = f"{prefix}{k}"
        if k in into and isinstance(into[k], dict) and isinstance(v, dict):
            _merge(into[k], v, source, key + ".")
        elif k in into:
            raise ValueError(f"{source}: copy key {key!r} is already defined")
        else:
            into[k] = v


@functools.lru_cache(maxsize=None)
def load_copy(tenant: str) -> dict:
    """A tenant's copy: tenants/<t>/copy.json, then every tenants/<t>/copy/*.json fragment in filename order, deep-merged
    (fragment shape {"backend": {...}, "frontend": {...}}, either section optional). Treat the result as read-only."""
    d = tenant_dir(tenant)
    files = [os.path.join(d, "copy.json")] + sorted(glob.glob(os.path.join(d, "copy", "*.json")))
    out: dict = {}
    for f in files:
        with open(f, encoding="utf-8") as fh:
            _merge(out, json.load(fh), os.path.relpath(f, ROOT))
    return out


def copy() -> dict:
    """The active tenant's merged copy (see load_copy)."""
    return load_copy(settings.tenant)


def frontend_copy() -> dict:
    return copy().get("frontend", {})


def lookup(key: str, section: str = "backend"):
    """A copy value by dotted key ("risk.reasons.no_benefit") from one section of copy.json; KeyError when missing."""
    node = copy()[section]
    for part in key.split("."):
        node = node[part]
    return node


def text(key: str, **values) -> str:
    """Backend copy as text. A list value is joined with newlines (long templates are stored one line per item);
    named {placeholders} are filled from `values`."""
    v = lookup(key)
    if isinstance(v, list):
        v = "\n".join(v)
    return v.format(**values) if values else v


def usecase(role: str):
    """Use-case id (or ids) the shared code calls by role, e.g. usecase("bridge") -> "PH-05" for pharma."""
    return pack()["usecases"][role]


@functools.lru_cache(maxsize=None)
def hooks():
    return importlib.import_module(f"tenants.{settings.tenant}.hooks")


@functools.lru_cache(maxsize=None)
def names():
    return importlib.import_module(f"tenants.{settings.tenant}.names")
