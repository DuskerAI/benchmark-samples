# TCFI crash unwinder

`cfi_unwind` reads one crash record and one compact call-frame table, then writes a JSON stack report. The command is:

```
/app/task_file/build/cfi_unwind <crash-record> <report-json>
```

The crash record is UTF-8 text. Blank lines are ignored. `CFI <path>` gives the table file. `BASE <hex>` gives the data-relative base used by encoded addresses. Each thread starts with `THREAD <id>`, followed by `START <hex>`, zero or more `REG <number> <hex>` and `MEM <address> <hex64>` lines, and an `END` line. Thread ids are emitted in record order. Register numbers and addresses are unsigned integers; `MEM` stores one little-endian machine word at the exact address.

The table file starts with `TCFI`, version byte `1`, little-endian `u16` CIE count, and little-endian `u16` FDE count. Each CIE is `u32 id`, ULEB code alignment, SLEB data alignment, ULEB return-address register, ULEB augmentation length, augmentation bytes, little-endian `u16` instruction length, then that many instruction bytes. Each FDE is `u32 cie_id`, one pointer-encoding byte, an encoded initial location, an encoded address range, little-endian `u16` instruction length, then instruction bytes. Supported pointer formats are absolute pointer, ULEB, SLEB, udata2, udata4, udata8, sdata4, and sdata8. `pcrel` values are relative to the start offset of the encoded field. `datarel` values are relative to `BASE`.

The instruction stream uses this CFI subset. Primary opcodes are `0x40..0x7f` for `advance_loc`, `0x80..0xbf` for `offset`, and `0xc0..0xff` for `restore`, with the low six bits carrying the delta or register number. Extended opcodes are `0x00 DW_CFA_nop`, `0x02 DW_CFA_advance_loc1`, `0x03 DW_CFA_advance_loc2`, `0x04 DW_CFA_advance_loc4`, `0x05 DW_CFA_offset_extended`, `0x06 DW_CFA_restore_extended`, `0x08 DW_CFA_same_value`, `0x09 DW_CFA_register`, `0x0a DW_CFA_remember_state`, `0x0b DW_CFA_restore_state`, `0x0c DW_CFA_def_cfa`, `0x0d DW_CFA_def_cfa_register`, `0x0e DW_CFA_def_cfa_offset`, `0x11 DW_CFA_offset_extended_sf`, `0x12 DW_CFA_def_cfa_sf`, `0x13 DW_CFA_def_cfa_offset_sf`, `0x14 DW_CFA_val_offset`, and `0x15 DW_CFA_val_offset_sf`. These byte values are part of TCFI and are not inferred from any other DWARF table. Factored offsets use the CIE data alignment. Location advances use the CIE code alignment. CIE instructions form the initial row for all FDEs that reference that CIE. An FDE applies to `initial_location <= pc < initial_location + address_range`; when several FDEs apply, select the one with the greatest `initial_location`.

For the first frame of a thread, use `START` as the lookup PC. For later frames, the emitted PC is the recovered return address and the FDE lookup PC is one byte before it. The CFA is `register + offset`. Register rules can read a word at `CFA + offset`, copy another register, preserve the same register value, or produce a value offset from the CFA. After a non-zero return address is recovered, the next frame uses the same register set with the current row's recoverable register rules applied, the CFA register set to the computed CFA, and the return-address register set to the recovered PC. Stop unwinding when the return address is zero, no matching FDE exists, a required register or memory word is absent, a cycle is detected, or 32 frames have been emitted.

The JSON output must have this shape and stable key order:

```
{
  "input": "<crash-record path>",
  "threads": [
    {
      "id": "<thread id>",
      "frames": [
        {"index": 0, "pc": "0x...", "cfa": "0x...", "return_address": "0x...", "status": "ok"}
      ]
    }
  ],
  "summary": {"thread_count": 1, "complete_threads": 1, "frame_count": 1}
}
```

Use lowercase hexadecimal without leading zero padding except for zero itself. If a frame recovers a zero return address, emit `"0x0"` for `return_address`, set `status` to `root`, and stop. If a frame cannot produce a return address at all, use `null` for `return_address` and put the stop reason in `status`; otherwise use `ok`. When a recovered non-zero return address would equal any PC already emitted for that thread, including the current frame's PC, append one final frame at that PC with `status` `cycle`, `cfa` `null`, and `return_address` `null`. A thread is complete when its final frame has `root` status.
