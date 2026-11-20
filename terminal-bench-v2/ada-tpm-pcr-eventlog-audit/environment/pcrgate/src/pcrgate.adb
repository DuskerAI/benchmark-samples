with Ada.Command_Line;
with Ada.Containers.Indefinite_Vectors;
with Ada.Directories;
with Ada.Streams;
with Ada.Streams.Stream_IO;
with Ada.Strings.Fixed;
with Ada.Strings.Unbounded;
with Ada.Text_IO;
with Crypto_Hash;
with Interfaces;

procedure Pcrgate is
   subtype U8 is Interfaces.Unsigned_8;
   subtype U32 is Interfaces.Unsigned_32;
   type Bytes is array (Natural range <>) of U8;
   type Digest20 is array (Natural range 0 .. 19) of U8;
   type Digest32 is array (Natural range 0 .. 31) of U8;
   type Bank20 is array (Natural range 0 .. 23) of Digest20;
   type Bank32 is array (Natural range 0 .. 23) of Digest32;
   use type Interfaces.Unsigned_32;

   package String_Vectors is new Ada.Containers.Indefinite_Vectors (Natural, String);
   package String_Sort is new String_Vectors.Generic_Sorting;
   use Ada.Strings.Unbounded;

   Pcr_SHA1 : Bank20 := (others => (others => 0));
   Pcr_SHA256 : Bank32 := (others => (others => 0));
   Errors : String_Vectors.Vector;
   Warnings : String_Vectors.Vector;
   Files : String_Vectors.Vector;
   Seen_Auth : String_Vectors.Vector;
   Event_Count : Natural := 0;
   Separator_Seen : Boolean := False;
   Exit_Seen : Boolean := False;
   Last_Authority : Integer := -1;
   Require_SHA1 : Boolean := False;
   Require_SHA256 : Boolean := False;
   Reject_Replay : Boolean := False;
   Min_Authority : Integer := 0;
   Policy_Text : Unbounded_String;
   Policy_Path : Unbounded_String;
   Events_Dir : Unbounded_String;
   Out_Path : Unbounded_String;

   function Contains (S : String; P : String) return Boolean is (Ada.Strings.Fixed.Index (S, P) /= 0);

   procedure Add_Error (S : String) is
   begin
      Errors.Append (S);
   end Add_Error;

   procedure Add_Warning (S : String) is
   begin
      Warnings.Append (S);
   end Add_Warning;

   function Hex (B : U8) return String is
      H : constant String := "0123456789abcdef";
      V : constant Natural := Natural (B);
   begin
      return "" & H (V / 16 + 1) & H (V mod 16 + 1);
   end Hex;

   function Hex20 (D : Digest20) return String is
      R : String (1 .. 40);
      P : Natural := 1;
      Hx : String (1 .. 2);
   begin
      for I in D'Range loop
         Hx := Hex (D (I));
         R (P .. P + 1) := Hx;
         P := P + 2;
      end loop;
      return R;
   end Hex20;

   function Hex32 (D : Digest32) return String is
      R : String (1 .. 64);
      P : Natural := 1;
      Hx : String (1 .. 2);
   begin
      for I in D'Range loop
         Hx := Hex (D (I));
         R (P .. P + 1) := Hx;
         P := P + 2;
      end loop;
      return R;
   end Hex32;

   function Lower (S : String) return String is
      R : String := S;
   begin
      for I in R'Range loop
         if R (I) in 'A' .. 'Z' then
            R (I) := Character'Val (Character'Pos (R (I)) + 32);
         end if;
      end loop;
      return R;
   end Lower;

   function Read_File_Text (Path : String) return String is
      F : Ada.Text_IO.File_Type;
      B : Unbounded_String;
   begin
      Ada.Text_IO.Open (F, Ada.Text_IO.In_File, Path);
      while not Ada.Text_IO.End_Of_File (F) loop
         B := B & Ada.Text_IO.Get_Line (F) & ASCII.LF;
      end loop;
      Ada.Text_IO.Close (F);
      return To_String (B);
   end Read_File_Text;

   function Read_File_Bytes (Path : String) return Bytes is
      package SIO renames Ada.Streams.Stream_IO;
      F : SIO.File_Type;
   begin
      SIO.Open (F, SIO.In_File, Path);
      declare
         N : constant Natural := Natural (SIO.Size (F));
         Raw : Ada.Streams.Stream_Element_Array (1 .. Ada.Streams.Stream_Element_Offset (N));
         Last : Ada.Streams.Stream_Element_Offset;
         R : Bytes (0 .. N - 1);
      begin
         SIO.Read (F, Raw, Last);
         SIO.Close (F);
         for I in R'Range loop
            R (I) := U8 (Raw (Ada.Streams.Stream_Element_Offset (I + 1)));
         end loop;
         return R;
      end;
   end Read_File_Bytes;

   function U16_At (B : Bytes; O : Natural) return Natural is
   begin
      return Natural (B (O)) * 256 + Natural (B (O + 1));
   end U16_At;

   function U32_At (B : Bytes; O : Natural) return U32 is
   begin
      return Interfaces.Shift_Left (U32 (B (O)), 24) or Interfaces.Shift_Left (U32 (B (O + 1)), 16) or Interfaces.Shift_Left (U32 (B (O + 2)), 8) or U32 (B (O + 3));
   end U32_At;

   procedure Extend_SHA1 (P : Natural; D : Bytes) is
      Inp : Crypto_Hash.Byte_Array (0 .. 39);
      Outp : Crypto_Hash.Byte_Array (0 .. 19);
   begin
      for I in 0 .. 19 loop
         Inp (I) := D (D'First + I);
         Inp (20 + I) := Pcr_SHA1 (P) (I);
      end loop;
      Crypto_Hash.SHA1_Hash (Inp, Outp);
      for I in 0 .. 19 loop
         Pcr_SHA1 (P) (I) := Outp (I);
      end loop;
   end Extend_SHA1;

   procedure Extend_SHA256 (P : Natural; D : Bytes) is
      Inp : Crypto_Hash.Byte_Array (0 .. 63);
      Outp : Crypto_Hash.Byte_Array (0 .. 31);
   begin
      for I in 0 .. 31 loop
         Inp (I) := D (D'First + I);
         Inp (32 + I) := Pcr_SHA256 (P) (I);
      end loop;
      Crypto_Hash.SHA256_Hash (Inp, Outp);
      for I in 0 .. 31 loop
         Pcr_SHA256 (P) (I) := Outp (I);
      end loop;
   end Extend_SHA256;

   function Payload_String (B : Bytes; F : Natural; L : Natural) return String is
      R : String (1 .. L);
   begin
      for I in 0 .. L - 1 loop
         R (I + 1) := Character'Val (B (F + I));
      end loop;
      return R;
   end Payload_String;

   function Version_Of (S : String) return Integer is
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
   end Version_Of;

   procedure Handle_Event (Typ : U32; Payload : String) is
      T : constant U32 := Typ;
      L : constant String := Lower (Payload);
      V : Integer;
   begin
      if T = 4 then
         Separator_Seen := True;
      elsif T = 16#800000E0# then
         V := Version_Of (L);
         if not Separator_Seen then
            Add_Error ("ordering");
         end if;
         if Exit_Seen then
            Add_Error ("ordering");
         end if;
         if V < Min_Authority then
            Add_Error ("authority-version");
         end if;
         if V < Last_Authority then
            Add_Error ("authority-downgrade");
         end if;
         for E of Seen_Auth loop
            if E = L and then Reject_Replay then
               Add_Error ("replay");
            end if;
         end loop;
         Seen_Auth.Append (L);
         Last_Authority := V;
      elsif T = 5 then
         if not Separator_Seen then
            Add_Error ("ordering");
         end if;
         if L = "exitbootservices" then
            Exit_Seen := True;
         end if;
      end if;
   end Handle_Event;

   procedure Process_File (Path : String) is
      B : constant Bytes := Read_File_Bytes (Path);
      O : Natural := 0;
      P : Natural;
      T : U32;
      C : Natural;
      L : Natural;
      Alg : Natural;
      DL : Natural;
      DStart : Natural;
      Tentative_SHA1 : Boolean := False;
      Tentative_SHA256 : Boolean := False;
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
          Tentative_SHA1 := False;
          Tentative_SHA256 := False;
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
             if I = 1 then
                if Alg = 1 and then DL >= 20 then
                   Tentative_SHA1 := True;
                   Extend_SHA1 (P, B (DStart .. DStart + 19));
                elsif Alg = 2 and then DL >= 32 then
                   Tentative_SHA256 := True;
                   Extend_SHA256 (P, B (DStart .. DStart + 31));
                end if;
             elsif (Alg = 1 and then DL >= 20 and then Tentative_SHA1)
               or else (Alg = 2 and then DL >= 32 and then Tentative_SHA256) then
                Add_Warning ("duplicate-digest");
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
         Handle_Event (T, Payload_String (B, O, L));
         O := O + L;
         Event_Count := Event_Count + 1;
      end loop;
   end Process_File;

   function Parse_Int_After (S : String; Key : String; Default : Integer) return Integer is
      P : Natural := Ada.Strings.Fixed.Index (S, Key);
      V : Integer := 0;
      Any : Boolean := False;
   begin
      if P = 0 then
         return Default;
      end if;
      P := P + Key'Length;
      while P <= S'Last and then not (S (P) in '0' .. '9') loop
         P := P + 1;
      end loop;
      while P <= S'Last and then S (P) in '0' .. '9' loop
         Any := True;
         V := V * 10 + Character'Pos (S (P)) - Character'Pos ('0');
         P := P + 1;
      end loop;
      if Any then return V; else return Default; end if;
   end Parse_Int_After;

   function Policy_Value (Key : String) return String is
      P : Natural := Ada.Strings.Fixed.Index (To_String (Policy_Text), '"' & Key & '"');
      S : constant String := To_String (Policy_Text);
   begin
      if P = 0 then
         return "";
      end if;
      P := P + Key'Length + 2;
      while P <= S'Last and then S (P) /= ':' loop
         P := P + 1;
      end loop;
      P := P + 1;
      while P <= S'Last and then S (P) /= '"' loop
         P := P + 1;
      end loop;
      P := P + 1;
      declare
         Q : Natural := P;
      begin
         while Q <= S'Last and then S (Q) /= '"' loop
            Q := Q + 1;
         end loop;
         if Q > S'Last then return ""; end if;
         return S (P .. Q - 1);
      end;
   end Policy_Value;

   procedure Load_Files is
      Search : Ada.Directories.Search_Type;
      Ent : Ada.Directories.Directory_Entry_Type;
      Name : String (1 .. 512);
   begin
      Ada.Directories.Start_Search (Search, To_String (Events_Dir), "*.evt");
      while Ada.Directories.More_Entries (Search) loop
         Ada.Directories.Get_Next_Entry (Search, Ent);
         Files.Append (Ada.Directories.Simple_Name (Ent));
      end loop;
      Ada.Directories.End_Search (Search);
      String_Sort.Sort (Files);
   end Load_Files;

   procedure Check_Policy is
   begin
      for P in 0 .. 23 loop
         declare
            Expected : constant String := Policy_Value ("sha1:" & Ada.Strings.Fixed.Trim (Integer'Image (P), Ada.Strings.Both));
         begin
            if Expected /= "" and then Expected /= Hex20 (Pcr_SHA1 (P)) then
               Add_Error ("pcr-mismatch");
            end if;
         end;
         declare
            Expected : constant String := Policy_Value ("sha256:" & Ada.Strings.Fixed.Trim (Integer'Image (P), Ada.Strings.Both));
         begin
            if Expected /= "" and then Expected /= Hex32 (Pcr_SHA256 (P)) then
               Add_Error ("pcr-mismatch");
            end if;
         end;
      end loop;
   end Check_Policy;

   procedure Write_Report is
      F : Ada.Text_IO.File_Type;
      First : Boolean;
   begin
      Ada.Text_IO.Create (F, Ada.Text_IO.Out_File, To_String (Out_Path));
      Ada.Text_IO.Put_Line (F, "{");
      Ada.Text_IO.Put_Line (F, "  ""accepted"": " & (if Errors.Is_Empty then "true," else "false,"));
      Ada.Text_IO.Put (F, "  ""errors"": [");
      First := True;
      for E of Errors loop
         if not First then Ada.Text_IO.Put (F, ", "); end if;
         Ada.Text_IO.Put (F, """" & E & """");
         First := False;
      end loop;
      Ada.Text_IO.Put_Line (F, "],");
      Ada.Text_IO.Put (F, "  ""warnings"": [");
      First := True;
      for W of Warnings loop
         if not First then Ada.Text_IO.Put (F, ", "); end if;
         Ada.Text_IO.Put (F, """" & W & """");
         First := False;
      end loop;
      Ada.Text_IO.Put_Line (F, "],");
      Ada.Text_IO.Put_Line (F, "  ""events"": " & Ada.Strings.Fixed.Trim (Integer'Image (Event_Count), Ada.Strings.Both) & ",");
      Ada.Text_IO.Put_Line (F, "  ""pcrs"": {");
      Ada.Text_IO.Put (F, "    ""sha1"": {");
      for P in 0 .. 23 loop
         if P > 0 then Ada.Text_IO.Put (F, ", "); end if;
         Ada.Text_IO.Put (F, """" & Ada.Strings.Fixed.Trim (Integer'Image (P), Ada.Strings.Both) & """: """ & Hex20 (Pcr_SHA1 (P)) & """");
      end loop;
      Ada.Text_IO.Put_Line (F, "},");
      Ada.Text_IO.Put (F, "    ""sha256"": {");
      for P in 0 .. 23 loop
         if P > 0 then Ada.Text_IO.Put (F, ", "); end if;
         Ada.Text_IO.Put (F, """" & Ada.Strings.Fixed.Trim (Integer'Image (P), Ada.Strings.Both) & """: """ & Hex32 (Pcr_SHA256 (P)) & """");
      end loop;
      Ada.Text_IO.Put_Line (F, "}");
      Ada.Text_IO.Put_Line (F, "  }");
      Ada.Text_IO.Put_Line (F, "}");
      Ada.Text_IO.Close (F);
   end Write_Report;

begin
   for I in 1 .. Ada.Command_Line.Argument_Count loop
      if Ada.Command_Line.Argument (I) = "--policy" and then I < Ada.Command_Line.Argument_Count then
         Policy_Path := To_Unbounded_String (Ada.Command_Line.Argument (I + 1));
      elsif Ada.Command_Line.Argument (I) = "--events" and then I < Ada.Command_Line.Argument_Count then
         Events_Dir := To_Unbounded_String (Ada.Command_Line.Argument (I + 1));
      elsif Ada.Command_Line.Argument (I) = "--out" and then I < Ada.Command_Line.Argument_Count then
         Out_Path := To_Unbounded_String (Ada.Command_Line.Argument (I + 1));
      end if;
   end loop;
   if Length (Policy_Path) = 0 or else Length (Events_Dir) = 0 or else Length (Out_Path) = 0 then
      Ada.Command_Line.Set_Exit_Status (2);
      return;
   end if;
   Policy_Text := To_Unbounded_String (Read_File_Text (To_String (Policy_Path)));
   Require_SHA1 := Contains (To_String (Policy_Text), "sha1");
   Require_SHA256 := Contains (To_String (Policy_Text), "sha256");
   Reject_Replay := Contains (To_String (Policy_Text), "true");
   Min_Authority := Parse_Int_After (To_String (Policy_Text), "min_authority_version", 0);
   Load_Files;
   for N of Files loop
      Process_File (To_String (Events_Dir) & "/" & N);
   end loop;
   Check_Policy;
   Write_Report;
   if Errors.Is_Empty then
      Ada.Command_Line.Set_Exit_Status (0);
   else
      Ada.Command_Line.Set_Exit_Status (1);
   end if;
end Pcrgate;
