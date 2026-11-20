#lang racket

(require json racket/list racket/string "dns-name.rkt")
(provide sha256-hex canonical-rrset dnskey-digest rrsig-valid?)

(define (sha256-hex s)
  (define-values (proc out in err)
    (subprocess #f #f #f "/usr/bin/sha256sum"))
  (display s in)
  (close-output-port in)
  (define line (read-line out))
  (close-input-port out)
  (close-input-port err)
  (subprocess-wait proc)
  (substring line 0 64))

(define (key-name k)
  (if (symbol? k) (symbol->string k) (format "~a" k)))

(define (canon-value v)
  (cond
    [(hash? v)
     (define parts
       (for/list ([k (in-list (sort (hash-keys v) string<? #:key key-name))])
         (format "~a=~a" (key-name k) (canon-value (hash-ref v k)))))
     (string-append "{" (string-join parts ",") "}")]
    [(list? v) (string-append "[" (string-join (map canon-value v) ",") "]")]
    [(string? v) (string-downcase v)]
    [(boolean? v) (if v "true" "false")]
    [else (format "~a" v)]))

(define (records-text records)
  (string-join (map canon-value records) ";"))

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

(define (rrsig-valid? rrset keys now)
  (define sig (hget rrset 'rrsig))
  (define key (find-key keys (hget sig 'key_tag) (hget sig 'algorithm)))
  (and key
       (string=? (string-upcase (hget sig 'type_covered)) (string-upcase (hget rrset 'type)))
       (<= now (hget sig 'expiration))
       (string=? (hget sig 'signature) (sha256-hex (rrsig-text rrset key sig)))))
