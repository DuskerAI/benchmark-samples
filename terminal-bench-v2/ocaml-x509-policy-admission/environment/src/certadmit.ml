type cert = {
  id : string;
  subject : string;
  issuer : string;
  serial : string;
  ca : bool;
  nb : int;
  na : int;
  ku : string list;
  eku : string list;
  dns : string list;
  ip : string list;
  permit : string list;
  exclude : string list;
  aki : string;
  path_len : int option;
  policies : string list;
  policy_maps : (string * string) list;
}

type crl = {
  issuer_name : string;
  number : int;
  base : int option;
  this_update : int;
  next_update : int;
  serials : string list;
}
type verdict = Accept | Reject of string

let split_on ch s =
  let rec loop i j acc =
    if j = String.length s then List.rev ((String.sub s i (j - i)) :: acc)
    else if s.[j] = ch then loop (j + 1) (j + 1) ((String.sub s i (j - i)) :: acc)
    else loop i (j + 1) acc
  in
  loop 0 0 []

let trim = String.trim
let lower = String.lowercase_ascii

let list_field s =
  let s = trim s in
  if s = "" || s = "-" then [] else List.map trim (split_on ',' s)

let has xs x = List.exists ((=) x) xs
let starts_with s prefix =
  String.length s >= String.length prefix && String.sub s 0 (String.length prefix) = prefix

type san_kind = DnsName | IpName

let optional_int s =
  if trim s = "-" then None else Some (int_of_string (trim s))

let parse_policy_maps s =
  list_field s
  |> List.map (fun entry ->
       match split_on '>' entry with
       | [child; issuer] when trim child <> "" && trim issuer <> "" ->
           let issuer_policy = match split_on '@' (trim issuer) with
             | policy :: _ -> policy
             | [] -> trim issuer
           in
           (trim child, trim issuer_policy)
       | _ -> failwith "invalid policy map")

let read_lines path =
  let ic = open_in path in
  let rec loop acc =
    match input_line ic with
    | line -> loop (line :: acc)
    | exception End_of_file -> close_in ic; List.rev acc
  in
  loop []

let parse_bool = function
  | "true" | "1" -> true
  | "false" | "0" -> false
  | _ -> failwith "invalid boolean"

let parse_cert line =
  match split_on '|' line with
  | ["cert"; id; subject; issuer; serial; ca; nb; na; ku; eku; dns; ip; permit; exclude; aki; path_len; policies; policy_maps] ->
      Some {
        id = trim id; subject = trim subject; issuer = trim issuer; serial = trim serial;
        ca = parse_bool (trim ca); nb = int_of_string (trim nb); na = int_of_string (trim na);
        ku = list_field ku; eku = list_field eku; dns = list_field dns; ip = list_field ip;
        permit = list_field permit; exclude = list_field exclude; aki = trim aki;
        path_len = optional_int path_len; policies = list_field policies;
        policy_maps = parse_policy_maps policy_maps;
      }
  | _ -> None

let parse_crl line =
  match split_on '|' line with
  | ["crl"; issuer_name; number; base; this_update; next_update; serials] ->
      Some {
        issuer_name = trim issuer_name; number = int_of_string (trim number);
        base = optional_int base; this_update = int_of_string (trim this_update);
        next_update = int_of_string (trim next_update); serials = list_field serials;
      }
  | _ -> None

let load_certs path =
  let parsed = read_lines path |> List.filter (fun l -> trim l <> "") |> List.map parse_cert in
  if List.exists ((=) None) parsed then None else Some (List.filter_map (fun x -> x) parsed)

let load_crls dir =
  try
    let files = Sys.readdir dir |> Array.to_list |> List.sort String.compare in
    let lines =
      List.concat_map (fun name ->
        let path = Filename.concat dir name in
        if Sys.is_directory path then [] else read_lines path |> List.filter (fun l -> trim l <> "")) files
    in
    let parsed = List.map parse_crl lines in
    if List.exists ((=) None) parsed then None else Some (List.filter_map (fun x -> x) parsed)
  with _ -> None

let suffix s suf =
  let s = lower s and suf = lower suf in
  let ls = String.length s and lf = String.length suf in
  ls >= lf && String.sub s (ls - lf) lf = suf

let labels s = split_on '.' (lower s) |> List.filter ((<>) "")

let dns_match pat host =
  let pat = lower pat and host = lower host in
  if pat = host then true
  else if String.length pat > 2 && String.sub pat 0 2 = "*." then
    let rest = String.sub pat 2 (String.length pat - 2) in
    match labels host with
    | _ :: tail -> String.concat "." tail = rest
    | [] -> false
  else false

let domain_contains base name =
  let name = lower name and base = lower base in
  name = base || suffix name ("." ^ base)

let host_is_ip host =
  String.for_all (fun c -> (c >= '0' && c <= '9') || c = '.') host && String.contains host '.'

let identity_ok leaf host =
  if host_is_ip host then List.exists ((=) host) leaf.ip
  else List.exists (fun d -> dns_match d host) leaf.dns

let has_requested_san leaf host =
  if host_is_ip host then leaf.ip <> [] else leaf.dns <> []

let normalize_constraint value =
  let value = trim value in
  let low = lower value in
  if String.length low >= 4 && String.sub low 0 4 = "dns:" then
    (DnsName, trim (String.sub value 4 (String.length value - 4)))
  else if String.length low >= 3 && String.sub low 0 3 = "ip:" then
    (IpName, trim (String.sub value 3 (String.length value - 3)))
  else if host_is_ip value then (IpName, value)
  else (DnsName, value)

let constraint_applies kind name c =
  let same_type value = fst (normalize_constraint value) = kind in
  let permits = List.filter same_type c.permit |> List.map (fun v -> snd (normalize_constraint v)) in
  let excludes = List.filter same_type c.exclude |> List.map (fun v -> snd (normalize_constraint v)) in
  let covers constraint_name =
    if kind = IpName then constraint_name = name else domain_contains constraint_name name
  in
  let excluded = List.exists covers excludes in
  let permitted = permits = [] || List.exists covers permits in
  (not excluded) && permitted

let constraints_ok leaf issuers =
  let names = match leaf.dns, leaf.ip with
    | d :: _, _ -> [(DnsName, d)]
    | [], i :: _ -> [(IpName, i)]
    | [], [] -> [] in
  List.for_all (fun issuer -> List.for_all (fun (kind, name) -> constraint_applies kind name issuer) names) issuers

let path_lengths_ok issuers =
  let rec loop ca_below = function
    | [] -> true
    | ca :: rest ->
        let within = match ca.path_len with None -> true | Some limit -> ca_below <= limit in
        within && loop (ca_below + 1) rest
  in
  loop 0 issuers

let policies_ok leaf issuers =
  let policy_tokens c = List.filter (fun p -> not (starts_with p "requireIssuer:")) c.policies in
  let step carried issuer =
    let issuer_policies = policy_tokens issuer in
    let accepts p = has issuer_policies p || has issuer_policies "anyPolicy" in
    carried
    |> List.concat_map (fun p ->
         let direct = if accepts p then [p] else [] in
         let mapped =
           issuer.policy_maps
           |> List.filter_map (fun (child, parent) ->
                if child = p && accepts parent then Some parent else None)
         in
         direct @ mapped)
  in
  let rec loop carried = function
    | [] -> carried <> []
    | issuer :: rest ->
        let carried' = step carried issuer in
        carried' <> [] && loop carried' rest
  in
  let leaf_policies = policy_tokens leaf in
  leaf_policies <> [] && loop leaf_policies issuers

let issuer_requirements_ok path =
  let rec loop = function
    | child :: issuer :: rest ->
        let reqs =
          child.policies
          |> List.filter (fun p -> starts_with p "requireIssuer:")
          |> List.map (fun p -> String.sub p 14 (String.length p - 14))
          |> List.filter ((<>) "")
        in
        (reqs = [] || has reqs issuer.id) && loop (issuer :: rest)
    | _ -> true
  in
  loop path

let code_rank = function
  | "MALFORMED" -> 0 | "NO_PATH" -> 1 | "TIME" -> 2 | "CA_POLICY" -> 3
  | "LEAF_POLICY" -> 4 | "IDENTITY" -> 5 | "NAME_CONSTRAINT" -> 6
  | "CRL_STALE" -> 7 | "REVOKED" -> 8 | _ -> 99

let best_code codes =
  match List.sort (fun a b -> compare (code_rank a) (code_rank b)) codes with
  | c :: _ -> c
  | [] -> "NO_PATH"

let find_arg name args =
  let rec loop = function
    | a :: b :: _ when a = name -> b
    | _ :: rest -> loop rest
    | [] -> raise Not_found
  in
  loop args

let paths leaf all trusted =
  let is_trusted c = List.exists ((=) c) trusted in
  let rec extend seen c =
    if List.mem c.id seen then []
    else if is_trusted c then [[c]]
    else
      all
      |> List.filter (fun p -> p.subject = c.issuer && c.aki <> "" && c.aki <> "-" && p.id = c.aki)
      |> List.concat_map (fun p -> List.map (fun rest -> c :: rest) (extend (c.id :: seen) p))
  in
  extend [] leaf

let fresh_crls now crls issuer =
  List.filter (fun r -> r.issuer_name = issuer && r.this_update <= now && now <= r.next_update) crls

let serial_revokes issuer_id child_serial entry =
  match split_on '@' entry with
  | [serial] -> serial = child_serial
  | [serial; id] -> serial <> "" && id <> "" && serial = child_serial && id = issuer_id
  | _ -> false

let serial_entry_malformed entry =
  match split_on '@' entry with
  | [_] -> false
  | [serial; id] -> serial = "" || id = ""
  | _ -> true

let check_crl now crls issuer child =
  let fresh = fresh_crls now crls child.issuer in
  let fulls = List.filter (fun r -> r.base = None) fresh in
  match fulls with
  | [] -> Reject "CRL_STALE"
  | first :: rest ->
      let _tied_full_count = List.length fulls in
      let base = List.fold_left (fun best r -> if r.number > best.number then r else best) first rest in
      let deltas = List.filter (fun r -> r.base = Some base.number && r.number > base.number && r.number = base.number + 1) fresh in
      let selected = base :: deltas in
      if List.exists (fun r -> List.exists serial_entry_malformed r.serials) selected then Reject "MALFORMED"
      else if List.exists (fun r -> List.exists (serial_revokes issuer.id child.serial) r.serials) selected
      then Reject "REVOKED" else Accept

let check_path now host crls path =
  let leaf = List.hd path in
  let issuers = List.tl path in
  if List.exists (fun c -> now < c.nb || now > c.na) path then Reject "TIME"
  else if List.exists (fun c -> not c.ca || not (has c.ku "keyCertSign")) issuers || not (path_lengths_ok issuers) then Reject "CA_POLICY"
  else if leaf.ca || not (has leaf.ku "digitalSignature") || not (has leaf.eku "serverAuth") || not (has_requested_san leaf host) || not (policies_ok leaf issuers) then Reject "LEAF_POLICY"
  else if not (identity_ok leaf host) then Reject "IDENTITY"
  else if not (constraints_ok leaf issuers) then Reject "NAME_CONSTRAINT"
  else
    let rec loop = function
      | child :: issuer :: rest -> (match check_crl now crls issuer child with Accept -> loop (issuer :: rest) | r -> r)
      | _ -> Accept
    in
    loop path

let unique_ids certs =
  let rec loop seen = function
    | [] -> true
    | c :: rest ->
        if has seen c.id then false else loop (c.id :: seen) rest
  in
  loop [] certs

let () =
  try
    let args = Array.to_list Sys.argv |> List.tl in
    let trust_path = find_arg "--trust" args in
    let crl_dir = find_arg "--crl" args in
    let now = int_of_string (find_arg "--now" args) in
    let host = find_arg "--host" args in
    let bundle_path = find_arg "--bundle" args in
    match load_certs trust_path, load_certs bundle_path, load_crls crl_dir with
    | Some trust, Some bundle, Some crls ->
        let all = bundle @ trust in
        if not (unique_ids all) then (print_endline "REJECT:MALFORMED"; exit 1);
        let candidates = List.concat_map (fun leaf -> paths leaf all trust) bundle in
        if candidates = [] then (print_endline "REJECT:NO_PATH"; exit 1)
        else
          let results = List.map (check_path now host crls) candidates in
          if List.exists ((=) Accept) results then (print_endline "ACCEPT"; exit 0)
          else
            let non_ca_results =
              List.map2 (fun path result -> path, result) candidates results
              |> List.filter_map (fun (path, result) -> if (List.hd path).ca then None else Some result)
            in
            let usable_results = if non_ca_results = [] then results else non_ca_results in
            let codes = List.filter_map (function Reject c -> Some c | Accept -> None) usable_results in
            print_endline ("REJECT:" ^ best_code codes); exit 1
    | _ -> print_endline "REJECT:MALFORMED"; exit 1
  with _ -> print_endline "REJECT:MALFORMED"; exit 1
