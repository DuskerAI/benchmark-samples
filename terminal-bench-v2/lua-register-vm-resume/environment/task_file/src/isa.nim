## Instruction encoding for the register machine.
##
## Each instruction is a packed 32-bit word laid out as:
##
##   bits  0.. 5  : opcode        (6 bits)
##   bits  6..13  : field A       (8 bits, unsigned register / count)
##   bits 14..22  : field B       (9 bits, unsigned register / const index)
##   bits 23..31  : field C       (9 bits, unsigned register)
##
## A second "iAx" shape reuses A as an 8-bit operand and packs a signed
## branch displacement into the top 18 bits as a two's-complement value so
## the displacement can be negative.  See `packSbx` / `unpackSbx`.

type
  OpCode* = enum
    opHALT  = 0
    opLOADK = 1
    opMOVE  = 2
    opADD   = 3
    opSUB   = 4
    opMUL   = 5
    opLT    = 6
    opEQ    = 7
    opJMP   = 8
    opTEST  = 9
    opCLOSURE = 10
    opGETUPVAL = 11
    opSETUPVAL = 12
    opCALL  = 13
    opTAILCALL = 14
    opRETURN = 15

  Instr* = uint32

const
  OpMask*  = 0x3F'u32
  AShift*  = 6
  AMask*   = 0xFF'u32
  BShift*  = 14
  BMask*   = 0x1FF'u32
  CShift*  = 23
  CMask*   = 0x1FF'u32

  ## The signed displacement field occupies the 18 bits above field A and
  ## is stored as a two's-complement value.
  SbxBits*  = 18
  SbxShift* = AShift + 8                        ## displacement starts above A
  SbxField* = (1'u32 shl SbxBits) - 1           ## 18-bit field mask
  SbxSign*  = (1'u32 shl (SbxBits - 1))         ## sign bit of the field

proc opcodeOf*(i: Instr): OpCode =
  OpCode(i and OpMask)

proc fieldA*(i: Instr): int =
  int((i shr AShift) and AMask)

proc fieldB*(i: Instr): int =
  int((i shr BShift) and BMask)

proc fieldC*(i: Instr): int =
  int((i shr CShift) and CMask)

proc packAbc*(op: OpCode, a, b, c: int): Instr =
  result = uint32(ord(op))
  result = result or (uint32(a and 0xFF) shl AShift)
  result = result or (uint32(b and 0x1FF) shl BShift)
  result = result or (uint32(c and 0x1FF) shl CShift)

proc packSbx*(op: OpCode, a, sbx: int): Instr =
  ## Store the signed displacement `sbx` as a two's-complement field.
  let field = uint32(sbx) and SbxField
  result = uint32(ord(op))
  result = result or (uint32(a and 0xFF) shl AShift)
  result = result or (field shl SbxShift)

proc unpackSbx*(i: Instr): int =
  ## Recover the signed displacement from its two's-complement field.
  let field = (i shr SbxShift) and SbxField
  if (field and SbxSign) != 0'u32:
    result = int(int32(field))
  else:
    result = int(int32(field))
