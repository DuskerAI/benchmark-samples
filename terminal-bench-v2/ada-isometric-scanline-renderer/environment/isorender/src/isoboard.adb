with Ada.Command_Line;
with Ada.Directories;
with Ada.Strings;
with Ada.Strings.Fixed;
with Ada.Text_IO;
with Interfaces;

procedure Isoboard is
   use Ada.Command_Line;
   use Ada.Strings;
   use Ada.Strings.Fixed;
   use Ada.Text_IO;
   use Interfaces;

   Max_Items : constant Positive := 512;

   type Item_Kind is (Tile_Item, Sprite_Item, Overlay_Item);
   type Item is record
      Kind : Item_Kind := Tile_Item;
      X : Integer := 0;
      Y : Integer := 0;
      Z : Integer := 0;
      R1 : Integer := 0;
      G1 : Integer := 0;
      B1 : Integer := 0;
      R2 : Integer := 0;
      G2 : Integer := 0;
      B2 : Integer := 0;
      W : Integer := 0;
      H : Integer := 0;
      A : Integer := 0;
      Seed : Integer := 0;
      Index : Integer := 0;
   end record;

   type Item_List is array (Positive range 1 .. Max_Items) of Item;

   type Pixel is record
      R : Integer := 0;
      G : Integer := 0;
      B : Integer := 0;
      D1 : Integer := Integer'First;
      D2 : Integer := Integer'First;
      D3 : Integer := Integer'First;
      D4 : Integer := Integer'First;
      D5 : Integer := Integer'First;
      Set : Boolean := False;
   end record;

   type Canvas is array (Natural range <>, Natural range <>) of Pixel;
   type Canvas_Access is access Canvas;

   Width : Integer := 64;
   Height : Integer := 48;
   Origin_X : Integer := 0;
   Origin_Y : Integer := 0;
   Camera_X : Integer := 0;
   Camera_Y : Integer := 0;
   Tick : Integer := 0;
   Items : Item_List;
   Count : Natural := 0;

   function Next_Token (S : String; Pos : in out Natural) return String is
      Start : Natural;
   begin
      while Pos <= S'Last and then S (Pos) = ' ' loop
         Pos := Pos + 1;
      end loop;
      if Pos > S'Last then
         return "";
      end if;
      Start := Pos;
      while Pos <= S'Last and then S (Pos) /= ' ' loop
         Pos := Pos + 1;
      end loop;
      return S (Start .. Pos - 1);
   end Next_Token;

   function To_Int (S : String) return Integer is
   begin
      return Integer'Value (S);
   end To_Int;

   function Abs_Int (N : Integer) return Integer is
   begin
      if N < 0 then
         return -N;
      end if;
      return N;
   end Abs_Int;

   procedure Read_Scene (Path : String) is
      F : File_Type;
   begin
      Open (F, In_File, Path);
      while not End_Of_File (F) loop
         declare
            Raw : constant String := Get_Line (F);
            L : constant String := Trim (Raw, Both);
            P : Natural := L'First;
            C : constant String := (if L'Length = 0 then "" else Next_Token (L, P));
         begin
            if C = "size" then
               Width := To_Int (Next_Token (L, P));
               Height := To_Int (Next_Token (L, P));
            elsif C = "origin" then
               Origin_X := To_Int (Next_Token (L, P));
               Origin_Y := To_Int (Next_Token (L, P));
            elsif C = "camera" then
               Camera_X := To_Int (Next_Token (L, P));
               Camera_Y := To_Int (Next_Token (L, P));
            elsif C = "tick" then
               Tick := To_Int (Next_Token (L, P));
            elsif C = "tile" and then Count < Max_Items then
               Count := Count + 1;
               Items (Count).Kind := Tile_Item;
               Items (Count).X := To_Int (Next_Token (L, P));
               Items (Count).Y := To_Int (Next_Token (L, P));
               Items (Count).R1 := To_Int (Next_Token (L, P));
               Items (Count).G1 := To_Int (Next_Token (L, P));
               Items (Count).B1 := To_Int (Next_Token (L, P));
               Items (Count).Index := Count;
            elsif C = "sprite" and then Count < Max_Items then
               Count := Count + 1;
               Items (Count).Kind := Sprite_Item;
               Items (Count).X := To_Int (Next_Token (L, P));
               Items (Count).Y := To_Int (Next_Token (L, P));
               Items (Count).Z := To_Int (Next_Token (L, P));
               Items (Count).R1 := To_Int (Next_Token (L, P));
               Items (Count).G1 := To_Int (Next_Token (L, P));
               Items (Count).B1 := To_Int (Next_Token (L, P));
               Items (Count).R2 := To_Int (Next_Token (L, P));
               Items (Count).G2 := To_Int (Next_Token (L, P));
               Items (Count).B2 := To_Int (Next_Token (L, P));
               Items (Count).Seed := To_Int (Next_Token (L, P));
               Items (Count).Index := Count;
            elsif C = "overlay" and then Count < Max_Items then
               Count := Count + 1;
               Items (Count).Kind := Overlay_Item;
               Items (Count).X := To_Int (Next_Token (L, P));
               Items (Count).Y := To_Int (Next_Token (L, P));
               Items (Count).W := To_Int (Next_Token (L, P));
               Items (Count).H := To_Int (Next_Token (L, P));
               Items (Count).R1 := To_Int (Next_Token (L, P));
               Items (Count).G1 := To_Int (Next_Token (L, P));
               Items (Count).B1 := To_Int (Next_Token (L, P));
               Items (Count).A := To_Int (Next_Token (L, P));
               Items (Count).Index := Count;
            end if;
         end;
      end loop;
      Close (F);
   end Read_Scene;

   function Better (Old : Pixel; A, B, C, D, E : Integer) return Boolean is
   begin
      if not Old.Set then
         return True;
      end if;
      if A /= Old.D1 then
         return A > Old.D1;
      elsif B /= Old.D2 then
         return B > Old.D2;
      elsif C /= Old.D3 then
         return C > Old.D3;
      elsif D /= Old.D4 then
         return D > Old.D4;
      else
         return E > Old.D5;
      end if;
   end Better;

   procedure Put_Pixel (C : in out Canvas; X, Y, R, G, B, D1, D2, D3, D4, D5 : Integer) is
   begin
      if X >= 0 and then X < Width and then Y >= 0 and then Y < Height then
         if Better (C (Y, X), D1, D2, D3, D4, D5) then
            C (Y, X).R := R;
            C (Y, X).G := G;
            C (Y, X).B := B;
            C (Y, X).D1 := D1;
            C (Y, X).D2 := D2;
            C (Y, X).D3 := D3;
            C (Y, X).D4 := D4;
            C (Y, X).D5 := D5;
            C (Y, X).Set := True;
         end if;
      end if;
   end Put_Pixel;

   procedure Render (C : in out Canvas) is
   begin
      for I in 1 .. Count loop
         declare
            It : Item renames Items (I);
            CX : constant Integer := Origin_X - Camera_X + (It.X - It.Y) * 8;
            CY : constant Integer := Origin_Y + (It.X + It.Y) * 4;
            R : Integer := It.R1;
            G : Integer := It.G1;
            B : Integer := It.B1;
            D2 : Integer := 0;
            D5 : Integer := It.Index;
         begin
            if It.Kind = Tile_Item then
               for PY in CY - 4 .. CY + 4 loop
                  for PX in CX - 8 .. CX + 8 loop
                     if Abs_Int (PX - CX) * 4 + Abs_Int (PY - CY) * 8 < 32 then
                        Put_Pixel (C, PX, PY, R, G, B, It.X + It.Y, 0, It.X, It.Y, D5);
                     end if;
                  end loop;
               end loop;
             elsif It.Kind = Sprite_Item then
                if It.Seed mod 2 /= 0 then
                   R := It.R2;
                   G := It.G2;
                  B := It.B2;
               end if;
               D2 := 0;
               D5 := 0;
               for DY in -3 .. 3 loop
                  for DX in -2 .. 2 loop
                     if Abs_Int (DX) <= 2 and then Abs_Int (DY) <= 3 and then Abs_Int (DX) + Abs_Int (DY) <= 4 then
                        Put_Pixel (C, CX + DX, CY - It.Z + DY, R, G, B, It.X + It.Y, D2, It.X, It.Y, D5);
                      end if;
                   end loop;
                end loop;
             else
                for PY in It.Y .. It.Y + It.H - 1 loop
                   for PX in It.X .. It.X + It.W - 1 loop
                      if PX >= 0 and then PX < Width and then PY >= 0 and then PY < Height then
                         C (PY, PX).R := (C (PY, PX).R * (255 - It.A) + It.R1 * It.A) / 256;
                         C (PY, PX).G := (C (PY, PX).G * (255 - It.A) + It.G1 * It.A) / 256;
                         C (PY, PX).B := (C (PY, PX).B * (255 - It.A) + It.B1 * It.A) / 256;
                      end if;
                   end loop;
                end loop;
             end if;
         end;
      end loop;
   end Render;

   function Hex64 (N : Unsigned_64) return String is
      Hex : constant String := "0123456789abcdef";
      V : Unsigned_64 := N;
      Res : String (1 .. 16);
   begin
      for I in reverse Res'Range loop
         Res (I) := Hex (Integer (V mod 16) + 1);
         V := V / 16;
      end loop;
      return Res;
   end Hex64;

   function Hash_Image (C : Canvas) return String is
      H : Unsigned_64 := 14695981039346656037;
      Prime : constant Unsigned_64 := 1099511628211;
      procedure Add_Byte (B : Integer) is
      begin
         H := (H xor Unsigned_64 (B mod 256)) * Prime;
      end Add_Byte;
   begin
      for X in 0 .. Width - 1 loop
         for Y in 0 .. Height - 1 loop
            Add_Byte (C (Y, X).R);
            Add_Byte (C (Y, X).G);
            Add_Byte (C (Y, X).B);
         end loop;
      end loop;
      return Hex64 (H);
   end Hash_Image;

   procedure Write_PPM (C : Canvas; Path : String) is
      F : File_Type;
   begin
      Create (F, Out_File, Path);
      Put_Line (F, "P3");
      Put_Line (F, Trim (Integer'Image (Width), Both) & " " & Trim (Integer'Image (Height), Both));
      Put_Line (F, "255");
      for Y in 0 .. Height - 1 loop
         for X in 0 .. Width - 1 loop
            Put (F, Trim (Integer'Image (C (Y, X).R), Both) & " " & Trim (Integer'Image (C (Y, X).G), Both) & " " & Trim (Integer'Image (C (Y, X).B), Both));
            if X < Width - 1 then
               Put (F, " ");
            end if;
         end loop;
         New_Line (F);
      end loop;
      Close (F);
   end Write_PPM;

   procedure Write_State (C : Canvas; Path : String) is
      F : File_Type;
   begin
      Create (F, Out_File, Path);
      Put_Line (F, "{""width"":" & Trim (Integer'Image (Width), Both) & ",""height"":" & Trim (Integer'Image (Height), Both) & ",""tick"":" & Trim (Integer'Image (Tick), Both) & ",""hash"":""" & Hash_Image (C) & """}");
      Close (F);
   end Write_State;

begin
   if Argument_Count /= 3 or else Argument (1) /= "render" then
      Put_Line (Standard_Error, "usage: isoboard render <scene-file> <output-dir>");
      Set_Exit_Status (Failure);
      return;
   end if;

   Read_Scene (Argument (2));

   declare
      C : Canvas_Access := new Canvas (0 .. Natural (Height - 1), 0 .. Natural (Width - 1));
      Out_Dir : constant String := Argument (3);
   begin
      if not Ada.Directories.Exists (Out_Dir) then
         Ada.Directories.Create_Directory (Out_Dir);
      end if;
      Render (C.all);
      Write_PPM (C.all, Out_Dir & "/board.ppm");
      Write_State (C.all, Out_Dir & "/state.json");
   end;
end Isoboard;
