"""Plain data the ingestion steps pass around (no graph, no network)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

KINDS = ("function", "class", "method", "attr", "module")
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")


def clean_signature(sig: str) -> str:
    """Drop memory addresses from repr() defaults (`<function fmin at 0x7f97ae28ed40>` -> `<function fmin>`): they
    differ in every process and would read as a signature change between versions."""
    return _ADDRESS.sub("", sig) if sig else sig


@dataclass
class Deprecation:
    """One piece of deprecation evidence found in the code of a version."""

    message: str
    source: str            # warn | decorator | docstring | getattr
    scope: str = "object"  # object (the whole API is deprecated) | partial (a parameter / a branch only)


@dataclass
class ApiRecord:
    """One public object of one library version."""

    path: str                      # dotted import path, starts with an import name
    kind: str                      # function | class | method | attr | module
    sig: str = ""                  # "(a, b=1, *, c=None)" — parameters and defaults, no annotations
    doc: str = ""                  # first docstring line
    target: str = ""               # where the object is defined when `path` is a re-export (alias)
    origin: str = "static"         # static | dynamic
    deprecations: list[Deprecation] = field(default_factory=list)

    def to_json(self) -> dict:
        d = asdict(self)
        if not self.deprecations:
            d.pop("deprecations")
        return {k: v for k, v in d.items() if v not in ("", None)}

    @classmethod
    def from_json(cls, d: dict) -> ApiRecord:
        deps = [Deprecation(**x) for x in d.get("deprecations", [])]
        return cls(path=d["path"], kind=d["kind"], sig=clean_signature(d.get("sig", "")), doc=d.get("doc", ""),
                   target=d.get("target", ""), origin=d.get("origin", "static"), deprecations=deps)


@dataclass
class Snapshot:
    """The public API of one exact release, as loaded from its wheel (plus dynamic inspection)."""

    library: str
    version: str                   # exact PyPI release
    import_names: list[str]
    source_file: str               # wheel / sdist file name the code came from
    python: str = ""               # interpreter used for dynamic inspection ("" = none needed)
    dynamic_modules: list[str] = field(default_factory=list)   # modules filled by dynamic inspection
    dynamic_errors: list[str] = field(default_factory=list)    # modules dynamic inspection could not import
    apis: dict[str, ApiRecord] = field(default_factory=dict)
    module_files: dict[str, str] = field(default_factory=dict)  # public module path -> extracted source file
    gone: dict[str, str] = field(default_factory=dict)          # probed path -> the library's own error message

    def to_json(self) -> dict:
        return {
            "library": self.library, "version": self.version, "import_names": self.import_names,
            "source_file": self.source_file, "python": self.python, "dynamic_modules": self.dynamic_modules,
            "dynamic_errors": self.dynamic_errors, "module_files": self.module_files, "gone": self.gone,
            "apis": [a.to_json() for a in sorted(self.apis.values(), key=lambda a: a.path)],
        }

    @classmethod
    def from_json(cls, d: dict) -> Snapshot:
        apis = {a["path"]: ApiRecord.from_json(a) for a in d["apis"]}
        return cls(library=d["library"], version=d["version"], import_names=d["import_names"],
                   source_file=d["source_file"], python=d.get("python", ""),
                   dynamic_modules=d.get("dynamic_modules", []), dynamic_errors=d.get("dynamic_errors", []), apis=apis,
                   module_files=d.get("module_files", {}), gone=d.get("gone", {}))
