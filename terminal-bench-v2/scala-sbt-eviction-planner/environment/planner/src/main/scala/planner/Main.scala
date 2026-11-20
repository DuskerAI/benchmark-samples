package planner

import java.io.{File, PrintWriter}
import scala.io.Source
import scala.collection.mutable

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
      val deps = readDeps(new File(repo, "deps.tsv"))
      val constraints = readConstraints(new File(repo, "constraints.tsv"))
      val exclusions = readExclusions(new File(repo, "exclusions.tsv"))
      val requests = readRequests(requestFile)
      val plan = resolve(modules, deps, constraints, exclusions, requests)
      write(lockFile, renderLock(plan._1, plan._2))
      write(auditFile, renderAudit(plan._1, plan._3))
    } catch {
      case e: Exception =>
        Console.err.println(e.getMessage)
        System.exit(1)
    }
  }

  def parseArgs(args: List[String]): Map[String, String] = {
    val pairs = args.grouped(2).toList
    val m = pairs.collect { case List(k, v) => k -> v }.toMap
    List("--repo", "--request", "--lock", "--audit").foreach { k => if (!m.contains(k)) sys.error("missing " + k) }
    m
  }

  def rows(file: File): List[Map[String, String]] = {
    if (!file.exists) return Nil
    val src = Source.fromFile(file)
    try {
      val lines = src.getLines().filter(_.trim.nonEmpty).toList
      if (lines.isEmpty) Nil else {
        val header = lines.head.split("\t", -1).toList
        lines.tail.map { line => header.zip(line.split("\t", -1).toList).toMap.withDefaultValue("") }
      }
    } finally src.close()
  }

  def readAliases(file: File): Map[(String, String, String), (String, String, String)] = rows(file).map { r =>
    (r("alias_org"), r("alias_name"), r("alias_scala_binary")) -> (r("org"), r("name"), r("scala_binary"))
  }.toMap
  def canonical(org: String, name: String, scalaBinary: String, aliases: Map[(String, String, String), (String, String, String)]): (String, String, String) = aliases.getOrElse((org, name, scalaBinary), (org, name, scalaBinary))
  def readModules(file: File, aliases: Map[(String, String, String), (String, String, String)]): List[Module] = rows(file).map { r =>
    val c = canonical(r("org"), r("name"), r("scala_binary"), aliases)
    Module(c._1, c._2, c._3, r("version"), r("version_scheme"))
  }
  def readDeps(file: File): List[Dep] = rows(file).map(r => Dep(r("from_org"), r("from_name"), r("from_scala_binary"), r("from_version"), r("dep_scope"), r("to_org"), r("to_name"), r("to_scala_binary"), r("version"), r("classifier"), r("platform")))
  def readRequests(file: File): List[Request] = rows(file).map(r => Request(r("org"), r("name"), r("scala_binary"), r("version"), r("scope"), r("classifier"), r("platform"), r("policy")))
  def readConstraints(file: File): List[Constraint] = rows(file).map { r =>
    Constraint(r("org"), r("name"), r("scala_binary"), r("classifier"), r("platform"), r("min_version"), r("max_version"), r("include_min") != "false", r("include_max") == "true")
  }
  def readExclusions(file: File): List[Exclusion] = rows(file).map { r =>
    Exclusion(r("from_org"), r("from_name"), r("from_scala_binary"), r("from_version"), r("to_org"), r("to_name"), r("to_scala_binary"), r("version"), r("classifier"), r("platform"))
  }

  def resolve(modules: List[Module], deps: List[Dep], constraints: List[Constraint], exclusions: List[Exclusion], requests: List[Request]): (Map[Key, mutable.Set[(String, String)]], Map[Key, Set[String]], Map[Key, String]) = {
    val validScopes = Set("compile", "runtime", "plugin", "bootstrap")
    deps.foreach(d => if (!validScopes.contains(d.depScope)) sys.error("bad dependency scope"))
    requests.foreach(r => if (!validScopes.contains(r.scope)) sys.error("bad request scope"))
    val byModule = modules.map(m => (m.org, m.name, m.scalaBinary, m.version) -> m).toMap
    val byFrom = deps.groupBy(d => (d.fromOrg, d.fromName, d.fromScala, d.fromVersion))
    val seen = mutable.Set[(String, String, String, String, String, String)]()
    val versions = mutable.Map[Key, mutable.Set[(String, String)]]()
    val scopes = mutable.Map[Key, mutable.Set[String]]()
    val forced = mutable.Map[Key, String]()
    val denied = mutable.Set[(Key, String)]()
    val rules = constraints.groupBy(c => Key(c.org, c.name, c.scalaBinary, c.classifier, c.platform))

    def inRange(c: Constraint, v: String): Boolean = {
      val minOk = c.minVersion == "" || (if (c.includeMin) v >= c.minVersion else v > c.minVersion)
      val maxOk = c.maxVersion == "" || (if (c.includeMax) v <= c.maxVersion else v < c.maxVersion)
      minOk && maxOk
    }
    def allowed(k: Key, v: String): Boolean = !denied.contains(k -> v) && rules.getOrElse(k, Nil).forall(inRange(_, v))

    def visit(org: String, name: String, scalaBinary: String, version: String, classifier: String, platform: String, rootScope: String, activeScope: String): Unit = {
      val m = byModule.getOrElse((org, name, scalaBinary, version), sys.error("unknown component " + org + ":" + name + ":" + scalaBinary + ":" + version))
      val key = Key(org, name, scalaBinary, classifier, platform)
      if (!allowed(key, version)) return
      versions.getOrElseUpdate(key, mutable.Set()) += version -> m.scheme
      scopes.getOrElseUpdate(key, mutable.Set()) += rootScope
      if (!seen.add((org, name, scalaBinary, version, rootScope, activeScope))) return
      byFrom.getOrElse((org, name, scalaBinary, version), Nil).foreach { d =>
        if (d.depScope == "compile" || (d.depScope == "runtime" && activeScope == "runtime") || ((d.depScope == "plugin" || d.depScope == "bootstrap") && activeScope == "plugin")) {
          val next = if (d.depScope == "runtime") "runtime" else activeScope
          visit(d.toOrg, d.toName, d.toScala, d.version, d.classifier, d.platform, rootScope, next)
        }
      }
    }
    requests.foreach { r =>
      val key = Key(r.org, r.name, r.scalaBinary, r.classifier, r.platform)
      r.policy match {
        case "force" => forced(key) = r.version
        case "deny" => denied += ((key, r.version))
        case "" =>
        case _ => sys.error("bad policy")
      }
    }
    requests.filter(_.policy != "deny").foreach(r => visit(r.org, r.name, r.scalaBinary, r.version, r.classifier, r.platform, r.scope, if (r.scope == "bootstrap") "bootstrap" else r.scope))
    (versions.toMap, scopes.map { case (k, v) => k -> v.toSet }.toMap, forced.toMap)
  }

  def versionParts(v: String): List[Int] = v.split("[.-]").toList.map(s => if (s.forall(_.isDigit) && s.nonEmpty) s.toInt else 0)
  def highest(vs: Iterable[String]): String = vs.toList.sortBy(versionParts).last
  def compatible(scheme: String, selected: String, evicted: String): Boolean = selected.take(1) == evicted.take(1)
  def esc(s: String): String = s.replace("\\", "\\\\").replace("\"", "\\\"")
  def q(s: String): String = "\"" + esc(s) + "\""

  def renderLock(plan: Map[Key, mutable.Set[(String, String)]], scopes: Map[Key, Set[String]]): String = {
    val entries = plan.toList.sortBy(_._1.toString).map { case (k, candidates) =>
      val selected = highest(candidates.map(_._1))
      val scope = scopes.getOrElse(k, Set.empty).toList.sorted.mkString(",")
      "{" + List("org" -> k.org, "name" -> k.name, "scala_binary" -> k.scalaBinary, "classifier" -> k.classifier, "platform" -> k.platform, "version" -> selected, "scope" -> scope).map { case (a, b) => q(a) + ":" + q(b) }.mkString(",") + "}"
    }
    "{\"schema\":\"sbt-eviction-plan/v1\",\"artifacts\":[" + entries.mkString(",") + "]}\n"
  }

  def renderAudit(plan: Map[Key, mutable.Set[(String, String)]], forced: Map[Key, String]): String = {
    val evs = plan.toList.flatMap { case (k, candidates) =>
      val selected = forced.getOrElse(k, highest(candidates.map(_._1)))
      val scheme = candidates.find(_._1 == selected).map(_._2).getOrElse("always")
      candidates.map(_._1).filter(_ != selected).toList.map { evicted =>
        val c = compatible(scheme, selected, evicted)
        "{" + List("org" -> k.org, "name" -> k.name, "scala_binary" -> k.scalaBinary, "classifier" -> k.classifier, "platform" -> k.platform, "selected" -> selected, "evicted" -> evicted, "compatible" -> c.toString, "scheme" -> scheme).map { case (a, b) => q(a) + ":" + (if (a == "compatible") b else q(b)) }.mkString(",") + "}"
      }
    }
    val status = if (evs.exists(_.contains("false"))) "conflict" else "ok"
    "{\"schema\":\"sbt-eviction-audit/v1\",\"status\":" + q(status) + ",\"evictions\":[" + evs.mkString(",") + "]}\n"
  }

  def write(file: File, text: String): Unit = {
    file.getParentFile.mkdirs()
    val out = new PrintWriter(file)
    try out.print(text) finally out.close()
  }
}
