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
      0x000b => ["sha256", 32]
    }

    def self.parse(path)
      bytes = File.binread(path)
      cursor = 0
      ordinal = 0
      events = []
      while cursor < bytes.bytesize
        layout = bytes.getbyte(cursor)
        cursor += 1
        pcr = bytes[cursor, 4].unpack1("L<")
        cursor += 4
        type = bytes[cursor, 4].unpack1("L<")
        cursor += 4
        digests = {}
        if layout == 1
          digests["sha1"] = bytes[cursor, 20]
          cursor += 20
          data_len = bytes[cursor, 4].unpack1("L>")
          cursor += 4
        elsif layout == 2
          count = bytes[cursor, 4].unpack1("L<")
          cursor += 4
          count.times do
            alg = bytes[cursor, 2].unpack1("S<")
            cursor += 2
            name, len = ALG.fetch(alg)
            digests[name] = bytes[cursor, len]
            cursor += len
          end
          data_len = bytes[cursor, 4].unpack1("L<")
          cursor += 4
        else
          raise "bad layout"
        end
        payload = bytes[cursor, data_len]
        cursor += data_len
        events << Event.new(ordinal, layout, pcr, type, digests, payload)
        ordinal += 1
      end
      events
    end
  end
end
