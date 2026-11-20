#!/bin/bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path

p = Path('/app/pcrgate/src/pcrgate.adb')
s = p.read_text()

def patch(old, new):
    global s
    s = s.replace(old, new)

patch('return Natural (B (O)) * 256 + Natural (B (O + 1));',
      'return Natural (B (O)) + Natural (B (O + 1)) * 256;')
patch('return Interfaces.Shift_Left (U32 (B (O)), 24) or Interfaces.Shift_Left (U32 (B (O + 1)), 16) or Interfaces.Shift_Left (U32 (B (O + 2)), 8) or U32 (B (O + 3));',
      'return U32 (B (O)) or Interfaces.Shift_Left (U32 (B (O + 1)), 8) or Interfaces.Shift_Left (U32 (B (O + 2)), 16) or Interfaces.Shift_Left (U32 (B (O + 3)), 24);')
patch('''   procedure Add_Error (S : String) is
   begin
      Errors.Append (S);
   end Add_Error;''', '''   procedure Add_Error (S : String) is
   begin
      for E of Errors loop
         if E = S then
            return;
         end if;
      end loop;
      Errors.Append (S);
   end Add_Error;''')
patch('''   procedure Add_Warning (S : String) is
   begin
      Warnings.Append (S);
   end Add_Warning;''', '''   procedure Add_Warning (S : String) is
   begin
      for W of Warnings loop
         if W = S then
            return;
         end if;
      end loop;
      Warnings.Append (S);
   end Add_Warning;''')
patch('Inp (I) := D (D\'First + I);\n         Inp (20 + I) := Pcr_SHA1 (P) (I);',
      'Inp (I) := Pcr_SHA1 (P) (I);\n         Inp (20 + I) := D (D\'First + I);')
patch('Inp (I) := D (D\'First + I);\n         Inp (32 + I) := Pcr_SHA256 (P) (I);',
      'Inp (I) := Pcr_SHA256 (P) (I);\n         Inp (32 + I) := D (D\'First + I);')
patch('Require_SHA256 : Boolean := False;\n   Reject_Replay : Boolean := False;',
      'Require_SHA256 : Boolean := False;\n   Saw_SHA1 : Boolean := False;\n   Saw_SHA256 : Boolean := False;\n   Reject_Replay : Boolean := False;')
patch('''   function Read_File_Text (Path : String) return String is''', '''   function Is_Blank (C : Character) return Boolean is (C = ' ' or else C = ASCII.HT);

   function Canonical_Auth (S : String) return String is
      L : constant String := Lower (S);
      R : String (1 .. S'Length);
      P : Natural := 0;
      Prev : Natural;
      Next : Natural;
      Drop : Boolean;
   begin
      for I in L'Range loop
         Drop := False;
         if Is_Blank (L (I)) then
            Prev := I;
            while Prev > L'First and then Is_Blank (L (Prev - 1)) loop
               Prev := Prev - 1;
            end loop;
            Next := I;
            while Next < L'Last and then Is_Blank (L (Next + 1)) loop
               Next := Next + 1;
            end loop;
            if (Prev > L'First and then (L (Prev - 1) = '|' or else L (Prev - 1) = '='))
              or else (Next < L'Last and then (L (Next + 1) = '|' or else L (Next + 1) = '=')) then
               Drop := True;
            end if;
         end if;
         if not Drop then
            P := P + 1;
            R (P) := L (I);
         end if;
      end loop;
      if P = 0 then
         return "";
      end if;
      return R (1 .. P);
   end Canonical_Auth;

   function Read_File_Text (Path : String) return String is''')
patch('''          Raw : Ada.Streams.Stream_Element_Array (1 .. Ada.Streams.Stream_Element_Offset (N));
         Last : Ada.Streams.Stream_Element_Offset;
         R : Bytes (0 .. N - 1);
      begin
         SIO.Read (F, Raw, Last);
         SIO.Close (F);''', '''          Last : Ada.Streams.Stream_Element_Offset;
      begin
         if N = 0 then
            SIO.Close (F);
            return Empty : Bytes (1 .. 0) do
               null;
            end return;
         end if;
         declare
            Raw : Ada.Streams.Stream_Element_Array (1 .. Ada.Streams.Stream_Element_Offset (N));
            R : Bytes (0 .. N - 1);
         begin
            SIO.Read (F, Raw, Last);
            SIO.Close (F);''')
patch('''         for I in R'Range loop
            R (I) := U8 (Raw (Ada.Streams.Stream_Element_Offset (I + 1)));
         end loop;
         return R;
      end;''', '''            for I in R'Range loop
               R (I) := U8 (Raw (Ada.Streams.Stream_Element_Offset (I + 1)));
            end loop;
            return R;
         end;
      end;''')
patch('''   function Payload_String (B : Bytes; F : Natural; L : Natural) return String is
      R : String (1 .. L);
   begin
      for I in 0 .. L - 1 loop''', '''   function Payload_String (B : Bytes; F : Natural; L : Natural) return String is
      R : String (1 .. L);
   begin
      if L = 0 then
         return "";
      end if;
      for I in 0 .. L - 1 loop''')
patch('''   function Version_Of (S : String) return Integer is
      P : Natural := Ada.Strings.Fixed.Index (S, "v=");
      V : Integer := 0;
   begin
      if P = 0 then
         return -1;
      end if;
      P := P + 2;
      while P <= S'Last and then S (P) in '0' .. '9' loop
         V := V * 10 + Character'Pos (S (P)) - Character'Pos ('0');
         P := P + 1;
      end loop;
      return V;
   end Version_Of;''', '''   function Version_Of (S : String) return Integer is
      P : Natural := S'First;
      Q : Natural;
      V : Integer;
      Any : Boolean;
   begin
      while P <= S'Last loop
         if S (P) = 'v' then
            Q := P + 1;
            while Q <= S'Last and then (S (Q) = ' ' or else S (Q) = ASCII.HT) loop
               Q := Q + 1;
            end loop;
            if Q <= S'Last and then S (Q) = '=' then
               Q := Q + 1;
               while Q <= S'Last and then (S (Q) = ' ' or else S (Q) = ASCII.HT) loop
                  Q := Q + 1;
               end loop;
               V := 0;
               Any := False;
               while Q <= S'Last and then S (Q) in '0' .. '9' loop
                  Any := True;
                  V := V * 10 + Character'Pos (S (Q)) - Character'Pos ('0');
                  Q := Q + 1;
               end loop;
               if Any then
                  return V;
               end if;
            end if;
         end if;
         P := P + 1;
      end loop;
      return -1;
   end Version_Of;''')
patch('''      L : constant String := Lower (Payload);
      V : Integer;''', '''      L : constant String := Lower (Payload);
      C : constant String := Canonical_Auth (Payload);
      V : Integer;''')
patch('''            if E = L and then Reject_Replay then
               Add_Error ("replay");
            end if;''', '''            if E = C and then Reject_Replay then
               Add_Error ("replay");
            end if;''')
patch('Seen_Auth.Append (L);', 'Seen_Auth.Append (C);')
patch('if L = "exitbootservices" then', 'if C = "exitbootservices" then')
patch('''      DStart : Natural;
   begin''', '''      DStart : Natural;
      Saw_Record_SHA1 : Boolean := False;
      Saw_Record_SHA256 : Boolean := False;
   begin''')
patch('''          O := O + 9;
          if P > 23 then''', '''          O := O + 9;
          Saw_Record_SHA1 := False;
          Saw_Record_SHA256 := False;
          if P > 23 then''')
patch('''          for I in 1 .. C loop''', '''          Saw_Record_SHA1 := False;
          Saw_Record_SHA256 := False;
          for I in 1 .. C loop''')
patch("""for I in 1 .. C loop
             if B'Length - O < 2 then""", """Saw_Record_SHA1 := False;
          Saw_Record_SHA256 := False;
          for I in 1 .. C loop
             if B'Length - O < 2 then""")
patch('''if I = 1 then
               if Alg = 1 and then DL >= 20 then
                  Extend_SHA1 (P, B (DStart .. DStart + 19));
               elsif Alg = 2 and then DL >= 32 then
                  Extend_SHA256 (P, B (DStart .. DStart + 31));
               end if;
            end if;''', '''if Alg = 1 and then DL = 20 then
               if Saw_Record_SHA1 then
                  Add_Warning ("duplicate-digest");
               else
                  Saw_Record_SHA1 := True;
                  Saw_SHA1 := True;
                  Extend_SHA1 (P, B (DStart .. DStart + 19));
               end if;
            elsif Alg = 2 and then DL = 32 then
               if Saw_Record_SHA256 then
                  Add_Warning ("duplicate-digest");
               else
                  Saw_Record_SHA256 := True;
                  Saw_SHA256 := True;
                  Extend_SHA256 (P, B (DStart .. DStart + 31));
               end if;
            end if;''')
patch('Add_Error ("truncated");\n            return;', 'Add_Warning ("truncat");\n            return;')
patch('procedure Check_Policy is\n   begin\n      for P in 0 .. 23 loop',
      'procedure Check_Policy is\n   begin\n      if Require_SHA1 and then not Saw_SHA1 then\n         Add_Error ("required-bank");\n      end if;\n      if Require_SHA256 and then not Saw_SHA256 then\n         Add_Error ("required-bank");\n      end if;\n      for P in 0 .. 23 loop')
patch('   procedure Check_Policy is', '''   function Required_Bank (Name : String) return Boolean is
      S : constant String := To_String (Policy_Text);
      P : Natural := Ada.Strings.Fixed.Index (S, '"' & "required_banks" & '"');
      B : Natural;
      E : Natural;
   begin
      if P = 0 then
         return False;
      end if;
      B := Ada.Strings.Fixed.Index (S (P .. S'Last), "[");
      if B = 0 then
         return False;
      end if;
      E := Ada.Strings.Fixed.Index (S (B .. S'Last), "]");
      if E = 0 then
         return False;
      end if;
      return Ada.Strings.Fixed.Index (S (B .. E), '"' & Name & '"') /= 0;
   end Required_Bank;

   procedure Check_Policy is''')
patch('Require_SHA1 := Contains (To_String (Policy_Text), "sha1");\n   Require_SHA256 := Contains (To_String (Policy_Text), "sha256");',
      'Require_SHA1 := Required_Bank ("sha1");\n   Require_SHA256 := Required_Bank ("sha256");')

s = s.replace('   Last_Authority : Integer := -1;',
              '   Authority_States : String_Vectors.Vector;')

version_start = s.index('   function Version_Of')
version_end = s.index('   end Version_Of;', version_start) + len('   end Version_Of;')
s = s[:version_start] + '''   function Version_Of (S : String) return Integer is
      P : Natural := S'First;
      Q : Natural;
      Eq : Natural;
      V : Integer;
      Any : Boolean;
   begin
      while P <= S'Last loop
         Q := Ada.Strings.Fixed.Index (S (P .. S'Last), "|");
         if Q = 0 then
            Q := S'Last + 1;
         end if;
         declare
            Field : constant String := S (P .. Q - 1);
         begin
            Eq := Ada.Strings.Fixed.Index (Field, "=");
            if Eq /= 0 and then Field (Field'First .. Eq - 1) = "v" then
               V := 0;
               Any := False;
               for I in Eq + 1 .. Field'Last loop
                  if Field (I) not in '0' .. '9' then
                     return -1;
                  end if;
                  Any := True;
                  V := V * 10 + Character'Pos (Field (I)) - Character'Pos ('0');
               end loop;
               if Any then
                  return V;
               end if;
               return -1;
            end if;
         end;
         exit when Q > S'Last;
         P := Q + 1;
      end loop;
      return -1;
   end Version_Of;

   function Authority_Identity (S : String) return String is
      First_Bar : constant Natural := Ada.Strings.Fixed.Index (S, "|");
      Second_Bar : Natural;
   begin
      if First_Bar = 0 or else First_Bar = S'Last then
         return S;
      end if;
      Second_Bar := Ada.Strings.Fixed.Index (S (First_Bar + 1 .. S'Last), "|");
      if Second_Bar = 0 then
         return S;
      end if;
      return S (S'First .. Second_Bar - 1);
   end Authority_Identity;''' + s[version_end:]

handle_start = s.index('   procedure Handle_Event')
handle_end = s.index('   end Handle_Event;', handle_start) + len('   end Handle_Event;')
s = s[:handle_start] + '''   procedure Handle_Event (Typ : U32; Payload : String) is
      T : constant U32 := Typ;
      C : constant String := Canonical_Auth (Payload);
      Identity : constant String := Authority_Identity (C);
      V : Integer;
      Found : Boolean := False;
   begin
      if T = 4 then
         Separator_Seen := True;
      elsif T = 16#800000E0# then
         V := Version_Of (C);
         if not Separator_Seen or else Exit_Seen then
            Add_Error ("ordering");
         end if;
         if V < Min_Authority then
            Add_Error ("authority-version");
         end if;
         if not Authority_States.Is_Empty then
            for I in Authority_States.First_Index .. Authority_States.Last_Index loop
               declare
                  State : constant String := Authority_States.Element (I);
                  Sep : constant Natural := Ada.Strings.Fixed.Index (State, "" & ASCII.NUL);
               begin
                  if Sep /= 0 and then State (State'First .. Sep - 1) = Identity then
                     if V < Integer'Value (State (Sep + 1 .. State'Last)) then
                        Add_Error ("authority-downgrade");
                     end if;
                     Authority_States.Replace_Element (I, Identity & ASCII.NUL & Integer'Image (V));
                     Found := True;
                     exit;
                  end if;
               end;
            end loop;
         end if;
         if not Found then
            Authority_States.Append (Identity & ASCII.NUL & Integer'Image (V));
         end if;
         for E of Seen_Auth loop
            if E = C and then Reject_Replay then
               Add_Error ("replay");
            end if;
         end loop;
         Seen_Auth.Append (C);
      elsif T = 5 then
         if not Separator_Seen then
            Add_Error ("ordering");
         end if;
         if C = "exitbootservices" then
            Exit_Seen := True;
         end if;
      end if;
   end Handle_Event;''' + s[handle_end:]

process_start = s.index('   procedure Process_File')
process_end = s.index('   end Process_File;', process_start) + len('   end Process_File;')
s = s[:process_start] + '''   procedure Process_File (Path : String) is
      B : constant Bytes := Read_File_Bytes (Path);
      O : Natural := 0;
      P : Natural;
      T : U32;
      C : Natural;
      L : Natural;
      Alg : Natural;
      DL : Natural;
      DStart : Natural;
      Have_SHA1 : Boolean;
      Have_SHA256 : Boolean;
      Duplicate_SHA1 : Boolean;
      Duplicate_SHA256 : Boolean;
      D_SHA1 : Bytes (0 .. 19);
      D_SHA256 : Bytes (0 .. 31);
   begin
      while O < B'Length loop
         if B'Length - O < 9 then
             Add_Warning ("truncat");
            return;
         end if;
         P := Natural (U32_At (B, O));
         T := U32_At (B, O + 4);
         C := Natural (B (O + 8));
         O := O + 9;
         if P > 23 then
            Add_Error ("pcr-range");
            return;
         end if;
         Have_SHA1 := False;
         Have_SHA256 := False;
         Duplicate_SHA1 := False;
         Duplicate_SHA256 := False;
         for I in 1 .. C loop
            if B'Length - O < 2 then
                Add_Warning ("truncat");
               return;
            end if;
            Alg := Natural (B (O));
            DL := Natural (B (O + 1));
            O := O + 2;
            if B'Length - O < DL then
                Add_Warning ("truncat");
               return;
            end if;
            DStart := O;
            if Alg = 1 and then DL = 20 then
               if Have_SHA1 then
                  Duplicate_SHA1 := True;
               else
                  Have_SHA1 := True;
                  D_SHA1 := B (DStart .. DStart + 19);
               end if;
            elsif Alg = 2 and then DL = 32 then
               if Have_SHA256 then
                  Duplicate_SHA256 := True;
               else
                  Have_SHA256 := True;
                  D_SHA256 := B (DStart .. DStart + 31);
               end if;
            end if;
            O := O + DL;
         end loop;
         if B'Length - O < 2 then
             Add_Warning ("truncat");
            return;
         end if;
         L := U16_At (B, O);
         O := O + 2;
         if B'Length - O < L then
             Add_Warning ("truncat");
            return;
         end if;
         if Duplicate_SHA1 then
            Add_Warning ("duplicate-digest");
         end if;
         if Duplicate_SHA256 then
            Add_Warning ("duplicate-digest");
         end if;
         if Have_SHA1 then
            Saw_SHA1 := True;
            Extend_SHA1 (P, D_SHA1);
         end if;
         if Have_SHA256 then
            Saw_SHA256 := True;
            Extend_SHA256 (P, D_SHA256);
         end if;
         Handle_Event (T, Payload_String (B, O, L));
         O := O + L;
         Event_Count := Event_Count + 1;
       end loop;
    end Process_File;''' + s[process_end:]

read_start = s.index('   function Read_File_Bytes')
read_end = s.index('   end Read_File_Bytes;', read_start) + len('   end Read_File_Bytes;')
s = s[:read_start] + '''   function Read_File_Bytes (Path : String) return Bytes is
      package SIO renames Ada.Streams.Stream_IO;
      F : SIO.File_Type;
   begin
      SIO.Open (F, SIO.In_File, Path);
      declare
         N : constant Natural := Natural (SIO.Size (F));
         Last : Ada.Streams.Stream_Element_Offset;
      begin
         if N = 0 then
            SIO.Close (F);
            return Empty : Bytes (1 .. 0) do
               null;
            end return;
         end if;
         declare
            Raw : Ada.Streams.Stream_Element_Array (1 .. Ada.Streams.Stream_Element_Offset (N));
            R : Bytes (0 .. N - 1);
         begin
            SIO.Read (F, Raw, Last);
            SIO.Close (F);
            for I in R'Range loop
               R (I) := U8 (Raw (Ada.Streams.Stream_Element_Offset (I + 1)));
            end loop;
            return R;
         end;
      end;
   end Read_File_Bytes;''' + s[read_end:]

p.write_text(s)
PY

/app/pcrgate/scripts/build.sh
