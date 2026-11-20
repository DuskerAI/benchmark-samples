#!/bin/bash
set -euo pipefail
cat > /app/src/main.ml <<'OCAML'
type rr = { owner : string list; typ : string; rdata : string list }
type nsec3 = { owner_hash : string; next_hash : string; flags : int; alg : int; iterations : int; salt : string; types : string list }

let is_digit c = c >= '0' && c <= '9'
let is_ttl s = s <> "" && String.for_all is_digit s
let drop n xs = let rec go i ys = if i <= 0 then ys else match ys with [] -> [] | _ :: zs -> go (i - 1) zs in go n xs
let take n xs = let rec go i acc ys = if i <= 0 then List.rev acc else match ys with [] -> List.rev acc | z :: zs -> go (i - 1) (z :: acc) zs in go n [] xs
let present labels = if labels = [] then "." else String.concat "." labels ^ "."
let key = present

let decode_name text origin =
  let origin_labels = lazy (origin) in
  let absolute = String.length text > 0 && text.[String.length text - 1] = '.' in
  let body = if absolute then String.sub text 0 (String.length text - 1) else text in
  if text = "@" then Lazy.force origin_labels else
  let labels = ref [] in
  let buf = Buffer.create 32 in
  let push () = labels := Buffer.contents buf :: !labels; Buffer.clear buf in
  let rec loop i =
    if i >= String.length body then ()
    else match body.[i] with
    | '.' -> push (); loop (i + 1)
    | '\\' when i + 3 < String.length body && is_digit body.[i+1] && is_digit body.[i+2] && is_digit body.[i+3] ->
        let v = (Char.code body.[i+1] - 48) * 100 + (Char.code body.[i+2] - 48) * 10 + (Char.code body.[i+3] - 48) in
        Buffer.add_char buf (Char.lowercase_ascii (Char.chr v)); loop (i + 4)
    | '\\' when i + 1 < String.length body -> Buffer.add_char buf (Char.lowercase_ascii body.[i+1]); loop (i + 2)
    | c -> Buffer.add_char buf (Char.lowercase_ascii c); loop (i + 1)
  in
  loop 0;
  if body <> "" || absolute then push ();
  let mine = List.filter ((<>) "") (List.rev !labels) in
  if absolute then mine else mine @ Lazy.force origin_labels

let parse_tokens line =
  let line = try String.sub line 0 (String.index line ';') with Not_found -> line in
  List.filter ((<>) "") (String.split_on_char ' ' (String.trim line))

let discover_origin lines =
  let rec find = function
    | [] -> ["example"]
    | line :: rest ->
        match parse_tokens line with
        | owner :: xs ->
            let rec skip = function
              | x :: ys when is_ttl x || String.uppercase_ascii x = "IN" -> skip ys
              | x :: _ when String.uppercase_ascii x = "SOA" -> decode_name owner ["example"]
              | _ :: ys -> skip ys
              | [] -> find rest
            in skip xs
        | [] -> find rest
  in find lines

let parse_rr origin line =
  match parse_tokens line with
  | [] -> None
  | owner :: rest ->
      let rec skip = function
        | x :: xs when is_ttl x || String.uppercase_ascii x = "IN" -> skip xs
        | x :: xs -> Some (String.uppercase_ascii x, xs)
        | [] -> None
      in
      match skip rest with
      | None -> None
      | Some (typ, rdata) -> Some { owner = decode_name owner origin; typ; rdata }

let read_zone path =
  let ic = open_in path in
  let lines = ref [] in
  (try while true do lines := input_line ic :: !lines done with End_of_file -> close_in ic);
  let lines = List.rev !lines in
  let origin = discover_origin lines in
  (origin, List.filter_map (parse_rr origin) lines)

let hex_value c =
  if c >= '0' && c <= '9' then Char.code c - 48
  else if c >= 'a' && c <= 'f' then Char.code c - 87
  else if c >= 'A' && c <= 'F' then Char.code c - 55
  else invalid_arg "hex"

let bytes_of_hex s =
  if s = "-" then "" else
  let n = String.length s / 2 in
  let out = Bytes.create n in
  for i = 0 to n - 1 do Bytes.set out i (Char.chr ((hex_value s.[2*i] lsl 4) lor hex_value s.[2*i+1])) done;
  Bytes.unsafe_to_string out

let hex_to_bytes h =
  let out = Bytes.create (String.length h / 2) in
  for i = 0 to Bytes.length out - 1 do Bytes.set out i (Char.chr ((hex_value h.[2*i] lsl 4) lor hex_value h.[2*i+1])) done;
  Bytes.unsafe_to_string out

let sha1 data =
  let file = Filename.temp_file "nsec3" ".bin" in
  let oc = open_out_bin file in
  output_string oc data; close_out oc;
  let ic = Unix.open_process_in ("sha1sum " ^ Filename.quote file) in
  let line = input_line ic in
  ignore (Unix.close_process_in ic); Sys.remove file;
  hex_to_bytes (String.sub line 0 40)

let wire labels =
  let b = Buffer.create 128 in
  List.iter (fun label -> Buffer.add_char b (Char.chr (String.length label)); Buffer.add_string b label) labels;
  Buffer.add_char b '\000'; Buffer.contents b

let base32hex bytes =
  let alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUV" in
  let b = Buffer.create 32 in
  let value = ref 0 and bits = ref 0 in
  String.iter (fun ch ->
    value := (!value lsl 8) lor Char.code ch; bits := !bits + 8;
    while !bits >= 5 do
      let idx = (!value lsr (!bits - 5)) land 31 in
      Buffer.add_char b alphabet.[idx]; bits := !bits - 5;
      value := if !bits = 0 then 0 else !value land ((1 lsl !bits) - 1)
    done) bytes;
  if !bits > 0 then Buffer.add_char b alphabet.[(!value lsl (5 - !bits)) land 31];
  Buffer.contents b

let nsec3_hash labels alg iterations salt =
  if alg <> 1 then invalid_arg "algorithm";
  let salt_b = bytes_of_hex salt in
  let h = ref (sha1 (wire labels ^ salt_b)) in
  for _ = 1 to iterations do h := sha1 (!h ^ salt_b) done;
  base32hex !h

let parse_nsec3 rr = match rr.typ, rr.owner, rr.rdata with
  | "NSEC3", h :: _, alg :: flags :: iters :: salt :: next :: types ->
      Some { owner_hash = String.uppercase_ascii h; next_hash = String.uppercase_ascii next; flags = int_of_string flags; alg = int_of_string alg; iterations = int_of_string iters; salt = String.uppercase_ascii salt; types = List.map String.uppercase_ascii types }
  | _ -> None

let params rrs n3s =
  match List.find_opt (fun r -> r.typ = "NSEC3PARAM") rrs with
  | Some r -> (match r.rdata with alg :: _flags :: iters :: salt :: _ -> (int_of_string alg, int_of_string iters, String.uppercase_ascii salt) | _ -> raise Not_found)
  | None -> match n3s with r :: _ -> (r.alg, r.iterations, r.salt) | [] -> raise Not_found

let covers r h =
  let o = r.owner_hash and n = r.next_hash in
  if o = n then h <> o else if o < n then h > o && h <= n else h > o || h <= n

let uniq_sorted xs =
  let sorted = List.sort String.compare xs in
  let rec go prev acc = function
    | [] -> List.rev acc
    | x :: ys -> if Some x = prev then go prev acc ys else go (Some x) (x :: acc) ys
  in go None [] sorted

let has_type rrs name typ = List.exists (fun r -> key r.owner = key name && r.typ = typ) rrs
let name_exists rrs name = List.exists (fun r -> r.typ <> "NSEC3" && key r.owner = key name) rrs

let closest rrs origin q =
  let qlen = List.length q in
  let rec loop i =
    if i > qlen then origin else
    let cand = drop i q in
    if name_exists rrs cand then cand else loop (i + 1)
  in loop 0

let under child parent =
  let lc = List.length child and lp = List.length parent in
  lc > lp && drop (lc - lp) child = parent

let delegation rrs origin q =
  let qlen = List.length q in
  let rec loop i best =
    if i >= qlen then best else
    let cand = drop i q in
    if cand <> origin && under q cand && has_type rrs cand "NS" && not (has_type rrs cand "DS") then Some cand else loop (i + 1) best
  in loop 0 None

let json qname qtype status owner proof =
  let quote s = "\"" ^ String.escaped s ^ "\"" in
  let owner_s = match owner with None -> "null" | Some s -> quote s in
  let proof_s = String.concat "," (List.map quote (uniq_sorted proof)) in
  Printf.printf "{\"qname\":%s,\"qtype\":%s,\"status\":%s,\"owner\":%s,\"proof\":[%s]}\n" (quote qname) (quote qtype) (quote status) owner_s proof_s

let () =
  let zone = ref "" and query = ref "" and qtype = ref "" in
  let rec args = function
    | "--zone" :: v :: xs -> zone := v; args xs
    | "--query" :: v :: xs -> query := v; args xs
    | "--type" :: v :: xs -> qtype := String.uppercase_ascii v; args xs
    | _ :: xs -> args xs
    | [] -> ()
  in
  args (List.tl (Array.to_list Sys.argv));
  let origin, rrs = read_zone !zone in
  let q = decode_name !query origin in
  let qname = present q in
  try
    if has_type rrs q !qtype then json qname !qtype "present" (Some qname) [] else
    match delegation rrs origin q with
    | Some d -> json qname !qtype "insecure_delegation" (Some (present d)) []
    | None ->
        let ce = closest rrs origin q in
        let wildcard = "*" :: ce in
        if not (name_exists rrs q) && has_type rrs wildcard !qtype then json qname !qtype "wildcard" (Some (present wildcard)) [] else
        let n3s_all = List.filter_map parse_nsec3 rrs in
        let alg, iterations, salt = params rrs n3s_all in
        let n3s = List.filter (fun r -> r.alg = alg && r.iterations = iterations && r.salt = salt) n3s_all in
        let hash labels = nsec3_hash labels alg iterations salt in
        let exact_nsec labels = let h = hash labels in List.find_opt (fun r -> r.owner_hash = h) n3s in
        let cover labels = let h = hash labels in List.find_opt (fun r -> covers r h) n3s in
        if name_exists rrs q then
          match exact_nsec q with
          | Some r when not (List.mem !qtype r.types) -> json qname !qtype "nodata" (Some qname) [r.owner_hash]
          | _ -> json qname !qtype "bogus" None []
        else
          let nc_len = List.length ce + 1 in
          let nc = drop (List.length q - nc_len) q in
          match cover nc with
          | Some r when r.flags land 1 = 1 -> json qname !qtype "insecure_delegation" None [r.owner_hash]
          | Some r1 ->
              (match cover wildcard with
               | Some r2 -> json qname !qtype "nxdomain" None [r1.owner_hash; r2.owner_hash]
               | None -> json qname !qtype "bogus" None [])
          | None -> json qname !qtype "bogus" None []
  with _ -> json qname !qtype "bogus" None []
OCAML
make -C /app clean all
