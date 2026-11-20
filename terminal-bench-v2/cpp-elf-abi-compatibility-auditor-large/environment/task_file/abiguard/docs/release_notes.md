# Release train note

The fixture release intentionally mixes clean libraries with incompatible changes. A correct audit must not assume every library is broken, and it must not infer ABI status from filenames alone. The dynamic metadata inside each shared object is the source of truth for the current release.
