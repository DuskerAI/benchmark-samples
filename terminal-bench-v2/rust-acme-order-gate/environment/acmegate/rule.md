# Local ACME admission rules

Identifiers in local evidence are ASCII strings using letters, digits, `.`, `_`, `:`, or `-`; quoted JSON strings therefore appear without escapes. The protected header is base64url without padding of the exact UTF-8 JSON bytes `{"alg":"ACME-MAC64","kid":"ACCOUNT","nonce":"NONCE","url":"https://ca.local/acme/order/ORDER"}`. The payload is base64url without padding of `{"account":"ACCOUNT","challenge":"CHALLENGE","order":"ORDER","status":"ready"}`. Field order and the absence of extra whitespace are part of the canonical bytes.

`ACME-MAC64` is the archived lab authenticator used for these dossiers. Starting with unsigned 64-bit value `14695981039346656037`, feed the bytes of `KEY`, newline, `PROTECTED`, `.`, and `PAYLOAD`; for each byte set `h = h xor byte`, then `h = h * 1099511628211` modulo `2^64`. The signature is `h` as exactly 16 lowercase hexadecimal digits.

A record is admitted only for an active account, matching canonical protected and payload bytes, a valid signature under that account's key, a nonce absent from both prior state and earlier records in the same bundle, a known order whose account matches, whose state is `pending`, and whose expiry is not before `--now`, plus a known challenge whose account matches, whose status is `valid`, and whose `NOT_BEFORE <= --now <= EXPIRES`.

All applicable rejection reason codes are emitted in this order, without duplicates: `BAD_ACCOUNT`, `BAD_PROTECTED`, `BAD_PAYLOAD`, `BAD_SIGNATURE`, `NONCE_REPLAY`, `ORDER_STATE`, `CHALLENGE_BINDING`, `CHALLENGE_TIME`. Report-level `accepted` is true only when every record is accepted.
