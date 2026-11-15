"""Runs INSIDE a throwaway venv that has the library installed (any Python >= 3.7; keep the syntax 3.7-compatible).

    python -I inspect_worker.py <request.json> <result.json>

request: {"modules": ["scipy.special", ...], "own": ["scipy"], "path": [optional dirs put first on sys.path]}
result:  {"griffe": "<version>", "python": "3.10.13", "records": [...], "errors": {"module": "import error"},
          "notes": {"module": "griffe failed, runtime walk used"}}

Module members come from griffe's dynamic inspection (`griffe.inspect`, or `griffe.agents.inspector.inspect` on
griffe 0.x). Members griffe models without parameters (numpy ufuncs, C builtins) get their parameters from
`inspect.signature` or from the "name(params)" first line of their docstring. Class members are read at runtime along
the MRO, keeping only members defined by classes of the same distribution.
"""

import importlib
import inspect
import json
import sys
import warnings

try:  # griffe >= 0.40
    from griffe import inspect as griffe_inspect
except ImportError:  # griffe 0.x (the newest griffe that installs on Python 3.7)
    from griffe.agents.inspector import inspect as griffe_inspect

KINDS = {
    inspect.Parameter.POSITIONAL_ONLY: "positional-only",
    inspect.Parameter.POSITIONAL_OR_KEYWORD: "positional or keyword",
    inspect.Parameter.VAR_POSITIONAL: "variadic positional",
    inspect.Parameter.KEYWORD_ONLY: "keyword-only",
    inspect.Parameter.VAR_KEYWORD: "variadic keyword",
}
GRIFFE_KINDS = {"positional-only", "positional or keyword", "variadic positional", "keyword-only", "variadic keyword"}


def first_line(doc):
    for line in (doc or "").splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def summary_line(name, doc):
    """First docstring line, skipping a leading "name(params)" signature line (ufuncs, C builtins)."""
    lines = [l.strip() for l in (doc or "").splitlines() if l.strip()]
    if lines and name and lines[0].startswith(name + "(") and len(lines) > 1:
        return lines[1][:200]
    return lines[0][:200] if lines else ""


def doc_signature(name, doc):
    """'i1(x, /, out=None, *, where=True)' -> '(x, /, out=None, *, where=True)' when the docstring starts with it."""
    line = first_line(doc)
    if not line.startswith(name + "("):
        return ""
    depth = 0
    for i, ch in enumerate(line[len(name):], start=len(name)):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return line[len(name):i + 1]
    return ""


def runtime_params(obj):
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return None
    out = []
    for p in sig.parameters.values():
        default = None if p.default is inspect.Parameter.empty else repr(p.default)
        out.append([p.name, KINDS[p.kind], default])
    return out


def griffe_params(gobj):
    try:
        params = list(gobj.parameters)
    except Exception:
        return None
    out = []
    for p in params:
        kind = p.kind.value if p.kind is not None else "positional or keyword"
        if kind not in GRIFFE_KINDS:
            kind = "positional or keyword"
        default = None if p.default is None or kind.startswith("variadic") else str(p.default)
        out.append([p.name, kind, default])
    return out


def own(path, owners):
    return bool(path) and path.split(".", 1)[0] in owners


def describe(path, obj, kind_hint, gobj=None):
    """One record for a runtime object."""
    doc = getattr(obj, "__doc__", None)
    doc = doc if isinstance(doc, str) else ""
    rec = {"path": path, "kind": kind_hint, "doc": summary_line(str(getattr(obj, "__name__", "") or ""), doc)}
    if inspect.isclass(obj):
        rec["kind"] = "class"
        init = obj.__dict__.get("__init__") if "__init__" in getattr(obj, "__dict__", {}) else None
        params = runtime_params(init) if init is not None and inspect.isfunction(init) else runtime_params(obj)
        if params is not None:
            rec["params"] = params
            rec["drop_first"] = bool(params and params[0][0] in ("self", "cls"))
        return rec
    if inspect.ismodule(obj):
        rec["kind"] = "module"
        return rec
    if callable(obj):
        rec["kind"] = kind_hint if kind_hint in ("function", "method") else "function"
        params = griffe_params(gobj) if gobj is not None else None
        if not params:
            params = runtime_params(obj)
        if params:
            rec["params"] = params
            rec["drop_first"] = rec["kind"] == "method" and params[0][0] in ("self", "cls")
        else:
            name = getattr(obj, "__name__", path.rsplit(".", 1)[-1])
            sig = doc_signature(str(name), doc) or doc_signature(path.rsplit(".", 1)[-1], doc)
            if sig:
                rec["sig"] = sig
        return rec
    rec["kind"] = "attr"
    return rec


def class_members(cls_path, cls, owners, records, seen):
    for name in sorted(dir(cls)):
        if name.startswith("_"):
            continue
        definer = None
        for klass in getattr(cls, "__mro__", ())[:-1] or (cls,):
            if name in getattr(klass, "__dict__", {}):
                definer = klass
                break
        if definer is None or not own(getattr(definer, "__module__", "") or "", owners):
            continue
        raw = definer.__dict__[name]
        try:
            obj = getattr(cls, name)
        except Exception:
            obj = raw
        path = cls_path + "." + name
        if isinstance(raw, (property,)) or type(raw).__name__ in ("getset_descriptor", "member_descriptor",
                                                                   "cached_property"):
            rec = {"path": path, "kind": "attr", "doc": first_line(getattr(raw, "__doc__", "") or "")}
        elif inspect.isclass(obj):
            rec = describe(path, obj, "class")
        else:
            rec = describe(path, obj, "method")
            if isinstance(raw, staticmethod) and "params" in rec:
                rec["drop_first"] = False
        target = "%s.%s.%s" % (definer.__module__, definer.__qualname__, name)
        if target != path:
            rec["target"] = target
        if path not in seen:
            seen.add(path)
            records.append(rec)


def module_records(name, owners, records, errors, notes, seen):
    try:
        mod = importlib.import_module(name)
    except BaseException as e:  # noqa: BLE001 - import of foreign code: anything can be raised
        errors[name] = ("%s: %s" % (type(e).__name__, e))[:300]
        return
    try:
        gmod = griffe_inspect(name)
    except BaseException as e:  # noqa: BLE001 - old griffe trips on some runtime objects: walk the module ourselves
        notes[name] = ("griffe inspection failed, runtime walk used: %s: %s" % (type(e).__name__, e))[:300]
        gmod = None
    exports = getattr(mod, "__all__", None)
    exports = set(x for x in exports if isinstance(x, str)) if isinstance(exports, (list, tuple, set)) else None
    file = getattr(mod, "__file__", "") or ""
    hub = file.endswith("__init__.py") or name.rsplit(".", 1)[-1] == "api" or not file.endswith(".py")
    gmembers = gmod.members if gmod is not None else {}
    try:
        runtime_names = set(dir(mod))  # includes names a module makes at runtime (__getattr__ + __dir__)
    except Exception:
        runtime_names = set()
    names = set(gmembers) | (exports or set()) | runtime_names
    for mname in sorted(names):
        if mname.startswith("_") or (exports is not None and mname not in exports):
            continue
        try:
            obj = getattr(mod, mname)
        except Exception:
            continue
        gobj = gmembers.get(mname)
        if gobj is None:
            omod = getattr(obj, "__name__", None) if inspect.ismodule(obj) else getattr(obj, "__module__", None)
            is_alias = not (isinstance(omod, str) and omod == name) or inspect.ismodule(obj)
        else:
            is_alias = gobj is None or gobj.is_alias
        target = ""
        if is_alias:
            target = gobj.target_path if gobj is not None else ""
            if not target:
                omod = getattr(obj, "__module__", None)
                qual = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None)
                if inspect.ismodule(obj):
                    target = obj.__name__
                elif isinstance(omod, str) and isinstance(qual, str):
                    target = omod + "." + qual
            if target and not own(target, owners):
                continue
            if exports is None and not hub:
                continue
        path = name + "." + mname
        if path in seen:
            continue
        if inspect.ismodule(obj):
            if not own(obj.__name__, owners):
                continue
            rec = {"path": path, "kind": "module", "doc": first_line(obj.__doc__ or "")}
            if obj.__name__ != path:
                rec["target"] = obj.__name__
            seen.add(path)
            records.append(rec)
            continue
        rec = describe(path, obj, "function", None if is_alias else gobj)
        if target and target != path:
            rec["target"] = target
        seen.add(path)
        records.append(rec)
        if inspect.isclass(obj):
            class_members(path, obj, owners, records, seen)


def main():
    warnings.simplefilter("ignore")
    with open(sys.argv[1]) as f:
        req = json.load(f)
    for entry in reversed(req.get("path") or []):
        sys.path.insert(0, entry)
    owners = set(req["own"])
    records, errors, notes, seen = [], {}, {}, set()
    for name in req["modules"]:
        module_records(name, owners, records, errors, notes, seen)
    try:
        import importlib.metadata as md
    except ImportError:
        md = None
    gver = ""
    if md is not None:
        try:
            gver = md.version("griffe")
        except Exception:
            gver = ""
    out = {"griffe": gver, "python": "%d.%d.%d" % sys.version_info[:3], "records": records, "errors": errors,
           "notes": notes}
    with open(sys.argv[2], "w") as f:
        json.dump(out, f)


if __name__ == "__main__":
    main()
