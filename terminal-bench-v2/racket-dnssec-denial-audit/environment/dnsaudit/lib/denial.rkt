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
  (define owner (canon-name (hget rrset 'owner)))
  (define next (canon-name (hget (first-record rrset) 'next)))
  (define q (canon-name qname))
  (cond
    [(string=? owner q) #t]
    [(string<? owner next) (and (string<? owner q) (string<? q next))]
    [else (or (string<? owner q) (string<? q next))]))

(define (nsec-exact? rrset qname)
  (name=? (hget rrset 'owner) qname))

(define (nsec-denial? proof qname qtype)
  (define nsecs (rrsets-of proof "NSEC"))
  (or
   (for/or ([rrset (in-list nsecs)])
     (and (nsec-exact? rrset qname)
          (not (type-present? rrset qtype))
          (not (type-present? rrset "CNAME"))))
   (for/or ([rrset (in-list nsecs)])
     (nsec-covers? rrset qname))))

(define (nsec3-hash name salt iterations)
  (sha256-hex (string-append (canon-name name) "|" salt "|" (number->string iterations))))

(define (hash-between? owner next h)
  (cond
    [(string<? owner next) (and (string<? owner h) (string<? h next))]
    [else (or (string<? owner h) (string<? h next))]))

(define (nsec3-covers? rrset h)
  (define rec (first-record rrset))
  (hash-between? (string-downcase (hget rrset 'owner))
                 (string-downcase (hget rec 'next_hash))
                 h))

(define (nsec3-denial? proof qname)
  (for/or ([rrset (in-list (rrsets-of proof "NSEC3"))])
    (define rec (first-record rrset))
    (define h (nsec3-hash qname (hget rec 'salt) (hget rec 'iterations)))
    (nsec3-covers? rrset h)))

(define (denial-admitted? proof qname qtype)
  (or (nsec-denial? proof qname qtype)
      (nsec3-denial? proof qname)))
