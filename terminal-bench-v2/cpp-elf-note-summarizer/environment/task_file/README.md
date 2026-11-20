# note_summary

`note_summary` reads one ELF64 little-endian file and writes a JSON summary of PT_NOTE records:

```text
/app/task_file/build/note_summary <input-elf> <output-json>
```

Only program headers identify note data. Section headers may be absent, except when the ELF header uses the standard extended program-header count: if `e_phnum` is `0xffff`, read the real program-header count from section header zero's little-endian `sh_info` field; if that section header cannot be read, scan no program headers. Every program header with `p_type == 4` is a note segment. Program headers with any other `p_type` are ignored. A PT_NOTE header whose file range is outside the input, including an offset-plus-size range that wraps around, is ignored. Notes use the usual ELF note layout: `namesz`, `descsz`, and `type` are 32-bit little-endian integers, followed by `namesz` bytes of owner name and `descsz` bytes of descriptor. The owner and descriptor fields are each padded independently to the next 4-byte boundary. The owner string used for matching and JSON output is the bytes before the first nul byte in the owner field; if there is no nul byte, use the whole owner field. A zero-length owner field is valid and has the empty owner string. A torn or malformed note stops scanning only the segment that contains it; other PT_NOTE segments are still valid input.

The output file is a JSON object with this schema:

```json
{
  "input": "basename of input file",
  "note_count": 0,
  "gnu_build_ids": ["lowercase hex descriptors for GNU type 3 notes"],
  "threads": [
    { "pid": 0, "signal": 0, "comm": "name from CORE type 1 descriptor" }
  ],
  "signals": [
    { "signo": 11, "errno": 0, "code": -6 }
  ],
  "processes": [
    { "pid": 123, "ppid": 1, "name": "app", "state": "worker" }
  ],
  "auxv": [
    { "type": "AT_PHDR", "value": "0x400040" }
  ],
  "mapped_files": [
    { "start": "0x400000", "end": "0x401000", "offset": "0x0", "path": "/bin/app" }
  ],
  "unknown_notes": [
    { "name": "OWNER", "type": 4660, "desc_size": 7 }
  ]
}
```

For this task the supported descriptor payloads are intentionally small. A `CORE` note with type `1` has exactly `uint32 pid`, `uint32 signal`, and a 16-byte nul-padded command name. Other descriptor lengths for this note type are unknown. A `CORE` note with type `0x53494749` has exactly three little-endian signed 32-bit integers: signal number, errno, and code. A `CORE` note with type `3` has exactly `uint32 pid`, `uint32 ppid`, a 16-byte process name, and a 16-byte state string; the two strings use bytes before the first nul byte, or the whole fixed field if there is no nul. Other descriptor lengths for these note types are unknown. A `CORE` note with type `6` is a stream of little-endian `uint64 type, uint64 value` pairs and ends with a final pair whose type is `0`; descriptors whose byte length is not a multiple of 16, whose terminator is missing, or whose terminator is followed by more descriptor bytes are unknown. Use symbolic names for auxv types `3` (`AT_PHDR`), `7` (`AT_BASE`), `9` (`AT_ENTRY`), and `25` (`AT_RANDOM`). Other auxv types are emitted as decimal strings. Auxv values are lowercase `0x`-prefixed hexadecimal strings, such as `0x7fff0000`.

A `CORE` note with type `0x46494c45` describes mapped files. Its descriptor starts with little-endian `uint64 count` and `uint64 page_size`, followed by `count` triples of little-endian `uint64 start`, `uint64 end`, and `uint64 file_ofs_pages`, then exactly `count` nul-terminated path strings. Emit one `mapped_files` object per triple, in descriptor order. `start` and `end` are hexadecimal values from the descriptor. `offset` is `file_ofs_pages * page_size`, also as lowercase `0x`-prefixed hexadecimal. If the descriptor is too short, a path string is not terminated, extra descriptor bytes remain after the last path string, `page_size` is zero for a non-empty table, `start` is greater than `end`, or the byte offset multiplication overflows 64 bits, treat the note as unknown.
