# crlink object format and linked-image contract

`crlink` combines a set of relocatable inputs into a single linked image. This
document defines the input object format, the archive format, and the exact
semantics of the linked image the tool must produce. Inputs are ASCII and
line-oriented so that results are fully deterministic.

## Invocation

    crlink <job-dir> <out-file>

`<job-dir>/link.list` names the inputs one per line in **link order**. Blank
lines and lines beginning with `#` are ignored. An entry ending in `.cro` is a
standalone object; an entry ending in `.cra` is an archive. Each entry is a path
relative to `<job-dir>`. The linked image is written to `<out-file>` as JSON.

## Object format (`.cro`)

Each record is one line; fields are separated by single spaces.

    OBJECT <id>
    SECTION <name> <comdat-key|-> <size> <hexbytes|->
    SYMBOL <name> <STRONG|WEAK> DEF <section> <offset>
    SYMBOL <name> <STRONG|WEAK> UNDEF
    SYMBOL <name> COMMON <size> <alignment>
    RELOC <section> <offset> <target> <REL32|ABS32|ABS16> <addend>

`<comdat-key>` is `-` when the section is not part of a COMDAT group. `<size>`
is a byte count; `<hexbytes>` is `2*size` hex digits or `-` for zero-filled.
`<offset>` and `<addend>` are decimal integers; `<addend>` may be negative.
For a `COMMON` record, `<size>` is a non-negative byte count and `<alignment>`
is a positive power of two.

## Archive format (`.cra`)

    ARCHIVE <id>
    MEMBER <member-id>
    <object records for this member>
    MEMBER <member-id>
    <object records for this member>

Members appear in a fixed order. A member is an object body without its own
`OBJECT` line; its identity is the `<member-id>`.

## Symbol resolution

Global symbols are resolved across all included inputs by name.

- A **strong** definition always takes precedence over a **weak** definition,
  regardless of the order in which they are encountered.
- A **common** definition is tentative zero-filled storage. A strong definition
  takes precedence over common, and common takes precedence over weak,
  regardless of encounter order.
- Multiple common definitions of one name coalesce without an error. The result
  has the largest declared size and largest declared alignment. Its position in
  common-storage order is set by the first included common declaration.
- When only weak definitions exist for a name, the **first** weak definition in
  link order provides the symbol.
- Two **strong** definitions of the same name are a collision; the first remains
  the provider and the error `duplicate strong symbol: <name>` is recorded
  (weak duplicates are not errors).
- A symbol referenced but never defined by any included input is unresolved;
  when it is the target of a relocation, record `unresolved symbol in reloc:
  <name>`.

## Archive inclusion

Standalone objects are always included. An archive member is included **only**
if it provides a definition for a name that is currently undefined at the point
the archive is processed. Pulling in a member can itself introduce new undefined
references and can make an **earlier** member of the same archive newly required,
so an archive must be rescanned until a full pass pulls in nothing new. Members
already included are not reconsidered. Archives are processed in link order; an
undefined reference introduced after an archive has been fully processed does not
reach back into it.

A common declaration is a definition for archive selection. It can satisfy a
currently undefined name and cause its member to be included, but a name already
defined as common is not undefined and does not pull another member merely to
seek a strong replacement.

## Section layout and COMDAT

Sections are laid out in the order their objects are included and, within an
object, in file order. Layout begins at address `0x1000`. Each section starts at
the next address that is a multiple of `4`; its size is its declared byte count.

Sections that share a non-empty `<comdat-key>` are a COMDAT group: exactly one
copy is kept in the image (the first one encountered in inclusion order) and all
later copies are discarded. A symbol defined in a discarded copy resolves to the
corresponding address in the surviving copy (same section-relative offset).
Relocations that live in a discarded copy are dropped.

## Common storage

After ordinary and surviving COMDAT sections are laid out, final common symbols
are allocated in one synthetic `.common` section. The section starts at the
first address aligned to the largest alignment among its symbols. Symbols are
placed in first-common-declaration order; before each symbol, the cursor is
rounded up to that symbol's coalesced alignment, then advanced by its coalesced
size. The `.common` section spans from its aligned base through the end of the
last allocation, including internal padding. It is omitted when no final common
symbols remain. Common symbol addresses participate in relocations normally.

## Relocations

For each relocation that survives COMDAT selection, let `S` be the final address
of the target symbol, `A` the addend, and `P` the final address of the
relocation site (its section base plus its offset).

- `REL32`: the value is the signed 32-bit result of `S + A - P`. If that result
  does not fit in the signed 32-bit range `[-2147483648, 2147483647]`, record
  `REL32 overflow at <object>/<section>+<offset>` and report the value truncated
  into signed 32-bit two's complement.
- `ABS32`: the value is `S + A` interpreted as an unsigned 32-bit quantity. If
  `S + A` is negative or exceeds `4294967295`, record `ABS32 overflow at
  <object>/<section>+<offset>` and report the low 32 bits.
- `ABS16`: the value is `S + A`. It must lie in `[0, 65535]`; otherwise record
  `ABS16 overflow at <object>/<section>+<offset>` and report the low 16 bits.

## Linked image

The output is a single JSON object:

    {
      "sections": [ {"name": <str>, "base": <int>, "size": <int>}, ... ],
      "symbols": { "<name>": {"address": <int>, "binding": "STRONG"|"WEAK"|"COMMON"}, ... },
      "relocs": [ {"object": <str>, "section": <str>, "offset": <int>,
                   "kind": <str>, "value": <int>}, ... ],
      "errors": [ <str>, ... ]
    }

`sections` lists the sections kept in the image in layout order. `symbols` maps
every resolved global symbol to its final address and effective binding.
`relocs` lists every surviving relocation in the order its object was included
and, within an object, in file order, with the computed `value`. `errors` lists
diagnostic strings in the order they are produced.

For every well-formed job, diagnostics in `errors` are non-fatal: `crlink`
writes the complete linked image to `<out-file>` and exits with status `0`, even
when duplicate definitions, unresolved relocation targets, or relocation
overflows were recorded. Invalid invocation, a missing input, or a malformed
record is fatal and exits nonzero without writing a linked image.
