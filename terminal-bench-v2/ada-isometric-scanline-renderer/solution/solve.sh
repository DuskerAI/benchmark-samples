#!/bin/bash
set -euo pipefail
python3 - <<'PY'
from pathlib import Path
import re

p = Path('/app/isorender/src/isoboard.adb')
s = p.read_text()
s = s.replace("while Pos <= S'Last and then S (Pos) = ' ' loop", "while Pos <= S'Last and then (S (Pos) = ' ' or else S (Pos) = Character'Val (9)) loop")
s = s.replace("while Pos <= S'Last and then S (Pos) /= ' ' loop", "while Pos <= S'Last and then S (Pos) /= ' ' and then S (Pos) /= Character'Val (9) loop")

render = '''   procedure Render (C : in out Canvas) is
   begin
      for I in 1 .. Count loop
         declare
            It : Item renames Items (I);
            CX : constant Integer := Origin_X - Camera_X + (It.X - It.Y) * 8;
            CY : constant Integer := Origin_Y - Camera_Y + (It.X + It.Y) * 4;
            R : Integer := It.R1;
            G : Integer := It.G1;
            B : Integer := It.B1;
            D2 : Integer := 0;
            D5 : Integer := It.Index;
         begin
            if It.Kind = Tile_Item then
               for PY in CY - 4 .. CY + 4 loop
                  for PX in CX - 8 .. CX + 8 loop
                     if Abs_Int (PX - CX) * 4 + Abs_Int (PY - CY) * 8 <= 32 then
                        Put_Pixel (C, PX, PY, R, G, B, It.X + It.Y, 0, It.X, It.Y, D5);
                     end if;
                  end loop;
               end loop;
            elsif It.Kind = Sprite_Item then
               if (Tick + It.Seed) mod 2 /= 0 then
                  R := It.R2;
                  G := It.G2;
                  B := It.B2;
               end if;
               D2 := It.Z;
               D5 := It.Index;
               for DY in -3 .. 3 loop
                  for DX in -2 .. 2 loop
                     if Abs_Int (DX) <= 2 and then Abs_Int (DY) <= 3 and then Abs_Int (DX) + Abs_Int (DY) <= 4 then
                        Put_Pixel (C, CX + DX, CY - It.Z + DY, R, G, B, It.X + It.Y, D2, It.X, It.Y, D5);
                     end if;
                  end loop;
               end loop;
            end if;
         end;
      end loop;

      for I in 1 .. Count loop
         declare
            It : Item renames Items (I);
         begin
            if It.Kind = Overlay_Item then
               for PY in It.Y .. It.Y + It.H - 1 loop
                  for PX in It.X .. It.X + It.W - 1 loop
                     if PX >= 0 and then PX < Width and then PY >= 0 and then PY < Height then
                        C (PY, PX).R := (C (PY, PX).R * (255 - It.A) + It.R1 * It.A) / 255;
                        C (PY, PX).G := (C (PY, PX).G * (255 - It.A) + It.G1 * It.A) / 255;
                        C (PY, PX).B := (C (PY, PX).B * (255 - It.A) + It.B1 * It.A) / 255;
                     end if;
                  end loop;
               end loop;
            end if;
         end;
      end loop;
   end Render;'''
s = re.sub(r'   procedure Render \(C : in out Canvas\) is\n   begin\n.*?\n   end Render;', render, s, flags=re.S)

hash_image = '''   function Hash_Image (C : Canvas) return String is
      H : Unsigned_64 := 14695981039346656037;
      Prime : constant Unsigned_64 := 1099511628211;
      procedure Add_Byte (B : Integer) is
      begin
         H := (H xor Unsigned_64 (B mod 256)) * Prime;
      end Add_Byte;
   begin
      for Y in 0 .. Height - 1 loop
         for X in 0 .. Width - 1 loop
            Add_Byte (C (Y, X).R);
            Add_Byte (C (Y, X).G);
            Add_Byte (C (Y, X).B);
         end loop;
      end loop;
      declare
         Tick_Text : constant String := Trim (Integer'Image (Tick), Both);
      begin
         for Ch of Tick_Text loop
            Add_Byte (Character'Pos (Ch));
         end loop;
      end;
      return Hex64 (H);
   end Hash_Image;'''
s = re.sub(r'   function Hash_Image \(C : Canvas\) return String is\n.*?\n   end Hash_Image;', hash_image, s, flags=re.S)
p.write_text(s)
PY
/app/isorender/scripts/build.sh
