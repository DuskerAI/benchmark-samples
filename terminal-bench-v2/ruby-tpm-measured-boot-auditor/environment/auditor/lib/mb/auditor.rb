require "digest"
require "json"
require_relative "event_log"
require_relative "variables"

module MB
  class Auditor
    SEP = 0x00000004
    VAR_TYPES = [0x80000002, 0x800000e0]

    def initialize(case_dir, report_path)
      @case_dir = case_dir
      @report_path = report_path
    end

    def run
      policy = JSON.parse(File.read(File.join(@case_dir, "policy.json")))
      banks = policy.fetch("banks")
      events = EventLog.parse(File.join(@case_dir, "eventlog.bin"))
      pcrs = Hash.new { |h, k| h[k] = {} }
      vars = Variables.new
      violations = []

      events.each do |event|
        event.digests.each do |bank, digest|
          next unless banks.include?(bank)
          expected = hash(bank, event.payload)
          if expected != digest
            violations << {"code" => "digest-mismatch", "event" => event.ordinal, "bank" => bank}
          end
        end

        if VAR_TYPES.include?(event.type)
          vars.add(event.payload)
        end

        chosen_bank = banks.find { |bank| event.digests.key?(bank) }
        next unless chosen_bank
        digest = event.digests.values.first
        prev = pcrs[chosen_bank][event.pcr] || "\x00".b * digest_size(chosen_bank)
        pcrs[chosen_bank][event.pcr] = hash(chosen_bank, prev + digest.ljust(digest_size(chosen_bank), "\x00")[0, digest_size(chosen_bank)])
      end

      if policy.key?("secure_boot")
        secure = vars.find("SecureBoot")
        if secure.nil? || secure.unpack1("H*") != policy["secure_boot"].downcase
          violations << {"code" => "secure-boot", "name" => "SecureBoot"}
        end
      end

      Array(policy["forbidden_authorities"]).each do |forbidden|
        db = vars.find("db")
        if db && db.unpack1("H*") == forbidden.downcase
          violations << {"code" => "forbidden-authority", "name" => "db"}
        end
      end

      policy.fetch("expected_pcrs", {}).each do |bank, values|
        values.each do |pcr, expected|
          got = pcrs[bank][pcr.to_i]
          if got.nil? || got.unpack1("H*") != expected.downcase
            violations << {"code" => "pcr-mismatch", "bank" => bank, "pcr" => pcr.to_i}
          end
        end
      end

      out_pcrs = {}
      pcrs.each do |bank, vals|
        out_pcrs[bank] = vals.keys.sort.map { |p| [p.to_s, vals[p].unpack1("H*")] }.to_h
      end
      File.write(@report_path, JSON.generate({"ok" => violations.empty?, "pcrs" => out_pcrs, "violations" => violations}) + "\n")
    end

    def hash(bank, bytes)
      bank == "sha1" ? Digest::SHA1.digest(bytes) : Digest::SHA256.digest(bytes)
    end

    def digest_size(bank)
      bank == "sha1" ? 20 : 32
    end
  end
end
