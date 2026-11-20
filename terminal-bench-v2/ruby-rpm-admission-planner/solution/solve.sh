#!/bin/bash
set -euo pipefail

cat > /app/rpmgate/lib/rpm_admit/planner.rb <<'RUBY'
require 'json'
require 'digest'
require 'fileutils'

module RpmAdmit
  Requirement = Struct.new(:cap, :op, :evr, :raw, keyword_init: true)

  class Package
    attr_reader :data

    def initialize(data)
      @data = data
    end

    def name = data.fetch('name')
    def epoch = data.fetch('epoch', 0).to_i
    def version = data.fetch('version')
    def release = data.fetch('release', '')
    def arch = data.fetch('arch')
    def requires = data.fetch('requires', [])
    def provides = data.fetch('provides', [])
    def obsoletes = data.fetch('obsoletes', [])
    def conflicts = data.fetch('conflicts', [])
    def mod = data['module']
    def evr = { 'epoch' => epoch, 'version' => version, 'release' => release }
    def evr_text = "#{epoch}:#{version}-#{release}"
    def nevra = "#{name}-#{epoch}:#{version}-#{release}.#{arch}"

    def capability_strings
      ["#{name} = #{evr_text}"] + provides
    end
  end

  module EVR
    module_function

    def parse(text)
      rest = text.to_s.strip
      epoch = 0
      if rest.include?(':')
        head, tail = rest.split(':', 2)
        epoch = head.to_i
        rest = tail
      end
      version, release = rest.split('-', 2)
      { 'epoch' => epoch, 'version' => version.to_s, 'release' => release.to_s }
    end

    def compare(a, b)
      left = normalize(a)
      right = normalize(b)
      epoch_cmp = left['epoch'].to_i <=> right['epoch'].to_i
      return epoch_cmp unless epoch_cmp.zero?
      version_cmp = compare_part(left['version'].to_s, right['version'].to_s)
      return version_cmp unless version_cmp.zero?
      compare_part(left['release'].to_s, right['release'].to_s)
    end

    def normalize(value)
      value.is_a?(Hash) ? { 'epoch' => value.fetch('epoch', 0).to_i, 'version' => value.fetch('version', '').to_s, 'release' => value.fetch('release', '').to_s } : parse(value)
    end

    def compare_part(a, b)
      aa = tokens(a)
      bb = tokens(b)
      max = [aa.length, bb.length].max
      (0...max).each do |i|
        return -1 if i >= aa.length
        return 1 if i >= bb.length
        cmp = compare_token(aa[i], bb[i])
        return cmp unless cmp.zero?
      end
      0
    end

    def tokens(text)
      text.scan(/[A-Za-z]+|\d+/)
    end

    def compare_token(a, b)
      an = a.match?(/\A\d+\z/)
      bn = b.match?(/\A\d+\z/)
      return 1 if an && !bn
      return -1 if !an && bn
      if an && bn
        ax = a.sub(/\A0+/, '')
        bx = b.sub(/\A0+/, '')
        ax = '0' if ax.empty?
        bx = '0' if bx.empty?
        len = ax.length <=> bx.length
        return len unless len.zero?
        return ax <=> bx
      end
      a <=> b
    end
  end

  module Parser
    module_function

    OPS = ['>=', '<=', '=', '>', '<'].freeze

    def requirement(text)
      raw = text.to_s.strip
      OPS.each do |op|
        token = " #{op} "
        next unless raw.include?(token)
        cap, evr = raw.split(token, 2)
        return Requirement.new(cap: cap.strip, op: op, evr: EVR.parse(evr.strip), raw: raw)
      end
      Requirement.new(cap: raw, op: nil, evr: nil, raw: raw)
    end
  end

  class Planner
    def initialize(repo, request)
      @repo = repo
      @request = request
      @packages = collapse_duplicate_nevras(repo.fetch('packages', []).map { |p| Package.new(p) })
      @decisions = []
      @selected = {}
    end

    def resolve
      queue = @request.fetch('install', []).map { |r| [Parser.requirement(r), 'requested'] }
      @failure_requirement = queue.first&.first&.raw || 'request'
      solved = resolve_state(queue.dup, {}, [])
      raise "unsatisfied #{@failure_requirement}" unless solved
      @selected, @decisions = solved
      lock
    end

    def resolve_state(queue, selected, decisions)
      return [selected, decisions] if queue.empty?

      req, reason = queue.shift
      @failure_requirement = req.raw
      candidates(req).each do |pkg, via, _evr|
        key = pkg.nevra
        if selected[key]
          solved = resolve_state(queue.dup, selected, decisions)
          return solved if solved
          next
        end
        next unless compatible_with_closure?(pkg, selected.values)

        next_selected = selected.merge(key => pkg)
        next_decisions = decisions + [{ 'requirement' => req.raw, 'selected' => pkg.nevra, 'via' => via, 'reason' => reason }]
        next_queue = queue.dup + pkg.requires.map { |r| [Parser.requirement(r), 'dependency'] }
        solved = resolve_state(next_queue, next_selected, next_decisions)
        return solved if solved
      end
      nil
    end

    def collapse_duplicate_nevras(packages)
      packages.group_by(&:nevra).values.flat_map do |rows|
        signatures = rows.map { |pkg| canonical(pkg.data) }.uniq
        signatures.length == 1 ? [rows.first] : []
      end
    end

    def canonical(value)
      case value
      when Hash
        value.keys.sort.each_with_object({}) { |key, out| out[key] = canonical(value[key]) }
      when Array
        value.map { |item| canonical(item) }
      else
        value
      end
    end

    def active_modules
      defaults = @repo.fetch('module_defaults', {})
      enabled = @request.fetch('enabled_streams', {})
      names = (defaults.keys + enabled.keys).uniq.sort
      names.map do |name|
        if enabled.key?(name)
          { 'name' => name, 'stream' => enabled[name], 'source' => 'enabled' }
        else
          { 'name' => name, 'stream' => defaults[name], 'source' => 'default' }
        end
      end
    end

    def visible
      active = active_modules.each_with_object({}) { |m, h| h[m['name']] = m['stream'] }
      @packages.select do |pkg|
        m = pkg.mod
        !m || active[m['name']] == m['stream']
      end
    end

    def allowed_arch?(pkg, req)
      native = @request.fetch('arch')
      return true if pkg.arch == native || pkg.arch == 'noarch'
      native == 'x86_64' && @request.fetch('multilib', false) && pkg.arch == 'i686' && req.cap.end_with?('()(32bit)')
    end

    def select(req)
      candidates(req).first
    end

    def candidates(req)
      matches = []
      visible.each do |pkg|
        next unless allowed_arch?(pkg, req)
        pkg.capability_strings.each do |cap|
          cap_req = Parser.requirement(cap)
          next unless cap_req.cap == req.cap
          next if req.op && !cap_req.evr
          next if req.op && !satisfies?(cap_req.evr, req.op, req.evr)
          matches << [pkg, pkg.name == req.cap ? 'name' : 'provide', cap_req.evr || pkg.evr]
        end
      end
      matches = remove_obsoleted(matches, req)
      matches.sort { |a, b| compare_candidates(b, a) }
    end

    def remove_obsoleted(matches, _req)
      matches.reject do |candidate, _, _|
        matches.any? do |other, _, _|
          next false if other.nevra == candidate.nevra
          other.obsoletes.any? do |obs|
            obs_req = Parser.requirement(obs)
            obs_req.cap == candidate.name && (!obs_req.op || satisfies?(candidate.evr, obs_req.op, obs_req.evr))
          end
        end
      end
    end

    def compare_candidates(a, b)
      pkg_cmp = EVR.compare(a[0].evr, b[0].evr)
      return pkg_cmp unless pkg_cmp.zero?
      arch_cmp = arch_rank(b[0].arch) <=> arch_rank(a[0].arch)
      return arch_cmp unless arch_cmp.zero?
      a[0].nevra <=> b[0].nevra
    end

    def compatible_with_closure?(pkg, selected)
      selected.none? { |other| conflicts_between?(pkg, other) }
    end

    def conflicts_between?(a, b)
      a.conflicts.any? { |raw| package_satisfies_requirement?(b, Parser.requirement(raw)) } ||
        b.conflicts.any? { |raw| package_satisfies_requirement?(a, Parser.requirement(raw)) }
    end

    def package_satisfies_requirement?(pkg, req)
      pkg.capability_strings.any? do |cap|
        cap_req = Parser.requirement(cap)
        next false unless cap_req.cap == req.cap
        next true unless req.op
        cap_req.evr && satisfies?(cap_req.evr, req.op, req.evr)
      end
    end

    def arch_rank(arch)
      native = @request.fetch('arch')
      return 0 if arch == native
      return 1 if arch == 'noarch'
      2
    end

    def satisfies?(have, op, need)
      c = EVR.compare(have, need)
      case op
      when '=' then c == 0
      when '>' then c > 0
      when '>=' then c >= 0
      when '<' then c < 0
      when '<=' then c <= 0
      else true
      end
    end

    def lock
      pkgs = @selected.values.sort_by { |p| [p.name, p.arch, p.nevra] }
      {
        'arch' => @request.fetch('arch'),
        'modules' => active_modules,
        'packages' => pkgs.map { |p| row(p) },
        'checksum' => Digest::SHA256.hexdigest(pkgs.map(&:nevra).sort.join("\n"))
      }
    end

    def row(pkg)
      reasons = @decisions.select { |d| d['selected'] == pkg.nevra }.map { |d| d['reason'] }
      { 'name' => pkg.name, 'epoch' => pkg.epoch, 'version' => pkg.version, 'release' => pkg.release, 'arch' => pkg.arch, 'nevra' => pkg.nevra, 'reason' => reasons.include?('requested') ? 'requested' : 'dependency' }
    end

    def audit(status, rejected = [])
      { 'status' => status, 'requested' => @request.fetch('install', []), 'decisions' => @decisions.sort_by { |d| [d['requirement'], d['selected']] }, 'rejected' => rejected }
    end
  end

  module CLI
    module_function

    def run(argv)
      args = {}
      argv.each_slice(2) { |k, v| args[k] = v }
      repo = JSON.parse(File.read(args.fetch('--repo')))
      request = JSON.parse(File.read(args.fetch('--request')))
      planner = Planner.new(repo, request)
      begin
        lock = planner.resolve
        write_json(args.fetch('--lock'), lock)
        write_json(args.fetch('--audit'), planner.audit('admitted'))
        exit 0
      rescue StandardError => e
        FileUtils.rm_f(args['--lock']) if args['--lock']
        write_json(args.fetch('--audit'), planner.audit('rejected', [{ 'requirement' => e.message.sub(/^unsatisfied /, ''), 'reason' => 'unsatisfied' }]))
        exit 1
      end
    end

    def write_json(path, object)
      dir = File.dirname(path)
      FileUtils.mkdir_p(dir) unless dir == '.'
      File.write(path, JSON.generate(object) + "\n")
    end
  end
end
RUBY

/app/rpmgate/bin/build.sh
