#!/bin/bash
set -euo pipefail

cat > /app/bin/rekor_gate.ml <<'ML'
exception Bad

let bundle = ref ""
let policy = ref ""
let state = ref ""
let out = ref ""
let gate_time = ref None

let args = [
  ("--bundle", Arg.Set_string bundle, "");
  ("--policy", Arg.Set_string policy, "");
  ("--state", Arg.Set_string state, "");
  ("--time", Arg.Int (fun n -> gate_time := Some n), "");
  ("--out", Arg.Set_string out, "")
]

let () = Arg.parse args (fun _ -> raise Bad) ""

let req v = if !v = "" then raise Bad else !v
let read_json p = Yojson.Safe.from_file p
let assoc k = function `Assoc xs -> (try List.assoc k xs with Not_found -> raise Bad) | _ -> raise Bad
let str k j = match assoc k j with `String s -> s | _ -> raise Bad
let int k j = match assoc k j with `Int n -> n | `Intlit s -> int_of_string s | _ -> raise Bad
let list k j = match assoc k j with `List xs -> xs | _ -> raise Bad
let obj k j = match assoc k j with `Assoc _ as x -> x | _ -> raise Bad
let opt k j = match j with `Assoc xs -> (try Some (List.assoc k xs) with Not_found -> None) | _ -> raise Bad

let hex_char = function '0'..'9' | 'a'..'f' -> true | _ -> false
let is_hex n s = String.length s = n && String.for_all hex_char s
let require_hex n s = if is_hex n s then s else raise Bad

let hex_val = function
  | '0'..'9' as c -> Char.code c - Char.code '0'
  | 'a'..'f' as c -> 10 + Char.code c - Char.code 'a'
  | _ -> raise Bad

let bytes_of_hex s =
  if String.length s mod 2 <> 0 then raise Bad;
  let b = Bytes.create (String.length s / 2) in
  for i = 0 to Bytes.length b - 1 do
    Bytes.set b i (Char.chr ((hex_val s.[2*i] lsl 4) lor hex_val s.[2*i+1]))
  done;
  Bytes.to_string b

let sha256 s =
  let p = Filename.temp_file "rg" ".bin" in
  let oc = open_out_bin p in
  output_string oc s;
  close_out oc;
  let ic = Unix.open_process_in ("sha256sum " ^ Filename.quote p) in
  let line = input_line ic in
  let rc = Unix.close_process_in ic in
  Sys.remove p;
  match rc with
  | Unix.WEXITED 0 -> String.sub line 0 64
  | _ -> raise Bad

let canonical_json j =
  let rec go = function
    | `Assoc xs ->
        let xs = List.sort (fun (a,_) (b,_) -> String.compare a b) xs in
        "{" ^ String.concat "," (List.map (fun (k,v) -> Yojson.Safe.to_string (`String k) ^ ":" ^ go v) xs) ^ "}"
    | `List xs -> "[" ^ String.concat "," (List.map go xs) ^ "]"
    | `String s -> Yojson.Safe.to_string (`String s)
    | `Bool b -> if b then "true" else "false"
    | `Null -> "null"
    | `Int n -> string_of_int n
    | `Intlit s -> s
    | `Float _ -> raise Bad
    | _ -> raise Bad
  in go j

let payload_hash entry = sha256 (canonical_json entry)
let leaf h = sha256 ("\000" ^ bytes_of_hex h)
let node a b = sha256 ("\001" ^ bytes_of_hex a ^ bytes_of_hex b)

let root_from_path size index h proof =
  if size <= 0 || index < 0 || index >= size then raise Bad;
  let rec loop level_size idx acc rest =
    if level_size = 1 then if rest = [] then acc else raise Bad
    else if idx mod 2 = 0 && idx + 1 >= level_size then
      loop ((level_size + 1) / 2) (idx / 2) acc rest
    else match rest with
      | sib :: tl ->
          let acc' = if idx mod 2 = 0 then node acc sib else node sib acc in
          loop ((level_size + 1) / 2) (idx / 2) acc' tl
      | [] -> raise Bad
  in loop size index (leaf h) proof

type checkpoint = { size: int; root: string }
type log_state = { cp: checkpoint option; entries: (string * string) list; witnesses: (string * checkpoint) list }
type saved_state = (string * log_state) list

let empty_log = { cp=None; entries=[]; witnesses=[] }
let json_cp c = `Assoc ["size", `Int c.size; "root", `String c.root]

let parse_cp = function
  | `Assoc _ as c -> { size=int "size" c; root=require_hex 64 (str "root" c) }
  | _ -> raise Bad

let load_state p : saved_state =
  if p = "" || not (Sys.file_exists p) then [] else
  let j = read_json p in
  match obj "logs" j with
  | `Assoc logs ->
      List.map (fun (lid, v) ->
        let lid = require_hex 64 lid in
        let cp = Some (parse_cp (assoc "checkpoint" v)) in
        let entries = match assoc "entries" v with
          | `Assoc xs -> List.map (fun (u, ev) ->
              (require_hex 32 u, require_hex 64 (str "payload_hash" ev))) xs
          | _ -> raise Bad
        in
        let witnesses = match opt "witnesses" v with
          | Some (`Assoc xs) -> List.map (fun (wid, c) -> (wid, parse_cp c)) xs
          | None -> []
          | _ -> raise Bad
        in
        (lid, { cp; entries; witnesses })) logs
  | _ -> raise Bad

let save_state p (st : saved_state) =
  let logs = List.sort compare st in
  let json_logs = List.map (fun (lid, ls) ->
    let entries = List.sort compare ls.entries in
    let cp_json = match ls.cp with Some c -> json_cp c | None -> raise Bad in
    let witness_json = List.sort compare ls.witnesses |> List.map (fun (wid, cp) -> (wid, json_cp cp)) in
    (lid, `Assoc [
      "checkpoint", cp_json;
      "entries", `Assoc (List.map (fun (u, ph) -> (u, `Assoc ["payload_hash", `String ph])) entries);
      "witnesses", `Assoc witness_json
    ])) logs
  in
  Yojson.Safe.to_file p (`Assoc ["logs", `Assoc json_logs])

let report admitted reason uuid cp =
  let j = `Assoc [
    "admitted", `Bool admitted;
    "reason", `String reason;
    "uuid", (match uuid with None -> `Null | Some u -> `String u);
    "checkpoint", (match cp with None -> `Null | Some c -> json_cp c)
  ] in
  let oc = open_out !out in
  output_string oc (Yojson.Safe.to_string j);
  output_char oc '\n';
  close_out oc

let string_opt k j = match opt k j with Some (`String s) -> Some s | Some `Null | None -> None | _ -> raise Bad

let matches_rule id rule =
  let fields = ["issuer"; "subject"; "san"; "key"] in
  let checked = ref false in
  let ok = List.for_all (fun field ->
    match string_opt field rule with
    | None -> true
    | Some v -> checked := true; (try str field id = v with _ -> false)) fields in
  ok && !checked

let identity_valid id t = int "not_before" id <= t && t <= int "not_after" id

let claim_pair = function
  | `Assoc _ as c -> (str "name" c, str "value" c)
  | _ -> raise Bad

let uniq xs = List.fold_left (fun acc x -> if List.mem x acc then acc else x :: acc) [] xs

let has_enough_claims policy entry =
  let required = uniq (List.map claim_pair (list "required_claims" policy)) in
  let min_claims = int "min_claims" policy in
  if min_claims < 0 then raise Bad;
  let artifact = obj "artifact" entry in
  let supplied = uniq (List.map claim_pair (list "claims" artifact)) in
  let count = List.fold_left (fun n pair -> if List.mem pair supplied then n + 1 else n) 0 required in
  count >= min_claims

let policy_logs policy =
  List.map (function
    | `Assoc _ as l -> (require_hex 64 (str "id" l), int "min_tree_size" l)
    | _ -> raise Bad) (list "logs" policy)

let list_opt k j = match opt k j with Some (`List xs) -> xs | None -> [] | _ -> raise Bad

let int_opt k j d = match opt k j with Some (`Int n) -> n | Some (`Intlit s) -> int_of_string s | None -> d | _ -> raise Bad

let witness_signature wid log_id cp =
  sha256 (wid ^ ":" ^ log_id ^ ":" ^ string_of_int cp.size ^ ":" ^ cp.root ^ ":")

let valid_witnesses pol b log_id cp integrated_time now =
  let trusted = List.map (function `String s -> s | _ -> raise Bad) (list_opt "trusted_witnesses" pol) in
  List.fold_left (fun acc w ->
    match w with
    | `Assoc _ as wj ->
        let wid = str "id" wj in
        let signed_at = int "signed_at" wj in
        let sigv = require_hex 64 (str "signature" wj) in
        if List.mem wid trusted && signed_at >= integrated_time && signed_at <= now && sigv = witness_signature wid log_id cp && not (List.mem_assoc wid acc)
        then (wid, cp) :: acc else acc
    | _ -> raise Bad) [] (list_opt "witnesses" b)

let revoked_payload pol ph =
  List.exists (function `String s -> require_hex 64 s = ph | _ -> raise Bad) (list_opt "revoked_payloads" pol)

let prior_list b =
  List.map (function
    | `Assoc _ as c -> { size=int "size" c; root=require_hex 64 (str "root" c) }
    | _ -> raise Bad) (list "consistency" b)

let checkpoint_ok ls cp priors =
  match ls.cp with
  | None -> true
  | Some old ->
      if cp.size < old.size then false
      else if cp.size = old.size then cp.root = old.root
      else List.exists (fun c -> c.size = old.size && c.root = old.root) priors

let replace_log lid ls st =
  let rec go acc = function
    | [] -> List.rev ((lid, ls) :: acc)
    | (k, _) :: tl when k = lid -> List.rev_append acc ((lid, ls) :: tl)
    | x :: tl -> go (x :: acc) tl
  in go [] st

let main () =
  let bpath = req bundle in
  let ppath = req policy in
  let spath = req state in
  let _ = req out in
  let now = match !gate_time with Some n -> n | None -> raise Bad in
  let b = read_json bpath in
  let pol = read_json ppath in
  let uuid = require_hex 32 (str "uuid" b) in
  let log_id = require_hex 64 (str "log_id" b) in
  let trusted_logs = policy_logs pol in
  let min_size = match List.assoc_opt log_id trusted_logs with Some n -> n | None -> -1 in
  if min_size < 0 then (report false "rejected" (Some uuid) None; exit 0);
  let integrated_time = int "integrated_time" b in
  if integrated_time > now then (report false "rejected" (Some uuid) None; exit 0);
  let checkpoint = obj "checkpoint" b in
  let cp = { size=int "size" checkpoint; root=require_hex 64 (str "root" checkpoint) } in
  let entry = obj "entry" b in
  let ph = require_hex 64 (str "payload_hash" b) in
  if ph <> payload_hash entry || revoked_payload pol ph then (report false "rejected" (Some uuid) (Some cp); exit 0);
  let inc = obj "inclusion" b in
  let idx = int "index" inc in
  let proof = List.map (function `String s -> require_hex 64 s | _ -> raise Bad) (list "hashes" inc) in
  let root = root_from_path cp.size idx ph proof in
  if root <> cp.root || cp.size < min_size then (report false "rejected" (Some uuid) (Some cp); exit 0);
  let id = obj "identity" entry in
  let signing_time = int "signature_time" entry in
  let allow = list "allow" pol in
  let ok_rule = List.exists (fun r -> matches_rule id r) allow in
  let quorum = int_opt "witness_quorum" pol 0 in
  if quorum < 0 then raise Bad;
  let witnesses = valid_witnesses pol b log_id cp integrated_time now in
  if not ok_rule || not (has_enough_claims pol entry) || List.length witnesses < quorum ||
     not (identity_valid id signing_time) || not (identity_valid id integrated_time) ||
     signing_time > integrated_time
  then (report false "rejected" (Some uuid) (Some cp); exit 0);
  let st = load_state spath in
  let ls = match List.assoc_opt log_id st with Some x -> x | None -> empty_log in
  let priors = prior_list b in
  let dup_ok = match List.assoc_opt uuid ls.entries with
    | None -> true
    | Some old_ph -> old_ph = ph
  in
  if (not dup_ok) || not (checkpoint_ok ls cp priors) then (report false "rejected" (Some uuid) (Some cp); exit 0);
  let entries = if List.mem_assoc uuid ls.entries then ls.entries else (uuid, ph) :: ls.entries in
  let new_log = { cp=Some cp; entries; witnesses } in
  save_state spath (replace_log log_id new_log st);
  report true "admitted" (Some uuid) (Some cp)

let () =
  try main () with _ ->
    (try report false "malformed" None None with _ -> ());
    exit 2
ML

make -C /app
