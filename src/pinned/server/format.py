"""Compact plain-text renderings an agent (or a person at a terminal) can use directly: one line per item, with the
evidence chain on that line. Shared by the MCP tools and the CLI."""

from __future__ import annotations

from pinned.contract import ApiTimeline, Context, Finding, Hop

OK_STATUSES = ("valid",)


def chain_text(chain: list[Hop]) -> str:
    return " ; ".join(f"{h.src} -{h.rel}-> {h.dst}" + (f" ({h.note})" if h.note else "") for h in chain)


def findings_text(library: str, version: str, findings: list[Finding]) -> str:
    if not findings:
        return f"{library}=={version}: no {library} calls found in this code, nothing to check."
    bad = [f for f in findings if f.status not in OK_STATUSES]
    head = (
        f"{library}=={version}: all {len(findings)} calls are valid."
        if not bad
        else f"{library}=={version}: {len(bad)} of {len(findings)} calls need attention."
    )
    lines = [head]
    for f in sorted(findings, key=lambda f: (f.status in OK_STATUSES, f.line or 0)):
        where = f"line {f.line}: " if f.line else ""
        parts = [f"- {where}{f.call}: {f.status}"]
        if f.fix:
            parts.append(f"-> use {f.fix}")
        if f.chain:
            parts.append(f"[{chain_text(f.chain)}]")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def context_text(ctx: Context) -> str:
    if not ctx.items:
        return f"No facts in the Pinned graph for this task on {ctx.library}=={ctx.version}."
    return ctx.to_prompt()


def timeline_text(tl: ApiTimeline) -> str:
    kind = f" ({tl.kind})" if tl.kind else ""
    lines = [f"{tl.library} {tl.path}{kind} across {len(tl.versions)} versions:"]
    # collapse runs of identical facts so a 40-version ladder stays short
    groups: list[list] = []  # [first, last, status, signature, note]
    for vf in tl.versions:
        if groups and groups[-1][2:] == [vf.status, vf.signature, vf.note]:
            groups[-1][1] = vf.version
        else:
            groups.append([vf.version, vf.version, vf.status, vf.signature, vf.note])
    for first, last, status, sig, note in groups:
        span = first if first == last else f"{first} .. {last}"
        lines.append(f"- {span}: {status}" + (f" {sig}" if sig else "") + (f" ({note})" if note else ""))
    if tl.replaced_by:
        lines.append(f"replaced by: {chain_text(tl.replaced_by)}")
    return "\n".join(lines)
