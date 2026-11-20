#!/bin/bash
set -euo pipefail
cat > /app/plock/src/plock_audit.cr <<'CRYSTAL'
require "json"
require "option_parser"
require "set"

class Dep
  property name : String
  property range : String
  property optional : Bool
  property feature : String?

  def initialize(@name : String, @range : String, @optional : Bool = false, @feature : String? = nil)
  end
end

class Provide
  property name : String
  property version : String

  def initialize(@name : String, @version : String)
  end
end

class Pkg
  property name : String
  property version : String
  property yanked : Bool
  property provides : Array(Provide)
  property deps : Array(Dep)
  property conflicts : Array(Dep)

  def initialize(@name : String, @version : String, @yanked : Bool, @provides : Array(Provide), @deps : Array(Dep), @conflicts : Array(Dep))
  end
end

class LockEntry
  property name : String
  property version : String

  def initialize(@name : String, @version : String)
  end
end

class Change
  property name : String
  property kind : String
  property from : String?
  property to : String?

  def initialize(@name : String, @kind : String, @from : String?, @to : String?)
  end
end

class Score
  property removed : Int32
  property changed : Int32
  property added : Int32
  property key : Array(String)

  def initialize(@removed : Int32, @changed : Int32, @added : Int32, @key : Array(String))
  end

  def better_than?(other : Score) : Bool
    return @removed < other.removed if @removed != other.removed
    return @changed < other.changed if @changed != other.changed
    return @added < other.added if @added != other.added
    compare_string_arrays(@key, other.key) < 0
  end
end

def compare_string_arrays(left : Array(String), right : Array(String)) : Int32
  limit = {left.size, right.size}.min
  i = 0
  while i < limit
    c = left[i] <=> right[i]
    return c unless c == 0
    i += 1
  end
  left.size <=> right.size
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
  feature = nil.as(String?)
  if f = obj["feature"]?
    feature = f.as_s
  end
  Dep.new(field_string(obj, "name"), field_string(obj, "range", "*"), field_bool(obj, "optional"), feature)
end

def parse_provide(obj : JSON::Any) : Provide
  Provide.new(field_string(obj, "name"), field_string(obj, "version"))
end

def parse_packages(path : String) : Array(Pkg)
  root = JSON.parse(File.read(path))
  packages = [] of Pkg
  root["packages"].as_a.each do |node|
    provides = [] of Provide
    deps = [] of Dep
    conflicts = [] of Dep
    if nodes = node["provides"]?
      nodes.as_a.each { |p| provides << parse_provide(p) }
    end
    if nodes = node["deps"]?
      nodes.as_a.each { |d| deps << parse_dep(d) }
    end
    if nodes = node["conflicts"]?
      nodes.as_a.each { |d| conflicts << parse_dep(d) }
    end
    packages << Pkg.new(field_string(node, "name"), field_string(node, "version"), field_bool(node, "yanked"), provides, deps, conflicts)
  end
  packages
end

def parse_roots(path : String) : Tuple(Array(Dep), Hash(String, Set(String)))
  request = JSON.parse(File.read(path))
  roots = [] of Dep
  request["roots"].as_a.each { |r| roots << parse_dep(r) }
  features = Hash(String, Set(String)).new { |h, k| h[k] = Set(String).new }
  if node = request["features"]?
    node.as_h.each do |pkg, values|
      values.as_a.each { |v| features[pkg].add(v.as_s) }
    end
  end
  {roots, features}
end

def parse_locks(path : String) : Array(LockEntry)
  root = JSON.parse(File.read(path))
  locks = [] of LockEntry
  if nodes = root["locked"]?
    nodes.as_a.each { |n| locks << LockEntry.new(field_string(n, "name"), field_string(n, "version")) }
  end
  locks
end

def version_nums(v : String) : Array(Int32)
  main = v.split('-', 2)[0]
  nums = main.split('.').map { |p| p.to_i? || 0 }
  while nums.size < 3
    nums << 0
  end
  nums[0, 3]
end

def prerelease_parts(v : String) : Array(String)
  pieces = v.split('-', 2)
  return [] of String if pieces.size == 1
  pieces[1].split('.')
end

def compare_ident(a : String, b : String) : Int32
  ai = a.to_i?
  bi = b.to_i?
  if ai && bi
    return ai <=> bi
  elsif ai && !bi
    return -1
  elsif !ai && bi
    return 1
  else
    return a <=> b
  end
end

def compare_versions(a : String, b : String) : Int32
  av = version_nums(a)
  bv = version_nums(b)
  3.times do |i|
    c = av[i] <=> bv[i]
    return c unless c == 0
  end
  ap = prerelease_parts(a)
  bp = prerelease_parts(b)
  return 0 if ap.empty? && bp.empty?
  return 1 if ap.empty?
  return -1 if bp.empty?
  limit = {ap.size, bp.size}.min
  i = 0
  while i < limit
    c = compare_ident(ap[i], bp[i])
    return c unless c == 0
    i += 1
  end
  ap.size <=> bp.size
end

def caret_upper(base : String) : String
  nums = version_nums(base)
  if nums[0] != 0
    "#{nums[0] + 1}.0.0"
  elsif nums[1] != 0
    "0.#{nums[1] + 1}.0"
  else
    "0.0.#{nums[2] + 1}"
  end
end

def version_satisfies?(version : String, range : String) : Bool
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
      compare_versions(version, base) >= 0 && compare_versions(version, caret_upper(base)) < 0
    else
      compare_versions(version, clause) == 0
    end
  end
end

def provides_for(pkg : Pkg) : Array(Provide)
  list = [Provide.new(pkg.name, pkg.version)]
  pkg.provides.each { |p| list << p }
  list
end

def active_dep?(dep : Dep, pkg : Pkg, features : Hash(String, Set(String))) : Bool
  return true unless dep.optional
  return false if dep.feature.nil?
  features[pkg.name].includes?(dep.feature.not_nil!)
end

def selected_satisfies?(selection : Array(Pkg), name : String, range : String) : Bool
  selection.any? do |pkg|
    provides_for(pkg).any? { |p| p.name == name && version_satisfies?(p.version, range) }
  end
end

def valid_selection?(selection : Array(Pkg), roots : Array(Dep), features : Hash(String, Set(String))) : Bool
  roots.each do |root|
    return false unless selected_satisfies?(selection, root.name, root.range)
  end
  selection.each do |pkg|
    pkg.deps.each do |dep|
      next unless active_dep?(dep, pkg, features)
      return false unless selected_satisfies?(selection, dep.name, dep.range)
    end
  end
  selection.each do |pkg|
    pkg.conflicts.each do |conflict|
      return false if selected_satisfies?(selection, conflict.name, conflict.range)
    end
  end
  true
end

def build_selections(idx : Int32, names : Array(String), groups : Hash(String, Array(Pkg))) : Array(Array(Pkg))
  if idx == names.size
    return [ [] of Pkg ]
  end
  tail = build_selections(idx + 1, names, groups)
  result = [] of Array(Pkg)
  tail.each { |selection| result << selection }
  groups[names[idx]].each do |pkg|
    tail.each do |selection|
      result << ([pkg] + selection)
    end
  end
  result
end

def score_selection(selection : Array(Pkg), locks : Array(LockEntry)) : Score
  selected_by_name = {} of String => Pkg
  selection.each { |pkg| selected_by_name[pkg.name] = pkg }
  locked_by_name = {} of String => String
  locks.each { |lock| locked_by_name[lock.name] = lock.version }
  removed = 0
  changed = 0
  locked_by_name.each do |name, version|
    if pkg = selected_by_name[name]?
      changed += 1 if pkg.version != version
    else
      removed += 1
    end
  end
  added = selection.count { |pkg| !locked_by_name.has_key?(pkg.name) }
  key = selection.sort_by { |pkg| {pkg.name, pkg.version} }.map { |pkg| "#{pkg.name}@#{pkg.version}" }
  Score.new(removed, changed, added, key)
end

def compute_changes(selection : Array(Pkg), locks : Array(LockEntry)) : Array(Change)
  selected_by_name = {} of String => String
  selection.each { |pkg| selected_by_name[pkg.name] = pkg.version }
  locked_by_name = {} of String => String
  locks.each { |lock| locked_by_name[lock.name] = lock.version }
  changes = [] of Change
  locked_by_name.keys.sort.each do |name|
    old = locked_by_name[name]
    if !selected_by_name.has_key?(name)
      changes << Change.new(name, "removed", old, nil)
    elsif selected_by_name[name] != old
      changes << Change.new(name, "changed", old, selected_by_name[name])
    end
  end
  selected_by_name.keys.sort.each do |name|
    unless locked_by_name.has_key?(name)
      changes << Change.new(name, "added", nil, selected_by_name[name])
    end
  end
  changes
end

def write_plan(path : String, status : String, selection : Array(Pkg), changes : Array(Change))
  selected = selection.sort_by { |pkg| {pkg.name, pkg.version} }
  File.open(path, "w") do |io|
    JSON.build(io) do |json|
      json.object do
        json.field "status", status
        json.field "selected" do
          json.array do
            selected.each do |pkg|
              json.object do
                json.field "name", pkg.name
                json.field "version", pkg.version
              end
            end
          end
        end
        json.field "changes" do
          json.array do
            changes.each do |change|
              json.object do
                json.field "name", change.name
                json.field "kind", change.kind
                json.field "from", change.from
                json.field "to", change.to
              end
            end
          end
        end
      end
    end
  end
end

def write_audit(path : String, status : String, selection : Array(Pkg), changes : Array(Change))
  selected = selection.sort_by { |pkg| {pkg.name, pkg.version} }
  File.open(path, "w") do |io|
    JSON.build(io) do |json|
      json.object do
        json.field "status", status
        json.field "selected", selected.size
        json.field "changes", changes.size
      end
    end
    io << "\n"
    if status == "unsat"
      JSON.build(io) do |json|
        json.object do
          json.field "reason", "no valid plan"
        end
      end
      io << "\n"
      return
    end
    selected.each do |pkg|
      JSON.build(io) do |json|
        json.object do
          json.field "package", pkg.name
          json.field "version", pkg.version
        end
      end
      io << "\n"
    end
    changes.each do |change|
      JSON.build(io) do |json|
        json.object do
          json.field "change", change.kind
          json.field "name", change.name
          json.field "from", change.from
          json.field "to", change.to
        end
      end
      io << "\n"
    end
  end
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
roots, features = parse_roots(request_path.not_nil!)
locks = parse_locks(lock_path.not_nil!)
groups = Hash(String, Array(Pkg)).new { |h, k| h[k] = [] of Pkg }
packages.each { |pkg| groups[pkg.name] << pkg unless pkg.yanked }
groups.each_value { |values| values.sort_by! { |pkg| pkg.version } }
names = groups.keys.sort
best = nil.as(Array(Pkg)?)
best_score = nil.as(Score?)
build_selections(0, names, groups).each do |selection|
  if valid_selection?(selection, roots, features)
    current_score = score_selection(selection, locks)
    if best_score.nil? || current_score.better_than?(best_score.not_nil!)
      best = selection.dup
      best_score = current_score
    end
  end
end

if best.nil?
  empty_changes = [] of Change
  empty_selection = [] of Pkg
  write_plan(plan_path.not_nil!, "unsat", empty_selection, empty_changes)
  write_audit(audit_path.not_nil!, "unsat", empty_selection, empty_changes)
else
  selection = best.not_nil!
  changes = compute_changes(selection, locks)
  write_plan(plan_path.not_nil!, "ok", selection, changes)
  write_audit(audit_path.not_nil!, "ok", selection, changes)
end
CRYSTAL
chmod +x /app/plock/bin/build.sh
/app/plock/bin/build.sh
