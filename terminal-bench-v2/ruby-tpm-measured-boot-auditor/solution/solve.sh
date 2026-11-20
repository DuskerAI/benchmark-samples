#!/bin/bash
set -euo pipefail

cat > /opt/measured_boot_auditor/lib/mb/event_log.rb <<'RUBY'
module MB
  class Event
    attr_reader :ordinal, :layout, :pcr, :type, :digests, :payload

    def initialize(ordinal, layout, pcr, type, digests, payload)
      @ordinal = ordinal
      @layout = layout
      @pcr = pcr
      @type = type
      @digests = digests
      @payload = payload
    end
  end

  class EventLog
    ALG = {
      0x0004 => ["sha1", 20],
      0x000b => ["sha256", 32],
      0x000c => ["sha384", 48]
    }

    def self.take(bytes, cursor, length)
      raise "truncated event log" if cursor + length > bytes.bytesize
      [bytes.byteslice(cursor, length), cursor + length]
    end

    def self.u16(bytes, cursor)
      raw, cursor = take(bytes, cursor, 2)
      [raw.unpack1("S<"), cursor]
    end

    def self.u32(bytes, cursor)
      raw, cursor = take(bytes, cursor, 4)
      [raw.unpack1("L<"), cursor]
    end

    def self.parse(path)
      bytes = File.binread(path)
      cursor = 0
      ordinal = 0
      events = []
      while cursor < bytes.bytesize
        layout = bytes.getbyte(cursor)
        cursor += 1
        pcr, cursor = u32(bytes, cursor)
        type, cursor = u32(bytes, cursor)
        digests = {}
        if layout == 1
          digest, cursor = take(bytes, cursor, 20)
          digests["sha1"] = digest
          data_len, cursor = u32(bytes, cursor)
        elsif layout == 2
          count, cursor = u32(bytes, cursor)
          count.times do
            alg, cursor = u16(bytes, cursor)
            name, len = ALG.fetch(alg) { raise "unsupported digest algorithm" }
            raise "duplicate digest algorithm" if digests.key?(name)
            value, cursor = take(bytes, cursor, len)
            digests[name] = value
          end
          data_len, cursor = u32(bytes, cursor)
        else
          raise "bad layout"
        end
        payload, cursor = take(bytes, cursor, data_len)
        events << Event.new(ordinal, layout, pcr, type, digests, payload)
        ordinal += 1
      end
      events
    end
  end
end
RUBY

cat > /opt/measured_boot_auditor/lib/mb/variables.rb <<'RUBY'
module MB
  class Variables
    def initialize
      @items = {}
    end

    def add(payload)
      raise "truncated variable payload" if payload.nil? || payload.bytesize < 22
      guid = payload.byteslice(0, 16).unpack1("H*").downcase
      name_len = payload.byteslice(16, 2).unpack1("S<")
      data_len = payload.byteslice(18, 4).unpack1("L<")
      raise "odd UTF-16LE name length" if name_len.odd?
      raise "variable length mismatch" unless payload.bytesize == 22 + name_len + data_len
      raw_name = payload.byteslice(22, name_len)
      name = raw_name.force_encoding("UTF-16LE").encode("UTF-8").downcase
      data = payload.byteslice(22 + name_len, data_len)
      @items[[guid, name]] = data
    end

    def find(name)
      needle = name.downcase
      pair = @items.find { |key, _| key[1] == needle }
      pair && pair[1]
    end
  end
end
RUBY

cat > /opt/measured_boot_auditor/lib/mb/auditor.rb <<'RUBY'
require "digest"
require "json"
require_relative "event_log"
require_relative "variables"

module MB
  class Auditor
    SEP = 0x00000004
    VAR_TYPES = [0x80000002, 0x800000e0]
    SIZE = {"sha1" => 20, "sha256" => 32, "sha384" => 48}

    def initialize(case_dir, report_path)
      @case_dir = case_dir
      @report_path = report_path
    end

    def run
      policy = JSON.parse(File.read(File.join(@case_dir, "policy.json")))
      banks = policy.fetch("banks")
      banks.each { |bank| SIZE.fetch(bank) }
      events = EventLog.parse(File.join(@case_dir, "eventlog.bin"))
      pcrs = banks.to_h { |bank| [bank, {}] }
      variables = Variables.new
      violations = []

      events.each do |event|
        event.digests.each do |bank, logged|
          next unless banks.include?(bank)
          if digest(bank, event.payload) != logged
            violations << {"code" => "digest-mismatch", "event" => event.ordinal, "bank" => bank}
          end
        end

        if event.type == SEP && !["\x00\x00\x00\x00".b, "\xff\xff\xff\xff".b].include?(event.payload)
          violations << {"code" => "separator", "event" => event.ordinal, "pcr" => event.pcr}
        end

        variables.add(event.payload) if VAR_TYPES.include?(event.type)

        banks.each do |bank|
          logged = event.digests[bank]
          next unless logged
          previous = pcrs[bank][event.pcr] || "\x00".b * SIZE.fetch(bank)
          pcrs[bank][event.pcr] = digest(bank, previous + logged)
        end
      end

      if policy.key?("secure_boot")
        secure = variables.find("SecureBoot")
        if secure.nil? || secure.unpack1("H*") != policy["secure_boot"].downcase
          violations << {"code" => "secure-boot", "name" => "SecureBoot"}
        end
      end

      forbidden = Array(policy["forbidden_authorities"]).map(&:downcase).uniq
      required = Array(policy["required_authorities"]).map(&:downcase).uniq
      db_hashes = authority_hashes(variables.find("db"))
      dbx_hashes = authority_hashes(variables.find("dbx"))

      if forbidden.any? { |item| db_hashes.include?(item) }
        violations << {"code" => "forbidden-authority", "name" => "db"}
      end

      required.sort.each do |item|
        unless db_hashes.include?(item)
          violations << {"code" => "required-authority", "name" => "db"}
        end
      end

      if required.any? { |item| dbx_hashes.include?(item) }
        violations << {"code" => "revoked-authority", "name" => "dbx"}
      end

      Array(policy["required_events"]).each do |want|
        found = events.any? do |event|
          event.pcr == want.fetch("pcr") &&
            event.type == want.fetch("event_type") &&
            event.digests.key?(want.fetch("bank")) &&
            Digest::SHA256.hexdigest(event.payload) == want.fetch("payload_sha256").downcase
        end
        unless found
          violations << {
            "code" => "missing-event",
            "pcr" => want.fetch("pcr"),
            "event_type" => want.fetch("event_type"),
            "bank" => want.fetch("bank"),
            "payload_sha256" => want.fetch("payload_sha256").downcase
          }
        end
      end

      policy.fetch("expected_pcrs", {}).each do |bank, values|
        values.sort_by { |pcr, _| pcr.to_i }.each do |pcr, expected|
          got = pcrs.fetch(bank, {})[pcr.to_i]
          if got.nil? || got.unpack1("H*") != expected.downcase
            violations << {"code" => "pcr-mismatch", "bank" => bank, "pcr" => pcr.to_i}
          end
        end
      end

      rendered = {}
      pcrs.each do |bank, values|
        next if values.empty?
        rendered[bank] = values.sort_by { |pcr, _| pcr }.to_h { |pcr, value| [pcr.to_s, value.unpack1("H*")] }
      end
      File.write(@report_path, JSON.generate({"ok" => violations.empty?, "pcrs" => rendered, "violations" => violations}) + "\n")
    end

    def digest(bank, bytes)
      case bank
      when "sha1" then Digest::SHA1.digest(bytes)
      when "sha256" then Digest::SHA256.digest(bytes)
      when "sha384" then Digest::SHA384.digest(bytes)
      else raise "unsupported bank"
      end
    end

    def authority_hashes(bytes)
      return [] if bytes.nil?
      hashes = []
      cursor = 0
      while cursor < bytes.bytesize
        raise "truncated signature list header" if cursor + 28 > bytes.bytesize
        _type_guid = bytes.byteslice(cursor, 16)
        list_size = bytes.byteslice(cursor + 16, 4).unpack1("L<")
        header_size = bytes.byteslice(cursor + 20, 4).unpack1("L<")
        signature_size = bytes.byteslice(cursor + 24, 4).unpack1("L<")
        raise "bad signature list size" if list_size < 28 + header_size
        raise "bad signature size" if signature_size <= 16
        list_end = cursor + list_size
        raise "truncated signature list" if list_end > bytes.bytesize
        sig_cursor = cursor + 28 + header_size
        raise "unaligned signature list" unless (list_end - sig_cursor) % signature_size == 0
        while sig_cursor < list_end
          authority = bytes.byteslice(sig_cursor + 16, signature_size - 16)
          hashes << Digest::SHA256.hexdigest(authority)
          sig_cursor += signature_size
        end
        cursor = list_end
      end
      hashes
    end
  end
end
RUBY

chmod +x /opt/measured_boot_auditor/bin/boot_audit
