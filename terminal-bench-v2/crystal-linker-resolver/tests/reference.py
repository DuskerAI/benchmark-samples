"""Independent reference implementation of the crlink linked-image contract.

This mirrors environment/task_file/spec.md with logic written from scratch
in Python, so the verifier can compute the expected linked image for any job
without trusting the candidate's Crystal implementation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

BASE = 0x1000
ALIGN = 4


@dataclass
class Sym:
    name: str
    binding: str  # "STRONG" | "WEAK"
    section: str
    offset: int
    defined: bool
    common_size: int = 0
    common_align: int = 1


@dataclass
class Section:
    name: str
    comdat: str | None
    size: int


@dataclass
class Reloc:
    section: str
    offset: int
    target: str
    kind: str
    addend: int


@dataclass
class Obj:
    id: str
    symbols: list = field(default_factory=list)
    sections: list = field(default_factory=list)
    relocs: list = field(default_factory=list)


def _parse_obj_lines(obj: Obj, lines):
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split(" ")
        if f[0] == "OBJECT":
            obj.id = f[1]
        elif f[0] == "SECTION":
            comdat = None if f[2] == "-" else f[2]
            obj.sections.append(Section(f[1], comdat, int(f[3])))
        elif f[0] == "SYMBOL":
            name, binding = f[1], f[2]
            if binding == "COMMON":
                obj.symbols.append(
                    Sym(name, binding, "", 0, True, int(f[3]), int(f[4]))
                )
            elif f[3] == "UNDEF":
                obj.symbols.append(Sym(name, binding, "", 0, False))
            else:
                obj.symbols.append(Sym(name, binding, f[4], int(f[5]), True))
        elif f[0] == "RELOC":
            obj.relocs.append(Reloc(f[1], int(f[2]), f[3], f[4], int(f[5])))
        else:
            raise ValueError(f"unknown record: {f[0]}")


def parse_object(text: str, default_id: str) -> Obj:
    obj = Obj(default_id)
    _parse_obj_lines(obj, text.splitlines())
    return obj


def parse_archive(text: str, default_id: str):
    members = []
    current = None
    buf: list = []

    def flush():
        nonlocal current, buf
        if current is not None:
            _parse_obj_lines(current, buf)
            members.append(current)
        buf = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split(" ")
        if f[0] == "ARCHIVE":
            continue
        if f[0] == "MEMBER":
            flush()
            current = Obj(f[1])
        else:
            buf.append(line)
    flush()
    return members


def _to_signed32(v: int) -> int:
    low = v & 0xFFFFFFFF
    return low - 0x100000000 if low >= 0x80000000 else low


def link_job(job_dir: str) -> dict:
    with open(os.path.join(job_dir, "link.list")) as fh:
        entries = [
            e.strip()
            for e in fh
            if e.strip() and not e.strip().startswith("#")
        ]

    inputs = []  # ("obj", Obj) or ("arc", [members])
    for entry in entries:
        text = open(os.path.join(job_dir, entry)).read()
        if entry.endswith(".cra"):
            inputs.append(("arc", parse_archive(text, entry)))
        else:
            inputs.append(("obj", parse_object(text, entry)))

    included: list = []
    defs: dict = {}  # name -> (obj, Sym)
    undef: set = set()
    errors: list = []

    def add_definition(obj, s):
        existing = defs.get(s.name)
        if existing is None:
            defs[s.name] = (obj, s)
            return
        old_obj, old = existing
        eb = old.binding
        if eb == "STRONG" and s.binding == "STRONG":
            errors.append(f"duplicate strong symbol: {s.name}")
            return
        if eb == "STRONG":
            return
        if s.binding == "STRONG":
            defs[s.name] = (obj, s)
            return
        if eb == "COMMON" and s.binding == "COMMON":
            old.common_size = max(old.common_size, s.common_size)
            old.common_align = max(old.common_align, s.common_align)
            return
        if eb == "COMMON":
            return
        if s.binding == "COMMON":
            defs[s.name] = (obj, s)

    def include_object(obj):
        included.append(obj)
        for s in obj.symbols:
            if s.defined:
                add_definition(obj, s)
                undef.discard(s.name)
            else:
                if s.name not in defs:
                    undef.add(s.name)
        for r in obj.relocs:
            if r.target not in defs:
                undef.add(r.target)

    def resolve_archive(members):
        pulled = set()
        while True:
            progressed = False
            for m in members:
                if m.id in pulled:
                    continue
                needed = any(s.defined and s.name in undef for s in m.symbols)
                if needed:
                    pulled.add(m.id)
                    include_object(m)
                    progressed = True
            if not progressed:
                break

    for kind, payload in inputs:
        if kind == "obj":
            include_object(payload)
        else:
            resolve_archive(payload)

    # layout with COMDAT dedup
    section_base: dict = {}
    comdat_owner: dict = {}
    layout: list = []
    cursor = BASE
    for obj in included:
        for sec in obj.sections:
            if sec.comdat is not None:
                if sec.comdat in comdat_owner:
                    continue
                comdat_owner[sec.comdat] = (obj.id, sec.name)
            cursor = (cursor + ALIGN - 1) // ALIGN * ALIGN
            section_base[(obj.id, sec.name)] = cursor
            layout.append((obj, sec))
            cursor += sec.size

    common_names = []
    seen_common = set()
    for obj in included:
        for sym in obj.symbols:
            final = defs.get(sym.name)
            if (
                sym.binding == "COMMON"
                and final is not None
                and final[1].binding == "COMMON"
                and sym.name not in seen_common
            ):
                seen_common.add(sym.name)
                common_names.append(sym.name)

    common_addr = {}
    common_base = None
    common_size = 0
    if common_names:
        max_align = max(defs[name][1].common_align for name in common_names)
        cursor = (cursor + max_align - 1) // max_align * max_align
        common_base = cursor
        for name in common_names:
            sym = defs[name][1]
            cursor = (cursor + sym.common_align - 1) // sym.common_align * sym.common_align
            common_addr[name] = cursor
            cursor += sym.common_size
        common_size = cursor - common_base

    def section_addr(objid, section, comdat):
        if comdat is not None and comdat in comdat_owner:
            owner = comdat_owner[comdat]
            return section_base.get((owner[0], owner[1]))
        return section_base.get((objid, section))

    def sec_comdat(obj, section):
        for x in obj.sections:
            if x.name == section:
                return x.comdat
        return None

    def symbol_address(name):
        rs = defs.get(name)
        if rs is None:
            return None
        obj, s = rs
        if s.binding == "COMMON":
            return common_addr.get(name)
        comdat = sec_comdat(obj, s.section)
        base = section_addr(obj.id, s.section, comdat)
        if base is None:
            return None
        return base + s.offset

    # relocations
    relocs_out = []
    for obj in included:
        for r in obj.relocs:
            comdat = sec_comdat(obj, r.section)
            if comdat is not None and comdat in comdat_owner:
                owner = comdat_owner[comdat]
                if not (owner[0] == obj.id and owner[1] == r.section):
                    continue
            p_base = section_addr(obj.id, r.section, comdat)
            if p_base is None:
                continue
            p = p_base + r.offset
            s = symbol_address(r.target)
            if s is None:
                errors.append(f"unresolved symbol in reloc: {r.target}")
                continue
            site = f"{obj.id}/{r.section}+{r.offset}"
            if r.kind == "REL32":
                rawv = s + r.addend - p
                if rawv < -2147483648 or rawv > 2147483647:
                    errors.append(f"REL32 overflow at {site}")
                value = _to_signed32(rawv)
            elif r.kind == "ABS32":
                rawv = s + r.addend
                if rawv < 0 or rawv > 4294967295:
                    errors.append(f"ABS32 overflow at {site}")
                value = rawv & 0xFFFFFFFF
            elif r.kind == "ABS16":
                rawv = s + r.addend
                if rawv < 0 or rawv > 65535:
                    errors.append(f"ABS16 overflow at {site}")
                value = rawv & 0xFFFF
            else:
                errors.append(f"unknown reloc kind: {r.kind}")
                value = 0
            relocs_out.append({
                "object": obj.id,
                "section": r.section,
                "offset": r.offset,
                "kind": r.kind,
                "value": value,
            })

    sections_out = []
    for obj, sec in layout:
        sections_out.append({
            "name": sec.name,
            "base": section_base[(obj.id, sec.name)],
            "size": sec.size,
        })
    if common_base is not None:
        sections_out.append({
            "name": ".common",
            "base": common_base,
            "size": common_size,
        })

    symbols_out = {}
    for name in sorted(defs.keys()):
        addr = symbol_address(name)
        if addr is None:
            continue
        symbols_out[name] = {
            "address": addr,
            "binding": defs[name][1].binding,
        }

    return {
        "sections": sections_out,
        "symbols": symbols_out,
        "relocs": relocs_out,
        "errors": errors,
    }
