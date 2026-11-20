#!/bin/bash
set -euo pipefail

cat > /app/macroguard/src/macroguard.rkt <<'RKT'
#lang racket

(require json racket/list racket/match racket/path racket/string)

(struct policy (root denies prefixes max-bytes allow-relative? invalid?) #:transparent)

(define (read-policy path)
  (define root "main.rkt")
  (define denies '())
  (define prefixes '())
  (define max-bytes #f)
  (define allow-relative? #t)
  (define invalid? #f)
  (define (bad!) (set! invalid? #t))
  (define (nonempty s) (and (not (string=? (string-trim s) "")) (string-trim s)))
  (for ([raw (in-list (file->lines path))])
    (define line (string-trim (car (string-split raw "#" #:trim? #f #:repeat? #f))))
    (unless (string=? line "")
      (match (string-split line "=" #:trim? #f #:repeat? #f)
        [(list "root" v) (define nv (nonempty v)) (if nv (set! root nv) (bad!))]
        [(list "deny" v) (define nv (nonempty v)) (if nv (set! denies (cons nv denies)) (bad!))]
        [(list "allow-prefix" v) (define nv (nonempty v)) (if nv (set! prefixes (cons nv prefixes)) (bad!))]
        [(list "max-bytes" v)
         (define n (string->number (string-trim v)))
         (if (and (exact-nonnegative-integer? n) (positive? n))
             (set! max-bytes n)
             (bad!))]
        [(list "allow-relative" v)
         (match (string-downcase (string-trim v))
           ["true" (set! allow-relative? #t)]
           ["false" (set! allow-relative? #f)]
           [_ (bad!)])]
        [_ (bad!)])))
  (policy root (remove-duplicates denies) (remove-duplicates prefixes) max-bytes allow-relative? invalid?))

(define (json-report admitted quarantined out)
  (define sorted-admitted (sort (remove-duplicates admitted) string<?))
  (define sorted-quarantined
    (sort quarantined
          (lambda (a b)
            (define am (hash-ref a 'path))
            (define bm (hash-ref b 'path))
            (cond [(string<? am bm) #t]
                  [(string=? am bm) (string<? (hash-ref a 'reason) (hash-ref b 'reason))]
                  [else #f]))))
  (define doc
    (hasheq 'admitted sorted-admitted
            'quarantined sorted-quarantined
            'summary (hasheq 'admitted (length sorted-admitted)
                             'quarantined (length sorted-quarantined))))
  (call-with-output-file out #:exists 'truncate
    (lambda (p) (write-json doc p))))

(define (starts-prefix? s prefixes)
  (for/or ([p (in-list prefixes)]) (string-prefix? s p)))

(define (strip-rkt s)
  (regexp-replace #rx"\\.rkt$" s ""))

(define (lib-name x)
  (cond [(symbol? x) (symbol->string x)]
        [(and (list? x) (pair? x) (eq? (car x) 'lib) (pair? (cdr x)) (string? (cadr x)))
         (strip-rkt (cadr x))]
        [else #f]))

(define (denied-lib? mod pol)
  (starts-prefix? mod (policy-denies pol)))

(define (allowed-lib? mod pol)
  (or (member mod '("racket" "racket/base"))
      (starts-prefix? mod (policy-prefixes pol))))

(define (lib-reason mod pol)
  (cond [(denied-lib? mod pol) 'capability]
        [(not (allowed-lib? mod pol)) 'policy]
        [else #f]))

(define (rel-has-parent? rel)
  (for/or ([part (in-list (explode-path rel))])
    (equal? part 'up)))

(define (path-str p)
  (path->string (simplify-path p #f)))

(define (resolve-local bundle current spec)
  (define cur-abs (build-path bundle current))
  (define base (or (path-only cur-abs) bundle))
  (define candidate (simplify-path (build-path base spec) #f))
  (define rel (find-relative-path bundle candidate))
  (cond [(or (absolute-path? (string->path spec)) (rel-has-parent? rel)) (values #f 'escape)]
        [else (values (path-str rel) #f)]))

(define (spec-result spec bundle current pol)
  (define locals '())
  (define reasons '())
  (define (add-reason! r) (set! reasons (cons r reasons)))
  (define (add-local! p) (set! locals (cons p locals)))
  (define (local-from str)
    (if (not (policy-allow-relative? pol))
        (add-reason! 'relative-disabled)
        (let-values ([(rel err) (resolve-local bundle current str)])
          (if err (add-reason! err) (add-local! rel)))))
  (define (walk s)
    (cond
      [(string? s) (local-from s)]
      [(symbol? s)
        (define ln (lib-name s))
        (define why (and ln (lib-reason ln pol)))
        (when why (add-reason! why))]
      [(list? s)
       (match s
         [(list 'file (? string? p)) (local-from p)]
          [(list 'lib (? string? l))
           (define why (lib-reason (strip-rkt l) pol))
           (when why (add-reason! why))]
         [(list 'submod target _ ...) (walk target)]
         [(list (or 'for-syntax 'for-template 'for-label) inner ...) (for-each walk inner)]
         [(list 'for-meta _ inner ...) (for-each walk inner)]
          [(list 'prefix-in _ inner) (walk inner)]
          [(list (or 'only-in 'rename-in 'except-in) inner _ ...) (walk inner)]
         [(list 'combine-in inner ...) (for-each walk inner)]
          [_ (let* ([ln (lib-name s)]
                    [why (and ln (lib-reason ln pol))])
               (when why (add-reason! why)))])]
      [else (void)]))
  (walk spec)
  (values (remove-duplicates locals) (remove-duplicates reasons)))

(define (reader-line? line)
  (regexp-match? #rx"^ *#reader( |$)" line))

(define (reader-and-forms path)
  (define text (file->string path))
  (define reasons '())
  (define lines (string-split text "\n" #:trim? #f))
  (when (for/or ([line (in-list lines)]) (reader-line? line))
    (set! reasons (cons 'reader reasons)))
  (define source-lines lines)
  (when (pair? source-lines)
    (match (regexp-match #rx"^#lang +([^ \r\n]+)" (car source-lines))
      [(list _ lang)
       (unless (member lang '("racket" "racket/base"))
         (set! reasons (cons 'reader reasons)))
       (set! source-lines (cdr source-lines))]
      [_ (void)]))
  (define body (string-join (filter (lambda (line) (not (reader-line? line))) source-lines) "\n"))
  (define forms '())
  (with-handlers ([exn:fail? (lambda (_e) (set! reasons (cons 'malformed reasons)))])
    (parameterize ([read-accept-reader #f])
      (define in (open-input-string body))
      (let loop ()
        (define v (read in))
        (unless (eof-object? v)
          (set! forms (cons v forms))
          (loop)))))
  (values (reverse forms) (remove-duplicates reasons)))

(define (extract-requires forms)
  (define specs '())
  (define (walk x)
    (cond [(pair? x)
           (when (and (symbol? (car x)) (member (car x) '(require #%require)))
             (set! specs (append (cdr x) specs)))
           (for-each walk x)]
          [(vector? x) (for-each walk (vector->list x))]
          [else (void)]))
  (for-each walk forms)
  specs)

(define (reason path r) (hasheq 'path path 'reason (symbol->string r)))

(define (audit bundle pol)
  (define accepted '())
  (define rejected '())
  (define visiting '())
  (define done (make-hash))
  (define (reject! m r) (set! rejected (cons (reason m r) rejected)))
  (define (scan rel referrer)
    (cond
      [(member rel visiting) (reject! (or referrer rel) 'cycle)]
      [(hash-has-key? done rel) (void)]
      [else
       (hash-set! done rel #t)
       (set! visiting (cons rel visiting))
       (define p (build-path bundle rel))
       (define local-bad? #f)
       (cond
          [(not (file-exists? p)) (reject! rel 'missing) (set! local-bad? #t)]
         [else
          (when (and (policy-max-bytes pol) (> (file-size p) (policy-max-bytes pol)))
            (reject! rel 'size-limit)
            (set! local-bad? #t))
          (define-values (forms read-reasons) (reader-and-forms p))
          (for ([r (in-list read-reasons)]) (reject! rel r) (set! local-bad? #t))
          (define nexts '())
          (for ([spec (in-list (extract-requires forms))])
            (define-values (locals reasons) (spec-result spec bundle rel pol))
            (for ([r (in-list reasons)]) (reject! rel r) (set! local-bad? #t))
            (set! nexts (append locals nexts)))
          (for ([n (in-list (sort (remove-duplicates nexts) string<?))])
            (scan n rel))])
       (set! visiting (remove rel visiting))
       (unless local-bad? (set! accepted (cons rel accepted)))]))
  (define root (policy-root pol))
  (define-values (root-rel root-err) (resolve-local bundle "main.rkt" root))
  (if root-err
      (begin
        (when (policy-invalid? pol) (reject! root 'policy))
        (reject! root root-err))
      (begin
        (when (policy-invalid? pol) (reject! root-rel 'policy))
         (scan root-rel #f)))
  (define bad-paths (remove-duplicates (map (lambda (h) (hash-ref h 'path)) rejected)))
  (values (filter (lambda (m) (not (member m bad-paths))) accepted)
          (remove-duplicates rejected equal?)))

(define (arg-value args flag)
  (define pos (member flag args))
  (and pos (pair? (cdr pos)) (cadr pos)))

(define (main)
  (define args (vector->list (current-command-line-arguments)))
  (define bundle-arg (arg-value args "--bundle"))
  (define policy-arg (arg-value args "--policy"))
  (define report-arg (arg-value args "--report"))
  (unless (and bundle-arg policy-arg report-arg)
    (eprintf "usage: macroguard --bundle DIR --policy POLICY --report REPORT\n")
    (exit 2))
  (define bundle (simplify-path (path->complete-path bundle-arg) #f))
  (define pol (read-policy policy-arg))
  (define-values (accepted rejected) (audit bundle pol))
  (json-report accepted rejected report-arg)
  (exit (if (null? rejected) 0 1)))

(module+ main (main))
RKT

chmod +x /app/macroguard/scripts/build.sh
/app/macroguard/scripts/build.sh
