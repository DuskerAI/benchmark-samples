import Foundation

func parseArgs() -> [String: String] {
    var out: [String: String] = [:]
    var i = 1
    let args = CommandLine.arguments
    while i + 1 < args.count {
        out[args[i]] = args[i + 1]
        i += 2
    }
    return out
}

let args = parseArgs()
guard let outPath = args["--out"] else { exit(2) }

let report: [String: Any] = [
    "accepted": [],
    "rejected": [],
    "summary": ["accepted": 0, "rejected": 0, "highestCounter": 0],
    "nextState": ["credentials": [:], "consumedChallenges": []]
]

do {
    let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
    let url = URL(fileURLWithPath: outPath)
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try data.write(to: url)
} catch {
    exit(2)
}
