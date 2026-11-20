with Ada.Command_Line;
with Ada.Directories;
with Ada.Streams;
with Ada.Streams.Stream_IO;
with Ada.Strings.Unbounded;
with Ada.Text_IO;
with Interfaces;

procedure Fsrecover is
   use Ada.Strings.Unbounded;
   use Interfaces;

   Max_Inodes  : constant Natural := 65535;
   Max_Blocks  : constant Natural := 65535;
   Max_Groups  : constant Natural := 65535;
   Max_Dirs    : constant Natural := 65535;

   type Byte_Array is array (Positive range <>) of Unsigned_8;

   type Inode_Info is record
      Used        : Boolean := False;
      Mode        : Natural := 0;
      Links       : Natural := 0;
      Size        : Natural := 0;
      First_Block : Natural := 0;
      Blocks      : Natural := 0;
      Next_Orphan : Natural := 0;
   end record;

   type Group_Info is record
      Used       : Boolean := False;
      Id         : Natural := 0;
      First      : Natural := 0;
      Count      : Natural := 0;
      Free_Hint  : Natural := 0;
   end record;

   type Dir_Info is record
      Used   : Boolean := False;
      Parent : Natural := 0;
      Child  : Natural := 0;
      Name   : Unbounded_String := Null_Unbounded_String;
   end record;

   type Inode_Array is array (Natural range 0 .. Max_Inodes) of Inode_Info;
   type Block_Array is array (Natural range 0 .. Max_Blocks) of Boolean;
   type Group_Array is array (Natural range 0 .. Max_Groups) of Group_Info;
   type Dir_Array is array (Natural range 1 .. Max_Dirs) of Dir_Info;

   Inodes       : Inode_Array := (others => <>);
   Bitmap       : Block_Array := (others => False);
   Groups       : Group_Array := (others => <>);
   Dirs         : Dir_Array := (others => <>);
   Dir_Count    : Natural := 0;
   Orphan_Head  : Natural := 0;
   Valid_Tx     : Natural := 0;
   Ignored_Tx   : Natural := 0;

   Bad_Image : exception;

   function Load_File (Path : String) return Byte_Array is
      package SIO renames Ada.Streams.Stream_IO;
      use Ada.Streams;
      F    : SIO.File_Type;
   begin
      SIO.Open (F, SIO.In_File, Path);
      declare
         Len  : constant Natural := Natural (SIO.Size (F));
         Raw  : Stream_Element_Array (1 .. Stream_Element_Offset (Len));
         Last : Stream_Element_Offset;
         Outb : Byte_Array (1 .. Len);
      begin
         SIO.Read (F, Raw, Last);
         SIO.Close (F);
         for I in 1 .. Len loop
            Outb (I) := Unsigned_8 (Raw (Stream_Element_Offset (I)));
         end loop;
         return Outb;
      end;
   end Load_File;

   function Get_U8 (D : Byte_Array; P : in out Natural) return Natural is
      V : Natural;
   begin
      if P > D'Last then
         raise Bad_Image;
      end if;
      V := Natural (D (P));
      P := P + 1;
      return V;
   end Get_U8;

   function Get_U16 (D : Byte_Array; P : in out Natural) return Natural is
      A : constant Natural := Get_U8 (D, P);
      B : constant Natural := Get_U8 (D, P);
   begin
      return A + B * 256;
   end Get_U16;

   function Get_U32 (D : Byte_Array; P : in out Natural) return Unsigned_32 is
      A : constant Unsigned_32 := Unsigned_32 (Get_U8 (D, P));
      B : constant Unsigned_32 := Unsigned_32 (Get_U8 (D, P));
      C : constant Unsigned_32 := Unsigned_32 (Get_U8 (D, P));
      E : constant Unsigned_32 := Unsigned_32 (Get_U8 (D, P));
   begin
      return A + Shift_Left (B, 8) + Shift_Left (C, 16) + Shift_Left (E, 24);
   end Get_U32;

   function Get_String (D : Byte_Array; P : in out Natural; Len : Natural) return String is
      S : String (1 .. Len);
   begin
      for I in 1 .. Len loop
         S (I) := Character'Val (Get_U8 (D, P));
      end loop;
      return S;
   end Get_String;

   function Sum_Bytes (D : Byte_Array; First : Natural; Last : Natural) return Unsigned_32 is
      S : Unsigned_32 := 0;
   begin
      if Last < First then
         return 0;
      end if;
      for I in First .. Last loop
         S := S + Unsigned_32 (D (I));
      end loop;
      return S;
   end Sum_Bytes;

   procedure Apply_Inode (Key : Natural; D : Byte_Array; P0 : Natural) is
      P : Natural := P0;
   begin
      if Key <= Max_Inodes then
         Inodes (Key).Used := True;
         Inodes (Key).Mode := Get_U8 (D, P);
         Inodes (Key).Links := Get_U8 (D, P);
         Inodes (Key).Size := Natural (Get_U32 (D, P));
         Inodes (Key).First_Block := Get_U16 (D, P);
         Inodes (Key).Blocks := Get_U16 (D, P);
         Inodes (Key).Next_Orphan := Get_U16 (D, P);
         if Inodes (Key).Mode = 0 then
            Inodes (Key).Used := False;
         end if;
      end if;
   end Apply_Inode;

   procedure Apply_Update (Kind : Natural; Key : Natural; D : Byte_Array; P0 : Natural; Len : Natural) is
      P : Natural := P0;
      L : Natural;
   begin
      if Kind = 1 and Len >= 12 then
         Apply_Inode (Key, D, P0);
      elsif Kind = 2 and Key <= Max_Blocks and Len >= 1 then
         Bitmap (Key) := Get_U8 (D, P) /= 0;
      elsif Kind = 3 and Key >= 1 and Key <= Max_Dirs and Len >= 6 then
         Dirs (Key).Used := Get_U8 (D, P) /= 0;
         Dirs (Key).Parent := Get_U16 (D, P);
         Dirs (Key).Child := Get_U16 (D, P);
         L := Get_U8 (D, P);
         if L <= Len - 6 then
            Dirs (Key).Name := To_Unbounded_String (Get_String (D, P, L));
            if Key > Dir_Count then
               Dir_Count := Key;
            end if;
         end if;
      elsif Kind = 4 and Key <= Max_Groups and Len >= 8 then
         Groups (Key).Used := True;
         Groups (Key).Id := Key;
         Groups (Key).First := Get_U16 (D, P);
         Groups (Key).Count := Get_U16 (D, P);
         Groups (Key).Free_Hint := Get_U16 (D, P);
      end if;
   end Apply_Update;

   procedure Parse_Base (Path : String) is
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
      for I in 0 .. GC - 1 loop
         Groups (I).Used := True;
         Groups (I).Id := Get_U16 (D, P);
         Groups (I).First := Get_U16 (D, P);
         Groups (I).Count := Get_U16 (D, P);
         Groups (I).Free_Hint := Get_U16 (D, P);
         declare
            Skip : constant Natural := Get_U16 (D, P);
         begin
            null;
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

   procedure Parse_Journal (Path : String) is
      D            : constant Byte_Array := Load_File (Path);
      P            : Natural := 1;
      Start_Pos    : Natural;
      Commit_Pos   : Natural;
      Kind         : Natural;
      Key          : Natural;
      Len          : Natural;
      Count        : Natural;
      Txid         : Unsigned_32;
      Commit_Txid  : Unsigned_32;
      Want         : Unsigned_32;
      Got          : Unsigned_32;
   begin
      if Get_String (D, P, 4) /= "JNL1" then
         raise Bad_Image;
      end if;
      while P <= D'Last loop
         Start_Pos := P;
         if Get_U8 (D, P) /= 16#D1# then
            Ignored_Tx := Ignored_Tx + 1;
            exit;
         end if;
         Txid := Get_U32 (D, P);
         Count := Get_U16 (D, P);
         for I in 1 .. Count loop
            Kind := Get_U8 (D, P);
            Key := Get_U16 (D, P);
            Len := Get_U16 (D, P);
            if P + Len - 1 > D'Last then
               Ignored_Tx := Ignored_Tx + 1;
               return;
            end if;
            if Kind /= 5 then
               Apply_Update (Kind, Key, D, P, Len);
            end if;
            P := P + Len;
         end loop;
         Commit_Pos := P;
         if Get_U8 (D, P) /= 16#C0# then
            Ignored_Tx := Ignored_Tx + 1;
            exit;
         end if;
         Commit_Txid := Get_U32 (D, P);
         Got := Get_U32 (D, P);
         Want := Sum_Bytes (D, Start_Pos, Commit_Pos - 1);
         if Commit_Txid = Txid and Got = Want then
            Valid_Tx := Valid_Tx + 1;
         else
            Ignored_Tx := Ignored_Tx + 1;
         end if;
      end loop;
   exception
      when Bad_Image =>
         Ignored_Tx := Ignored_Tx + 1;
   end Parse_Journal;

   function Name_OK (S : String) return Boolean is
   begin
      if S'Length = 0 or else S'Length > 32 then
         return False;
      end if;
      for C of S loop
         if C = '/' or else Character'Pos (C) = 0 then
            return False;
         end if;
      end loop;
      return True;
   end Name_OK;

   procedure Finish_State is
      Cur : Natural := Orphan_Head;
   begin
      if Cur >= 1 and then Cur <= Max_Inodes and then Inodes (Cur).Used and then Inodes (Cur).Links = 0 then
         if Inodes (Cur).Blocks > 0 then
            for B in Inodes (Cur).First_Block .. Inodes (Cur).First_Block + Inodes (Cur).Blocks - 1 loop
               if B <= Max_Blocks then
                  Bitmap (B) := False;
               end if;
            end loop;
         end if;
         Inodes (Cur).Used := False;
      end if;
   end Finish_State;

   function Mode_Name (M : Natural) return String is
   begin
      if M = 2 then
         return "dir";
      else
         return "file";
      end if;
   end Mode_Name;

   function Nat_Image (N : Natural) return String is
      S : constant String := Natural'Image (N);
   begin
      return S (S'First + 1 .. S'Last);
   end Nat_Image;

   function Quote (S : String) return String is
   begin
      return Character'Val (34) & S & Character'Val (34);
   end Quote;

   procedure Put_Names (F : Ada.Text_IO.File_Type; Ino : Natural) is
      First : Boolean := True;
   begin
      Ada.Text_IO.Put (F, "[");
      for I in 1 .. Dir_Count loop
         if Dirs (I).Used and then Dirs (I).Child = Ino then
            if not First then
               Ada.Text_IO.Put (F, ",");
            end if;
            First := False;
            Ada.Text_IO.Put (F, Quote (Nat_Image (Dirs (I).Parent) & "/" & To_String (Dirs (I).Name)));
         end if;
      end loop;
      Ada.Text_IO.Put (F, "]");
   end Put_Names;

   procedure Write_Output (Outdir : String) is
      F     : Ada.Text_IO.File_Type;
      First : Boolean := True;
      Free  : Natural;
   begin
      Ada.Directories.Create_Path (Outdir);
      Ada.Text_IO.Create (F, Ada.Text_IO.Out_File, Outdir & "/summary.json");
      Ada.Text_IO.Put (F, "{" & Quote ("valid_transactions") & ":" & Nat_Image (Valid_Tx));
      Ada.Text_IO.Put (F, "," & Quote ("ignored_transactions") & ":" & Nat_Image (Ignored_Tx));
      Ada.Text_IO.Put (F, "," & Quote ("allocated_blocks") & ":");
      Ada.Text_IO.Put (F, "[");
      First := True;
      for B in 0 .. Max_Blocks loop
         if Bitmap (B) then
            if not First then
               Ada.Text_IO.Put (F, ",");
            end if;
            First := False;
            Ada.Text_IO.Put (F, Nat_Image (B));
         end if;
      end loop;
      Ada.Text_IO.Put (F, "]," & Quote ("groups") & ":[");
      First := True;
      for G in Groups'Range loop
         if Groups (G).Used then
            Free := Groups (G).Free_Hint;
            if not First then
               Ada.Text_IO.Put (F, ",");
            end if;
            First := False;
            Ada.Text_IO.Put (F, "{" & Quote ("id") & ":" & Nat_Image (Groups (G).Id) & "," & Quote ("free_blocks") & ":" & Nat_Image (Free) & "}");
         end if;
      end loop;
      Ada.Text_IO.Put_Line (F, "]}");
      Ada.Text_IO.Close (F);

      Ada.Text_IO.Create (F, Ada.Text_IO.Out_File, Outdir & "/inodes.ndjson");
      for I in 1 .. Max_Inodes loop
         if Inodes (I).Used and then Inodes (I).Mode /= 0 then
            Ada.Text_IO.Put (F, "{" & Quote ("ino") & ":" & Nat_Image (I) & "," & Quote ("mode") & ":" & Quote (Mode_Name (Inodes (I).Mode)));
            Ada.Text_IO.Put (F, "," & Quote ("links") & ":" & Nat_Image (Inodes (I).Links) & "," & Quote ("size") & ":" & Nat_Image (Inodes (I).Size));
            Ada.Text_IO.Put (F, "," & Quote ("blocks") & ":[");
            First := True;
            if Inodes (I).Blocks > 0 then
               for B in Inodes (I).First_Block .. Inodes (I).First_Block + Inodes (I).Blocks - 1 loop
                  if not First then
                     Ada.Text_IO.Put (F, ",");
                  end if;
                  First := False;
                  Ada.Text_IO.Put (F, Nat_Image (B));
               end loop;
            end if;
            Ada.Text_IO.Put (F, "]," & Quote ("names") & ":");
            Put_Names (F, I);
            Ada.Text_IO.Put_Line (F, "}");
         end if;
      end loop;
      Ada.Text_IO.Close (F);
   end Write_Output;

   Base_Path    : Unbounded_String := Null_Unbounded_String;
   Journal_Path : Unbounded_String := Null_Unbounded_String;
   Out_Path     : Unbounded_String := Null_Unbounded_String;
   A            : Natural := 1;
begin
   while A <= Ada.Command_Line.Argument_Count loop
      declare
         Arg : constant String := Ada.Command_Line.Argument (A);
      begin
         if Arg = "--base" and then A < Ada.Command_Line.Argument_Count then
            A := A + 1;
            Base_Path := To_Unbounded_String (Ada.Command_Line.Argument (A));
         elsif Arg = "--journal" and then A < Ada.Command_Line.Argument_Count then
            A := A + 1;
            Journal_Path := To_Unbounded_String (Ada.Command_Line.Argument (A));
         elsif Arg = "--out" and then A < Ada.Command_Line.Argument_Count then
            A := A + 1;
            Out_Path := To_Unbounded_String (Ada.Command_Line.Argument (A));
         end if;
         A := A + 1;
      end;
   end loop;

   Parse_Base (To_String (Base_Path));
   Parse_Journal (To_String (Journal_Path));
   Finish_State;
   Write_Output (To_String (Out_Path));
end Fsrecover;
