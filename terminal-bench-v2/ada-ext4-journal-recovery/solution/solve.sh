#!/bin/bash
set -euo pipefail

cd /app/task_file

python3 - <<'PY'
from pathlib import Path

path = Path("src/fsrecover.adb")
text = path.read_text()

def replace_between(src, start, end, replacement):
    a = src.index(start)
    b = src.index(end, a)
    return src[:a] + replacement + src[b:]

parse_base = r'''   procedure Parse_Base (Path : String) is
      D        : constant Byte_Array := Load_File (Path);
      P        : Natural := 1;
      GC       : Natural;
      IC       : Natural;
      BC       : Natural;
      DC       : Natural;
      Bytes    : Natural;
      Name_Len : Natural;
   begin
      if Get_String (D, P, 4) /= "MFS1" then
         raise Bad_Image;
      end if;
      GC := Get_U16 (D, P);
      IC := Get_U16 (D, P);
      BC := Get_U16 (D, P);
      DC := Get_U16 (D, P);
      Orphan_Head := Get_U16 (D, P);
      for I in 1 .. GC loop
         declare
            Gid       : constant Natural := Get_U16 (D, P);
            First     : constant Natural := Get_U16 (D, P);
            Count     : constant Natural := Get_U16 (D, P);
            Free_Hint : constant Natural := Get_U16 (D, P);
            Skip      : constant Natural := Get_U16 (D, P);
         begin
            if Gid <= Max_Groups then
               Groups (Gid).Used := True;
               Groups (Gid).Id := Gid;
               Groups (Gid).First := First;
               Groups (Gid).Count := Count;
               Groups (Gid).Free_Hint := Free_Hint;
            end if;
         end;
      end loop;
      for I in 1 .. IC loop
         declare
            N : constant Natural := Get_U16 (D, P);
         begin
            Apply_Inode (N, D, P);
            P := P + 12;
         end;
      end loop;
      Bytes := (BC + 7) / 8;
      for B in 0 .. BC - 1 loop
         if (Natural (D (P + B / 8)) / (2 ** (B mod 8))) mod 2 = 1 then
            Bitmap (B) := True;
         end if;
      end loop;
      P := P + Bytes;
      Dir_Count := DC;
      for I in 1 .. DC loop
         Dirs (I).Used := True;
         Dirs (I).Parent := Get_U16 (D, P);
         Dirs (I).Child := Get_U16 (D, P);
         Name_Len := Get_U8 (D, P);
         Dirs (I).Name := To_Unbounded_String (Get_String (D, P, Name_Len));
      end loop;
   end Parse_Base;

'''

parse_journal = r'''   procedure Parse_Journal (Path : String) is
      D            : constant Byte_Array := Load_File (Path);
      P            : Natural := 1;
      Start_Pos    : Natural;
      Commit_Pos   : Natural;
      Count        : Natural;
      Txid         : Unsigned_32;
      Commit_Txid  : Unsigned_32;
      Want         : Unsigned_32;
      Got          : Unsigned_32;

      type Tx_Record is record
         Kind  : Natural := 0;
         Key   : Natural := 0;
         Pos   : Natural := 0;
         Len   : Natural := 0;
      end record;
       Records : array (Natural range 1 .. 65535) of Tx_Record;
      NRec    : Natural;

      function Is_Revoked (K : Natural; Key : Natural) return Boolean is
         RP : Natural;
         RK : Natural;
         RKey : Natural;
      begin
         for I in 1 .. NRec loop
            if Records (I).Kind = 5 and then Records (I).Len >= 3 then
               RP := Records (I).Pos;
               RK := Get_U8 (D, RP);
               RKey := Get_U16 (D, RP);
               if RK = K and then RKey = Key then
                  return True;
               end if;
            end if;
         end loop;
         return False;
      end Is_Revoked;
   begin
      if Get_String (D, P, 4) /= "JNL1" then
         raise Bad_Image;
      end if;
      while P <= D'Last loop
         Start_Pos := P;
         if P + 6 > D'Last or else Get_U8 (D, P) /= 16#D1# then
            Ignored_Tx := Ignored_Tx + 1;
            exit;
         end if;
         Txid := Get_U32 (D, P);
         Count := Get_U16 (D, P);
         NRec := 0;
         for I in 1 .. Count loop
            declare
               Kind : Natural;
               Key  : Natural;
               Len  : Natural;
            begin
               if P + 4 > D'Last then
                  Ignored_Tx := Ignored_Tx + 1;
                  return;
               end if;
               Kind := Get_U8 (D, P);
               Key := Get_U16 (D, P);
               Len := Get_U16 (D, P);
               if P + Len - 1 > D'Last then
                  Ignored_Tx := Ignored_Tx + 1;
                  return;
               end if;
               if NRec = Records'Last then
                  Ignored_Tx := Ignored_Tx + 1;
                  return;
               end if;
               NRec := NRec + 1;
               Records (NRec) := (Kind => Kind, Key => Key, Pos => P, Len => Len);
               P := P + Len;
            end;
         end loop;
         Commit_Pos := P;
         if P + 8 > D'Last or else Get_U8 (D, P) /= 16#C0# then
            Ignored_Tx := Ignored_Tx + 1;
            exit;
         end if;
         Commit_Txid := Get_U32 (D, P);
         Got := Get_U32 (D, P);
         Want := Sum_Bytes (D, Start_Pos, Commit_Pos - 1);
         if Commit_Txid = Txid and then Got = Want then
            for I in 1 .. NRec loop
               if Records (I).Kind /= 5 and then not Is_Revoked (Records (I).Kind, Records (I).Key) then
                  Apply_Update (Records (I).Kind, Records (I).Key, D, Records (I).Pos, Records (I).Len);
               end if;
            end loop;
            Valid_Tx := Valid_Tx + 1;
         else
            Ignored_Tx := Ignored_Tx + 1;
            exit;
         end if;
      end loop;
   exception
      when Bad_Image =>
         Ignored_Tx := Ignored_Tx + 1;
   end Parse_Journal;

'''

put_names = r'''   procedure Put_Names (F : Ada.Text_IO.File_Type; Ino : Natural) is
      Done  : array (Natural range 1 .. Max_Dirs) of Boolean := (others => False);
      First : Boolean := True;

      function Ref (Idx : Natural) return String is
      begin
         return Nat_Image (Dirs (Idx).Parent) & "/" & To_String (Dirs (Idx).Name);
      end Ref;
   begin
      Ada.Text_IO.Put (F, "[");
      loop
         declare
            Best : Natural := 0;
         begin
            for I in 1 .. Dir_Count loop
               if Dirs (I).Used and then Dirs (I).Child = Ino and then not Done (I) then
                  if Best = 0 or else Ref (I) < Ref (Best) then
                     Best := I;
                  end if;
               end if;
            end loop;
            exit when Best = 0;
            if not First then
               Ada.Text_IO.Put (F, ",");
            end if;
            First := False;
            Done (Best) := True;
            Ada.Text_IO.Put (F, Quote (Ref (Best)));
         end;
      end loop;
      Ada.Text_IO.Put (F, "]");
   end Put_Names;

'''

finish_state = r'''   procedure Finish_State is
      type Ref_Array is array (Natural range 0 .. Max_Inodes) of Natural;
      type Seen_Array is array (Natural range 0 .. Max_Inodes) of Boolean;
      Refs : Ref_Array;
      Seen : Seen_Array := (others => False);

      procedure Filter_Dirs is
      begin
         Refs := (others => 0);
         if Inodes (1).Used and then Inodes (1).Mode = 2 then
            Refs (1) := 1;
         end if;
         for I in 1 .. Dir_Count loop
            if Dirs (I).Used then
               declare
                  N : constant String := To_String (Dirs (I).Name);
               begin
                  if Dirs (I).Parent <= Max_Inodes
                    and then Dirs (I).Child <= Max_Inodes
                    and then Inodes (Dirs (I).Parent).Used
                    and then Inodes (Dirs (I).Child).Used
                    and then Inodes (Dirs (I).Parent).Mode = 2
                    and then Name_OK (N)
                  then
                     Refs (Dirs (I).Child) := Refs (Dirs (I).Child) + 1;
                  else
                     Dirs (I).Used := False;
                  end if;
               end;
            end if;
         end loop;
      end Filter_Dirs;

      procedure Clear_Inode_Blocks (Idx : Natural) is
      begin
         if Inodes (Idx).Blocks > 0 then
            for B in Inodes (Idx).First_Block .. Inodes (Idx).First_Block + Inodes (Idx).Blocks - 1 loop
               if B <= Max_Blocks then
                  Bitmap (B) := False;
               end if;
            end loop;
         end if;
      end Clear_Inode_Blocks;

      procedure Mark_Inode_Blocks (Idx : Natural) is
      begin
         if Inodes (Idx).Blocks > 0 then
            for B in Inodes (Idx).First_Block .. Inodes (Idx).First_Block + Inodes (Idx).Blocks - 1 loop
               if B <= Max_Blocks then
                  Bitmap (B) := True;
               end if;
            end loop;
         end if;
      end Mark_Inode_Blocks;

      Cur : Natural := Orphan_Head;
      Nxt : Natural;
   begin
      Filter_Dirs;
      while Cur >= 1 and then Cur <= Max_Inodes and then Inodes (Cur).Used and then not Seen (Cur) loop
         Seen (Cur) := True;
         Nxt := Inodes (Cur).Next_Orphan;
         if Refs (Cur) = 0 then
            Clear_Inode_Blocks (Cur);
            Inodes (Cur).Used := False;
            Inodes (Cur).Mode := 0;
         end if;
         Cur := Nxt;
      end loop;
      for I in 1 .. Max_Inodes loop
         if Inodes (I).Used and then Inodes (I).Mode /= 0 then
            Mark_Inode_Blocks (I);
         end if;
      end loop;
      Filter_Dirs;
      for I in 1 .. Max_Inodes loop
         if Inodes (I).Used and then Inodes (I).Mode /= 0 then
            Inodes (I).Links := Refs (I);
         end if;
      end loop;
   end Finish_State;

'''

text = replace_between(text, "   procedure Parse_Base", "   procedure Parse_Journal", parse_base)
text = replace_between(text, "   procedure Parse_Journal", "   function Name_OK", parse_journal)
text = replace_between(text, "   procedure Finish_State", "   function Mode_Name", finish_state)
text = replace_between(text, "   procedure Put_Names", "   procedure Write_Output", put_names)
text = text.replace('''   function Quote (S : String) return String is
   begin
      return Character'Val (34) & S & Character'Val (34);
   end Quote;

''', '''   function Quote (S : String) return String is
       Outbuf : Unbounded_String := To_Unbounded_String ("");

       function Hex_Digit (V : Natural) return Character is
       begin
          if V < 10 then
             return Character'Val (Character'Pos ('0') + V);
          else
             return Character'Val (Character'Pos ('A') + V - 10);
          end if;
       end Hex_Digit;
    begin
       Append (Outbuf, Character'Val (34));
       for C of S loop
          if C = Character'Val (34) then
             Append (Outbuf, Character'Val (16#5C#));
             Append (Outbuf, Character'Val (34));
          elsif C = Character'Val (16#5C#) then
             Append (Outbuf, Character'Val (16#5C#));
             Append (Outbuf, Character'Val (16#5C#));
          elsif Character'Pos (C) < 32 then
             Append (Outbuf, Character'Val (16#5C#));
             Append (Outbuf, "u00");
             Append (Outbuf, Hex_Digit (Character'Pos (C) / 16));
             Append (Outbuf, Hex_Digit (Character'Pos (C) mod 16));
          else
             Append (Outbuf, C);
          end if;
      end loop;
      Append (Outbuf, Character'Val (34));
      return To_String (Outbuf);
   end Quote;

''')
text = text.replace("Free := Groups (G).Free_Hint;", "Free := 0;\n            if Groups (G).Count > 0 then\n               for B in Groups (G).First .. Groups (G).First + Groups (G).Count - 1 loop\n                  if B > Max_Blocks or else not Bitmap (B) then\n                     Free := Free + 1;\n                  end if;\n               end loop;\n            end if;")

path.write_text(text)
PY

make clean
make
