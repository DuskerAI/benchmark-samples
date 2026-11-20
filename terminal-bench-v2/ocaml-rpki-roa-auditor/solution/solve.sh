#!/bin/bash
set -euo pipefail

cat > /app/src/rpki_audit.ml <<'OCAML'
type prefix = { net : int64; len : int }
type trust = { t_subject : string; t_ski : string; t_nb : int; t_na : int; t_res : prefix list }
type cert = { c_path:string; c_subject:string; c_issuer:string; c_serial:string; c_ski:string; c_aki:string; c_nb:int; c_na:int; c_ca:bool; c_res:prefix list; c_pathlen:int option; c_hash:string }
type mft = { m_path:string; m_issuer:string; m_aki:string; m_this:int; m_next:int; m_entries:(string * string) list; m_hash:string }
type crl = { r_path:string; r_issuer:string; r_aki:string; r_this:int; r_next:int; r_serials:string list; r_hash:string }
type roa = { o_path:string; o_id:string; o_issuer:string; o_serial:string; o_aki:string; o_nb:int; o_na:int; o_asn:int; o_max:int; o_pfx:prefix list; o_hash:string }

let split c s = String.split_on_char c s
let trim s = String.trim s
let is_empty s = trim s = ""
let list_field f s = if s = "-" || s = "" then [] else List.map f (split ',' s)
let int_of_field s = int_of_string (trim s)

let read_file path =
  let ic = open_in_bin path in
  let n = in_channel_length ic in
  let s = really_input_string ic n in
  close_in ic; s

let first_line path =
  let ic = open_in path in
  let rec loop () =
    match input_line ic with
    | line when is_empty line -> loop ()
    | line -> close_in ic; line
    | exception End_of_file -> close_in ic; "" in
  loop ()

let sha256 path =
  let q = Filename.quote path in
  let ic = Unix.open_process_in ("sha256sum " ^ q) in
  let line = input_line ic in
  ignore (Unix.close_process_in ic);
  match split ' ' line with h :: _ -> h | [] -> ""

let rec files_under root =
  let rec walk dir prefix =
    Sys.readdir dir |> Array.to_list |> List.sort String.compare |> List.concat_map (fun name ->
      let full = Filename.concat dir name in
      let rel = if prefix = "" then name else prefix ^ "/" ^ name in
      if Sys.is_directory full then walk full rel else [rel, full]) in
  walk root ""

let ip4 s =
  match List.map int_of_field (split '.' s) with
  | [a;b;c;d] when List.for_all (fun x -> 0 <= x && x <= 255) [a;b;c;d] ->
      Int64.(logor (shift_left (of_int a) 24) (logor (shift_left (of_int b) 16) (logor (shift_left (of_int c) 8) (of_int d))))
  | _ -> failwith "ip"

let mask len = if len = 0 then 0L else Int64.(logand 0xffffffffL (shift_left 0xffffffffL (32 - len)))

let parse_prefix s =
  match split '/' (trim s) with
  | [addr; l] ->
      let len = int_of_field l in
      if len < 0 || len > 32 then failwith "prefix";
      { net = Int64.logand (ip4 addr) (mask len); len }
  | _ -> failwith "prefix"

let contains a b = a.len <= b.len && Int64.logand b.net (mask a.len) = a.net
let within_any p xs = List.exists (fun r -> contains r p) xs
let resources_subset child parent = List.for_all (fun p -> within_any p parent) child
let current nb na now = nb <= now && now <= na
let fresh this next now = this <= now && now < next

let parse_entries s = list_field (fun e -> match split '=' e with [p;h] -> (p,h) | _ -> failwith "entry") s
let parse_pathlen s = let n = int_of_field s in if n < 0 then failwith "pathLen" else Some n

let parse_record rel full =
  let h = sha256 full in
  let line = first_line full in
  if is_empty line then `Empty rel else
  let fs = split '|' line in
  try
    match fs with
    | ["cert"; subject; issuer; serial; ski; aki; nb; na; ca; res] ->
        `Cert { c_path=rel; c_subject=subject; c_issuer=issuer; c_serial=serial; c_ski=ski; c_aki=aki; c_nb=int_of_field nb; c_na=int_of_field na; c_ca=(ca="1"); c_res=list_field parse_prefix res; c_pathlen=None; c_hash=h }
    | ["cert"; subject; issuer; serial; ski; aki; nb; na; ca; res; pathlen] ->
        `Cert { c_path=rel; c_subject=subject; c_issuer=issuer; c_serial=serial; c_ski=ski; c_aki=aki; c_nb=int_of_field nb; c_na=int_of_field na; c_ca=(ca="1"); c_res=list_field parse_prefix res; c_pathlen=parse_pathlen pathlen; c_hash=h }
    | ["mft"; issuer; aki; thisu; nextu; entries] ->
        `Mft { m_path=rel; m_issuer=issuer; m_aki=aki; m_this=int_of_field thisu; m_next=int_of_field nextu; m_entries=parse_entries entries; m_hash=h }
    | ["crl"; issuer; aki; thisu; nextu; serials] ->
        `Crl { r_path=rel; r_issuer=issuer; r_aki=aki; r_this=int_of_field thisu; r_next=int_of_field nextu; r_serials=list_field trim serials; r_hash=h }
    | ["roa"; id; issuer; serial; aki; nb; na; asn; maxl; prefixes] ->
        `Roa { o_path=rel; o_id=id; o_issuer=issuer; o_serial=serial; o_aki=aki; o_nb=int_of_field nb; o_na=int_of_field na; o_asn=int_of_field asn; o_max=int_of_field maxl; o_pfx=list_field parse_prefix prefixes; o_hash=h }
    | _ -> `Bad rel
  with _ -> `Bad rel

let parse_trust path =
  match split '|' (first_line path) with
  | ["trust"; subject; ski; nb; na; res] -> { t_subject=subject; t_ski=ski; t_nb=int_of_field nb; t_na=int_of_field na; t_res=list_field parse_prefix res }
  | _ -> failwith "bad trust"

let json_escape s =
  let b = Buffer.create (String.length s + 8) in
  String.iter (function '"' -> Buffer.add_string b "\\\"" | '\\' -> Buffer.add_string b "\\\\" | c -> Buffer.add_char b c) s;
  Buffer.contents b
let jq s = "\"" ^ json_escape s ^ "\""

let assoc_opt k xs = try Some (List.assoc k xs) with Not_found -> None
let unique_sorted xs = List.sort_uniq String.compare xs

type ca = { subj:string; ski:string; res:prefix list; depth:int option }
let same_ca issuer aki ca = issuer = ca.subj && aki = ca.ski

let selected_mft mfts issuer aki now =
  mfts
  |> List.filter (fun m -> m.m_issuer=issuer && m.m_aki=aki && fresh m.m_this m.m_next now)
  |> List.sort (fun a b -> let c = compare b.m_this a.m_this in if c <> 0 then c else String.compare a.m_path b.m_path)
  |> function [] -> None | m :: _ -> Some m

let has_listed_mft mfts issuer aki path hash now =
  match selected_mft mfts issuer aki now with
  | None -> `No
  | Some m ->
      match assoc_opt path m.m_entries with
      | Some h when h = hash -> `Yes
      | Some _ -> `Hash
      | None -> `No

let selected_crl crls mfts ca now =
  crls
  |> List.filter (fun r -> same_ca r.r_issuer r.r_aki ca && fresh r.r_this r.r_next now && has_listed_mft mfts ca.subj ca.ski r.r_path r.r_hash now = `Yes)
  |> List.sort (fun a b -> let c = compare b.r_this a.r_this in if c <> 0 then c else String.compare a.r_path b.r_path)
  |> function [] -> None | r :: _ -> Some r

let revoked crls mfts ca serial now = match selected_crl crls mfts ca now with Some r -> List.mem serial r.r_serials | None -> false
let has_crl crls mfts ca now = selected_crl crls mfts ca now <> None

let artifact_state mfts crls ca path hash serial now =
  match has_listed_mft mfts ca.subj ca.ski path hash now with
  | `Hash -> `Bad "hash-mismatch"
  | `No -> `Bad "not-on-manifest"
  | `Yes ->
      if not (has_crl crls mfts ca now) then `Bad "stale"
      else if revoked crls mfts ca serial now then `Bad "revoked"
      else `Ok

let may_delegate ca = match ca.depth with None -> true | Some n -> n > 0
let child_depth parent child =
  let inherited = match parent.depth with None -> None | Some n -> Some (n - 1) in
  match inherited, child.c_pathlen with
  | None, None -> None
  | Some n, None | None, Some n -> Some n
  | Some a, Some b -> Some (min a b)

let build_cas trust certs mfts crls now =
  let initial = if current trust.t_nb trust.t_na now then [{subj=trust.t_subject; ski=trust.t_ski; res=trust.t_res; depth=None}] else [] in
  let rec loop cas =
    let next = List.fold_left (fun acc c ->
      if not c.c_ca || not (current c.c_nb c.c_na now) then acc
      else if List.exists (fun ca -> ca.subj=c.c_subject && ca.ski=c.c_ski) acc then acc
      else match List.find_opt (same_ca c.c_issuer c.c_aki) acc with
        | None -> acc
        | Some parent ->
            if may_delegate parent && artifact_state mfts crls parent c.c_path c.c_hash c.c_serial now = `Ok && resources_subset c.c_res parent.res
            then {subj=c.c_subject; ski=c.c_ski; res=c.c_res; depth=child_depth parent c} :: acc else acc) cas certs in
    if List.length next = List.length cas then cas else loop next in
  loop initial

let roa_reason mfts crls cas now o =
  if not (current o.o_nb o.o_na now) then Some "stale"
  else match List.find_opt (same_ca o.o_issuer o.o_aki) cas with
  | None -> Some "untrusted"
  | Some ca ->
      match artifact_state mfts crls ca o.o_path o.o_hash o.o_serial now with
      | `Bad r -> Some r
      | `Ok -> if List.for_all (fun p -> within_any p ca.res) o.o_pfx then None else Some "overclaim"

let route_of_line line =
  match split '|' line with
  | ["route"; id; asn; p] -> Some (id, int_of_field asn, parse_prefix p, p)
  | _ -> None

let report routes valid_roas rejected accepted now out =
  let route_json line =
    match route_of_line line with
    | None -> "{\"id\":\"\",\"asn\":0,\"prefix\":\"\",\"decision\":\"invalid\",\"matched\":[]}"
    | Some (id, asn, p, ps) ->
        let covers o = List.exists (fun rp -> contains rp p && p.len <= o.o_max) o.o_pfx in
        let all_cover = List.filter covers valid_roas in
        let as0 = List.filter (fun o -> o.o_asn = 0) all_cover in
        let same = List.filter (fun o -> o.o_asn = asn && o.o_asn <> 0) all_cover in
        let other = List.filter (fun o -> o.o_asn <> asn && o.o_asn <> 0) all_cover in
        let decision, matched =
          if as0 <> [] then ("rejected", List.map (fun o -> o.o_id) as0)
          else if same <> [] then ("accepted", List.map (fun o -> o.o_id) same)
          else if other <> [] then ("rejected", List.map (fun o -> o.o_id) other)
          else ("not-found", []) in
        Printf.sprintf "{\"id\":%s,\"asn\":%d,\"prefix\":%s,\"decision\":%s,\"matched\":[%s]}" (jq id) asn (jq ps) (jq decision) (String.concat "," (List.map jq (unique_sorted matched))) in
  let acc_json = accepted |> unique_sorted |> List.map jq |> String.concat "," in
  let rej_json = rejected |> List.sort compare |> List.map (fun (p,r) -> Printf.sprintf "{\"path\":%s,\"reason\":%s}" (jq p) (jq r)) |> String.concat "," in
  let body = Printf.sprintf "{\"generatedAt\":%d,\"routes\":[%s],\"evidence\":{\"accepted\":[%s],\"rejected\":[%s]}}\n" now (String.concat "," (List.map route_json routes)) acc_json rej_json in
  let oc = open_out out in output_string oc body; close_out oc

let () =
  let trust = ref "" and evidence = ref "" and routes = ref "" and now = ref 0 and out = ref "" in
  Arg.parse ["--trust", Arg.String ((:=) trust), ""; "--evidence", Arg.String ((:=) evidence), ""; "--routes", Arg.String ((:=) routes), ""; "--now", Arg.Int ((:=) now), ""; "--out", Arg.String ((:=) out), ""] (fun _ -> ()) "";
  if !trust="" || !evidence="" || !routes="" || !out="" then (prerr_endline "missing argument"; exit 2);
  let trust = parse_trust !trust in
  let certs = ref [] and mfts = ref [] and crls = ref [] and roas = ref [] and bad = ref [] in
  List.iter (fun (rel, full) -> match parse_record rel full with `Cert x -> certs := x :: !certs | `Mft x -> mfts := x :: !mfts | `Crl x -> crls := x :: !crls | `Roa x -> roas := x :: !roas | `Bad p -> bad := (p,"malformed") :: !bad | `Empty _ -> ()) (files_under !evidence);
  let cas = build_cas trust !certs !mfts !crls !now in
  let valid_roas, rejected_roas = List.fold_left (fun (ok,bad) o -> match roa_reason !mfts !crls cas !now o with None -> (o::ok,bad) | Some r -> (ok,(o.o_path,r)::bad)) ([],[]) !roas in
  let accepted = List.map (fun o -> o.o_path) valid_roas in
  let route_lines = read_file !routes |> split '\n' |> List.filter (fun s -> not (is_empty s)) in
  report route_lines valid_roas (!bad @ rejected_roas) accepted !now !out
OCAML

make -C /app clean all
