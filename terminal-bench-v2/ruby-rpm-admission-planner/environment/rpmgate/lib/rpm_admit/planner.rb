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
        head, rest = rest.split(':', 2)
        epoch = head.to_i
      end
      version, release = rest.split('-', 2)
      { 'epoch' => epoch, 'version' => version.to_s, 'release' => release.to_s }
    end

    def compare(a, b)
      left = normalize(a)
      right = normalize(b)
      epoch_cmp = left['epoch'].to_i <=> right['epoch'].to_i
      return epoch_cmp unless epoch_cmp.zero?
      version_cmp = compare_part(left['version'], right['version'])
      return version_cmp unless version_cmp.zero?
      compare_part(left['release'], right['release'])
    end

    def normalize(value)
      value.is_a?(Hash) ? { 'epoch' => value.fetch('epoch', 0).to_i, 'version' => value.fetch('version', '').to_s, 'release' => value.fetch('release', '').to_s } : parse(value)
    end

    def compare_part(a, b)
      aa = a.to_s.scan(/[A-Za-z]+|\d+/)
      bb = b.to_s.scan(/[A-Za-z]+|\d+/)
      max = [aa.length, bb.length].max
      (0...max).each do |i|
        return -1 if i >= aa.length
        return 1 if i >= bb.length
        left = aa[i]
        right = bb[i]
        ln = left.match?(/\A\d+\z/)
        rn = right.match?(/\A\d+\z/)
        return 1 if ln && !rn
        return -1 if !ln && rn
        cmp = ln ? (left.to_i <=> right.to_i) : (left <=> right)
        return cmp unless cmp.zero?
      end
      0
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
      @packages = repo.fetch('packages', []).map { |row| Package.new(row) }
      @decisions = []
      @selected = {}
    end

    def resolve
      @request.fetch('install', []).each do |raw|
        require_package(Parser.requirement(raw), 'requested')
      end
      lock
    end

    def require_package(req, reason)
      pkg, via, = candidates(req).first
      raise "unsatisfied #{req.raw}" unless pkg
      return if @selected.key?(pkg.nevra)

      @selected[pkg.nevra] = pkg
      @decisions << { 'requirement' => req.raw, 'selected' => pkg.nevra, 'via' => via, 'reason' => reason }
      pkg.requires.each { |raw| require_package(Parser.requirement(raw), 'dependency') }
      if @selected.values.any? { |other| other.nevra != pkg.nevra && conflicts_between?(pkg, other) }
        raise "unsatisfied #{req.raw}"
      end
    end

    def active_modules
      defaults = @repo.fetch('module_defaults', {})
      enabled = @request.fetch('enabled_streams', {})
      (defaults.keys + enabled.keys).uniq.sort.map do |name|
        if enabled.key?(name)
          { 'name' => name, 'stream' => enabled[name], 'source' => 'enabled' }
        else
          { 'name' => name, 'stream' => defaults[name], 'source' => 'default' }
        end
      end
    end

    def visible
      active = active_modules.each_with_object({}) { |entry, out| out[entry['name']] = entry['stream'] }
      @packages.select do |pkg|
        mod = pkg.mod
        !mod || active[mod['name']] == mod['stream']
      end
    end

    def candidates(req)
      rows = []
      visible.each do |pkg|
        next unless allowed_arch?(pkg, req)
        pkg.capability_strings.each do |cap|
          cap_req = Parser.requirement(cap)
          next unless cap_req.cap == req.cap
          next if req.op && (!cap_req.evr || !satisfies?(cap_req.evr, req.op, req.evr))
          rows << [pkg, pkg.name == req.cap ? 'name' : 'provide', cap_req.evr || pkg.evr]
        end
      end
      rows = remove_obsoleted(rows)
      rows.sort { |a, b| compare_candidates(b, a) }
    end

    def allowed_arch?(pkg, req)
      native = @request.fetch('arch')
      return true if pkg.arch == native || pkg.arch == 'noarch'
      native == 'x86_64' && @request.fetch('multilib', false) && pkg.arch == 'i686' && req.cap.end_with?('()(32bit)')
    end

    def remove_obsoleted(rows)
      rows.reject do |candidate, _, _|
        rows.any? do |other, _, _|
          next false if other.nevra == candidate.nevra
          other.obsoletes.any? do |raw|
            obs = Parser.requirement(raw)
            obs.cap == candidate.name && (!obs.op || satisfies?(candidate.evr, obs.op, obs.evr))
          end
        end
      end
    end

    def compare_candidates(a, b)
      cmp = EVR.compare(a[0].evr, b[0].evr)
      return cmp unless cmp.zero?
      arch_cmp = arch_rank(b[0].arch) <=> arch_rank(a[0].arch)
      return arch_cmp unless arch_cmp.zero?
      a[0].nevra <=> b[0].nevra
    end

    def arch_rank(arch)
      native = @request.fetch('arch')
      return 0 if arch == native
      return 1 if arch == 'noarch'
      2
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

    def satisfies?(have, op, need)
      cmp = EVR.compare(have, need)
      case op
      when '=' then cmp.zero?
      when '>' then cmp.positive?
      when '>=' then cmp >= 0
      when '<' then cmp.negative?
      when '<=' then cmp <= 0
      else true
      end
    end

    def lock
      packages = @selected.values.sort_by { |pkg| [pkg.name, pkg.arch, pkg.nevra] }
      {
        'arch' => @request.fetch('arch'),
        'modules' => active_modules,
        'packages' => packages.map { |pkg| package_row(pkg) },
        'checksum' => Digest::SHA256.hexdigest(packages.map(&:nevra).sort.join("\n"))
      }
    end

    def package_row(pkg)
      reasons = @decisions.select { |decision| decision['selected'] == pkg.nevra }.map { |decision| decision['reason'] }
      {
        'name' => pkg.name,
        'epoch' => pkg.epoch,
        'version' => pkg.version,
        'release' => pkg.release,
        'arch' => pkg.arch,
        'nevra' => pkg.nevra,
        'reason' => reasons.include?('requested') ? 'requested' : 'dependency'
      }
    end

    def audit(status, decisions: @decisions, rejected: [])
      {
        'status' => status,
        'requested' => @request.fetch('install', []),
        'decisions' => decisions.sort_by { |decision| [decision['requirement'], decision['selected']] },
        'rejected' => rejected
      }
    end
  end

  module CLI
    module_function

    def run(argv)
      args = parse_args(argv)
      repo = JSON.parse(File.read(args.fetch('--repo')))
      request = JSON.parse(File.read(args.fetch('--request')))
      planner = Planner.new(repo, request)
      lock = planner.resolve
      write_json(args.fetch('--lock'), lock)
      write_json(args.fetch('--audit'), planner.audit('admitted'))
      exit 0
    rescue StandardError => e
      FileUtils.rm_f(args['--lock']) if args && args['--lock']
      if defined?(planner) && args && args['--audit']
        requirement = e.message.sub(/\Aunsatisfied\s*/, '')
        requirement = request.fetch('install', []).first.to_s if requirement.empty? && defined?(request)
        write_json(args.fetch('--audit'), planner.audit('rejected', rejected: [{ 'requirement' => requirement, 'reason' => 'unsatisfied' }]))
      end
      exit 1
    end

    def parse_args(argv)
      argv.each_slice(2).to_h
    end

    def write_json(path, object)
      dir = File.dirname(path)
      FileUtils.mkdir_p(dir) unless dir == '.'
      File.write(path, JSON.generate(object) + "\n")
    end
  end
end
