#lang racket
(require json racket/list racket/string)

(define (other c) (if (string=? c "B") "W" "B"))
(define (pos-key x y) (string (integer->char (+ 97 x)) (integer->char (+ 97 y))))

(define (coord->xy size s)
  (and (= (string-length s) 2)
       (let ([x (- (char->integer (string-ref s 0)) 97)]
             [y (- (char->integer (string-ref s 1)) 97)])
         (and (<= 0 x) (< x size) (<= 0 y) (< y size) (list x y)))))

(define (neighbors size c)
  (define xy (coord->xy size c))
  (if xy
      (let ([x (first xy)] [y (second xy)])
        (for/list ([d '((1 0) (-1 0) (0 1) (0 -1))]
                   #:when (let ([nx (+ x (first d))] [ny (+ y (second d))])
                            (and (<= 0 nx) (< nx size) (<= 0 ny) (< ny size))))
          (pos-key (+ x (first d)) (+ y (second d)))))
      '()))

(define (board-key size board)
  (define out (open-output-string))
  (for* ([y (in-range size)] [x (in-range size)])
    (display (hash-ref board (pos-key x y) ".") out))
  (get-output-string out))

(define (stone-group size board start)
  (define color (hash-ref board start #f))
  (define seen (make-hash))
  (define libs (make-hash))
  (let loop ([front (list start)])
    (cond [(null? front) (values (hash-keys seen) (hash-keys libs))]
          [else
           (define p (car front))
           (if (hash-has-key? seen p)
               (loop (cdr front))
               (let ([more '()])
                 (hash-set! seen p #t)
                 (for ([n (neighbors size p)])
                   (define v (hash-ref board n #f))
                   (cond [(not v) (hash-set! libs n #t)]
                         [(and color (string=? v color)) (set! more (cons n more))]))
                 (loop (append more (cdr front)))))])))

(define (try-play size board history color coord)
  (cond [(not (coord->xy size coord)) (values #f board history 0)]
        [(hash-has-key? board coord) (values #f board history 0)]
        [else
         (define nb (hash-copy board))
         (define captured 0)
         (hash-set! nb coord color)
         (for ([n (neighbors size coord)])
            (when (and (hash-has-key? nb n) (string=? (hash-ref nb n) (other color)))
              (define-values (grp libs) (stone-group size nb n))
              (when (null? libs)
                (set! captured (+ captured (length grp)))
                (for ([p grp]) (hash-remove! nb p)))))
         (define-values (_grp own-libs) (stone-group size nb coord))
         (cond [(null? own-libs) (values #f board history 0)]
                [else
                 (define k (board-key size nb))
                 (if (member k history)
                     (values #f board history 0)
                     (values #t nb (cons k history) captured))])]))

(define (empty-region size board start seen)
  (define region '())
  (define adjacent (make-hash))
  (let loop ([front (list start)])
    (unless (null? front)
      (define p (car front))
      (unless (hash-has-key? seen p)
        (hash-set! seen p #t)
        (set! region (cons p region))
        (for ([n (neighbors size p)])
          (define v (hash-ref board n #f))
          (cond [(not v) (unless (hash-has-key? seen n) (loop (list n)))]
                [else (hash-set! adjacent v #t)])))
      (loop (cdr front))))
  (values region (hash-keys adjacent)))

(define (score-board size board komi scoring black-captures white-captures)
  (define area? (string=? scoring "area"))
  (define b (if area? 0 black-captures))
  (define w (+ komi (if area? 0 white-captures)))
  (define seen-empty (make-hash))
  (for* ([y (in-range size)] [x (in-range size)])
    (define p (pos-key x y))
    (define v (hash-ref board p #f))
    (cond [(equal? v "B") (when area? (set! b (+ b 1)))]
          [(equal? v "W") (when area? (set! w (+ w 1)))]
          [(not (hash-has-key? seen-empty p))
           (define-values (reg adj) (empty-region size board p seen-empty))
           (when (= (length adj) 1)
             (if (string=? (first adj) "B")
                 (set! b (+ b (length reg)))
                 (set! w (+ w (length reg)))))]))
  (values b w))

(define args (current-command-line-arguments))
(define input (vector-ref args 0))
(define output (vector-ref args 1))

(define players (make-hash))
(define completed '())
(define suspended (make-hash))
(define gid #f)
(define size 0)
(define komi 0)
(define scoring "area")
(define black "")
(define white "")
(define board (make-hash))
(define history '())
(define stack '())
(define next #f)
(define pass-count 0)
(define black-captures 0)
(define white-captures 0)
(define done #f)

(define (register! p) (hash-set! players p #t))
(define (player-of color) (if (string=? color "B") black white))

(define (finish! winner reason margin)
  (when (and gid (not done))
    (set! completed (cons (hasheq 'id gid 'black black 'white white 'winner winner 'reason reason 'margin margin) completed))
    (set! done #t)))

(define (finish-score!)
  (define-values (bs ws) (score-board size board komi scoring black-captures white-captures))
  (define diff (- bs ws))
  (cond [(> diff 0) (finish! black "score" diff)]
        [(< diff 0) (finish! white "score" (- diff))]
        [else (finish! "draw" "score" 0)]))

(define (apply-ruling! target side margin-text)
  (define changed? #f)
  (define rewritten
    (for/list ([g completed])
      (if (and (not changed?) (string=? (hash-ref g 'id) target))
          (begin
            (set! changed? #t)
            (hasheq 'id (hash-ref g 'id)
                    'black (hash-ref g 'black)
                    'white (hash-ref g 'white)
                    'winner (cond [(string=? side "draw") "draw"]
                                  [(string=? side "B") (hash-ref g 'black)]
                                  [else (hash-ref g 'white)])
                    'reason "ruling"
                    'margin (if (string=? side "draw") 0 (string->number margin-text))))
          g)))
  (when changed? (set! completed rewritten)))

(define (start-game! t)
  (set! gid (list-ref t 1))
  (set! size (string->number (list-ref t 2)))
  (set! komi (string->number (list-ref t 3)))
  (set! black (list-ref t 4))
  (set! white (list-ref t 5))
  (set! scoring (if (> (length t) 6) (list-ref t 6) "area"))
  (register! black)
  (register! white)
  (set! board (make-hash))
  (set! history (list (board-key size board)))
  (set! stack '())
  (set! next #f)
  (set! pass-count 0)
  (set! black-captures 0)
  (set! white-captures 0)
  (set! done #f))

(define (record-setup! color coords)
  (for ([c coords])
    (when (and (coord->xy size c) (not (hash-has-key? board c)))
      (hash-set! board c color)))
  (void))

(define (push-frame! old-board old-history old-next old-pass-count old-black-captures old-white-captures)
  (set! stack (cons (vector (hash-copy old-board) old-history old-next old-pass-count
                            old-black-captures old-white-captures) stack)))

(define (current-state)
  (vector gid size komi scoring black white (hash-copy board) history stack next
          pass-count black-captures white-captures done))

(define (restore-state! state)
  (set! gid (vector-ref state 0))
  (set! size (vector-ref state 1))
  (set! komi (vector-ref state 2))
  (set! scoring (vector-ref state 3))
  (set! black (vector-ref state 4))
  (set! white (vector-ref state 5))
  (set! board (hash-copy (vector-ref state 6)))
  (set! history (vector-ref state 7))
  (set! stack (vector-ref state 8))
  (set! next (vector-ref state 9))
  (set! pass-count (vector-ref state 10))
  (set! black-captures (vector-ref state 11))
  (set! white-captures (vector-ref state 12))
  (set! done (vector-ref state 13)))

(define (pause! key)
  (when (and gid (not done))
    (unless (hash-has-key? suspended key)
      (hash-set! suspended key (current-state)))
    (set! gid #f)
    (set! done #t)))

(define (resume! key)
  (when (hash-has-key? suspended key)
    (restore-state! (hash-ref suspended key))
    (hash-remove! suspended key)))

(define (undo! n)
  (for ([i (in-range n)] #:break (null? stack))
    (define f (car stack))
    (set! board (hash-copy (vector-ref f 0)))
    (set! history (vector-ref f 1))
    (set! next (vector-ref f 2))
    (set! pass-count (vector-ref f 3))
    (set! black-captures (vector-ref f 4))
    (set! white-captures (vector-ref f 5))
    (set! stack (cdr stack))))

(for ([line (file->lines input)])
  (define trimmed (string-trim line))
  (unless (or (string=? trimmed "") (string-prefix? trimmed "#"))
    (define t (string-split trimmed))
    (define tag (first t))
    (cond
      [(string=? tag "GAME") (start-game! t)]
      [(string=? tag "RULING") (apply-ruling! (list-ref t 1) (list-ref t 2) (list-ref t 3))]
      [(string=? tag "RESUME") (resume! (list-ref t 1))]
      [(and gid (not done) (string=? tag "SETUP")) (record-setup! (list-ref t 1) (drop t 2))]
      [(and gid (not done) (string=? tag "PLAY"))
       (define color (list-ref t 1))
       (define move (list-ref t 2))
       (cond [(and next (not (string=? color next)))
               (finish! (player-of (other color)) "illegal" 0)]
               [(string=? move "pass")
                (push-frame! board history next pass-count black-captures white-captures)
                (set! next (other color))
               (set! pass-count (+ pass-count 1))
               (when (>= pass-count 2) (finish-score!))]
              [else
               (define old-board (hash-copy board))
               (define old-history history)
                (define old-next next)
                (define old-pass-count pass-count)
                (define old-black-captures black-captures)
                (define old-white-captures white-captures)
                (define-values (ok nb nh captured) (try-play size board history color move))
                (if ok
                    (begin
                      (push-frame! old-board old-history old-next old-pass-count
                                   old-black-captures old-white-captures)
                      (set! board nb)
                      (set! history nh)
                      (if (string=? color "B")
                          (set! black-captures (+ black-captures captured))
                          (set! white-captures (+ white-captures captured)))
                      (set! next (other color)))
                   (finish! (player-of (other color)) "illegal" 0))])]
      [(and gid (not done) (string=? tag "UNDO")) (undo! (string->number (list-ref t 1)))]
      [(and gid (not done) (string=? tag "PAUSE")) (pause! (list-ref t 1))]
      [(and gid (not done) (string=? tag "FORFEIT")) (finish! (list-ref t 1) "forfeit" 0)]
      [(and gid (not done) (string=? tag "SCORE")) (finish-score!)])))

(define ordered-games (reverse completed))
(define stats (make-hash))
(define (stat p) (hash-ref! stats p (lambda () (vector 0 0 0 0 0 '() '()))))
(for ([p (hash-keys players)]) (stat p))

(for ([g ordered-games])
  (define b (hash-ref g 'black))
  (define w (hash-ref g 'white))
  (define win (hash-ref g 'winner))
  (define m (hash-ref g 'margin))
  (define sb (stat b))
  (define sw (stat w))
  (vector-set! sb 5 (cons w (vector-ref sb 5)))
  (vector-set! sw 5 (cons b (vector-ref sw 5)))
  (cond [(string=? win "draw")
         (vector-set! sb 6 (cons (cons w 0.5) (vector-ref sb 6)))
         (vector-set! sw 6 (cons (cons b 0.5) (vector-ref sw 6)))
         (for ([s (list sb sw)])
           (vector-set! s 0 (+ (vector-ref s 0) 0.5))
           (vector-set! s 3 (+ (vector-ref s 3) 1)))]
        [else
         (define loser (if (string=? win b) w b))
         (define s-win (stat win))
         (define s-lose (stat loser))
         (vector-set! s-win 0 (+ (vector-ref s-win 0) 1))
         (vector-set! s-win 1 (+ (vector-ref s-win 1) 1))
         (vector-set! s-lose 2 (+ (vector-ref s-lose 2) 1))
         (vector-set! s-win 4 (+ (vector-ref s-win 4) m))
         (vector-set! s-lose 4 (- (vector-ref s-lose 4) m))
         (vector-set! s-win 6 (cons (cons loser 1) (vector-ref s-win 6)))
         (vector-set! s-lose 6 (cons (cons win 0) (vector-ref s-lose 6)))]))

(define (points-of p) (vector-ref (stat p) 0))
(define (sos-of p)
  (for/sum ([o (remove-duplicates (vector-ref (stat p) 5))]) (points-of o)))

(define rows
  (for/list ([p (hash-keys players)])
    (define s (stat p))
    (define sos (sos-of p))
    (define h2h
      (for/sum ([entry (vector-ref s 6)]
                #:when (and (= (points-of (car entry)) (vector-ref s 0))
                            (= (sos-of (car entry)) sos)))
        (cdr entry)))
    (hasheq 'player p 'points (vector-ref s 0) 'wins (vector-ref s 1)
            'losses (vector-ref s 2) 'draws (vector-ref s 3)
            'sos sos 'margin (vector-ref s 4) 'sort-h2h h2h)))

(define (better? a b)
  (cond [(not (= (hash-ref a 'points) (hash-ref b 'points))) (> (hash-ref a 'points) (hash-ref b 'points))]
         [(not (= (hash-ref a 'sos) (hash-ref b 'sos))) (> (hash-ref a 'sos) (hash-ref b 'sos))]
        [(not (= (hash-ref a 'sort-h2h) (hash-ref b 'sort-h2h))) (> (hash-ref a 'sort-h2h) (hash-ref b 'sort-h2h))]
         [(not (= (hash-ref a 'margin) (hash-ref b 'margin))) (> (hash-ref a 'margin) (hash-ref b 'margin))]
         [else (string<? (hash-ref a 'player) (hash-ref b 'player))]))

(call-with-output-file output #:exists 'replace
  (lambda (out)
    (write-json (hasheq 'games ordered-games
                        'standings (map (lambda (row) (hash-remove row 'sort-h2h))
                                        (sort rows better?)))
                out)))
