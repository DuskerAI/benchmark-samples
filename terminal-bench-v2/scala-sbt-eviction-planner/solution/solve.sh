#!/bin/bash
set -euo pipefail

cat > /app/planner/src/main/scala/planner/Main.scala <<'SCALA'
package planner

import java.io.{File, PrintWriter}
import scala.collection.mutable
import scala.io.Source

case class Module(org: String, name: String, scalaBinary: String, version: String, scheme: String)
case class Dep(fromOrg: String, fromName: String, fromScala: String, fromVersion: String, depScope: String, toOrg: String, toName: String, toScala: String, version: String, classifier: String, platform: String)
case class Request(org: String, name: String, scalaBinary: String, version: String, scope: String, classifier: String, platform: String, policy: String)
case class Constraint(org: String, name: String, scalaBinary: String, classifier: String, platform: String, minVersion: String, maxVersion: String, includeMin: Boolean, includeMax: Boolean)
case class Exclusion(fromOrg: String, fromName: String, fromScala: String, fromVersion: String, toOrg: String, toName: String, toScala: String, version: String, classifier: String, platform: String)
case class Alias(aliasOrg: String, aliasName: String, aliasScala: String, org: String, name: String, scalaBinary: String)
case class Key(org: String, name: String, scalaBinary: String, classifier: String, platform: String)

object Main {
  def main(args: Array[String]): Unit = {
    try {
      val opts = parseArgs(args.toList)
      val repo = new File(opts("--repo"))
      val requestFile = new File(opts("--request"))
      val lockFile = new File(opts("--lock"))
      val auditFile = new File(opts("--audit"))
      val aliases = readAliases(new File(repo, "aliases.tsv"))
      val modules = readModules(new File(repo, "modules.tsv"), aliases)
      val deps = readDeps(new File(repo, "deps.tsv"), aliases)
      val constraints = readConstraints(new File(repo, "constraints.tsv"), aliases)
      val exclusions = readExclusions(new File(repo, "exclusions.tsv"), aliases)
      val requests = readRequests(requestFile, aliases)
      val resolved = resolve(modules, deps, constraints, exclusions, requests)
      val lockText = renderLock(resolved._1, resolved._2, resolved._3)
      val auditText = renderAudit(resolved._1, resolved._3)
      writeAtomically(lockFile, lockText)
      writeAtomically(auditFile, auditText)
    } catch {
      case e: Exception =>
        Console.err.println(e.getMessage)
        System.exit(1)
    }
  }

  def parseArgs(args: List[String]): Map[String, String] = {
    if (args.length % 2 != 0) sys.error("invalid arguments")
    val m = args.grouped(2).map { case List(k, v) => k -> v }.toMap
    List("--repo", "--request", "--lock", "--audit").foreach { k => if (!m.contains(k)) sys.error("missing " + k) }
    m
  }

  def rows(file: File, required: Boolean): List[Map[String, String]] = {
    if (!file.exists) {
      if (required) sys.error("missing input " + file.getPath) else return Nil
    }
    val src = Source.fromFile(file)
    try {
      val lines = src.getLines().filter(_.trim.nonEmpty).toList
      if (lines.isEmpty) Nil else {
        val header = lines.head.split("\t", -1).toList
        lines.tail.map { line =>
          val values = line.split("\t", -1).toList
          header.zipAll(values, "", "").toMap.withDefaultValue("")
        }
      }
    } finally src.close()
  }

  def canonicalTriple(org: String, name: String, scalaBinary: String, aliases: Map[(String, String, String), (String, String, String)]): (String, String, String) = {
    var cur = (org, name, scalaBinary)
    val seen = mutable.Set[(String, String, String)]()
    while (aliases.contains(cur)) {
      if (!seen.add(cur)) sys.error("alias cycle at " + cur.toString)
      cur = aliases(cur)
    }
    cur
  }
  def canonKey(org: String, name: String, scalaBinary: String, classifier: String, platform: String, aliases: Map[(String, String, String), (String, String, String)]): Key = {
    val c = canonicalTriple(org, name, scalaBinary, aliases)
    Key(c._1, c._2, c._3, classifier, platform)
  }

  def readAliases(file: File): Map[(String, String, String), (String, String, String)] = {
    rows(file, required = false).map { r =>
      val from = (r("alias_org"), r("alias_name"), r("alias_scala_binary"))
      val to = (r("org"), r("name"), r("scala_binary"))
      if (from == to) sys.error("self alias " + from.toString)
      from -> to
    }.groupBy(_._1).map { case (k, pairs) =>
      val targets = pairs.map(_._2).distinct
      if (targets.size != 1) sys.error("conflicting aliases for " + k.toString)
      k -> targets.head
    }
  }

  def readModules(file: File, aliases: Map[(String, String, String), (String, String, String)]): List[Module] = rows(file, required = true).map { r =>
    val c = canonicalTriple(r("org"), r("name"), r("scala_binary"), aliases)
    Module(c._1, c._2, c._3, r("version"), r("version_scheme"))
  }
  def readDeps(file: File, aliases: Map[(String, String, String), (String, String, String)]): List[Dep] = rows(file, required = false).map { r =>
    val f = canonicalTriple(r("from_org"), r("from_name"), r("from_scala_binary"), aliases)
    val t = canonicalTriple(r("to_org"), r("to_name"), r("to_scala_binary"), aliases)
    Dep(f._1, f._2, f._3, r("from_version"), r("dep_scope"), t._1, t._2, t._3, r("version"), r("classifier"), r("platform"))
  }
  def readRequests(file: File, aliases: Map[(String, String, String), (String, String, String)]): List[Request] = rows(file, required = true).map { r =>
    val c = canonicalTriple(r("org"), r("name"), r("scala_binary"), aliases)
    Request(c._1, c._2, c._3, r("version"), r("scope"), r("classifier"), r("platform"), r("policy"))
  }
  def readConstraints(file: File, aliases: Map[(String, String, String), (String, String, String)]): List[Constraint] = rows(file, required = false).map { r =>
    val c = canonicalTriple(r("org"), r("name"), r("scala_binary"), aliases)
    Constraint(c._1, c._2, c._3, r("classifier"), r("platform"), r("min_version"), r("max_version"), parseBool(r("include_min"), default = true), parseBool(r("include_max"), default = false))
  }
  def readExclusions(file: File, aliases: Map[(String, String, String), (String, String, String)]): List[Exclusion] = rows(file, required = false).map { r =>
    val f = canonicalTriple(r("from_org"), r("from_name"), r("from_scala_binary"), aliases)
    val t = canonicalTriple(r("to_org"), r("to_name"), r("to_scala_binary"), aliases)
    Exclusion(f._1, f._2, f._3, r("from_version"), t._1, t._2, t._3, r("version"), r("classifier"), r("platform"))
  }

  def parseBool(raw: String, default: Boolean): Boolean = raw match {
    case "" => default
    case "true" => true
    case "false" => false
    case other => sys.error("invalid boolean flag " + other)
  }

  def resolve(modules: List[Module], deps: List[Dep], constraints: List[Constraint], exclusions: List[Exclusion], requests: List[Request]): (Map[Key, Map[String, String]], Map[Key, Set[String]], Map[Key, String]) = {
    val validScopes = Set("compile", "runtime", "plugin", "bootstrap")
    val moduleKeys = modules.map(m => (m.org, m.name, m.scalaBinary, m.version))
    if (moduleKeys.distinct.size != moduleKeys.size) sys.error("duplicate module row")
    deps.foreach { d => if (!validScopes.contains(d.depScope)) sys.error("unknown dependency scope " + d.depScope) }
    requests.foreach { r => if (!validScopes.contains(r.scope)) sys.error("unknown request scope " + r.scope) }
    val byModule = modules.map(m => (m.org, m.name, m.scalaBinary, m.version) -> m).toMap
    val byFrom = deps.groupBy(d => (d.fromOrg, d.fromName, d.fromScala, d.fromVersion))
    val exclusionsByFrom = exclusions.groupBy(e => (e.fromOrg, e.fromName, e.fromScala, e.fromVersion))
    val constraintsByKey = constraints.groupBy(c => Key(c.org, c.name, c.scalaBinary, c.classifier, c.platform))
    val candidates = mutable.Map[Key, mutable.Map[String, String]]()
    val scopes = mutable.Map[Key, mutable.Set[String]]()
    val forced = mutable.Map[Key, String]()
    val denied = mutable.Set[(Key, String)]()
    val blocked = mutable.Set[Key]()
    val expanded = mutable.Set[(String, String, String, String, String, String, String, String)]()
    val active = mutable.Set[(String, String, String, String, String)]()

    def satisfiesConstraint(c: Constraint, version: String): Boolean = {
      val lower = c.minVersion == "" || { val cmp = cmpVersion(version, c.minVersion); if (c.includeMin) cmp >= 0 else cmp > 0 }
      val upper = c.maxVersion == "" || { val cmp = cmpVersion(version, c.maxVersion); if (c.includeMax) cmp <= 0 else cmp < 0 }
      lower && upper
    }
    def admitted(key: Key, version: String): Boolean = constraintsByKey.getOrElse(key, Nil).forall(c => satisfiesConstraint(c, version))
    def edgeExcluded(d: Dep): Boolean = {
      exclusionsByFrom.getOrElse((d.fromOrg, d.fromName, d.fromScala, d.fromVersion), Nil).exists { e =>
        e.toOrg == d.toOrg && e.toName == d.toName && e.toScala == d.toScala && e.version == d.version && e.classifier == d.classifier && e.platform == d.platform
      }
    }

    def include(org: String, name: String, scalaBinary: String, version: String, classifier: String, platform: String, rootScope: String, mode: String): Unit = {
      val module = byModule.getOrElse((org, name, scalaBinary, version), sys.error("unknown component " + org + ":" + name + ":" + scalaBinary + ":" + version))
      val key = Key(org, name, scalaBinary, classifier, platform)
      if (denied.contains(key -> version) || !admitted(key, version)) { blocked += key; return }
      candidates.getOrElseUpdate(key, mutable.Map[String, String]())(version) = module.scheme
      scopes.getOrElseUpdate(key, mutable.Set[String]()) += rootScope
      val cycleKey = (org, name, scalaBinary, version, mode)
      if (mode != "plugin" && active.contains(cycleKey)) sys.error("unrepresentable dependency cycle at " + org + ":" + name + ":" + scalaBinary + ":" + version)
      val state = (org, name, scalaBinary, version, classifier, platform, rootScope, mode)
      if (!expanded.add(state)) return
      active += cycleKey
      try {
        byFrom.getOrElse((org, name, scalaBinary, version), Nil).filterNot(edgeExcluded).foreach { d =>
          val follow = d.depScope match {
            case "compile" => true
            case "runtime" => mode == "runtime"
            case "plugin" | "bootstrap" => mode == "plugin"
            case _ => false
          }
          if (follow) {
            val nextMode = d.depScope match {
              case "runtime" => "runtime"
              case "plugin" | "bootstrap" => "plugin"
              case _ => mode
            }
            include(d.toOrg, d.toName, d.toScala, d.version, d.classifier, d.platform, rootScope, nextMode)
          }
        }
      } finally active -= cycleKey
    }

    requests.foreach { r =>
      val key = Key(r.org, r.name, r.scalaBinary, r.classifier, r.platform)
      byModule.getOrElse((r.org, r.name, r.scalaBinary, r.version), sys.error("unknown component " + r.org + ":" + r.name + ":" + r.scalaBinary + ":" + r.version))
      r.policy match {
        case "" =>
        case "force" => forced.get(key).filter(_ != r.version).foreach(_ => sys.error("conflicting forced versions for " + key.toString)); forced(key) = r.version
        case "deny" => denied += ((key, r.version))
        case other => sys.error("unknown request policy " + other)
      }
    }

    requests.filter(_.policy != "deny").foreach { r =>
      val mode = r.scope match { case "runtime" => "runtime"; case "plugin" | "bootstrap" => "plugin"; case "compile" => "compile" }
      include(r.org, r.name, r.scalaBinary, r.version, r.classifier, r.platform, r.scope, mode)
    }
    forced.foreach { case (k, v) =>
      if (denied.contains(k -> v) || !admitted(k, v)) sys.error("forced version is not admitted for " + k.toString)
      if (!candidates.get(k).exists(_.contains(v))) sys.error("forced version was not admitted for " + k.toString)
    }
    blocked.foreach { k => if (!candidates.contains(k)) sys.error("all reached versions denied or constrained for " + k.toString) }
    (candidates.map { case (k, v) => k -> v.toMap }.toMap, scopes.map { case (k, v) => k -> v.toSet }.toMap, forced.toMap)
  }

  def splitVersion(v: String): List[String] = v.split("[.-]", -1).toList.filter(_.nonEmpty)
  def isNum(s: String): Boolean = s.nonEmpty && s.forall(_.isDigit)
  def cmpVersion(a: String, b: String): Int = {
    val as = splitVersion(a); val bs = splitVersion(b); val n = math.max(as.length, bs.length); var i = 0
    while (i < n) {
      val av = if (i < as.length) as(i) else "0"; val bv = if (i < bs.length) bs(i) else "0"
      val c = if (isNum(av) && isNum(bv)) BigInt(av).compare(BigInt(bv)) else av.compareTo(bv)
      if (c != 0) return c
      i += 1
    }
    0
  }
  def highest(vs: Iterable[String]): String = vs.toList.sortWith((a, b) => cmpVersion(a, b) < 0).last
  def numericParts(v: String): List[Int] = splitVersion(v).takeWhile(isNum).map(_.toInt)
  def part(parts: List[Int], i: Int): Int = if (i < parts.length) parts(i) else 0
  def compatible(scheme: String, selected: String, evicted: String): Boolean = {
    val s = numericParts(selected); val e = numericParts(evicted)
    scheme match {
      case "always" => true
      case "pvp" => part(s, 0) == part(e, 0) && part(s, 1) == part(e, 1)
      case "early-semver" if part(s, 0) == 0 => part(e, 0) == 0 && part(s, 1) == part(e, 1)
      case "early-semver" | "semver-spec" => part(s, 0) == part(e, 0)
      case _ => false
    }
  }

  def keyOrder(k: Key): (String, String, String, String, String) = (k.org, k.name, k.scalaBinary, k.classifier, k.platform)
  def esc(s: String): String = s.replace("\\", "\\\\").replace("\"", "\\\"")
  def q(s: String): String = "\"" + esc(s) + "\""
  def field(name: String, value: String): String = q(name) + ":" + q(value)
  def boolField(name: String, value: Boolean): String = q(name) + ":" + value.toString
  def selectedVersion(k: Key, versions: Map[String, String], forced: Map[Key, String]): String = forced.getOrElse(k, highest(versions.keys))

  def renderLock(candidates: Map[Key, Map[String, String]], scopes: Map[Key, Set[String]], forced: Map[Key, String]): String = {
    val entries = candidates.toList.sortBy { case (k, _) => keyOrder(k) }.map { case (k, versions) =>
      val selected = selectedVersion(k, versions, forced)
      val scope = scopes.getOrElse(k, Set.empty).toList.sorted.mkString(",")
      "{" + List(field("org", k.org), field("name", k.name), field("scala_binary", k.scalaBinary), field("classifier", k.classifier), field("platform", k.platform), field("version", selected), field("scope", scope)).mkString(",") + "}"
    }
    "{\"schema\":\"sbt-eviction-plan/v1\",\"artifacts\":[" + entries.mkString(",") + "]}\n"
  }

  def renderAudit(candidates: Map[Key, Map[String, String]], forced: Map[Key, String]): String = {
    val evictions = candidates.toList.sortBy { case (k, _) => keyOrder(k) }.flatMap { case (k, versions) =>
      val selected = selectedVersion(k, versions, forced); val scheme = versions(selected)
      versions.keys.toList.filter(_ != selected).sortWith((a, b) => cmpVersion(a, b) < 0).map { evicted =>
        "{" + List(field("org", k.org), field("name", k.name), field("scala_binary", k.scalaBinary), field("classifier", k.classifier), field("platform", k.platform), field("selected", selected), field("evicted", evicted), boolField("compatible", compatible(scheme, selected, evicted)), field("scheme", scheme)).mkString(",") + "}"
      }
    }
    val status = if (evictions.exists(_.contains("\"compatible\":false"))) "conflict" else "ok"
    "{\"schema\":\"sbt-eviction-audit/v1\",\"status\":" + q(status) + ",\"evictions\":[" + evictions.mkString(",") + "]}\n"
  }

  def writeAtomically(file: File, text: String): Unit = {
    val parent = file.getParentFile
    if (parent != null) parent.mkdirs()
    val tmp = new File(file.getPath + ".tmp")
    val out = new PrintWriter(tmp)
    try out.print(text) finally out.close()
    if (file.exists && !file.delete()) sys.error("could not replace " + file.getPath)
    if (!tmp.renameTo(file)) sys.error("could not publish " + file.getPath)
  }
}
SCALA

chmod +x /app/planner/scripts/build.sh
/app/planner/scripts/build.sh
