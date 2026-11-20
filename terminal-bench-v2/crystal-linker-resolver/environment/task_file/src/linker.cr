require "./model"

# The linker driver: combines a set of standalone objects and archives into a
# single linked image, following the resolution, inclusion, layout and
# relocation rules described in spec.md.

# A resolved global symbol after the symbol-resolution pass.
class ResolvedSym
  property name : String
  property binding : Binding
  property object : ObjectFile
  property section : String
  property offset : Int64

  def initialize(@name, @binding, @object, @section, @offset)
  end
end

class Linker
  BASE  = 0x1000_i64
  ALIGN =      4_i64

  getter errors : Array(String)

  def initialize
    @objects = [] of ObjectFile
    @defs = {} of String => ResolvedSym
    @undef = Set(String).new
    @errors = [] of String
    @section_base = {} of String => Int64
    @comdat_owner = {} of String => Tuple(String, String)
    @layout = [] of Tuple(ObjectFile, SectionRecord)
    @reloc_results = [] of JSON::Any
  end

  # --- driver: include standalone objects, then pull archive members lazily ---

  def link(inputs : Array(ObjectFile | Archive))
    inputs.each do |inp|
      case inp
      when ObjectFile
        include_object(inp)
      when Archive
        resolve_archive(inp)
      end
    end
    layout_sections
    build_symbol_table
    apply_relocations
  end

  def include_object(obj : ObjectFile)
    @objects << obj
    obj.symbols.each do |s|
      if s.defined
        add_definition(obj, s)
        @undef.delete(s.name)
      else
        @undef.add(s.name) unless @defs.has_key?(s.name)
      end
    end
    obj.relocs.each do |r|
      @undef.add(r.target) unless @defs.has_key?(r.target)
    end
  end

  # Pull in archive members that provide a definition for a currently
  # undefined symbol.
  def resolve_archive(arc : Archive)
    pulled = Set(String).new
    arc.members.each do |m|
      next if pulled.includes?(m.id)
      needed = m.symbols.any? { |s| s.defined && @undef.includes?(s.name) }
      if needed
        pulled.add(m.id)
        include_object(m)
      end
    end
  end

  # --- symbol resolution: weak vs strong precedence ---

  def add_definition(obj : ObjectFile, s : SymRecord)
    existing = @defs[s.name]?
    if existing && existing.binding == Binding::Strong && s.binding == Binding::Strong
      @errors << "duplicate strong symbol: #{s.name}"
    end
    @defs[s.name] = ResolvedSym.new(s.name, s.binding, obj, s.section, s.offset)
  end

  # --- section layout with COMDAT dedup ---

  def layout_sections
    cursor = BASE
    @objects.each do |obj|
      obj.sections.each do |sec|
        if key = sec.comdat
          if @comdat_owner.has_key?(key)
            next
          else
            @comdat_owner[key] = {obj.id, sec.name}
          end
        end
        cursor = align_up(cursor, ALIGN)
        @section_base["#{obj.id}\x00#{sec.name}"] = cursor
        @layout << {obj, sec}
        cursor += sec.size
      end
    end
  end

  # Map a (object, section) reference to its laid-out base address.
  def section_addr(objid : String, section : String, comdat : String?) : Int64?
    @section_base["#{objid}\x00#{section}"]?
  end

  def build_symbol_table
    @defs.each do |name, rs|
      sec = rs.object.sections.find { |x| x.name == rs.section }
      comdat = sec ? sec.comdat : nil
      base = section_addr(rs.object.id, rs.section, comdat)
      @errors << "unresolved section for symbol: #{name}" if base.nil?
    end
  end

  def symbol_address(name : String) : Int64?
    rs = @defs[name]?
    return nil if rs.nil?
    sec = rs.object.sections.find { |x| x.name == rs.section }
    comdat = sec ? sec.comdat : nil
    base = section_addr(rs.object.id, rs.section, comdat)
    return nil if base.nil?
    base + rs.offset
  end

  # --- relocation application ---

  def apply_relocations
    @objects.each do |obj|
      obj.relocs.each do |r|
        sec = obj.sections.find { |x| x.name == r.section }
        comdat = sec ? sec.comdat : nil
        if comdat && (owner = @comdat_owner[comdat]?)
          next unless owner[0] == obj.id && owner[1] == r.section
        end
        p_base = section_addr(obj.id, r.section, comdat)
        if p_base.nil?
          @errors << "reloc in unmapped section: #{obj.id}/#{r.section}"
          next
        end
        p = p_base + r.offset
        s = symbol_address(r.target)
        if s.nil?
          @errors << "unresolved symbol in reloc: #{r.target}"
          next
        end
        value = compute_reloc(r.kind, s, r.addend, p, obj.id, r.section, r.offset)
        @reloc_results << JSON::Any.new({
          "object"  => JSON::Any.new(obj.id),
          "section" => JSON::Any.new(r.section),
          "offset"  => JSON::Any.new(r.offset),
          "kind"    => JSON::Any.new(r.kind),
          "value"   => JSON::Any.new(value),
        })
      end
    end
  end

  def compute_reloc(kind : String, s : Int64, addend : Int64, p : Int64,
                    objid : String, section : String, offset : Int64) : Int64
    site = "#{objid}/#{section}+#{offset}"
    case kind
    when "REL32"
      raw = s + addend - p
      raw & 0xffffffff_i64
    when "ABS32"
      raw = s + addend
      raw & 0xffffffff_i64
    when "ABS16"
      raw = s + addend
      raw & 0xffff
    else
      @errors << "unknown reloc kind: #{kind}"
      0_i64
    end
  end

  def align_up(v : Int64, a : Int64) : Int64
    (v + a - 1) // a * a
  end

  # --- output image ---

  def to_json_image : String
    sections = [] of JSON::Any
    @layout.each do |obj, sec|
      base = @section_base["#{obj.id}\x00#{sec.name}"]
      sections << JSON::Any.new({
        "name" => JSON::Any.new(sec.name),
        "base" => JSON::Any.new(base),
        "size" => JSON::Any.new(sec.size),
      })
    end
    symbols = {} of String => JSON::Any
    @defs.keys.sort.each do |name|
      addr = symbol_address(name)
      next if addr.nil?
      rs = @defs[name]
      binding = rs.binding == Binding::Weak ? "WEAK" : "STRONG"
      symbols[name] = JSON::Any.new({
        "address" => JSON::Any.new(addr),
        "binding" => JSON::Any.new(binding),
      })
    end
    root = {
      "sections" => JSON::Any.new(sections),
      "symbols"  => JSON::Any.new(symbols),
      "relocs"   => JSON::Any.new(@reloc_results),
      "errors"   => JSON::Any.new(@errors.map { |e| JSON::Any.new(e) }),
    }
    JSON::Any.new(root).to_json
  end
end
