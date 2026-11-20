#!/bin/bash
set -euo pipefail

cat > /app/smoother/src/smoother/core.clj <<'CLJ'
(ns smoother.core
  (:require [clojure.edn :as edn]
            [clojure.string :as str]))

(defn finite-num? [x] (and (number? x) (not (Double/isNaN (double x))) (not (Double/isInfinite (double x)))))
(defn fmt-num [x] (let [y (if (< (Math/abs (double x)) 5.0e-13) 0.0 (double x))] (String/format java.util.Locale/US "%.12f" (object-array [y]))))
(defn escape-json [s] (str/escape s {\\ "\\\\" \" "\\\"" \newline "\\n" \return "\\r" \tab "\\t"}))
(declare encode-json)
(defn encode-map [m] (str "{" (str/join "," (for [k (sort-by name (keys m))] (str (encode-json (name k)) ":" (encode-json (get m k))))) "}"))
(defn encode-json [v]
  (cond (map? v) (encode-map v)
        (sequential? v) (str "[" (str/join "," (map encode-json v)) "]")
        (string? v) (str "\"" (escape-json v) "\"")
        (keyword? v) (encode-json (name v))
        (number? v) (fmt-num v)
        (nil? v) "null"
        :else (encode-json (str v))))
(defn write-report! [out report] (spit out (str (encode-json report) "\n")))
(defn invalid! [out run-id msg] (write-report! out {:run_id (or run-id "unknown") :status "invalid" :error msg}) (System/exit 1))

(defn vnum? [v n] (and (vector? v) (= n (count v)) (every? finite-num? v)))
(defn mat? [m n] (and (vector? m) (= n (count m)) (every? #(vnum? % n) m)))
(defn sym-close? [m] (every? true? (for [i (range (count m)) j (range (count m))] (< (Math/abs (- (double (get-in m [i j])) (double (get-in m [j i])))) 1.0e-10))))
(defn spd? [m]
  (and (or (and (mat? m 1) (pos? (double (get-in m [0 0]))))
           (and (mat? m 2) (sym-close? m) (pos? (double (get-in m [0 0])))
                (pos? (- (* (double (get-in m [0 0])) (double (get-in m [1 1]))) (* (double (get-in m [0 1])) (double (get-in m [1 0])))))))))
(defn sym-mat [p] (vec (for [i (range (count p))] (vec (for [j (range (count p))] (/ (+ (double (get-in p [i j])) (double (get-in p [j i]))) 2.0))))))
(defn mat-add [a b] (vec (for [i (range (count a))] (vec (for [j (range (count (a 0)))] (+ (get-in a [i j]) (get-in b [i j])))))))
(defn mat-sub [a b] (vec (for [i (range (count a))] (vec (for [j (range (count (a 0)))] (- (get-in a [i j]) (get-in b [i j])))))))
(defn mat-t [a] (vec (for [j (range (count (a 0)))] (vec (for [i (range (count a))] (get-in a [i j]))))))
(defn mat-mul [a b] (vec (for [i (range (count a))] (vec (for [j (range (count (b 0)))] (reduce + (for [k (range (count b))] (* (get-in a [i k]) (get-in b [k j])))))))))
(defn mat-vec [m v] (vec (for [row m] (reduce + (map * row v)))))
(defn ident [n] (vec (for [i (range n)] (vec (for [j (range n)] (if (= i j) 1.0 0.0))))))
(defn det [m] (if (= 1 (count m)) (get-in m [0 0]) (- (* (get-in m [0 0]) (get-in m [1 1])) (* (get-in m [0 1]) (get-in m [1 0])))))
(defn inv [m]
  (if (= 1 (count m)) [[(/ 1.0 (get-in m [0 0]))]]
      (let [d (det m)] [[(/ (get-in m [1 1]) d) (/ (- (get-in m [0 1])) d)] [(/ (- (get-in m [1 0])) d) (/ (get-in m [0 0]) d)]])))
(defn chol2 [p]
  (let [p (sym-mat p) a (get-in p [0 0]) b (get-in p [1 0]) c (get-in p [1 1]) l00 (Math/sqrt a) l10 (/ b l00) d (- c (* l10 l10))]
    (when-not (and (pos? l00) (pos? d) (finite-num? d)) (throw (Exception. "covariance")))
    [[l00 0.0] [l10 (Math/sqrt d)]]))

(defn predict [x p dt q accel]
  (let [f [[1.0 dt] [0.0 1.0]] qq [[(* q dt dt dt dt 0.25) (* q dt dt dt 0.5)] [(* q dt dt dt 0.5) (* q dt dt)]]]
    [(vec (map + (mat-vec f x) [(* 0.5 dt dt accel) (* dt accel)])) (sym-mat (mat-add (mat-mul (mat-mul f p) (mat-t f)) qq))]))

(defn scalar-model? [h] (vnum? h 2))
(defn normalize-bias [bias n]
  (cond (nil? bias) (vec (repeat n 0.0))
        (and (= n 1) (finite-num? bias)) [(double bias)]
        (and (vnum? bias n)) (vec (map double bias))
        :else (throw (Exception. "bias"))))
(defn sensor-name [k] (cond (keyword? k) (name k) (string? k) k :else nil))
(defn normalize-sensor [v]
  (when-not (and (map? v) (#{#{:h :r} #{:h :r :bias} #{:h :r :gate} #{:h :r :bias :gate}} (set (keys v)))) (throw (Exception. "sensor")))
  (when-not (or (nil? (:gate v)) (and (finite-num? (:gate v)) (pos? (double (:gate v))))) (throw (Exception. "gate")))
  (let [h (:h v) r (:r v) gate (when (contains? v :gate) (double (:gate v)))]
    (cond
      (scalar-model? h) (do (when-not (and (finite-num? r) (pos? (double r))) (throw (Exception. "sensor")))
                             {:h [(vec (map double h))] :r [[(double r)]] :bias (normalize-bias (:bias v) 1) :dim 1 :gate gate})
      (and (vector? h) (= 2 (count h)) (every? #(vnum? % 2) h))
      (do (when-not (spd? r) (throw (Exception. "sensor")))
          {:h (vec (map #(vec (map double %)) h)) :r (sym-mat r) :bias (normalize-bias (:bias v) 2) :dim 2 :gate gate})
      :else (throw (Exception. "sensor")))))
(defn normalize-sensors [sensors]
  (when-not (map? sensors) (throw (Exception. "sensors")))
  (into {} (for [[k v] sensors]
             (let [n (sensor-name k)]
               (when-not n (throw (Exception. "sensor-name")))
               [n (normalize-sensor v)]))))
(defn normalize-z [z dim]
  (cond (and (= dim 1) (finite-num? z)) [(double z)]
        (and (vnum? z dim)) (vec (map double z))
        :else (throw (Exception. "observation"))))
(defn validate-config [cfg]
  (when-not (map? cfg) (throw (Exception. "config")))
  (when-not (or (= (set (keys cfg)) #{:run-id :dt :accel-variance :initial :sensors :observations})
                (= (set (keys cfg)) #{:run-id :dt :accel-variance :initial :sensors :observations :controls})) (throw (Exception. "keys")))
  (when-not (and (string? (:run-id cfg)) (not (str/blank? (:run-id cfg)))) (throw (Exception. "run-id")))
  (when-not (and (finite-num? (:dt cfg)) (pos? (double (:dt cfg))) (finite-num? (:accel-variance cfg)) (pos? (double (:accel-variance cfg)))) (throw (Exception. "scale")))
  (let [initial (:initial cfg)] (when-not (and (map? initial) (= (set (keys initial)) #{:mean :cov}) (vnum? (:mean initial) 2) (spd? (:cov initial))) (throw (Exception. "initial"))))
  (let [sensors (normalize-sensors (:sensors cfg))]
    (when-not (vector? (:observations cfg)) (throw (Exception. "observations")))
    (when-not (or (nil? (:controls cfg)) (vector? (:controls cfg))) (throw (Exception. "controls")))
    (let [obs (vec (for [ob (:observations cfg)]
                    (let [sn (sensor-name (:sensor ob)) sensor (get sensors sn)]
                      (when-not (and (map? ob) (= (set (keys ob)) #{:t :sensor :z}) (integer? (:t ob)) (not (neg? (:t ob))) sn sensor) (throw (Exception. "observation")))
                      {:t (:t ob) :sensor sn :z (normalize-z (:z ob) (:dim sensor))})))
          controls (vec (for [c (or (:controls cfg) [])]
                          (do (when-not (and (map? c) (= (set (keys c)) #{:t :a}) (integer? (:t c)) (pos? (:t c)) (finite-num? (:a c))) (throw (Exception. "control")))
                              {:t (:t c) :a (double (:a c))})))]
      {:run-id (:run-id cfg) :dt (double (:dt cfg)) :accel-variance (double (:accel-variance cfg))
        :initial {:mean (vec (map double (get-in cfg [:initial :mean]))) :cov (sym-mat (get-in cfg [:initial :cov]))}
        :sensors sensors :observations obs :controls controls})))

(defn update-one [x p ob sensor]
  (let [h (:h sensor) r (:r sensor) z (vec (map - (:z ob) (:bias sensor))) hx (mat-vec h x)
        y (vec (map - z hx)) ht (mat-t h) s (sym-mat (mat-add (mat-mul (mat-mul h p) ht) r))]
    (when-not (spd? s) (throw (Exception. "innovation")))
    (let [si (inv s) k (mat-mul (mat-mul p ht) si) kh (mat-mul k h) i-kh (mat-sub (ident 2) kh)
          xn (vec (map + x (mat-vec k y))) pn (sym-mat (mat-add (mat-mul (mat-mul i-kh p) (mat-t i-kh)) (mat-mul (mat-mul k r) (mat-t k))))
          ycol (mapv vector y) ni (get-in (mat-mul (mat-mul [y] si) ycol) [0 0]) ll (* -0.5 (+ (* (count y) (Math/log (* 2.0 Math/PI))) (Math/log (det s)) ni))]
      (if (and (:gate sensor) (> ni (:gate sensor)))
        [x p ni 0.0 1.0]
        [xn pn ni ll 0.0]))))

(defn filter-pass [cfg]
  (let [dt (:dt cfg) q (:accel-variance cfg) ordered (sort-by (fn [[idx ob]] [(:t ob) (:sensor ob) idx]) (map-indexed vector (:observations cfg)))
        control-by-t (reduce (fn [m c] (update m (:t c) (fnil + 0.0) (:a c))) {} (:controls cfg))
        max-t (apply max 0 (concat (map (comp :t second) ordered) (keys control-by-t))) grouped (group-by (comp :t second) ordered)]
    (loop [t 0 x (get-in cfg [:initial :mean]) p (get-in cfg [:initial :cov]) states [] ll 0.0]
      (let [[xp pp] (if (zero? t) [x p] (predict x p dt q (get control-by-t t 0.0)))
            [xf pf inn llt rej] (reduce (fn [[cx cp ci cl cr] [_ ob]] (let [[nx np ni nl nr] (update-one cx cp ob (get (:sensors cfg) (:sensor ob)))] [nx np (+ ci ni) (+ cl nl) (+ cr nr)])) [xp pp 0.0 0.0 0.0] (get grouped t []))
            states2 (conj states {:t t :pred-x xp :pred-p pp :filt-x xf :filt-p pf :innovation_norm inn :rejected_observations rej})]
        (if (= t max-t) [states2 (+ ll llt)] (recur (inc t) xf pf states2 (+ ll llt)))))))

(defn smooth-states [states dt]
  (let [f [[1.0 dt] [0.0 1.0]]]
    (loop [k (dec (count states)) sx (:filt-x (last states)) sp (:filt-p (last states)) acc ()]
      (let [cur (assoc (states k) :smooth-x sx :smooth-p sp) acc2 (cons cur acc)]
        (if (zero? k)
          (vec acc2)
          (let [prev (states (dec k)) c (mat-mul (mat-mul (:filt-p prev) (mat-t f)) (inv (:pred-p cur)))
                dx (vec (map - sx (:pred-x cur))) adj (mat-vec c dx) nsx (vec (map + (:filt-x prev) adj))
                nsp (sym-mat (mat-add (:filt-p prev) (mat-mul (mat-mul c (mat-sub sp (:pred-p cur))) (mat-t c))))]
            (recur (dec k) nsx nsp acc2)))))))

(defn report [cfg]
  (let [[states ll] (filter-pass cfg) smoothed (smooth-states states (:dt cfg))]
    {:run_id (:run-id cfg) :status "ok" :log_likelihood ll
     :time_steps (mapv (fn [s] {:t (:t s) :filtered (:filt-x s) :smoothed (:smooth-x s) :cov_sqrt (chol2 (:smooth-p s)) :innovation_norm (:innovation_norm s) :rejected_observations (:rejected_observations s)}) smoothed)}))

(defn -main [& args]
  (if (not= 2 (count args))
    (invalid! (or (second args) "/tmp/sensor_smooth_invalid.json") "unknown" "arity")
    (let [[in out] args]
      (try (let [cfg (edn/read-string (slurp in)) rid (when (and (map? cfg) (string? (:run-id cfg))) (:run-id cfg))]
             (write-report! out (report (validate-config cfg))))
           (catch Exception e
             (let [rid (try (let [cfg (edn/read-string (slurp in))] (when (and (map? cfg) (string? (:run-id cfg))) (:run-id cfg))) (catch Exception _ "unknown"))]
               (invalid! out rid (or (.getMessage e) "invalid"))))))))
CLJ
