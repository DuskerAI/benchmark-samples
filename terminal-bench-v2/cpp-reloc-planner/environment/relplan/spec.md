# Offline release link-map contract

Relplan manifests describe the object and archive inventory for an offline release admission decision. They are UTF-8 text. Blank lines and `#` comments are ignored. Names do not contain spaces. Integers are decimal or `0x` prefixed hexadecimal. Top-level blocks are processed in file order.

```text
object NAME
  section SECTION SIZE ALIGN [GROUP]
  got SYMBOL
  define SYMBOL SECTION OFFSET strong|weak
  alias SYMBOL TARGET strong|weak
  common SYMBOL SIZE ALIGN
  need SYMBOL strong|weak
  reloc SECTION OFFSET R_ABS32|R_PC32 SYMBOL ADDEND
end

archive NAME
  member NAME
    section SECTION SIZE ALIGN [GROUP]
    got SYMBOL
    define SYMBOL SECTION OFFSET strong|weak
    alias SYMBOL TARGET strong|weak
    common SYMBOL SIZE ALIGN
    need SYMBOL strong|weak
    reloc SECTION OFFSET R_ABS32|R_PC32 SYMBOL ADDEND
  endmember
endarchive

group NAME
  archive NAME
    member NAME
      section SECTION SIZE ALIGN [GROUP]
      got SYMBOL
      define SYMBOL SECTION OFFSET strong|weak
      alias SYMBOL TARGET strong|weak
      common SYMBOL SIZE ALIGN
      need SYMBOL strong|weak
      reloc SECTION OFFSET R_ABS32|R_PC32 SYMBOL ADDEND
    endmember
  endarchive
endgroup
```

Root objects are always included. A section may carry an optional COMDAT group name after its alignment; `-` or an omitted name means ordinary non-COMDAT input. For each COMDAT group name, only the first included input section with that group contributes bytes, symbols, and relocations; later sections with that same group are discarded after archive inclusion, so their section-defined symbols are absent and their relocations are omitted. An object or member may contain `got SYMBOL` records. Each distinct referenced symbol allocates one 8-byte `.got` slot, aligned to 8, in first requesting included-object order. The planner emits a synthetic strong symbol named `__got_SYMBOL`, defined by the first requester, at the slot address; the slot value is the target symbol address, or 0 when the target is unresolved. `.got` is ordered after `.data` and before `.bss`. An archive is scanned when its block is reached; members are extracted in member order if they define a currently unresolved strong need with a strong definition, a strong alias, or a common definition. Weak definitions and weak aliases do not satisfy a strong need for archive extraction. Extraction can add new needs, so the same archive is rescanned until no more members can be extracted. Later objects do not rescan earlier archives, and weak needs do not trigger extraction. A group contains only archives. When a group block is reached, scan its archives in group order and rescan the whole group until a complete pass extracts no new members; members still retain labels from their own archive, as `ARCHIVE(MEMBER)`, and a member already extracted is not extracted again during later group passes.

Strong definitions and strong aliases override weak and common definitions. Among weak definitions and weak aliases, the first included definition or alias wins. If duplicate strong definitions or strong aliases for the same symbol are included, report the duplicate error but keep the first included strong definition or alias as the selected symbol for entry checks, relocations, aliases, and the `symbols` array. An alias defines `SYMBOL` at the final address and section of `TARGET`, has size 0, uses the alias contributor as `defined_by`, and participates in archive extraction according to its own binding. Alias chains are followed after ordinary symbol precedence is chosen; a strong alias whose target is absent reports `undefined symbol TARGET needed by LABEL`, a weak alias whose target is absent resolves to address 0 with an empty section, and any alias cycle reports `alias cycle SYMBOL in LABEL` for every included alias in the cycle. Common definitions allocate one symbol in `.bss` using the largest size and largest alignment unless a strong definition or strong alias exists for the same symbol. When a strong definition or strong alias exists, common records for that symbol do not appear in `symbols` and do not contribute to `.bss` size, alignment, or placement. The common symbol's `defined_by` label is the included object or archive member that supplied the selected largest size; if sizes tie, use the contributor with the largest alignment, and if both tie, use the first included contributor. Duplicate strong definitions and strong aliases for the same symbol and unresolved strong needs are link errors; unresolved weak needs resolve to address 0.

Output sections are `.text`, `.rodata`, `.data`, `.got`, `.bss`, then any other section names in lexical order. Emit only sections that contain included input bytes or allocated common symbols; omit empty standard sections. Each emitted output section starts after aligning the current address to 16, beginning at `0x1000`, and its map `alignment` field is this fixed output-section boundary value, always 16. Included input sections are placed in included-object order and aligned to their own `ALIGN`. GOT slots are placed in first-request order. Common symbols are placed at the end of `.bss` in symbol-name order. `R_ABS32` is `S + A`; `R_PC32` is `S + A - P`, where `S` is the target symbol address, `A` the addend, and `P` is the placed in-memory address of the input section contribution that contains the relocation plus the relocation's manifest offset. Equivalently, `P` is the output section base plus that included object's aligned offset within the output section plus the relocation offset.

The map file is JSON with `status`, `entry`, `included`, `sections`, `symbols`, and `relocations`. Arrays must have deterministic order: included order for `included`, output-section order for `sections`, symbol-name order for `symbols`, and included-object order for `relocations`. Root object labels are the object name. Archive member labels are `ARCHIVE(MEMBER)`, and the same label format is used in `included`, symbol `defined_by`, relocation `object`, and diagnostics. Each section records `name`, `address`, `size`, and `alignment`. Each symbol records `name`, `binding`, `section`, `address`, `size`, and `defined_by`; symbols selected from `common` records use `binding` exactly `common`. Each relocation records `object`, `section`, `offset`, `type`, `symbol`, and `value`; the `offset` field is the raw offset from the manifest record, not the final absolute address. On success the diagnostics file contains exactly `OK\n`; otherwise it contains one lexicographically sorted diagnostic per line and the map status is `error`.

Error diagnostics use these exact forms: `undefined entry ENTRY`, `undefined symbol SYMBOL needed by LABEL`, `duplicate strong symbol SYMBOL in LABEL (first LABEL)`, and `alias cycle SYMBOL in LABEL`. If the same error is discovered more than once, emit it once.
