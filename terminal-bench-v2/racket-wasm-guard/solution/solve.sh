#!/bin/bash
set -euo pipefail
cat > /app/wasmguard/src/guard.rkt <<'RKT'
#lang racket
(require json racket/list racket/match racket/string)

(struct section (id payload) #:transparent)
(struct modinfo (sections limits imports malformed dup control-ok) #:transparent)

(define (json-null) 'null)

(define (read-leb bs pos)
  (let loop ([i pos] [shift 0] [acc 0] [count 0])
    (cond
      [(or (>= i (bytes-length bs)) (>= count 5)) (values #f i)]
      [else
       (define b (bytes-ref bs i))
       (define part (bitwise-and b #x7f))
       (define v (+ acc (arithmetic-shift part shift)))
       (cond
         [(and (= count 4) (or (positive? (bitwise-and b #x80)) (> part #x0f)))
          (values #f (+ i 1))]
         [(zero? (bitwise-and b #x80))
          (if (and (positive? count) (< v (arithmetic-shift 1 (* 7 count))))
              (values #f (+ i 1))
              (values v (+ i 1)))]
         [else (loop (+ i 1) (+ shift 7) v (+ count 1))])])))

(define (sections-with secs id)
  (filter (lambda (s) (= (section-id s) id)) secs))

(define (one-section secs id)
  (match (sections-with secs id) [(list s _ ...) s] [_ #f]))

(define (strict-utf8 bs start end)
  (with-handlers ([exn:fail? (lambda (_) #f)])
    (bytes->string/utf-8 bs #f start end)))

(define (parse-imports p)
  (define-values (count start) (read-leb p 0))
  (if (not count)
      (values '() #t)
      (let loop ([left count] [pos start] [names '()])
        (cond
          [(zero? left) (values (reverse names) (not (= pos (bytes-length p))))]
          [else
           (define-values (n next) (read-leb p pos))
           (if (or (not n) (> (+ next n) (bytes-length p)))
               (values (reverse names) #t)
               (let ([name (strict-utf8 p next (+ next n))])
                 (if name
                     (loop (- left 1) (+ next n) (cons name names))
                     (values (reverse names) #t))))]))))

(define (parse-pair p)
  (define-values (a j) (read-leb p 0))
  (if (not a)
      (values #f #t)
      (let-values ([(b k) (read-leb p j)])
        (if (and b (= k (bytes-length p)))
            (values (cons a b) #f)
            (values #f #t)))))

(define (control-state bs)
  (let loop ([i 0] [stack '()] [semantic #t])
    (cond
      [(= i (bytes-length bs)) (values #f (and semantic (null? stack)))]
      [else
       (define op (bytes-ref bs i))
       (cond
         [(or (= op #x02) (= op #x03))
          (loop (+ i 1) (cons op stack) semantic)]
         [(= op #x04)
          (loop (+ i 1) (cons (cons 'if #f) stack) semantic)]
         [(= op #x05)
          (if (and (pair? stack) (pair? (car stack))
                   (eq? (caar stack) 'if) (not (cdar stack)))
              (loop (+ i 1) (cons (cons 'if #t) (cdr stack)) semantic)
              (loop (+ i 1) stack #f))]
         [(= op #x0b)
          (if (pair? stack)
              (loop (+ i 1) (cdr stack) semantic)
              (loop (+ i 1) stack #f))]
         [(or (= op #x0c) (= op #x0d))
          (define-values (depth next) (read-leb bs (+ i 1)))
          (if depth
              (loop next stack (and semantic (< depth (length stack))))
              (values #t #f))]
         [(= op #x0e)
          (define-values (count next) (read-leb bs (+ i 1)))
          (if (or (not count) (> (+ count 1) (- (bytes-length bs) next)))
              (values #t #f)
              (let targets ([left (+ count 1)] [at next] [ok semantic])
                (if (zero? left)
                    (loop at stack ok)
                    (let-values ([(depth after) (read-leb bs at)])
                      (if depth
                          (targets (- left 1) after (and ok (< depth (length stack))))
                          (values #t #f))))))]
         [else (loop (+ i 1) stack semantic)])])))

(define (finish secs dup)
  (define lims (make-hash))
  (define imports '())
  (define bad #f)
  (for ([id '(2 3)])
    (define s (one-section secs id))
    (when s
      (define-values (pair malformed?) (parse-pair (section-payload s)))
      (if malformed? (set! bad #t) (hash-set! lims id pair))))
  (define isec (one-section secs 1))
  (when isec
    (define-values (names malformed?) (parse-imports (section-payload isec)))
    (set! imports names)
    (when malformed? (set! bad #t)))
  (define csec (one-section secs 4))
  (define-values (control-malformed? control-ok?)
    (if csec (control-state (section-payload csec)) (values #f #t)))
  (modinfo secs lims imports (or bad control-malformed?) dup control-ok?))

(define (parse bs)
  (if (or (< (bytes-length bs) 5)
          (not (bytes=? (subbytes bs 0 4) #"WSMG"))
          (not (= (bytes-ref bs 4) 1)))
      (modinfo '() (hash) '() #t #f #f)
      (let loop ([pos 5] [seen (set)] [secs '()] [dup #f])
        (cond
          [(= pos (bytes-length bs)) (finish (reverse secs) dup)]
          [(> pos (bytes-length bs)) (modinfo (reverse secs) (hash) '() #t dup #f)]
          [else
           (define sid (bytes-ref bs pos))
           (define-values (len next) (read-leb bs (+ pos 1)))
           (if (or (not len) (> (+ next len) (bytes-length bs)))
               (modinfo (reverse secs) (hash) '() #t dup #f)
               (loop (+ next len)
                     (if (zero? sid) seen (set-add seen sid))
                     (cons (section sid (subbytes bs next (+ next len))) secs)
                     (or dup (and (not (zero? sid)) (set-member? seen sid)))))]))))

(define (present? xs value) (if (member value xs) #t #f))

(define (imports-ok? m)
  (define names (modinfo-imports m))
  (and (andmap (lambda (s) (member s '("env.memory" "env.table" "env.clock"))) names)
       (= (length names) (length (remove-duplicates names)))
       (equal? (present? names "env.memory") (if (one-section (modinfo-sections m) 2) #t #f))
       (equal? (present? names "env.table") (if (one-section (modinfo-sections m) 3) #t #f))))

(define (limits-ok? lims id lo hi)
  (define p (hash-ref lims id #f))
  (or (not p) (<= lo (car p) (cdr p) hi)))

(define (reason m)
  (cond
    [(modinfo-dup m) "duplicate-section"]
    [(modinfo-malformed m) "malformed"]
    [(not (imports-ok? m)) "import-policy"]
    [(not (limits-ok? (modinfo-limits m) 2 1 16)) "memory-policy"]
    [(not (limits-ok? (modinfo-limits m) 3 0 128)) "table-policy"]
    [(not (modinfo-control-ok m)) "control-policy"]
    [else "ok"]))

(define (rel root p)
  (string-replace (path->string (find-relative-path root p)) "\\" "/"))

(define (all-wsm root)
  (sort (for/list ([p (in-directory root)] #:when (and (file-exists? p) (regexp-match? #rx"[.]wsm$" (path->string p)))) p)
        string<? #:key (lambda (p) (rel root p))))

(define (limits-json m id)
  (define p (hash-ref (modinfo-limits m) id #f))
  (if p (values (car p) (cdr p)) (values (json-null) (json-null))))

(define (record root p)
  (define m (parse (with-input-from-file p port->bytes)))
  (define r (reason m))
  (define-values (mn mx) (limits-json m 2))
  (define-values (tn tx) (limits-json m 3))
  (hasheq 'path (rel root p)
          'status (if (string=? r "ok") "accepted" "rejected")
          'reason r
          'limits (hasheq 'memory_min mn 'memory_max mx 'table_min tn 'table_max tx)))

(define (arg name xs)
  (match xs
    [(list _ ... (? (lambda (x) (string=? x name))) v _ ...) v]
    [_ #f]))

(module+ main
  (define xs (current-command-line-arguments))
  (define in (arg "--input" (vector->list xs)))
  (define out (arg "--report" (vector->list xs)))
  (unless (and in out) (error 'wasmguard "usage"))
  (define root (string->path in))
  (define rows (for/list ([p (all-wsm root)]) (record root p)))
  (define accepted (count (lambda (r) (string=? (hash-ref r 'status) "accepted")) rows))
  (make-directory* (or (path-only (string->path out)) (current-directory)))
  (call-with-output-file out #:exists 'replace
    (lambda (o)
      (write-json (hasheq 'modules rows 'accepted accepted 'rejected (- (length rows) accepted)) o))))
RKT
/app/wasmguard/scripts/build.sh
