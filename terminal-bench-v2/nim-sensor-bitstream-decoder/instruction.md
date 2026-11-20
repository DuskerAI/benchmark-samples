Our instrumentation group keeps its long-term telemetry in the compact SARC
sensor-archive format, and the reconstruction utility under /app is the only
thing that turns those archives back into the raw per-channel sample series that
downstream calibration depends on. On the small single-block captures it looks
fine, but on the real archives coming off the loggers the reconstructed samples
come out wrong, and analysts have started to distrust every number it prints. I
need it reading the format correctly again so the samples it emits exactly match
what was originally recorded.

The Nim sources live in /app/src and build with /app/scripts/build.sh into
/app/bin/sardecode, which runs as sardecode <archive-path> <output-path>. The
authoritative wire format, the canonical Huffman rule, the block and window
structure, the back-reference semantics, the handling of a truncated final
block, and the exact text layout of the reconstructed output are all specified
in /app/spec.md; a working example archive is at /app/archives/sample.sar. The
decoder must reconstruct the samples exactly as recorded for any archive that
conforms to that specification, across the full range of tables, block layouts,
and back-reference patterns it permits, and it must produce the same output on
every host. Keep the build entry point and the command-line interface unchanged.
