open System
open System.IO
open System.Text.Json

type Dependency = { Id: string; Range: string }
type Package = { Id: string; Version: string; Listed: bool; Aliases: string list; Frameworks: Map<string, Dependency list> }

let text (e: JsonElement) (name: string) = e.GetProperty(name).GetString()

let readDeps (v: JsonElement) =
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
        if root.TryGetProperty("frameworks", &fw) then
            [ for g in fw.EnumerateObject() -> g.Name, readDeps g.Value ] |> Map.ofList
        else Map.empty
    { Id = text root "id"; Version = text root "version"; Listed = listed; Aliases = aliases; Frameworks = groups }

let matchesId (wanted: string) (p: Package) =
    let same (a: string) (b: string) = String.Equals(a, b, StringComparison.OrdinalIgnoreCase)
    same wanted p.Id || (p.Aliases |> List.exists (same wanted))

let nearestFramework (target: string) (p: Package) =
    if p.Frameworks.ContainsKey target then Some(target, p.Frameworks[target])
    else
        p.Frameworks
        |> Map.toList
        |> List.choose (fun (fw, deps) ->
            if target.StartsWith("net") && fw.StartsWith("net") then Some(fw, deps) else None)
        |> List.sortByDescending fst
        |> List.tryHead

let argMap (argv: string array) =
    argv
    |> Array.chunkBySize 2
    |> Array.map (fun a -> a[0], a[1])
    |> Map.ofArray

let writeJson (path: string) (value: obj) =
    let options = JsonSerializerOptions(WriteIndented = true)
    File.WriteAllText(path, JsonSerializer.Serialize(value, options))

[<EntryPoint>]
let main argv =
    try
        let args = argMap argv
        let repo = args["--repo"]
        let request = args["--request"]
        let lockPath = args["--lock"]
        let auditPath = args["--audit"]
        use req = JsonDocument.Parse(File.ReadAllText request)
        let target = text req.RootElement "targetFramework"
        let roots =
            [ for p in req.RootElement.GetProperty("packages").EnumerateArray() -> text p "id", text p "range" ]
        let packages = Directory.GetFiles(repo, "*.json", SearchOption.AllDirectories) |> Array.map readPackage
        let chosen =
            roots
            |> List.choose (fun (id: string, range: string) ->
                packages
                |> Array.filter (fun p -> p.Listed && matchesId id p && (nearestFramework target p).IsSome && String.CompareOrdinal(p.Version, range.Trim('[',']','(',')')) >= 0)
                |> Array.sortByDescending (fun p -> p.Version)
                |> Array.tryHead
                |> Option.map (fun p ->
                    let fw, deps = nearestFramework target p |> Option.get
                    dict [ "id", box p.Id; "version", box p.Version; "requested", box true; "framework", box fw; "dependencies", box (deps |> List.map (fun d -> dict [ "id", box d.Id; "range", box d.Range ])) ]))
        let ok = chosen.Length = roots.Length
        writeJson lockPath (dict [ "accepted", box ok; "targetFramework", box target; "packages", box chosen; "rejected", box [||] ])
        writeJson auditPath (dict [ "decision", box (if ok then "accepted" else "rejected"); "targetFramework", box target; "roots", box (roots |> List.map fst); "events", box [||]; "repositoryHash", box "" ])
        if ok then 0 else 1
    with ex ->
        Console.Error.WriteLine(ex.Message)
        2
