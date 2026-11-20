#lang racket
(require json racket/list racket/string)

(struct section (id payload) #:transparent)
(struct modinfo (sections limits imports code malformed dup) #:transparent)

(define (json-null) 'null)

(define (u8 bs i) (and (< i (bytes-length bs)) (bytes-ref bs i)))

(define (read-leb bs pos)
  (let loop ([i pos] [shift 0] [acc 0] [count 0])
    (cond
      [(or (>= i (bytes-length bs)) (> count 4)) (values #f i)]
      [else
       (define b (bytes-ref bs i))
       (define v (+ acc (arithmetic-shift (bitwise-and b #x7f) shift)))
       (if (zero? (bitwise-and b #x80))
           (values v (+ i 1))
           (loop (+ i 1) (+ shift 7) v (+ count 1)))])))

(define (parse bs)
  (if (or (< (bytes-length bs) 5)
          (not (bytes=? (subbytes bs 0 4) #"WSMG"))
          (not (= (bytes-ref bs 4) 1)))
      (modinfo '() (hash) '() #"" #t #f)
      (let loop ([pos 5] [seen (set)] [secs '()] [dup #f] [bad #f])
        (cond
          [(or bad (> pos (bytes-length bs))) (modinfo (reverse secs) (hash) '() #"" #t dup)]
          [(= pos (bytes-length bs)) (finish (reverse secs) dup)]
          [else
           (define sid (u8 bs pos))
           (define-values (len next) (read-leb bs (+ pos 1)))
           (if (or (not sid) (not len) (> (+ next len) (bytes-length bs)))
               (modinfo (reverse secs) (hash) '() #"" #t dup)
               (loop (+ next len)
                     (if (= sid 0) seen (set-add seen sid))
                     (cons (section sid (subbytes bs next (+ next len))) secs)
                     (or dup (and (not (= sid 0)) (set-member? seen sid)))
                     #f))]))))

(define (payload-lebs p)
  (let loop ([i 0] [xs '()] [bad #f])
    (cond
      [bad (values (reverse xs) #t)]
      [(= i (bytes-length p)) (values (reverse xs) #f)]
      [else
       (define-values (v j) (read-leb p i))
       (if v (loop j (cons v xs) #f) (values (reverse xs) #t))])))

(define (one-section secs id)
  (for/first ([s secs] #:when (= (section-id s) id)) s))

(define (finish secs dup)
  (define lims (make-hash))
  (define bad #f)
  (for ([id '(2 3)])
    (define s (one-section secs id))
    (when s
      (define-values (xs b) (payload-lebs (section-payload s)))
      (when (or b (< (length xs) 1)) (set! bad #t))
      (when (pair? xs)
        (hash-set! lims id (cons (first xs) (if (pair? (rest xs)) (second xs) #f))))))
  (define imports
    (for/list ([s secs] #:when (= (section-id s) 1))
      (define p (section-payload s))
      (define-values (n j) (read-leb p 0))
      (if (and n (<= (+ j n) (bytes-length p)))
          (bytes->string/utf-8 (subbytes p j (+ j n)) #\?)
          (begin (set! bad #t) ""))))
  (define c (let ([s (one-section secs 4)]) (if s (section-payload s) #"")))
  (modinfo secs lims imports c bad dup))

(define (allowed-import? s)
  (or (string-prefix? s "env.memory")
      (string-prefix? s "env.table")
      (string=? s "env.clock")))

(define (limits-ok? lims id lo hi)
  (define p (hash-ref lims id (cons lo hi)))
  (and p (cdr p) (<= lo (car p) (cdr p) hi)))

(define (control-ok? bs)
  (let loop ([i 0] [depth 0])
    (cond
      [(> depth 128) #f]
      [(= i (bytes-length bs)) (= depth 0)]
      [else
       (define b (bytes-ref bs i))
       (cond
         [(or (= b #x02) (= b #x03)) (loop (+ i 1) (+ depth 1))]
         [(= b #x0b) (if (zero? depth) (loop (+ i 1) depth) (loop (+ i 1) (- depth 1)))]
         [(= b #x0c)
          (define-values (n j) (read-leb bs (+ i 1)))
          (and n (< n depth) (loop j depth))]
         [else (loop (+ i 1) depth)])])))

(define (reason m)
  (cond
    [(modinfo-malformed m) "malformed"]
    [(modinfo-dup m) "duplicate-section"]
    [(ormap (lambda (x) (not (allowed-import? x))) (modinfo-imports m)) "import-policy"]
    [(not (limits-ok? (modinfo-limits m) 2 1 16)) "memory-policy"]
    [(not (limits-ok? (modinfo-limits m) 3 0 128)) "table-policy"]
    [(not (control-ok? (modinfo-code m))) "control-policy"]
    [else "ok"]))

(define (rel root p)
  (string-replace (path->string (find-relative-path root p)) "\\" "/"))

(define (all-wsm root)
  (sort (for/list ([p (in-directory root)] #:when (and (file-exists? p) (regexp-match? #rx"[.]wsm$" (path->string p)))) p)
        string<? #:key path->string))

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
