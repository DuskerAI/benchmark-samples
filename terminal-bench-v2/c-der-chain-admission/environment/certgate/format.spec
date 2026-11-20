# Certificate evidence format, version 1

All words in this file are normative. A policy is UTF-8 text containing exactly one
of each of these four keys, in any order, with no blank or unknown lines:

```
time=<canonical unsigned decimal integer no greater than 9223372036854775807>
trust_subject=<identifier>
trust_key=<64 lowercase hexadecimal digits>
dns_suffix=<DNS name>
```

An identifier is 1 through 63 ASCII letters, digits, `.`, `_`, or `-`. A DNS name
is lowercase ASCII, at most 253 bytes, and consists of dot-separated labels of 1
through 63 letters, digits, or hyphens; a label cannot start or end with `-`.
A name matches a suffix when it equals the suffix or ends with `.` followed by the
suffix.
Policy lines are separated by LF. The final line may end at EOF or with one LF;
CR and embedded NUL bytes are invalid.
Canonical decimal writes zero as `0`; every nonzero value has no leading zeroes.

## Bundle and DER

A bundle is 1 through 8 adjacent `Certificate` values in leaf-to-root order and is
at most 32768 bytes. A certificate is at most 4096 bytes. The ASN.1-like schema is:

```
Certificate ::= SEQUENCE {
  version       INTEGER,                 -- exactly 1
  serial        INTEGER,                 -- 1..2147483647
  issuer        UTF8String,              -- identifier
  subject       UTF8String,              -- identifier
  validity      SEQUENCE { notBefore INTEGER, notAfter INTEGER },
  extensions    SEQUENCE {
    isCA          BOOLEAN,
    pathLen       INTEGER,               -- 0..7
    keyUsage      BIT STRING,             -- two content octets: 00, flags
    dnsNames      SEQUENCE OF IA5String,
    permittedDNS [0] SEQUENCE OF IA5String,
    excludedDNS  [1] SEQUENCE OF IA5String
  },
  subjectKey    OCTET STRING,             -- exactly 32 octets
  authorityKey  OCTET STRING,             -- exactly 32 octets
  signature     OCTET STRING              -- exactly 32 octets
}
```

The context fields use constructed tags `a0` and `a1` and each directly encloses
one DER `SEQUENCE`. Every constructed value must be consumed exactly. Tags use one
octet. Lengths are definite, use the shortest DER form, use at most three length
octets, and cannot exceed their enclosing value. INTEGERs are non-negative,
shortest-form DER encodings with no more than 8 value octets after an optional
sign-protecting zero. BOOLEAN is exactly `00` or `ff`. The BIT STRING's unused-bit
count is zero. No bytes may precede, separate, or follow the certificates.

Each DNS list has at most 8 entries and every entry is a DNS name. The leaf has 1
through 8 `dnsNames` and empty constraint lists. CA certificates have no
`dnsNames`; their constraint lists may be empty. Unknown key-usage bits are not
allowed. The leaf is not a CA, has `pathLen=0`, and has flags `80`
(`digitalSignature`). Every other certificate is a CA and has flags `04`
(`keyCertSign`). Subjects and serials are each unique across the bundle.
Empty DNS IA5String values and DNS IA5String values containing NUL bytes are
malformed bundle fields. Other DNS label-syntax failures are NAME failures.

## Admission

For every adjacent pair, the lower certificate's issuer equals the upper
certificate's subject and its authority key equals the upper subject key. The root
is self-issued and has equal authority and subject keys. A certificate signature is
SHA-256 over the exact encoded TLVs from its `version` through its `authorityKey`,
followed by the issuer's 32-byte subject key (the root uses its own key). All
certificates are valid when `notBefore <= policy time <= notAfter`; an inverted
interval is invalid. A CA's `pathLen` is at least the count of CA certificates
beneath it. Every leaf DNS name must match the policy suffix, must match at least
one entry in each nonempty CA permitted list, and must not match any CA excluded
entry. The root subject and key must equal the policy trust values.

Validation uses this rejection priority: `DER`, `ROLE`, `DUPLICATE`, `TIME`,
`LINK`, `SIGNATURE`, `PATHLEN`, `NAME`, then `TRUST`. The first applicable code is
reported. A malformed bundle is `DER`; policy or I/O errors are operational errors.

## Process and reports

The command exits 0 for `ACCEPT`, 2 for an evidence `REJECT`, and 1 for bad
arguments, policy, build, or I/O errors. Evidence decisions atomically replace the
output directory as a whole, even when a previous directory at that path contains
stale files or nested directories, and leave exactly two regular files.
`decision.json` is one UTF-8 line, with keys in this order and no extra whitespace:

```
{"decision":"ACCEPT|REJECT","code":"CODE","certificates":N,"leaf":"SUBJECT"}
```

Accepted evidence uses code `OK`. For a DER rejection, `N` is the number of whole
certificates decoded before the fault and `SUBJECT` is empty if no leaf decoded.
`trace.tsv` starts with `index\tsubject\tissuer\trole\ttime\tsignature\tconstraints`.
It then has one line per decoded certificate, including the decoded prefix before
a later DER fault, with zero-based index; role is `leaf` or `ca`, and each final
status is `ok` or `fail`. Fields contain no escaping.
Status columns are diagnostic and do not alter rejection priority. Both files end
in one newline. Runs may not modify policy or bundle files and identical inputs
produce byte-identical reports.
