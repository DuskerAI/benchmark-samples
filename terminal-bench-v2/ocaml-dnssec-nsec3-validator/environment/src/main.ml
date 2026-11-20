type rr = { owner : string list; typ : string; rdata : string list }
type nsec3 = { owner_hash : string; next_hash : string; flags : int; alg : int; iterations : int; salt : string; types : string list }

let is_digit c = c >= '0' && c <= '9'
let code3 s i = (Char.code s.[i] - 48) * 100 + (Char.code s.[i+1] - 48) * 10 + (Char.code s.[i+2] - 48)

let split_name_simple s origin =
  let base = if s = "@" then "" else String.lowercase_ascii s in
  let full = if base = "" then origin else if String.length base > 0 && base.[String.length base - 1] = '.' then base else base ^ "." ^ origin in
  let trimmed = if String.length full > 0 && full.[String.length full - 1] = '.' then String.sub full 0 (String.length full - 1) else full in
  List.filter (fun x -> x <> "") (String.split_on_char '.' trimmed)

let present labels = if labels = [] then "." else String.concat "." labels ^ "."
let key labels = present labels

let parse_line origin line =
  let line = try String.sub line 0 (String.index line ';') with Not_found -> line in
  let parts = List.filter ((<>) "") (String.split_on_char ' ' (String.trim line)) in
  match parts with
  | [] -> None
  | owner :: rest ->
      let rec skip xs = match xs with
        | x :: ys when String.uppercase_ascii x = "IN" -> skip ys
        | x :: ys when x <> "" && List.for_all is_digit (List.init (String.length x) (String.get x)) -> skip ys
        | x :: ys -> Some (String.uppercase_ascii x, ys)
        | [] -> None
      in
      match skip rest with
      | None -> None
      | Some (typ, rdata) -> Some { owner = split_name_simple owner origin; typ; rdata }

let read_zone path =
  let lines = ref [] in
  let ic = open_in path in
  (try while true do lines := input_line ic :: !lines done with End_of_file -> close_in ic);
  let raw = List.rev !lines in
  let origin =
    let rec find = function
      | [] -> "example."
      | l :: ls ->
          let parts = List.filter ((<>) "") (String.split_on_char ' ' (String.trim l)) in
          match List.map String.uppercase_ascii parts with
          | owner :: _ttl :: _cls :: "SOA" :: _ -> if owner = "@" then "example." else String.lowercase_ascii owner
          | _ -> find ls
    in find raw
  in
  List.filter_map (parse_line origin) raw

let find_nsec3 rrs =
  List.filter_map (fun rr ->
    match rr.typ, rr.owner, rr.rdata with
    | "NSEC3", h :: _, alg :: flags :: it :: salt :: next :: types ->
        Some { owner_hash = String.uppercase_ascii h; next_hash = String.uppercase_ascii next; flags = int_of_string flags; alg = int_of_string alg; iterations = int_of_string it; salt = String.uppercase_ascii salt; types = List.map String.uppercase_ascii types }
    | _ -> None) rrs

let bad_hash labels _alg _iters salt =
  let material = present labels ^ ":" ^ salt in
  String.uppercase_ascii (Digest.to_hex (Digest.string material))

let covers recd h =
  let o = recd.owner_hash and n = recd.next_hash in
  if o < n then h > o && h <= n else h > o || h <= n

let uniq_sorted xs =
  let xs = List.sort String.compare xs in
  let rec go acc = function [] -> List.rev acc | x :: ys -> if List.mem x acc then go acc ys else go (x :: acc) ys in
  go [] xs

let json qname qtype status owner proof =
  let quote s = "\"" ^ String.escaped s ^ "\"" in
  let owner_s = match owner with None -> "null" | Some s -> quote s in
  let proof_s = String.concat "," (List.map quote proof) in
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
  let rrs = read_zone !zone in
  let origin = match List.find_opt (fun r -> r.typ = "SOA") rrs with Some r -> present r.owner | None -> "example." in
  let qlabels = split_name_simple !query origin in
  let qname = present qlabels in
  let exists = List.filter (fun r -> key r.owner = qname) rrs in
  if List.exists (fun r -> r.typ = !qtype) exists then json qname !qtype "present" (Some qname) []
  else
    let n3s = find_nsec3 rrs in
    let h = bad_hash qlabels 1 0 "" in
    match List.find_opt (fun r -> r.owner_hash = h || covers r h) n3s with
    | None -> json qname !qtype "bogus" None []
    | Some r ->
        if exists <> [] then json qname !qtype "nodata" (Some qname) [r.owner_hash]
        else if r.flags land 1 = 1 then json qname !qtype "insecure_delegation" None [r.owner_hash]
        else json qname !qtype "nxdomain" None (uniq_sorted [r.owner_hash])
