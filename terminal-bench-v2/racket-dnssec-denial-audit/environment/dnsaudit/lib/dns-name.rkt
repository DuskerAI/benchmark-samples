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
  (define s (string-trim (format "~a" v)))
  (if (ends-dot? s)
      (string-downcase s)
      (string-append s ".")))

(define (labels name)
  (filter (lambda (x) (not (string=? x ""))) (string-split (canon-name name) ".")))

(define (name-wire name)
  (string->bytes/utf-8 (canon-name name)))

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
  (string=? (canon-name a) (canon-name b)))

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
