#lang racket

(require json racket/cmdline racket/list racket/string
         "lib/dns-name.rkt"
         "lib/crypto.rkt"
         "lib/denial.rkt")

(define proof-path #f)
(define out-path #f)

(command-line
 #:program "dnssec-denial-audit"
 #:once-each
 [("--proof") p "proof JSON" (set! proof-path p)]
 [("--out") p "report JSON" (set! out-path p)])

(unless (and proof-path out-path)
  (eprintf "usage: racket audit.rkt --proof PROOF --out REPORT\n")
  (exit 2))

(define (read-proof path)
  (call-with-input-file path read-json))

(define (ok reason) (values #t reason))
(define (bad reason) (values #f reason))

(define (dnskey-matches-anchor? key anchor)
  (and (= (hget key 'key_tag) (hget anchor 'key_tag))
       (= (hget key 'algorithm) (hget anchor 'algorithm))))

(define (anchor-admitted? proof)
  (define anchor (hget proof 'trust_anchor))
  (for/or ([key (in-list (hget proof 'dnskeys))])
    (dnskey-matches-anchor? key anchor)))

(define (rrset-type rrset)
  (string-upcase (hget rrset 'type)))

(define (relevant-rrsets proof)
  (filter (lambda (rrset)
            (member (rrset-type rrset) '("DNSKEY" "NSEC" "NSEC3")))
          (hget proof 'rrsets)))

(define (all-signatures-valid? proof)
  (define now (hget proof 'now))
  (define keys (hget proof 'dnskeys))
  (for/and ([rrset (in-list (relevant-rrsets proof))])
    (rrsig-valid? rrset keys now)))

(define (evaluate proof)
  (define q (hget proof 'question))
  (cond
    [(not (anchor-admitted? proof)) (bad "bogus")]
    [(not (all-signatures-valid? proof)) (bad "bogus")]
    [(denial-admitted? proof (hget q 'name) (hget q 'type)) (ok "secure-denial")]
    [else (bad "bogus")]))

(define proof (read-proof proof-path))
(define-values (admitted reason) (evaluate proof))
(define report (hash 'admitted admitted 'reason reason))
(call-with-output-file out-path
  #:exists 'replace
  (lambda (out)
    (write-json report out)
    (newline out)))
