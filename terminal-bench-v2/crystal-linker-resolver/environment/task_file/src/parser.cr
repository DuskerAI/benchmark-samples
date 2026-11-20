require "./model"

# Parser for the line-oriented object (.cro) and archive (.cra) text format.
#
# .cro grammar (one record per line, fields separated by single spaces):
#   OBJECT <id>
#   SECTION <name> <comdat-key|-> <size> <hexbytes|->
#   SYMBOL <name> <STRONG|WEAK> <DEF <section> <offset> | UNDEF>
#   SYMBOL <name> COMMON <size> <alignment>
#   RELOC <section> <offset> <target> <REL32|ABS32|ABS16> <addend>
#
# .cra grammar:
#   ARCHIVE <id>
#   MEMBER <member-id>
#   ...object records for that member...
#   (members are separated by the next MEMBER line)

module Parser
  def self.parse_object(text : String, default_id : String) : ObjectFile
    obj = ObjectFile.new(default_id)
    parse_object_lines(obj, text.lines)
    obj
  end

  def self.parse_object_lines(obj : ObjectFile, lines : Array(String))
    lines.each do |raw|
      line = raw.strip
      next if line.empty? || line.starts_with?("#")
      fields = line.split(' ')
      case fields[0]
      when "OBJECT"
        obj.id = fields[1]
      when "SECTION"
        name = fields[1]
        comdat = fields[2] == "-" ? nil : fields[2]
        size = fields[3].to_i64
        data = decode_hex(fields[4], size)
        obj.sections << SectionRecord.new(name, comdat, size, data)
      when "SYMBOL"
        name = fields[1]
        if fields[2] == "COMMON"
          size = fields[3].to_i64
          alignment = fields[4].to_i64
          obj.symbols << SymRecord.new(
            name, Binding::Common, "", 0_i64, true, size, alignment
          )
        else
          binding = fields[2] == "WEAK" ? Binding::Weak : Binding::Strong
          if fields[3] == "UNDEF"
            obj.symbols << SymRecord.new(name, binding, "", 0_i64, false)
          else
            section = fields[4]
            offset = fields[5].to_i64
            obj.symbols << SymRecord.new(name, binding, section, offset, true)
          end
        end
      when "RELOC"
        section = fields[1]
        offset = fields[2].to_i64
        target = fields[3]
        kind = fields[4]
        addend = fields[5].to_i64
        obj.relocs << RelocRecord.new(section, offset, target, kind, addend)
      else
        raise LinkError.new("unknown record: #{fields[0]}")
      end
    end
  end

  def self.decode_hex(field : String, size : Int64) : Bytes
    if field == "-"
      return Bytes.new(size)
    end
    bytes = Bytes.new(size)
    i = 0
    while i < size
      pair = field[i * 2, 2]
      bytes[i] = pair.to_u8(16)
      i += 1
    end
    bytes
  end

  def self.parse_archive(text : String, default_id : String) : Archive
    arc = Archive.new(default_id)
    current : ObjectFile? = nil
    buffer = [] of String
    flush = ->do
      if c = current
        parse_object_lines(c, buffer)
        arc.members << c
      end
      buffer = [] of String
    end
    text.lines.each do |raw|
      line = raw.strip
      next if line.empty? || line.starts_with?("#")
      fields = line.split(' ')
      case fields[0]
      when "ARCHIVE"
        arc.id = fields[1]
      when "MEMBER"
        flush.call
        current = ObjectFile.new(fields[1])
      else
        buffer << line
      end
    end
    flush.call
    arc
  end
end
