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

let req _name v = if !v = "" then raise Bad else !v

let read_json p = Yojson.Safe.from_file p

let assoc k = function `Assoc xs -> (try List.assoc k xs with Not_found -> raise Bad) | _ -> raise Bad
let str k j = match assoc k j with `String s -> s | _ -> raise Bad
let int k j = match assoc k j with `Int n -> n | `Intlit s -> int_of_string s | _ -> raise Bad
let list k j = match assoc k j with `List xs -> xs | _ -> raise Bad
let obj k j = match assoc k j with `Assoc _ as x -> x | _ -> raise Bad
let opt k j = match j with `Assoc xs -> (try Some (List.assoc k xs) with Not_found -> None) | _ -> raise Bad

let hex_char = function
  | '0'..'9' | 'a'..'f' | 'A'..'F' -> true
  | _ -> false

let is_hex n s = String.length s = n && String.for_all hex_char s

let lower s = String.lowercase_ascii s

let hex_val c =
  match c with
  | '0'..'9' -> Char.code c - Char.code '0'
  | 'a'..'f' -> 10 + Char.code c - Char.code 'a'
  | 'A'..'F' -> 10 + Char.code c - Char.code 'A'
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
  let _ = Unix.close_process_in ic in
  Sys.remove p;
  String.sub line 0 64

let canonical_json j =
  let rec go = function
    | `Assoc xs ->
        "{" ^ String.concat "," (List.map (fun (k,v) -> Yojson.Safe.to_string (`String k) ^ ":" ^ go v) xs) ^ "}"
    | `List xs -> "[" ^ String.concat "," (List.map go xs) ^ "]"
    | `String s -> Yojson.Safe.to_string (`String s)
    | `Bool b -> if b then "true" else "false"
    | `Null -> "null"
    | `Int n -> string_of_int n
    | `Intlit s -> s
    | `Float f -> Printf.sprintf "%.17g" f
    | _ -> raise Bad
  in go j

let payload_hash entry = sha256 (canonical_json entry)
let leaf h = sha256 ("\000" ^ bytes_of_hex h)
let node a b = sha256 ("\001" ^ bytes_of_hex a ^ bytes_of_hex b)

let root_from_path size index h proof =
  if size <= 0 || index < 0 || index >= size then raise Bad;
  let rec loop level_size idx acc rest =
    if level_size = 1 then if rest = [] then acc else raise Bad
    else match rest with
      | sib :: tl ->
          let acc' = if idx mod 2 = 0 then node acc sib else node sib acc in
          loop ((level_size + 1) / 2) (idx / 2) acc' tl
      | [] -> raise Bad
  in loop size index (leaf h) proof

type checkpoint = { size: int; root: string }
type saved = { cp: checkpoint option; entries: (string * (string * string)) list }

let empty_state = { cp=None; entries=[] }

let load_state p =
  if p = "" || not (Sys.file_exists p) then empty_state else
  let j = read_json p in
  let cp = match opt "checkpoint" j with
    | Some (`Assoc _ as c) -> Some { size=int "size" c; root=lower (str "root" c) }
    | _ -> None
  in
  let entries = match opt "entries" j with
    | Some (`Assoc xs) -> List.map (fun (u,v) -> (lower u, (lower (str "payload_hash" v), lower (str "log_id" v)))) xs
    | _ -> []
  in { cp; entries }

let json_cp c = `Assoc ["size", `Int c.size; "root", `String c.root]

let save_state p st =
  let entries = List.sort compare st.entries in
  let j = `Assoc [
    "checkpoint", (match st.cp with None -> `Null | Some c -> json_cp c);
    "entries", `Assoc (List.map (fun (u,(ph,lid)) -> (u, `Assoc ["log_id", `String lid; "payload_hash", `String ph])) entries)
  ] in
  Yojson.Safe.to_file p j

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
  let eq field = match string_opt field rule with None -> true | Some v -> (try str field id = v with _ -> false) in
  eq "issuer" || eq "subject" || eq "san" || eq "key"

let identity_ok pol_id now =
  let nbf = int "not_before" pol_id in
  let naf = int "not_after" pol_id in
  nbf <= now && now <= naf

let main () =
  let bpath = req "bundle" bundle in
  let ppath = req "policy" policy in
  let spath = req "state" state in
  let _ = req "out" out in
  let now = match !gate_time with Some n -> n | None -> raise Bad in
  let b = read_json bpath in
  let pol = read_json ppath in
  let uuid = lower (str "uuid" b) in
  let log_id = lower (str "log_id" b) in
  if not (is_hex 32 uuid && is_hex 64 log_id) then raise Bad;
  let policy_log = lower (str "log_id" pol) in
  if log_id <> policy_log then (report false "rejected" (Some uuid) None; exit 0);
  let integrated_time = int "integrated_time" b in
  if integrated_time > now then (report false "rejected" (Some uuid) None; exit 0);
  let checkpoint = obj "checkpoint" b in
  let cp = { size=int "size" checkpoint; root=lower (str "root" checkpoint) } in
  let min_size = int "min_tree_size" pol in
  let entry = obj "entry" b in
  let ph = lower (str "payload_hash" b) in
  if not (is_hex 64 ph) then raise Bad;
  if ph <> payload_hash entry then (report false "rejected" (Some uuid) (Some cp); exit 0);
  let inc = obj "inclusion" b in
  let idx = int "index" inc in
  let proof = List.map (function `String s -> lower s | _ -> raise Bad) (list "hashes" inc) in
  if List.exists (fun x -> not (is_hex 64 x)) proof then raise Bad;
  let root = root_from_path cp.size idx ph proof in
  if root <> cp.root || cp.size < min_size then (report false "rejected" (Some uuid) (Some cp); exit 0);
  let id = obj "identity" entry in
  let signing_time = int "signature_time" entry in
  let allow = list "allow" pol in
  let ok_rule = List.exists (fun r -> matches_rule id r) allow in
  if not ok_rule || not (identity_ok id now) || signing_time > integrated_time
  then (report false "rejected" (Some uuid) (Some cp); exit 0);
  let st = load_state spath in
  let st' = { cp=Some cp; entries=(uuid,(ph,log_id)) :: List.remove_assoc uuid st.entries } in
  save_state spath st';
  report true "admitted" (Some uuid) (Some cp)

let () =
  try main () with _ ->
    (try report false "malformed" None None with _ -> ());
    exit 2
