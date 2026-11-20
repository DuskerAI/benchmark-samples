#!/bin/bash
set -euo pipefail

cat > /opt/dnssec-audit/lib/dns-name.rkt <<'RKT'
#lang racket

(require racket/string racket/list)
(provide hget canon-name name-wire name<? name=? parent-name wildcard-name strict-subdomain?)

(define (hget h key [default (lambda () (error 'hget "missing key: ~a" key))])
  (cond
    [(hash-has-key? h key) (hash-ref h key)]
    [(and (symbol? key) (hash-has-key? h (symbol->string key))) (hash-ref h (symbol->string key))]
    [(procedure? default) (default)]
    [else default]))

(define (ends-dot? s)
  (and (positive? (string-length s)) (char=? (string-ref s (sub1 (string-length s))) #\.)))

(define (canon-name v)
  (define s (string-downcase (string-trim (format "~a" v))))
  (if (ends-dot? s) s (string-append s ".")))

(define (labels name)
  (filter (lambda (x) (not (string=? x ""))) (string-split (canon-name name) ".")))

(define (name-wire name)
  (apply bytes-append
         (append
          (for/list ([label (in-list (labels name))])
            (define b (string->bytes/utf-8 label))
            (bytes-append (bytes (bytes-length b)) b))
          (list #"\0"))))

(define (bytes<? a b)
  (define la (bytes-length a))
  (define lb (bytes-length b))
  (let loop ([i 0])
    (cond
      [(and (= i la) (= i lb)) #f]
      [(= i la) #t]
      [(= i lb) #f]
      [else
       (define av (bytes-ref a i))
       (define bv (bytes-ref b i))
       (cond [(< av bv) #t]
             [(> av bv) #f]
             [else (loop (add1 i))])])))

(define (name<? a b)
  (bytes<? (name-wire a) (name-wire b)))

(define (name=? a b)
  (bytes=? (name-wire a) (name-wire b)))

(define (parent-name name)
  (define ls (labels name))
  (cond
    [(null? ls) "."]
    [(null? (cdr ls)) "."]
    [else (string-append (string-join (cdr ls) ".") ".")]))

(define (wildcard-name name)
  (string-append "*." (canon-name name)))

(define (strict-subdomain? child parent)
  (define c (canon-name child))
  (define p (canon-name parent))
  (and (not (string=? c p))
       (string-suffix? c p)))
RKT

cat > /opt/dnssec-audit/lib/crypto.rkt <<'RKT'
#lang racket

(require json racket/format racket/list racket/string file/sha1 "dns-name.rkt")
(provide sha256-hex canonical-rrset dnskey-digest rrsig-valid?)

(define sha256-cache (make-hash))

(define (bytes->hex bs)
  (apply string-append
         (for/list ([b (in-bytes bs)])
           (~r b #:base 16 #:min-width 2 #:pad-string "0"))))

(define (sha256-hex s)
  (hash-ref!
   sha256-cache s
   (lambda ()
     (bytes->hex (sha256-bytes (open-input-string s))))))

(define (key-name k)
  (if (symbol? k) (symbol->string k) (format "~a" k)))

(define (canon-value v)
  (cond
    [(hash? v)
     (define parts
       (for/list ([k (in-list (sort (hash-keys v) string<? #:key key-name))])
         (format "~a=~a" (key-name k) (canon-value (hash-ref v k)))))
     (string-append "{" (string-join parts ",") "}")]
    [(list? v)
     (string-append "[" (string-join (sort (map canon-value v) string<?) ",") "]")]
    [(string? v) (string-downcase v)]
    [(boolean? v) (if v "true" "false")]
    [else (format "~a" v)]))

(define (records-text records)
  (string-join (sort (map canon-value records) string<?) ";"))

(define (canonical-rrset rrset)
  (string-join
   (list (canon-name (hget rrset 'owner))
         (string-upcase (hget rrset 'type))
         (number->string (hget rrset 'ttl))
         (records-text (hget rrset 'records)))
   "|"))

(define (dnskey-digest key)
  (sha256-hex
   (string-join
    (list (number->string (hget key 'key_tag))
          (number->string (hget key 'algorithm))
          (string-upcase (hget key 'role))
          (hget key 'public))
    "|")))

(define (find-key keys tag algorithm)
  (for/first ([key (in-list keys)]
              #:when (and (= (hget key 'key_tag) tag)
                          (= (hget key 'algorithm) algorithm)))
    key))

(define (rrsig-text rrset key sig)
  (string-join
   (list (hget key 'public)
         (canonical-rrset rrset)
         (string-upcase (hget sig 'type_covered))
         (canon-name (hget sig 'signer))
         (number->string (hget sig 'inception))
         (number->string (hget sig 'expiration)))
   "|"))

(define (role-ok? rrtype key)
  (if (string=? (string-upcase rrtype) "DNSKEY")
      (string=? (string-upcase (hget key 'role)) "KSK")
      (string=? (string-upcase (hget key 'role)) "ZSK")))

(define (signer-ok? sig zone)
  (string=? (canon-name (hget sig 'signer)) (canon-name zone)))

(define (rrsig-valid? rrset keys now zone)
  (define sig (hget rrset 'rrsig))
  (define key (find-key keys (hget sig 'key_tag) (hget sig 'algorithm)))
  (and key
        (role-ok? (hget rrset 'type) key)
        (signer-ok? sig zone)
        (string=? (string-upcase (hget sig 'type_covered)) (string-upcase (hget rrset 'type)))
        (<= (hget sig 'inception) now)
        (<= now (hget sig 'expiration))
        (string=? (hget sig 'signature) (sha256-hex (rrsig-text rrset key sig)))))
RKT

cat > /opt/dnssec-audit/lib/denial.rkt <<'RKT'
#lang racket

(require racket/list racket/string "dns-name.rkt" "crypto.rkt")
(provide denial-admitted?)

(define (rrtype rrset) (string-upcase (hget rrset 'type)))
(define (first-record rrset) (car (hget rrset 'records)))

(define (rrsets-of proof typ)
  (filter (lambda (rrset) (string=? (rrtype rrset) typ)) (hget proof 'rrsets)))

(define (type-present? rrset qtype)
  (member (string-upcase qtype) (map string-upcase (hget (first-record rrset) 'types))))

(define (nsec-covers? rrset qname)
  (define owner (hget rrset 'owner))
  (define next (hget (first-record rrset) 'next))
  (cond
    [(name=? owner qname) #t]
    [(name<? owner next) (and (name<? owner qname) (name<? qname next))]
    [else (or (name<? owner qname) (name<? qname next))]))

(define (nsec-exact? rrset qname)
  (name=? (hget rrset 'owner) qname))

(define (nsec-nodata? rrset qtype)
  (and (not (type-present? rrset qtype))
       (not (type-present? rrset "CNAME"))))

(define (nsec-proves-name-absent? nsecs name qtype)
  (for/or ([rrset (in-list nsecs)])
    (or (and (nsec-exact? rrset name) (nsec-nodata? rrset qtype))
        (and (not (nsec-exact? rrset name)) (nsec-covers? rrset name)))))

(define (owner-set nsecs)
  (map (lambda (rrset) (canon-name (hget rrset 'owner))) nsecs))

(define (closest-existing nsecs qname zone)
  (define owners (owner-set nsecs))
  (let loop ([name (parent-name qname)])
    (cond
      [(member (canon-name name) owners) (canon-name name)]
      [(name=? name zone) (canon-name zone)]
      [(string=? (canon-name name) ".") (canon-name zone)]
      [else (loop (parent-name name))])))

(define (nsec-denial? proof qname qtype)
  (define nsecs (rrsets-of proof "NSEC"))
  (or
   (for/or ([rrset (in-list nsecs)])
     (and (nsec-exact? rrset qname) (nsec-nodata? rrset qtype)))
   (and (not (for/or ([rrset (in-list nsecs)])
               (nsec-exact? rrset qname)))
        (for/or ([rrset (in-list nsecs)])
           (and (not (nsec-exact? rrset qname)) (nsec-covers? rrset qname)))
        (let* ([ce (closest-existing nsecs qname (hget proof 'zone))]
               [wc (wildcard-name ce)])
          (nsec-proves-name-absent? nsecs wc qtype)))))

(define (nsec3-hash name salt iterations)
  (let loop ([i iterations]
              [h (sha256-hex (string-append (canon-name name) "|" salt))])
    (if (zero? i)
        h
        (loop (sub1 i) (sha256-hex (string-append h "|" salt))))))

(define (nsec3-rec rrset) (first-record rrset))

(define (nsec3-owner rrset)
  (string-downcase (hget rrset 'owner)))

(define (nsec3-next rrset)
  (string-downcase (hget (nsec3-rec rrset) 'next_hash)))

(define (nsec3-salt rrset)
  (hget (nsec3-rec rrset) 'salt))

(define (nsec3-iterations rrset)
  (hget (nsec3-rec rrset) 'iterations))

(define (nsec3-opt-out? rrset)
  (hget (nsec3-rec rrset) 'opt_out #f))

(define (nsec3-config? rrset salt iterations)
  (and (string=? (nsec3-salt rrset) salt)
       (= (nsec3-iterations rrset) iterations)))

(define (nsec3-exact-hash? rrset h)
  (string=? (nsec3-owner rrset) (string-downcase h)))

(define (hash-between? owner next h)
  (cond
    [(string<? owner next) (and (string<? owner h) (string<? h next))]
    [else (or (string<? owner h) (string<? h next))]))

(define (nsec3-covers? rrset h salt iterations)
  (and (nsec3-config? rrset salt iterations)
       (not (nsec3-opt-out? rrset))
       (hash-between? (nsec3-owner rrset) (nsec3-next rrset) (string-downcase h))))

(define (name-labels name)
  (filter (lambda (x) (not (string=? x ""))) (string-split (canon-name name) ".")))

(define (labels->name ls)
  (if (null? ls) "." (string-append (string-join ls ".") ".")))

(define (ancestor-names qname zone)
  (let loop ([name (parent-name qname)])
    (cond
      [(or (name=? name zone) (string=? (canon-name name) ".")) (list (canon-name zone))]
      [else (cons (canon-name name) (loop (parent-name name)))])))

(define (next-closer-name qname closest-encloser)
  (define q-labels (name-labels qname))
  (define ce-labels (name-labels closest-encloser))
  (define extra (- (length q-labels) (length ce-labels)))
  (and (positive? extra)
       (labels->name (drop q-labels (sub1 extra)))))

(define (nsec3-exact-for-name? nsec3s name salt iterations)
  (define h (nsec3-hash name salt iterations))
  (for/or ([rrset (in-list nsec3s)])
    (and (nsec3-config? rrset salt iterations)
         (not (nsec3-opt-out? rrset))
         (nsec3-exact-hash? rrset h))))

(define (nsec3-cover-name? nsec3s name salt iterations)
  (define h (nsec3-hash name salt iterations))
  (for/or ([rrset (in-list nsec3s)])
    (nsec3-covers? rrset h salt iterations)))

(define (closest-nsec3-encloser nsec3s qname zone salt iterations)
  (for/first ([candidate (in-list (ancestor-names qname zone))]
              #:when (nsec3-exact-for-name? nsec3s candidate salt iterations))
    candidate))

(define (nsec3-configs nsec3s)
  (remove-duplicates
   (for/list ([rrset (in-list nsec3s)])
     (cons (nsec3-salt rrset) (nsec3-iterations rrset)))
   equal?))

(define (nsec3-nodata? nsec3s qname qtype)
  (for/or ([rrset (in-list nsec3s)])
    (define salt (nsec3-salt rrset))
    (define iterations (nsec3-iterations rrset))
    (define h (nsec3-hash qname salt iterations))
    (and (not (nsec3-opt-out? rrset))
         (nsec3-exact-hash? rrset h)
         (not (type-present? rrset qtype))
         (not (type-present? rrset "CNAME")))))

(define (nsec3-nxdomain? proof nsec3s qname)
  (for/or ([config (in-list (nsec3-configs nsec3s))])
    (define salt (car config))
    (define iterations (cdr config))
    (define ce (closest-nsec3-encloser nsec3s qname (hget proof 'zone) salt iterations))
    (and ce
         (not (nsec3-exact-for-name? nsec3s qname salt iterations))
         (let ([next-closer (next-closer-name qname ce)])
           (and next-closer
                 (nsec3-cover-name? nsec3s next-closer salt iterations)
                 (nsec3-cover-name? nsec3s (wildcard-name ce) salt iterations))))))

(define (nsec3-denial? proof qname qtype)
  (define nsec3s (rrsets-of proof "NSEC3"))
  (or (nsec3-nodata? nsec3s qname qtype)
      (nsec3-nxdomain? proof nsec3s qname)))

(define (denial-admitted? proof qname qtype)
  (or (nsec-denial? proof qname qtype)
      (nsec3-denial? proof qname qtype)))
RKT

python - <<'PY'
from pathlib import Path
p = Path('/opt/dnssec-audit/audit.rkt')
s = p.read_text()
s = s.replace('(define (dnskey-matches-anchor? key anchor)\n  (and (= (hget key \'key_tag) (hget anchor \'key_tag))\n       (= (hget key \'algorithm) (hget anchor \'algorithm))))', '(define (dnskey-matches-anchor? key anchor)\n  (and (= (hget key \'key_tag) (hget anchor \'key_tag))\n       (= (hget key \'algorithm) (hget anchor \'algorithm))\n       (= (hget anchor \'digest_type) 2)\n       (string=? (string-upcase (hget key \'role)) "KSK")\n       (string=? (string-downcase (hget anchor \'digest)) (dnskey-digest key))))')
s = s.replace('(rrsig-valid? rrset keys now)', '(rrsig-valid? rrset keys now (hget proof \'zone))')
p.write_text(s)
PY
