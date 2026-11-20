#!/bin/bash
set -euo pipefail

cat > /app/fwrestore/src/fwplan.scm <<'SCM'
#!/usr/bin/env guile
!#

(use-modules (ice-9 match)
             (ice-9 rdelim)
             (ice-9 regex)
             (srfi srfi-1)
             (srfi srfi-13)
             (srfi srfi-9))

(define-record-type <rec>
  (make-rec kind data pos)
  rec?
  (kind rec-kind)
  (data rec-data)
  (pos rec-pos))

(define (usage)
  (display "usage: fwplan --case CASEDIR --out OUTDIR\n")
  (exit 2))

(define (trim-comment line)
  (let ((m (string-index line #\#)))
    (if m (substring line 0 m) line)))

(define (words line)
  (let loop ((chars (string->list (trim-comment line))) (current '()) (out '()))
    (match chars
      (()
       (reverse (if (null? current)
                    out
                    (cons (list->string (reverse current)) out))))
      ((ch . rest)
       (if (char-whitespace? ch)
           (loop rest '() (if (null? current)
                              out
                              (cons (list->string (reverse current)) out)))
           (loop rest (cons ch current) out))))))

(define (split-list s)
  (if (string=? s "") '() (string-split s #\,)))

(define (sorted-strings xs) (sort xs string<?))

(define (join xs sep)
  (if (null? xs) "" (string-join xs sep)))

(define (parse-int s)
  (let ((n (string->number s)))
    (and (integer? n) n)))

(define (valid-port? n) (and n (<= 1 n) (<= n 65535)))

(define (dirname path)
  (let ((m (string-match "^(.*)/[^/]*$" path)))
    (if m (match:substring m 1) ".")))

(define (absolute-path path)
  (if (string-prefix? "/" path)
      path
      (string-append (getcwd) "/" path)))

(define (canon path)
  (let loop ((parts (string-split (absolute-path path) #\/)) (out '()))
    (match parts
      (() (string-append "/" (string-join (reverse out) "/")))
      ((part . rest)
       (cond
        ((or (string=? part "") (string=? part ".")) (loop rest out))
        ((string=? part "..") (loop rest (if (null? out) '() (cdr out))))
        (else (loop rest (cons part out))))))))

(define (inside? root path)
  (let ((r (if (string-suffix? "/" root) root (string-append root "/"))))
    (or (string=? root path) (string-prefix? r path))))

(define (json-str s)
  (string-append "\""
                 (list->string
                  (append-map
                   (lambda (c)
                     (case c
                       ((#\\) (string->list "\\\\"))
                       ((#\") (string->list "\\\""))
                       ((#\newline) (string->list "\\n"))
                       ((#\tab) (string->list "\\t"))
                       (else (list c))))
                   (string->list s)))
                 "\""))

(define (alist-ref key alist)
  (let ((p (assoc key alist))) (and p (cdr p))))

(define (replace-by key-fn item items)
  (cons item (filter (lambda (x) (not (equal? (key-fn x) (key-fn item)))) items)))

(define (parse-record w)
  (match w
    (("service" name "owner" owner "exposure" exposure "ports" ports "zones" zones)
     (unless (member exposure '("internal" "public")) (throw 'reject "bad-record"))
     (let ((parsed-ports
            (map (lambda (entry)
                   (match (string-split entry #\:)
                     ((proto port)
                      (let ((n (parse-int port)))
                        (unless (valid-port? n) (throw 'reject "bad-port"))
                        (cons proto n)))
                     (_ (throw 'reject "bad-port"))))
                 (split-list ports))))
       (list 'service name owner exposure parsed-ports (sorted-strings (split-list zones)))))
    (("chain" family table name "priority" priority "policy" policy)
     (unless (member policy '("accept" "drop")) (throw 'reject "bad-record"))
     (let ((n (parse-int priority)))
       (unless n (throw 'reject "bad-record"))
       (list 'chain family table name n policy)))
    (((or "allow" "deny") family table chain proto port "service" svc "zones" zones "ticket" ticket)
     (let ((n (parse-int port)))
       (unless (valid-port? n) (throw 'reject "bad-port"))
       (list 'rule (car w) family table chain proto n svc (sorted-strings (split-list zones)) ticket)))
    (("checkpoint" id status)
     (unless (member status '("prepared" "committed" "failed")) (throw 'reject "bad-checkpoint"))
     (list 'checkpoint id status))
    (_ (throw 'reject "bad-record"))))

(define (read-lines path)
  (catch #t
    (lambda ()
      (call-with-input-file path
        (lambda (port)
          (let loop ((acc '()))
            (let ((line (read-line port)))
              (if (eof-object? line) (reverse acc) (loop (cons line acc))))))))
    (lambda args (throw 'reject "missing-include"))))

(define (expand-file root path stack next-pos)
  (let ((full (canon path))
        (case-root (canon root)))
    (unless (inside? case-root full) (throw 'reject "include-outside-case"))
    (when (member full stack) (throw 'reject "include-cycle"))
    (unless (file-exists? full) (throw 'reject "missing-include"))
    (let loop ((lines (read-lines full)) (records '()) (pos next-pos))
      (match lines
        (() (values (reverse records) pos))
        ((line . rest)
         (let ((w (words line)))
           (if (null? w)
               (loop rest records pos)
               (match w
                 (("include" rel)
                  (let ((target (string-append (dirname full) "/" rel)))
                    (call-with-values
                        (lambda () (expand-file root target (cons full stack) pos))
                      (lambda (child new-pos)
                        (loop rest (append (reverse child) records) new-pos)))))
                 (("include" . _) (throw 'reject "bad-record"))
                 (_ (loop rest (cons (make-rec (car (parse-record w)) (parse-record w) pos) records) (+ pos 1)))))))))))

(define (checkpoint-record? rec) (eq? (rec-kind rec) 'checkpoint))

(define (find-active records)
  (let ((checkpoints (filter checkpoint-record? records)))
    (when (null? checkpoints) (throw 'reject "no-usable-checkpoint"))
    (let* ((latest (last checkpoints))
           (ldata (rec-data latest))
           (latest-status (caddr ldata)))
      (if (string=? latest-status "committed")
          (values "apply" (cadr ldata) (rec-pos latest))
          (let ((prior (filter (lambda (r)
                                 (and (< (rec-pos r) (rec-pos latest))
                                      (string=? (caddr (rec-data r)) "committed")))
                               checkpoints)))
            (when (null? prior) (throw 'reject "no-committed-checkpoint"))
            (let ((target (last prior)))
              (values "rollback" (cadr (rec-data target)) (rec-pos target))))))))

(define (service-key s) (cadr s))
(define (chain-key c) (list (cadr c) (caddr c) (cadddr c)))
(define (chain-priority-key c) (list (cadr c) (caddr c) (list-ref c 4)))
(define (rule-key r)
  (list (list-ref r 2) (list-ref r 3) (list-ref r 4) (list-ref r 5)
        (list-ref r 6) (list-ref r 7) (list-ref r 8)))

(define (materialize records horizon)
  (let ((services '()) (chains '()) (rules '()))
    (for-each
     (lambda (rec)
       (when (<= (rec-pos rec) horizon)
         (match (rec-data rec)
           (('service . _) (set! services (replace-by service-key (rec-data rec) services)))
           (('chain . _) (set! chains (replace-by chain-key (rec-data rec) chains)))
           (('rule . _) (set! rules (replace-by rule-key (rec-data rec) rules)))
           (_ #t))))
     records)
    (let ((priority-keys '()))
      (for-each
       (lambda (c)
         (let ((pk (chain-priority-key c)))
           (when (member pk priority-keys) (throw 'reject "chain-priority-conflict"))
           (set! priority-keys (cons pk priority-keys))))
       chains))
    (for-each
     (lambda (r)
       (unless (find (lambda (c) (equal? (chain-key c) (list (list-ref r 2) (list-ref r 3) (list-ref r 4)))) chains)
         (throw 'reject "unknown-chain"))
       (unless (find (lambda (s) (string=? (service-key s) (list-ref r 7))) services)
         (throw 'reject "unknown-service")))
     rules)
    (values services chains rules)))

(define (chain<? a b)
  (let ((ka (list (cadr a) (caddr a) (list-ref a 4) (cadddr a)))
        (kb (list (cadr b) (caddr b) (list-ref b 4) (cadddr b))))
    (let loop ((x ka) (y kb))
      (cond ((null? x) #f)
            ((and (number? (car x)) (< (car x) (car y))) #t)
            ((and (number? (car x)) (> (car x) (car y))) #f)
            ((and (string? (car x)) (string<? (car x) (car y))) #t)
            ((equal? (car x) (car y)) (loop (cdr x) (cdr y)))
            (else #f)))))

(define (rule<? a b)
  (let ((ka (list (list-ref a 2) (list-ref a 3) (list-ref a 4) (list-ref a 5)
                  (list-ref a 6) (list-ref a 7) (join (list-ref a 8) ",")))
        (kb (list (list-ref b 2) (list-ref b 3) (list-ref b 4) (list-ref b 5)
                  (list-ref b 6) (list-ref b 7) (join (list-ref b 8) ","))))
    (let loop ((x ka) (y kb))
      (cond ((null? x) #f)
            ((and (number? (car x)) (< (car x) (car y))) #t)
            ((and (number? (car x)) (> (car x) (car y))) #f)
            ((and (string? (car x)) (string<? (car x) (car y))) #t)
            ((equal? (car x) (car y)) (loop (cdr x) (cdr y)))
            (else #f)))))

(define (tx-json records)
  (join (map (lambda (r)
               (string-append "{\"id\":" (json-str (cadr (rec-data r)))
                              ",\"status\":" (json-str (caddr (rec-data r))) "}"))
             (filter checkpoint-record? records))
        ","))

(define (chain-json c)
  (string-append "{\"family\":" (json-str (cadr c))
                 ",\"table\":" (json-str (caddr c))
                 ",\"name\":" (json-str (cadddr c))
                 ",\"priority\":" (number->string (list-ref c 4))
                 ",\"policy\":" (json-str (list-ref c 5)) "}"))

(define (rule-json r)
  (string-append "{\"family\":" (json-str (list-ref r 2))
                 ",\"table\":" (json-str (list-ref r 3))
                 ",\"chain\":" (json-str (list-ref r 4))
                 ",\"action\":" (json-str (list-ref r 1))
                 ",\"proto\":" (json-str (list-ref r 5))
                 ",\"port\":" (number->string (list-ref r 6))
                 ",\"service\":" (json-str (list-ref r 7))
                 ",\"zones\":[" (join (map json-str (list-ref r 8)) ",") "]"
                 ",\"ticket\":" (json-str (list-ref r 9)) "}"))

(define (endpoint-set service rules)
  (let ((name (service-key service)))
    (append-map
     (lambda (r)
       (if (and (string=? (list-ref r 1) "allow") (string=? (list-ref r 7) name))
           (map (lambda (zone) (list zone (list-ref r 5) (list-ref r 6))) (list-ref r 8))
           '()))
     rules)))

(define (service-status service rules)
  (let* ((declared-ports (list-ref service 4))
         (declared-zones (list-ref service 5))
         (allowed (endpoint-set service rules))
         (allowed-zones (delete-duplicates (sorted-strings (map car allowed))))
         (declared-endpoints (append-map (lambda (z) (map (lambda (p) (list z (car p) (cdr p))) declared-ports)) declared-zones))
         (missing? (any (lambda (e) (not (member e allowed))) declared-endpoints))
         (extra? (any (lambda (e) (not (member e declared-endpoints))) allowed))
         (status (if (string=? (list-ref service 3) "public")
                     (cond (extra? "expanded") (missing? "closed") (else "ok"))
                     (if (any (lambda (z) (not (member z declared-zones))) allowed-zones) "exposed" "ok"))))
    (list allowed-zones status)))

(define (audit-line service rules)
  (let ((ss (service-status service rules)))
    (string-append (service-key service) "\t" (list-ref service 2) "\t" (list-ref service 3)
                   "\t" (join (car ss) ",") "\t" (cadr ss) "\n")))

(define (write-outputs out status reason rollback records services chains rules)
  (system* "mkdir" "-p" out)
  (call-with-output-file (string-append out "/plan.json")
    (lambda (p)
      (display "{\"status\":" p) (display (json-str status) p)
      (display ",\"reason\":" p) (display (json-str reason) p)
      (display ",\"rollback_to\":" p) (display (if rollback (json-str rollback) "null") p)
      (display ",\"transactions\":[" p) (display (tx-json records) p)
      (display "],\"chains\":[" p) (display (join (map chain-json (sort chains chain<?)) ",") p)
      (display "],\"rules\":[" p) (display (join (map rule-json (sort rules rule<?)) ",") p)
      (display "]}" p)))
  (call-with-output-file (string-append out "/service-audit.tsv")
    (lambda (p)
      (display "service\towner\texposure\tallowed_zones\tstatus\n" p)
      (for-each (lambda (s) (display (audit-line s rules) p))
                (sort services (lambda (a b) (string<? (service-key a) (service-key b))))))))

(define (main args)
  (match (cdr args)
    (("--case" case-dir "--out" out-dir)
     (catch 'reject
       (lambda ()
         (call-with-values
             (lambda () (expand-file case-dir (string-append case-dir "/root.fw") '() 0))
           (lambda (records next-pos)
             (call-with-values
                 (lambda () (find-active records))
               (lambda (status rollback horizon)
                 (call-with-values
                     (lambda () (materialize records horizon))
                   (lambda (services chains rules)
                     (write-outputs out-dir status "" rollback records services chains rules))))))))
       (lambda (key reason)
         (write-outputs out-dir "reject" reason #f '() '() '() '()))))
    (_ (usage))))

(main (command-line))
SCM

/app/fwrestore/scripts/build.sh
