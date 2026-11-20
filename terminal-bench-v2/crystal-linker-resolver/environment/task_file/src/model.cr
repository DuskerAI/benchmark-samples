require "json"

# Core data model for the mini static linker.
#
# Object files (.cro) and archives (.cra) are parsed into these structures.
# The linker driver combines a set of inputs into a single linked image.

enum Binding
  Strong
  Weak
  Common
end

# A symbol as declared inside one object file.
class SymRecord
  property name : String
  property binding : Binding
  # section name where the symbol is defined; empty when undefined.
  property section : String
  property offset : Int64
  property defined : Bool
  property common_size : Int64
  property common_align : Int64

  def initialize(@name, @binding, @section, @offset, @defined,
                 @common_size = 0_i64, @common_align = 1_i64)
  end
end

# A section inside one object file.
class SectionRecord
  property name : String
  # COMDAT group key, or nil when the section is not a COMDAT group.
  property comdat : String?
  property size : Int64
  property data : Bytes

  def initialize(@name, @comdat, @size, @data)
  end
end

# A relocation applied inside a section of an object file.
class RelocRecord
  property section : String
  property offset : Int64
  property target : String
  property kind : String
  property addend : Int64

  def initialize(@section, @offset, @target, @kind, @addend)
  end
end

# One parsed object file (either standalone or an archive member).
class ObjectFile
  property id : String
  property symbols : Array(SymRecord)
  property sections : Array(SectionRecord)
  property relocs : Array(RelocRecord)

  def initialize(@id)
    @symbols = [] of SymRecord
    @sections = [] of SectionRecord
    @relocs = [] of RelocRecord
  end
end

# An archive is an ordered collection of member object files.
class Archive
  property id : String
  property members : Array(ObjectFile)

  def initialize(@id)
    @members = [] of ObjectFile
  end
end

class LinkError < Exception
end
