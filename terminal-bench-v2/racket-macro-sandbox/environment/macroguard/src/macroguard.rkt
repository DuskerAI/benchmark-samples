#lang racket

(require json racket/list racket/match racket/path racket/string)

(struct policy (root denies prefixes max-bytes allow-relative?) #:transparent)

(define (read-policy path)
  (define root "main.rkt")
  (define denies '())
  (define prefixes '())
  (define max-bytes #f)
  (define allow-relative? #t)
  (for ([raw (in-list (file->lines path))])
    (define line (string-trim (car (string-split raw "#" #:trim? #f #:repeat? #f))))
    (unless (string=? line "")
      (match (string-split line "=" #:trim? #f #:repeat? #f)
        [(list "root" v) (set! root (string-trim v))]
        [(list "deny" v) (set! denies (cons (string-trim v) denies))]
        [(list "allow-prefix" v) (set! prefixes (cons (string-trim v) prefixes))]
        [(list "max-bytes" v) (set! max-bytes (string->number (string-trim v)))]
        [(list "allow-relative" v) (set! allow-relative? (equal? (string-downcase (string-trim v)) "true"))]
        [_ (void)])))
  (policy root denies prefixes max-bytes allow-relative?))

(define (write-report admitted quarantined out)
  (call-with-output-file out #:exists 'truncate
    (lambda (p)
      (write-json
       (hasheq 'admitted (sort admitted string<?)
               'quarantined (sort quarantined (lambda (a b) (string<? (hash-ref a 'path) (hash-ref b 'path))))
               'summary (hasheq 'admitted (length admitted) 'quarantined (length quarantined)))
       p))))

(define (strip-lang text)
  (define lines (string-split text "\n" #:trim? #f))
  (if (and (pair? lines) (regexp-match? #rx"^#lang" (car lines)))
      (string-join (cdr lines) "\n")
      text))

(define (read-forms path)
  (with-handlers ([exn:fail? (lambda (_e) '())])
    (define in (open-input-string (strip-lang (file->string path))))
    (let loop ([acc '()])
      (define v (read in))
      (if (eof-object? v) (reverse acc) (loop (cons v acc))))))

(define (denied? spec pol)
  (and (symbol? spec) (member (symbol->string spec) (policy-denies pol))))

(define (scan-file bundle rel pol)
  (define p (build-path bundle rel))
  (define rejected '())
  (define nexts '())
  (when (not (file-exists? p))
    (set! rejected (cons (hasheq 'path rel 'reason "missing") rejected)))
  (when (and (file-exists? p) (policy-max-bytes pol) (> (file-size p) (policy-max-bytes pol)))
    (set! rejected (cons (hasheq 'path rel 'reason "size-limit") rejected)))
  (when (file-exists? p)
    (for ([form (in-list (read-forms p))])
      (match form
        [(list 'require specs ...)
         (for ([s (in-list specs)])
           (cond [(string? s) (set! nexts (cons (path->string (simplify-path (build-path (or (path-only rel) ".") s) #f)) nexts))]
                  [(denied? s pol) (set! rejected (cons (hasheq 'path rel 'reason "capability") rejected))]))]
        [_ (void)])))
  (values nexts rejected))

(define (audit bundle pol)
  (define seen '())
  (define accepted '())
  (define rejected '())
  (define (scan rel)
    (unless (member rel seen)
      (set! seen (cons rel seen))
      (define-values (nexts r) (scan-file bundle rel pol))
      (set! rejected (append r rejected))
      (when (null? r) (set! accepted (cons rel accepted)))
      (for ([n (in-list nexts)]) (scan n))))
  (scan (policy-root pol))
  (if (null? rejected) (values accepted rejected) (values '() rejected)))

(define (arg args flag)
  (define m (member flag args))
  (and m (pair? (cdr m)) (cadr m)))

(define (main)
  (define args (vector->list (current-command-line-arguments)))
  (define bundle (arg args "--bundle"))
  (define pol-path (arg args "--policy"))
  (define out (arg args "--report"))
  (unless (and bundle pol-path out) (exit 2))
  (define pol (read-policy pol-path))
  (define-values (accepted rejected) (audit bundle pol))
  (write-report accepted rejected out)
  (exit (if (null? rejected) 0 1)))

(module+ main (main))
