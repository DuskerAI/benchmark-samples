require "json"
require "option_parser"

class Dep
  property name : String
  property range : String

  def initialize(@name : String, @range : String)
  end
end

class Pkg
  property name : String
  property version : String
  property yanked : Bool
  property deps : Array(Dep)

  def initialize(@name : String, @version : String, @yanked : Bool, @deps : Array(Dep))
  end
end

def field_string(obj : JSON::Any, key : String, fallback : String = "") : String
  if value = obj[key]?
    value.as_s
  else
    fallback
  end
end

def field_bool(obj : JSON::Any, key : String, fallback : Bool = false) : Bool
  if value = obj[key]?
    value.as_bool
  else
    fallback
  end
end

def parse_dep(obj : JSON::Any) : Dep
  Dep.new(field_string(obj, "name"), field_string(obj, "range", "*"))
end

def parse_packages(path : String) : Array(Pkg)
  root = JSON.parse(File.read(path))
  packages = [] of Pkg
  root["packages"].as_a.each do |node|
    deps = [] of Dep
    if dep_nodes = node["deps"]?
      dep_nodes.as_a.each { |d| deps << parse_dep(d) }
    end
    packages << Pkg.new(field_string(node, "name"), field_string(node, "version"), field_bool(node, "yanked"), deps)
  end
  packages
end

def version_parts(v : String) : Array(Int32)
  main = v.split('-', 2)[0]
  parts = main.split('.').map { |p| p.to_i? || 0 }
  while parts.size < 3
    parts << 0
  end
  parts
end

def compare_versions(a : String, b : String) : Int32
  av = version_parts(a)
  bv = version_parts(b)
  3.times do |i|
    return av[i] <=> bv[i] unless av[i] == bv[i]
  end
  0
end

def matches_range?(version : String, range : String) : Bool
  text = range.strip
  return true if text.empty? || text == "*"
  text.split(',').all? do |raw|
    clause = raw.strip
    if clause.starts_with?(">=")
      compare_versions(version, clause[2..]) >= 0
    elsif clause.starts_with?("<=")
      compare_versions(version, clause[2..]) <= 0
    elsif clause.starts_with?(">")
      compare_versions(version, clause[1..]) > 0
    elsif clause.starts_with?("<")
      compare_versions(version, clause[1..]) < 0
    elsif clause.starts_with?("=")
      compare_versions(version, clause[1..]) == 0
    elsif clause.starts_with?("^")
      base = clause[1..]
      major = version_parts(base)[0]
      compare_versions(version, base) >= 0 && compare_versions(version, "#{major + 1}.0.0") < 0
    else
      compare_versions(version, clause) == 0
    end
  end
end

def resolve(name : String, range : String, packages : Array(Pkg), selected : Hash(String, Pkg)) : Bool
  if current = selected[name]?
    return matches_range?(current.version, range)
  end
  choices = packages.select { |p| p.name == name && !p.yanked && matches_range?(p.version, range) }
  choices.sort! { |a, b| compare_versions(b.version, a.version) }
  return false if choices.empty?
  choice = choices.first
  selected[name] = choice
  choice.deps.all? { |d| resolve(d.name, d.range, packages, selected) }
end

repo_path = nil.as(String?)
request_path = nil.as(String?)
lock_path = nil.as(String?)
plan_path = nil.as(String?)
audit_path = nil.as(String?)
OptionParser.parse do |parser|
  parser.on("--repo PATH", "repository") { |v| repo_path = v }
  parser.on("--request PATH", "request") { |v| request_path = v }
  parser.on("--lock PATH", "lock") { |v| lock_path = v }
  parser.on("--plan PATH", "plan") { |v| plan_path = v }
  parser.on("--audit PATH", "audit") { |v| audit_path = v }
end

packages = parse_packages(repo_path.not_nil!)
request = JSON.parse(File.read(request_path.not_nil!))
selected = {} of String => Pkg
ok = request["roots"].as_a.all? do |r|
  resolve(field_string(r, "name"), field_string(r, "range", "*"), packages, selected)
end

entries = selected.values.sort_by { |p| {p.name, p.version} }
File.open(plan_path.not_nil!, "w") do |io|
  JSON.build(io) do |json|
    json.object do
      json.field "status", ok ? "ok" : "unsat"
      json.field "selected" do
        json.array do
          if ok
            entries.each do |p|
              json.object do
                json.field "name", p.name
                json.field "version", p.version
              end
            end
          end
        end
      end
      json.field "changes" do
        json.array { }
      end
    end
  end
end
File.open(audit_path.not_nil!, "w") do |io|
  JSON.build(io) do |json|
    json.object do
      json.field "status", ok ? "ok" : "unsat"
      json.field "selected", ok ? entries.size : 0
      json.field "changes", 0
    end
  end
  io << "\n"
  if ok
    entries.each do |p|
      JSON.build(io) do |json|
        json.object do
          json.field "package", p.name
          json.field "version", p.version
        end
      end
      io << "\n"
    end
  end
end
