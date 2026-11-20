#!/bin/bash
set -euo pipefail

cat > /app/arbiter.rkt <<'RKT'
#!/usr/bin/env racket
#lang racket
(require json racket/list racket/string)

(define letters "ABCDEFGHJKLMNOPQRST")

(define (letter-index c)
  (for/first ([ch (in-string letters)] [i (in-naturals)] #:when (char=? ch c)) i))

(define (jget h k [d #f])
  (define s (symbol->string k))
  (cond [(and (hash? h) (hash-has-key? h k)) (hash-ref h k)]
        [(and (hash? h) (hash-has-key? h s)) (hash-ref h s)]
        [else d]))

(define (sha256-text s)
  (define-values (proc out in err) (subprocess #f #f #f "/usr/bin/sha256sum"))
  (write-bytes (string->bytes/utf-8 s) in)
  (close-output-port in)
  (define line (read-line out))
  (close-input-port out)
  (close-input-port err)
  (subprocess-wait proc)
  (substring line 0 64))

(define (clean-lines path)
  (for/list ([ln (in-list (file->lines path))]
             #:when (not (string=? (string-trim ln) "")))
    (string-trim ln)))

(define (digest-lines xs)
  (sha256-text (if (null? xs) "" (string-append (string-join xs "\n") "\n"))))

(define (idx n x y) (+ x (* y n)))
(define (at b n x y) (vector-ref b (idx n x y)))
(define (put! b n x y v) (vector-set! b (idx n x y) v))
(define (inside? n x y) (and (<= 0 x) (< x n) (<= 0 y) (< y n)))
(define (other c) (if (eq? c 'B) 'W 'B))

(define (coord->xy s n)
  (define u (string-upcase s))
  (and (>= (string-length u) 2)
       (let* ([c (string-ref u 0)]
              [x (letter-index c)]
              [num (string->number (substring u 1))])
         (and x (integer? num) (<= 1 num) (<= num n) (< x n) (cons x (- num 1))))))

(define (neighbors n x y)
  (filter values (list (and (inside? n (- x 1) y) (cons (- x 1) y))
                       (and (inside? n (+ x 1) y) (cons (+ x 1) y))
                       (and (inside? n x (- y 1)) (cons x (- y 1)))
                       (and (inside? n x (+ y 1)) (cons x (+ y 1))))))

(define (group-and-libs b n x y)
  (define color (at b n x y))
  (define seen (make-hash))
  (define libs (make-hash))
  (let walk ([p (cons x y)])
    (unless (hash-has-key? seen p)
      (hash-set! seen p #t)
      (for ([q (in-list (neighbors n (car p) (cdr p)))])
        (define v (at b n (car q) (cdr q)))
        (cond [(not v) (hash-set! libs q #t)]
              [(eq? v color) (walk q)]))))
  (values (hash-keys seen) (hash-count libs)))

(define (board-key st with-turn?)
  (define n (hash-ref st 'size))
  (define b (hash-ref st 'board))
  (define s (list->string (for/list ([i (in-range (* n n))])
                            (case (vector-ref b i) [(B) #\B] [(W) #\W] [else #\.]))))
  (if with-turn? (string-append (symbol->string (hash-ref st 'turn)) ":" s) s))

(define (history-key st)
  (board-key st (string=? (hash-ref st 'superko) "situational")))

(define (make-state n komi sk board turn)
  (make-hash (list (cons 'size n) (cons 'komi komi) (cons 'superko sk) (cons 'board board)
                   (cons 'turn turn) (cons 'passes 0) (cons 'history (make-hash)))))

(define (parse-setup line)
  (define parts (string-split line))
  (and (pair? parts) (string=? (car parts) "setup")
       (let ([h (make-hash)])
         (for ([p (in-list (cdr parts))])
           (define kv (string-split p "="))
           (when (= (length kv) 2) (hash-set! h (car kv) (cadr kv))))
         (define n (string->number (hash-ref h "size" "")))
         (define komi (string->number (hash-ref h "komi" "")))
         (define sk (hash-ref h "superko" ""))
         (define hs (hash-ref h "handicap" "none"))
         (and (integer? n) (<= 2 n 19) komi (member sk '("positional" "situational"))
              (let* ([board (make-vector (* n n) #f)]
                     [coords (if (string=? hs "none") '() (filter (lambda (s) (not (string=? s ""))) (string-split hs ",")))]
                     [bad #f])
                (for ([c (in-list coords)])
                  (define p (coord->xy c n))
                  (if (and p (not (at board n (car p) (cdr p))))
                      (put! board n (car p) (cdr p) 'B)
                      (set! bad #t)))
                (and (not bad)
                     (let ([st (make-state n komi sk board (if (null? coords) 'B 'W))])
                       (hash-set! (hash-ref st 'history) (history-key st) #t)
                       st)))))))

(define (copy-st st)
  (define ns (make-state (hash-ref st 'size) (hash-ref st 'komi) (hash-ref st 'superko)
                         (vector-copy (hash-ref st 'board)) (hash-ref st 'turn)))
  (hash-set! ns 'passes (hash-ref st 'passes))
  (hash-set! ns 'history (hash-copy (hash-ref st 'history)))
  ns)

(define (try-move st color raw line-no)
  (cond
    ((not (eq? color (hash-ref st 'turn))) (values st (list line-no "turn")))
    ((string-ci=? raw "pass")
     (define ns (copy-st st))
     (hash-set! ns 'turn (other color))
     (hash-set! ns 'passes (+ 1 (hash-ref st 'passes)))
     (values ns #f))
    (else
     (define n (hash-ref st 'size))
     (define p (coord->xy raw n))
     (cond
       ((not p) (values st (list line-no "coordinate")))
       ((at (hash-ref st 'board) n (car p) (cdr p)) (values st (list line-no "occupied")))
       (else
        (define ns (copy-st st))
        (define b (hash-ref ns 'board))
        (put! b n (car p) (cdr p) color)
        (for ([q (in-list (neighbors n (car p) (cdr p)))])
          (when (eq? (at b n (car q) (cdr q)) (other color))
            (define-values (grp libs) (group-and-libs b n (car q) (cdr q)))
            (when (= libs 0)
              (for ([r (in-list grp)]) (put! b n (car r) (cdr r) #f)))))
        (define-values (mine mine-libs) (group-and-libs b n (car p) (cdr p)))
        (if (= mine-libs 0)
            (values st (list line-no "suicide"))
            (begin
              (hash-set! ns 'turn (other color))
              (hash-set! ns 'passes 0)
              (let ([k (history-key ns)])
                (if (hash-has-key? (hash-ref ns 'history) k)
                    (values st (list line-no "superko"))
                    (begin
                      (hash-set! (hash-ref ns 'history) k #t)
                      (values ns #f)))))))))))

(define (apply-lines xs [limit #f])
  (if (null? xs)
      (values #f (list 1 "setup") '())
      (let ([st0 (parse-setup (car xs))])
        (if (not st0)
            (values #f (list 1 "setup") '())
            (let loop ([st st0] [rest (cdr xs)] [line-no 2] [used (list (car xs))])
              (cond [(and limit (>= (length used) limit)) (values st #f used)]
                    [(null? rest) (values st #f used)]
                    [(>= (hash-ref st 'passes) 2) (values st (list line-no "game-over") used)]
                    [else
                     (define parts (string-split (car rest)))
                     (if (not (= (length parts) 2))
                         (values st (list line-no "record") used)
                         (let ([c (cond [(string=? (car parts) "B") 'B] [(string=? (car parts) "W") 'W] [else #f])])
                           (if (not c)
                               (values st (list line-no "record") used)
                               (let-values ([(ns err) (try-move st c (cadr parts) line-no)])
                                 (if err
                                     (values st err used)
                                     (loop ns (cdr rest) (+ line-no 1) (append used (list (car rest)))))))))]))))))

(define (score st)
  (define n (hash-ref st 'size))
  (define b (hash-ref st 'board))
  (define seen (make-hash))
  (define bs 0)
  (define ws (hash-ref st 'komi))
  (for* ([y (in-range n)] [x (in-range n)])
    (case (at b n x y)
      [(B) (set! bs (+ bs 1))]
      [(W) (set! ws (+ ws 1))]
      [else
       (unless (hash-has-key? seen (cons x y))
         (define pts '())
         (define owners '())
         (let walk ([p (cons x y)])
           (unless (hash-has-key? seen p)
             (hash-set! seen p #t)
             (set! pts (cons p pts))
             (for ([q (in-list (neighbors n (car p) (cdr p)))])
               (define v (at b n (car q) (cdr q)))
               (cond [(not v) (walk q)]
                     [(and v (not (member v owners))) (set! owners (cons v owners))]))))
         (when (= (length owners) 1)
           (if (eq? (car owners) 'B)
               (set! bs (+ bs (length pts)))
               (set! ws (+ ws (length pts))))))]))
  (values bs ws))

(define (board-rows st)
  (define n (hash-ref st 'size))
  (define b (hash-ref st 'board))
  (for/list ([yy (in-range n)])
    (define y (- n 1 yy))
    (list->string (for/list ([x (in-range n)])
                    (case (at b n x y) [(B) #\B] [(W) #\W] [else #\.])))))

(define (report st err accepted-lines)
  (define-values (bs ws) (if st (score st) (values 0 0)))
  (define base (hash 'status (if err "invalid" "ok")
                     'accepted (length accepted-lines)
                     'event_digest (digest-lines accepted-lines)
                     'scores (hash 'B bs 'W ws)
                     'winner (cond [(> bs ws) "B"] [(< bs ws) "W"] [else "draw"])
                     'board (if st (board-rows st) '())))
  (if err
      (hash-set (hash-set base 'line (car err)) 'reason (cadr err))
      base))

(define (prefix-error)
  (hash 'status "invalid" 'accepted 0 'event_digest "" 'scores (hash 'B 0 'W 0) 'winner "draw"
        'board '() 'line 0 'reason "prefix"))

(define (write-report path data)
  (call-with-output-file path #:exists 'replace (lambda (out) (write-json data out) (newline out))))

(define (adjudicate events report-path)
  (define xs (clean-lines events))
  (define-values (st err used) (apply-lines xs))
  (write-report report-path (report st err used)))

(define (checkpoint events count-str state-path report-path)
  (define count (string->number count-str))
  (define xs (clean-lines events))
  (define-values (st err used) (apply-lines xs count))
  (define prefix (take xs (min count (length xs))))
  (call-with-output-file state-path #:exists 'replace
    (lambda (out) (write-json (hash 'count count 'prefix_digest (digest-lines prefix)) out) (newline out)))
  (write-report report-path (report st err used)))

(define (resume events state-path report-path)
  (define xs (clean-lines events))
  (with-handlers ([exn:fail? (lambda (e) (write-report report-path (prefix-error)))])
    (define stdata (call-with-input-file state-path read-json))
    (define count (jget stdata 'count))
    (define saved (jget stdata 'prefix_digest ""))
    (define ok? (and (integer? count) (<= 0 count) (<= count (length xs))
                     (string=? saved (digest-lines (take xs count)))))
    (if (not ok?)
        (write-report report-path (prefix-error))
        (adjudicate events report-path))))

(module+ main
  (define args (current-command-line-arguments))
  (match (vector->list args)
    [(list "adjudicate" events report-path) (adjudicate events report-path)]
    [(list "checkpoint" events count state-path report-path) (checkpoint events count state-path report-path)]
    [(list "resume" events state-path report-path) (resume events state-path report-path)]
    [_ (eprintf "usage\n") (exit 2)]))
RKT

chmod 0755 /app/arbiter.rkt
