# ForwardLog admission protocol

ForwardLog is an offline evidence format. All integers below are unsigned decimal in their shortest form (`0` or a nonzero digit followed by digits). All hexadecimal is lowercase, with two characters per byte. Tabs separate fields, LF terminates a committed line, and CR and NUL are forbidden in committed lines. Text is ASCII. Limits are measured before validation.

## Invocation and limits

`flogaudit --policy POLICY --log LOG --report REPORT` reads regular files no larger than 64 KiB and 1 MiB respectively. The three paths must be distinct. A valid invocation always replaces `REPORT`; it returns 0 for admission and 2 for denial. Any other argument form, aliased paths, unreadable input, oversized input, or unwritable report is invalid usage and returns 64 without creating or changing `REPORT`. The program writes nothing to standard output or standard error. It must not create any other file or modify an input.

## Policy

A policy is LF-terminated and has this exact order:

    FSPOLICY<TAB>1
    STREAM<TAB>stream-id
    GENESIS<TAB>32-byte-hex
    MAX_TAIL<TAB>byte-count
    KEY<TAB>epoch<TAB>32-byte-hex
    ...
    END

`stream-id` is 1 through 32 characters from lowercase `a`-`z`, digits, dot, underscore, and hyphen. `MAX_TAIL` is 0 through 4096. There are 1 through 32 `KEY` lines, with epochs exactly 0, 1, ... in order. No content follows `END`. A policy violation has reason `POLICY`.

## Record lines

A log has zero or more LF-terminated committed lines and may have one unterminated suffix. A committed line is at most 4096 bytes excluding LF. Sequence numbers start at 1 and increase by one across every record. `prev` is the current 32-byte chain value and `mac` is a 32-byte HMAC. The forms are:

    E<TAB>seq<TAB>epoch<TAB>timestamp<TAB>payload<TAB>prev<TAB>mac
    R<TAB>seq<TAB>epoch<TAB>new-epoch<TAB>commitment<TAB>prev<TAB>mac
    C<TAB>seq<TAB>epoch<TAB>first<TAB>last<TAB>count<TAB>root<TAB>prev<TAB>mac

An event timestamp is in 0..9223372036854775807 and cannot decrease between event records. Its payload is 1 through 512 bytes encoded as lowercase hexadecimal. Every record's `epoch` is the active epoch before that record. A rotation's `new-epoch` is active epoch + 1 and must exist in the policy. Its commitment is:

    SHA256("FLOG-KEY\0" || uint64-be(new-epoch) || new-key-bytes)

The rotation is authenticated with the old active key; the new key becomes active only after the record is accepted.

For each line, `body` is the bytes through the field immediately before `prev`, exactly as shown in the corresponding form. Given the previous chain value, its record hash is:

    SHA256("FLOG-RECORD\0" || previous-chain || uint32-be(length(body)) || body)

`prev` must encode the previous chain value. Authentication uses the active policy key:

    HMAC-SHA256(key, "FLOG-MAC\0" || stream-id || "\0" || record-hash)

The new chain value is the record hash. The initial chain value is `GENESIS`.

## Checkpoints

Each event and rotation hash since the preceding accepted checkpoint is a checkpoint leaf; checkpoint records are not leaves. A checkpoint must cover at least one leaf. `first` and `last` are the sequence numbers of the first and last leaf, and `count` is the number of leaves. Its `root` is computed in original order. At each tree level, hash adjacent pairs as `SHA256("FLOG-NODE\0" || left || right)` and promote an unpaired final node unchanged. Repeat until one root remains. A valid checkpoint clears the leaf set. Admission requires a nonempty log whose final committed record is a checkpoint, so no leaf remains uncovered.

## Interrupted writes and decisions

If the log does not end in LF, the bytes after its final LF are an interrupted-write suffix. Ignore that suffix without parsing it only when its length is at most `MAX_TAIL`; otherwise deny it. Every LF-terminated line is committed: malformed or unauthenticated committed input is never recoverable. Ignoring a suffix cannot change sequence, epoch, chain, checkpoint, or timestamp state.

The first failing committed record stops evaluation and is not applied. Reasons are selected in this order at the point they become applicable: `FORMAT` for syntax, encoding, range, or line limits; `SEQUENCE`; `EPOCH`; `PREVIOUS`; `AUTH`; `TIMESTAMP`; `ROTATION`; `CHECKPOINT`. After all committed lines, use `TAIL` for an excessive suffix, then `UNSEALED` for an empty log, a non-checkpoint final record, or uncovered leaves. Successful admission uses `OK`.

`REPORT` is one UTF-8 line with a final LF and exactly these JSON fields in this order:

    {"admitted":boolean,"reason":string,"stream":string,"committed_records":integer,"last_sequence":integer,"epoch":integer,"chain_hash":string,"last_checkpoint":integer,"recovered_tail_bytes":integer}

`committed_records` counts accepted records, `last_sequence` is zero before the first accepted record, `last_checkpoint` is zero before an accepted checkpoint, and `chain_hash` is the genesis or last accepted record hash. For a `POLICY` denial, `stream` is empty and `chain_hash` is 64 zeroes. `recovered_tail_bytes` is the unterminated suffix length whether the result is admission or denial. JSON strings use ordinary JSON escaping.
