#!/bin/bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path

src = Path('/app/src/certadmit.ml')
text = src.read_text()

replacements = [
    (
        '  policy_maps : (string * string) list;\n',
        '  policy_maps : (string * string * string option) list;\n',
    ),
    (
        '''let parse_policy_maps s =
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
''',
        '''let parse_policy_maps s =
  let parse_issuer_side side =
    match split_on '@' (trim side) with
    | [issuer] when issuer <> "" -> (issuer, None)
    | [issuer; issuer_id] when issuer <> "" && trim issuer_id <> "" -> (issuer, Some (trim issuer_id))
    | _ -> failwith "invalid policy map"
  in
  list_field s
  |> List.map (fun entry ->
       match split_on '>' entry with
       | [child; issuer_side] when trim child <> "" ->
           let issuer, issuer_id = parse_issuer_side issuer_side in
           (trim child, issuer, issuer_id)
       | _ -> failwith "invalid policy map")
''',
    ),
    (
        '''let parse_bool = function
  | "true" | "1" -> true
  | "false" | "0" -> false
  | _ -> failwith "invalid boolean"
''',
        '''let parse_bool = function
  | "true" -> true
  | "false" -> false
  | _ -> failwith "invalid boolean"
''',
    ),
    (
        '''let constraints_ok leaf issuers =
  let names = match leaf.dns, leaf.ip with
    | d :: _, _ -> [(DnsName, d)]
    | [], i :: _ -> [(IpName, i)]
    | [], [] -> [] in
  List.for_all (fun issuer -> List.for_all (fun (kind, name) -> constraint_applies kind name issuer) names) issuers
''',
        '''let constraints_ok leaf issuers =
  let names = (List.map (fun name -> (DnsName, name)) leaf.dns) @ (List.map (fun name -> (IpName, name)) leaf.ip) in
  List.for_all (fun issuer -> List.for_all (fun (kind, name) -> constraint_applies kind name issuer) names) issuers
''',
    ),
    (
        '''           |> List.filter_map (fun (child, parent) ->
                if child = p && accepts parent then Some parent else None)
''',
        '''           |> List.filter_map (fun (child, parent, issuer_id) ->
                 let scope_ok = match issuer_id with None -> true | Some id -> id = issuer.id in
                 if child = p && scope_ok && accepts parent then Some parent else None)
''',
    ),
    (
        '''  else if List.exists (fun c -> not c.ca || not (has c.ku "keyCertSign")) issuers || not (path_lengths_ok issuers) then Reject "CA_POLICY"
''',
        '''  else if List.exists (fun c -> not c.ca || not (has c.ku "keyCertSign")) issuers || not (path_lengths_ok issuers) || not (issuer_requirements_ok path) then Reject "CA_POLICY"
''',
    ),
    (
        '''      let _tied_full_count = List.length fulls in
      let base = List.fold_left (fun best r -> if r.number > best.number then r else best) first rest in
      let deltas = List.filter (fun r -> r.base = Some base.number && r.number > base.number && r.number = base.number + 1) fresh in
      let selected = base :: deltas in
''',
        '''      let max_number = List.fold_left (fun best r -> max best r.number) first.number rest in
      let bases = List.filter (fun r -> r.number = max_number) fulls in
      let deltas = List.filter (fun r -> r.base = Some max_number && r.number > max_number) fresh in
      let selected = bases @ deltas in
''',
    ),
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f'expected source fragment not found:\n{old}')
    text = text.replace(old, new, 1)

src.write_text(text)
PY

make -C /app clean all
