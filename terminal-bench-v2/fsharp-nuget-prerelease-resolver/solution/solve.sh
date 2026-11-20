#!/bin/bash
set -euo pipefail

cat > /app/nugate/src/Program.fs <<'FSHARP'
open System
open System.Collections.Generic
open System.Globalization
open System.IO
open System.Security.Cryptography
open System.Text
open System.Text.Json

type Dependency = { Id: string; Range: string }
type Package = { Id: string; Aliases: string list; VersionRaw: string; Version: SemVer; Listed: bool; Frameworks: Map<string, Dependency list>; Path: string }
and SemVer = { Major: int; Minor: int; Patch: int; Pre: string list option }
type Bound = { Version: SemVer; Inclusive: bool }
type RangeSpec = { Text: string; Exact: SemVer option; Lower: Bound option; Upper: Bound option; HasPrerelease: bool }
type Choice = { Package: Package; Framework: string; Deps: Dependency list; Requested: bool }
type ConstraintSet = { Display: string; Ranges: string list }
type OutDep = { id: string; range: string }
type OutPackage = { id: string; version: string; requested: bool; framework: string; dependencies: OutDep array }
type OutReject = { id: string; ranges: string array; reason: string }
type LockOut = { accepted: bool; targetFramework: string; packages: OutPackage array; rejected: OutReject array }
type AuditEvent = { action: string; id: string; version: string; detail: string }
type AuditOut = { decision: string; targetFramework: string; roots: string array; events: AuditEvent array; repositoryHash: string }

let lower (s: string) = s.ToLowerInvariant()

let matchesPackageId (id: string) (p: Package) =
    let k = lower id
    lower p.Id = k || (p.Aliases |> List.exists (fun a -> lower a = k))

let parseSemVer (s: string) =
    let trimmed = s.Trim()
    let dash = trimmed.IndexOf('-')
    let main, pre =
        if dash >= 0 then trimmed.Substring(0, dash), Some(trimmed.Substring(dash + 1).Split('.') |> Array.toList)
        else trimmed, None
    let parts = main.Split('.')
    if parts.Length <> 3 then failwithf "invalid SemVer: %s" s
    { Major = Int32.Parse(parts[0], CultureInfo.InvariantCulture)
      Minor = Int32.Parse(parts[1], CultureInfo.InvariantCulture)
      Patch = Int32.Parse(parts[2], CultureInfo.InvariantCulture)
      Pre = pre }

let compareIdentifier (a: string) (b: string) =
    match Int32.TryParse(a), Int32.TryParse(b) with
    | (true, ai), (true, bi) -> compare ai bi
    | (true, _), (false, _) -> -1
    | (false, _), (true, _) -> 1
    | _ -> StringComparer.Ordinal.Compare(a, b)

let rec comparePre a b =
    match a, b with
    | [], [] -> 0
    | [], _ -> -1
    | _, [] -> 1
    | x :: xs, y :: ys ->
        let c = compareIdentifier x y
        if c <> 0 then c else comparePre xs ys

let compareSemVer a b =
    let c1 = compare a.Major b.Major
    if c1 <> 0 then c1 else
    let c2 = compare a.Minor b.Minor
    if c2 <> 0 then c2 else
    let c3 = compare a.Patch b.Patch
    if c3 <> 0 then c3 else
    match a.Pre, b.Pre with
    | None, None -> 0
    | None, Some _ -> 1
    | Some _, None -> -1
    | Some xs, Some ys -> comparePre xs ys

let parseRange (s: string) =
    let t = s.Trim()
    if t.Length >= 2 && (t[0] = '[' || t[0] = '(') then
        let last = t[t.Length - 1]
        if last <> ']' && last <> ')' then failwithf "invalid range: %s" s
        let inner = t.Substring(1, t.Length - 2)
        let parts = inner.Split(',', 2)
        if parts.Length <> 2 then failwithf "invalid range: %s" s
        let mkBound inclusive (raw: string) =
            let v = raw.Trim()
            if v = "" then None else Some { Version = parseSemVer v; Inclusive = inclusive }
        let lo = mkBound (t[0] = '[') parts[0]
        let hi = mkBound (last = ']') parts[1]
        { Text = s; Exact = None; Lower = lo; Upper = hi; HasPrerelease = [ lo; hi ] |> List.exists (function Some b -> b.Version.Pre.IsSome | None -> false) }
    else
        let v = parseSemVer t
        { Text = s; Exact = Some v; Lower = None; Upper = None; HasPrerelease = v.Pre.IsSome }

let satisfiesOne version range =
    match range.Exact with
    | Some exact -> compareSemVer version exact = 0
    | None ->
        let lowerOk =
            match range.Lower with
            | None -> true
            | Some b -> let c = compareSemVer version b.Version in c > 0 || (c = 0 && b.Inclusive)
        let upperOk =
            match range.Upper with
            | None -> true
            | Some b -> let c = compareSemVer version b.Version in c < 0 || (c = 0 && b.Inclusive)
        lowerOk && upperOk

let satisfiesAll version ranges =
    let preAllowed = version.Pre.IsNone || (ranges |> List.exists (fun r -> r.HasPrerelease))
    preAllowed && (ranges |> List.forall (satisfiesOne version))

let tryNumberAfter (prefix: string) (s: string) =
    if not (s.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) then None else
    let rest = s.Substring(prefix.Length)
    let parts = rest.Split('.')
    match Int32.TryParse(parts[0]) with
    | true, major ->
        let minor = if parts.Length > 1 then match Int32.TryParse(parts[1]) with true, m -> m | _ -> 0 else 0
        Some(major, minor)
    | _ -> None

let frameworkRank (target: string) (group: string) =
    if String.Equals(target, group, StringComparison.OrdinalIgnoreCase) then Some 100000 else
    match tryNumberAfter "net" target, tryNumberAfter "net" group, tryNumberAfter "netstandard" target, tryNumberAfter "netstandard" group with
    | Some(tMaj, tMin), Some(gMaj, gMin), _, _ when gMaj < tMaj || (gMaj = tMaj && gMin <= tMin) -> Some(50000 + gMaj * 100 + gMin)
    | Some(tMaj, _), _, _, Some(sMaj, sMin) when tMaj >= 5 && sMaj = 2 && (sMin = 0 || sMin = 1) -> Some(10000 + sMaj * 100 + sMin)
    | _, _, Some(tMaj, tMin), Some(sMaj, sMin) when sMaj < tMaj || (sMaj = tMaj && sMin <= tMin) -> Some(10000 + sMaj * 100 + sMin)
    | _ -> None

let chooseFramework target (p: Package) =
    p.Frameworks
    |> Map.toList
    |> List.choose (fun (fw, deps) -> frameworkRank target fw |> Option.map (fun r -> r, fw, deps))
    |> List.sortByDescending (fun (r, _, _) -> r)
    |> List.tryHead
    |> Option.map (fun (_, fw, deps) -> fw, deps)

let text (e: JsonElement) (name: string) =
    let mutable p = Unchecked.defaultof<JsonElement>
    if e.TryGetProperty(name, &p) && p.ValueKind = JsonValueKind.String then p.GetString()
    else failwithf "missing string property %s" name

let readDeps (v: JsonElement) =
    if v.ValueKind <> JsonValueKind.Array then failwith "dependencies must be arrays"
    [ for d in v.EnumerateArray() -> { Id = text d "id"; Range = text d "range" } ]

let readPackage (path: string) =
    use doc = JsonDocument.Parse(File.ReadAllText path)
    let root = doc.RootElement
    let listed =
        let mutable p = Unchecked.defaultof<JsonElement>
        if root.TryGetProperty("listed", &p) then p.GetBoolean() else true
    let mutable fw = Unchecked.defaultof<JsonElement>
    let aliases =
        let mutable a = Unchecked.defaultof<JsonElement>
        if root.TryGetProperty("aliases", &a) && a.ValueKind = JsonValueKind.Array then
            [ for item in a.EnumerateArray() do
                if item.ValueKind = JsonValueKind.String then item.GetString() ]
        else []
    let groups =
        if root.TryGetProperty("frameworks", &fw) && fw.ValueKind = JsonValueKind.Object then
            [ for g in fw.EnumerateObject() -> g.Name, readDeps g.Value ] |> Map.ofList
        else failwith "missing frameworks object"
    let versionRaw = text root "version"
    { Id = text root "id"; Aliases = aliases; VersionRaw = versionRaw; Version = parseSemVer versionRaw; Listed = listed; Frameworks = groups; Path = path }

let parseArgs (argv: string array) =
    if argv.Length % 2 <> 0 then failwith "arguments must be flag/value pairs"
    argv
    |> Array.chunkBySize 2
    |> Array.map (fun a -> a[0], a[1])
    |> Map.ofArray

let canonicalKeyAndDisplay (packages: Package list) (id: string) =
    let matches = packages |> List.filter (matchesPackageId id)
    match matches with
    | [] -> lower id, id
    | _ ->
        let canonical = matches |> List.map (fun p -> lower p.Id, p.Id) |> List.sortBy fst |> List.head
        canonical

let addRangeWithPackages (packages: Package list) (id: string) (range: string) (m: Map<string, ConstraintSet>) =
    let k, display = canonicalKeyAndDisplay packages id
    match Map.tryFind k m with
    | None -> Map.add k { Display = display; Ranges = [ range ] } m
    | Some c when c.Ranges |> List.contains range -> m
    | Some c -> Map.add k { c with Ranges = c.Ranges @ [ range ] } m

let packageSort (a: Package) (b: Package) = -(compareSemVer a.Version b.Version)

let selectionSignature (s: Map<string, Choice>) =
    s |> Map.toList |> List.map (fun (k, c) -> k, c.Package.VersionRaw, c.Framework, (c.Deps |> List.map (fun d -> lower d.Id, d.Range)))

let repoHash (repo: string) =
    use sha = SHA256.Create()
    use ms = new MemoryStream()
    let files =
        Directory.GetFiles(repo, "*.json", SearchOption.AllDirectories)
        |> Array.map (fun p -> Path.GetRelativePath(repo, p).Replace(Path.DirectorySeparatorChar, '/'), p)
        |> Array.sortBy fst
    for rel, path in files do
        let rb = Encoding.UTF8.GetBytes(rel)
        ms.Write(rb, 0, rb.Length)
        ms.WriteByte(0uy)
        let fb = File.ReadAllBytes(path)
        ms.Write(fb, 0, fb.Length)
        ms.WriteByte(0uy)
    let hash = sha.ComputeHash(ms.ToArray())
    hash |> Array.map (fun b -> b.ToString("x2", CultureInfo.InvariantCulture)) |> String.concat ""

let writeJson (path: string) value =
    let dir = Path.GetDirectoryName(path)
    if not (String.IsNullOrWhiteSpace dir) then Directory.CreateDirectory(dir) |> ignore
    let opts = JsonSerializerOptions(WriteIndented = true)
    File.WriteAllText(path, JsonSerializer.Serialize(value, opts))

let makeOutputs (accepted: bool) (target: string) (roots: string array) (selected: Map<string, Choice>) (rejected: OutReject list) (hash: string) =
    let events =
        let selectEvents =
            selected
            |> Map.toList
            |> List.sortBy fst
            |> List.map (fun (_, c) -> { action = "select"; id = c.Package.Id; version = c.Package.VersionRaw; detail = c.Framework })
        let rejectEvents = rejected |> List.map (fun (r: OutReject) -> { action = "reject"; id = r.id; version = ""; detail = r.reason })
        (selectEvents @ rejectEvents) |> List.toArray
    let packages =
        if not accepted then [||] else
        selected
        |> Map.toList
        |> List.sortBy fst
        |> List.map (fun (_, c) ->
            { id = c.Package.Id
              version = c.Package.VersionRaw
              requested = c.Requested
              framework = c.Framework
              dependencies = c.Deps |> List.sortBy (fun d -> lower d.Id, d.Range) |> List.map (fun d -> { id = d.Id; range = d.Range }) |> List.toArray })
        |> List.toArray
    { accepted = accepted
      targetFramework = target
      packages = packages
      rejected = rejected |> List.toArray },
    { decision = (if accepted then "accepted" else "rejected")
      targetFramework = target
      roots = roots
      events = events
      repositoryHash = hash }

let resolve (repo: string) (requestPath: string) =
    use req = JsonDocument.Parse(File.ReadAllText requestPath)
    let target = text req.RootElement "targetFramework"
    let rootDeps = readDeps (req.RootElement.GetProperty("packages"))
    let roots = rootDeps |> List.map (fun d -> d.Id) |> List.toArray
    let packages = Directory.GetFiles(repo, "*.json", SearchOption.AllDirectories) |> Array.map readPackage |> Array.toList
    let rootMap = rootDeps |> List.fold (fun m d -> addRangeWithPackages packages d.Id d.Range m) Map.empty
    let requested = rootDeps |> List.map (fun d -> canonicalKeyAndDisplay packages d.Id |> fst) |> Set.ofList
    let mutable selection : Map<string, Choice> = Map.empty
    let mutable constraints = rootMap
    let mutable rejected : OutReject list = []
    let mutable stable = false
    let mutable iterations = 0
    while not stable && rejected.IsEmpty && iterations < 80 do
        iterations <- iterations + 1
        let mutable next : Map<string, Choice> = Map.empty
        let rejects = ResizeArray<OutReject>()
        for KeyValue(k, c) in constraints do
            let ranges = c.Ranges |> List.map parseRange
            let candidates =
                packages
                |> List.filter (fun p -> lower p.Id = k && p.Listed && satisfiesAll p.Version ranges && (chooseFramework target p).IsSome)
                |> List.sortWith packageSort
            match candidates with
            | [] ->
                let active = String.concat ", " c.Ranges
                rejects.Add({ id = c.Display; ranges = c.Ranges |> List.toArray; reason = sprintf "no listed compatible version satisfies ranges %s for target %s" active target })
            | p :: _ ->
                let fw, deps = chooseFramework target p |> Option.get
                next <- Map.add k { Package = p; Framework = fw; Deps = deps; Requested = requested.Contains k } next
        if rejects.Count > 0 then rejected <- rejects |> Seq.toList
        else
            let expanded =
                next
                |> Map.fold (fun m _ c -> c.Deps |> List.fold (fun mm d -> addRangeWithPackages packages d.Id d.Range mm) m) constraints
            if selectionSignature selection = selectionSignature next && constraints = expanded then stable <- true
            else
                selection <- next
                constraints <- expanded
    if iterations >= 80 && not stable then
        rejected <- [{ id = "resolution"; ranges = [||]; reason = "resolution did not converge" }]
    target, roots, selection, rejected

let writeFailure argv message =
    try
        let args = parseArgs argv
        if args.ContainsKey("--lock") && args.ContainsKey("--audit") then
            let target =
                try
                    if args.ContainsKey("--request") then
                        use d = JsonDocument.Parse(File.ReadAllText args["--request"])
                        text d.RootElement "targetFramework"
                    else ""
                with _ -> ""
            let hash = try if args.ContainsKey("--repo") && Directory.Exists(args["--repo"]) then repoHash args["--repo"] else "" with _ -> ""
            let rej : OutReject list = [{ id = "input"; ranges = [||]; reason = message }]
            let lock, audit = makeOutputs false target [||] Map.empty rej hash
            writeJson args["--lock"] lock
            writeJson args["--audit"] audit
    with _ -> ()

[<EntryPoint>]
let main argv =
    try
        let args = parseArgs argv
        for required in [ "--repo"; "--request"; "--lock"; "--audit" ] do
            if not (args.ContainsKey required) then failwithf "missing %s" required
        let target, roots, selected, rejected = resolve args["--repo"] args["--request"]
        let accepted = rejected.IsEmpty
        let hash = repoHash args["--repo"]
        let lock, audit = makeOutputs accepted target roots selected rejected hash
        writeJson args["--lock"] lock
        writeJson args["--audit"] audit
        if accepted then 0 else 1
    with ex ->
        Console.Error.WriteLine(ex.Message)
        writeFailure argv ex.Message
        2
FSHARP

/app/nugate/bin/build.sh
