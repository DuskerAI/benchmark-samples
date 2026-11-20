module MB
  class Variables
    def initialize
      @items = {}
    end

    def add(payload)
      return unless payload && payload.bytesize >= 22
      guid = payload[0, 16].unpack1("H*")
      name_len = payload[16, 2].unpack1("S<")
      data_len = payload[18, 4].unpack1("L<")
      name = payload[22, name_len].delete("\x00")
      data = payload[22 + name_len, data_len]
      @items[[guid, name]] ||= data
    end

    def find(name)
      pair = @items.find { |key, _| key[1] == name }
      pair && pair[1]
    end
  end
end
