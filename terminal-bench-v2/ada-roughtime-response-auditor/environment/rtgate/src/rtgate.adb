with Ada.Command_Line;
with Ada.Directories;
with Ada.Strings.Fixed;
with Ada.Strings.Unbounded;
with Ada.Text_IO;
with Ada.Containers.Vectors;

procedure Rtgate is
   use Ada.Strings.Unbounded;

   type Item is record
      Id       : Unbounded_String;
      Source   : Unbounded_String;
      Server   : Unbounded_String;
      Root     : Unbounded_String;
      Midpoint : Unbounded_String;
      Radius   : Unbounded_String;
      Nonce    : Unbounded_String;
      Prev_Hash  : Unbounded_String;
      Chain_Hash : Unbounded_String;
      Delegate_Sig : Unbounded_String;
      Response_Sig : Unbounded_String;
      Reason   : Unbounded_String;
      Ok       : Boolean := False;
   end record;

   package Items is new Ada.Containers.Vectors (Index_Type => Natural, Element_Type => Item);
   type Revocation is record
      Root    : Unbounded_String;
      Server  : Unbounded_String;
      Start_T : Long_Long_Integer := 0;
      End_T   : Long_Long_Integer := 0;
   end record;
   package Revocations is new Ada.Containers.Vectors (Index_Type => Natural, Element_Type => Revocation);
   Results : Items.Vector;
   Revoked : Revocations.Vector;

   function Arg_Value (Name : String) return String is
   begin
      for I in 1 .. Ada.Command_Line.Argument_Count - 1 loop
         if Ada.Command_Line.Argument (I) = Name then
            return Ada.Command_Line.Argument (I + 1);
         end if;
      end loop;
      return "";
   end Arg_Value;

   function Json (S : String) return String is
      R : Unbounded_String;
   begin
      Append (R, '"');
      for C of S loop
         if C = '"' or else C = '\' then
            Append (R, '\');
         end if;
         Append (R, C);
      end loop;
      Append (R, '"');
      return To_String (R);
   end Json;

   procedure Load_Trust_State (Path : String) is
      F : Ada.Text_IO.File_Type;
      Line : String (1 .. 4096);
      Last : Natural;
   begin
      Ada.Text_IO.Open (F, Ada.Text_IO.In_File, Path);
      while not Ada.Text_IO.End_Of_File (F) loop
         Ada.Text_IO.Get_Line (F, Line, Last);
         declare
            S : constant String := Line (1 .. Last);
            P1 : constant Natural := Ada.Strings.Fixed.Index (S, " ");
         begin
            if P1 > 0 and then S (S'First .. P1 - 1) = "revoke" then
               declare
                  Rest1 : constant String := S (P1 + 1 .. S'Last);
                  P2 : constant Natural := Ada.Strings.Fixed.Index (Rest1, " ");
               begin
                  if P2 > 1 then
                     declare
                        Root_Name : constant String := Rest1 (Rest1'First .. P2 - 1);
                        Rest2 : constant String := Rest1 (P2 + 1 .. Rest1'Last);
                        P3 : constant Natural := Ada.Strings.Fixed.Index (Rest2, " ");
                     begin
                        if P3 > 1 then
                           declare
                              Server_Name : constant String := Rest2 (Rest2'First .. P3 - 1);
                              Rest3 : constant String := Rest2 (P3 + 1 .. Rest2'Last);
                              P4 : constant Natural := Ada.Strings.Fixed.Index (Rest3, " ");
                           begin
                              if P4 > 1 then
                                 Revoked.Append (Revocation'(Root => To_Unbounded_String (Root_Name), Server => To_Unbounded_String (Server_Name), Start_T => Long_Long_Integer'Value (Rest3 (Rest3'First .. P4 - 1)), End_T => Long_Long_Integer'Value (Rest3 (P4 + 1 .. Rest3'Last))));
                              end if;
                           exception
                              when others => null;
                           end;
                        end if;
                     end;
                  end if;
               end;
            end if;
         end;
      end loop;
      Ada.Text_IO.Close (F);
   exception
      when others =>
         if Ada.Text_IO.Is_Open (F) then Ada.Text_IO.Close (F); end if;
   end Load_Trust_State;

   function Is_Revoked (Root_Name : String; Server_Name : String; Mid : Long_Long_Integer) return Boolean is
   begin
      for R of Revoked loop
         if To_String (R.Root) = Root_Name and then To_String (R.Server) = Server_Name and then Mid >= R.Start_T and then Mid <= R.End_T then
            return True;
         end if;
      end loop;
      return False;
   end Is_Revoked;

   procedure Read_File (Path : String; Base : String; Now_Text : String) is
      F : Ada.Text_IO.File_Type;
      Line : String (1 .. 4096);
      Last : Natural;
      R : Item;
      Need : Natural := 0;
   begin
      R.Source := To_Unbounded_String (Base);
      Ada.Text_IO.Open (F, Ada.Text_IO.In_File, Path);
      if Ada.Text_IO.End_Of_File (F) then
         R.Reason := To_Unbounded_String ("malformed");
         Results.Append (R);
         return;
      end if;
      Ada.Text_IO.Get_Line (F, Line, Last);
      if Line (1 .. Last) /= "RTE1" then
         R.Reason := To_Unbounded_String ("malformed");
      end if;
      while not Ada.Text_IO.End_Of_File (F) loop
         Ada.Text_IO.Get_Line (F, Line, Last);
         declare
            S : constant String := Line (1 .. Last);
            P : constant Natural := Ada.Strings.Fixed.Index (S, ": ");
         begin
            if P > 1 then
               declare
                  K : constant String := S (S'First .. P - 1);
                  V : constant String := S (P + 2 .. S'Last);
               begin
                   if K = "id" then R.Id := To_Unbounded_String (V); Need := Need + 1;
                    elsif K = "server" then R.Server := To_Unbounded_String (V); Need := Need + 1;
                    elsif K = "root" then R.Root := To_Unbounded_String (V); Need := Need + 1;
                    elsif K = "midpoint" then R.Midpoint := To_Unbounded_String (V); Need := Need + 1;
                     elsif K = "radius" then R.Radius := To_Unbounded_String (V); Need := Need + 1;
                     elsif K = "nonce" then R.Nonce := To_Unbounded_String (V); Need := Need + 1;
                    elsif K = "prev-hash" then R.Prev_Hash := To_Unbounded_String (V); Need := Need + 1;
                    elsif K = "chain-hash" then R.Chain_Hash := To_Unbounded_String (V); Need := Need + 1;
                    elsif K = "delegate-sig" then R.Delegate_Sig := To_Unbounded_String (V); Need := Need + 1;
                    elsif K = "response-sig" then R.Response_Sig := To_Unbounded_String (V); Need := Need + 1;
                    end if;
               end;
            end if;
         end;
      end loop;
      Ada.Text_IO.Close (F);
      if Length (R.Reason) = 0 then
          if Need < 9 or else Length (R.Id) = 0 then
            R.Reason := To_Unbounded_String ("malformed");
          elsif Is_Revoked (To_String (R.Root), To_String (R.Server), Long_Long_Integer'Value (To_String (R.Midpoint))) then
             R.Reason := To_Unbounded_String ("bad-delegation");
          elsif Long_Long_Integer'Value (To_String (R.Midpoint)) > Long_Long_Integer'Value (Now_Text) then
             R.Reason := To_Unbounded_String ("stale");
         else
            R.Ok := True;
         end if;
      end if;
      Results.Append (R);
   exception
      when others =>
         begin
            if Ada.Text_IO.Is_Open (F) then
               Ada.Text_IO.Close (F);
            end if;
         exception
            when others => null;
         end;
         R.Ok := False;
         R.Reason := To_Unbounded_String ("malformed");
         Results.Append (R);
   end Read_File;

   Trust : constant String := Arg_Value ("--trust");
   Dir   : constant String := Arg_Value ("--evidence");
   Now_T : constant String := Arg_Value ("--now");
   Outp  : constant String := Arg_Value ("--out");
   Search : Ada.Directories.Search_Type;
    Ent : Ada.Directories.Directory_Entry_Type;
    Accepted, Rejected : Natural := 0;
    OutF : Ada.Text_IO.File_Type;
begin
   if Trust = "" or else Dir = "" or else Now_T = "" or else Outp = "" then
      Ada.Command_Line.Set_Exit_Status (2);
      return;
   end if;

   Load_Trust_State (Trust);

   Ada.Directories.Start_Search (Search, Dir, "*.rte", (Ada.Directories.Ordinary_File => True, others => False));
   while Ada.Directories.More_Entries (Search) loop
      Ada.Directories.Get_Next_Entry (Search, Ent);
      Read_File (Ada.Directories.Full_Name (Ent), Ada.Directories.Simple_Name (Ent), Now_T);
   end loop;
    Ada.Directories.End_Search (Search);

   for I in Results.First_Index .. Results.Last_Index loop
      declare
         R : Item := Results.Element (I);
      begin
         if R.Ok and then Length (R.Nonce) > 0 and then I > Results.First_Index then
            for J in Results.First_Index .. I - 1 loop
               declare
                  P : constant Item := Results.Element (J);
               begin
                  if P.Ok and then To_String (P.Server) = To_String (R.Server) and then To_String (P.Nonce) = To_String (R.Nonce) then
                     R.Ok := False;
                     R.Reason := To_Unbounded_String ("bad-chain");
                  end if;
               end;
            end loop;
            Results.Replace_Element (I, R);
         end if;
      end;
   end loop;

    for R of Results loop
      if R.Ok then Accepted := Accepted + 1; else Rejected := Rejected + 1; end if;
   end loop;

   Ada.Text_IO.Create (OutF, Ada.Text_IO.Out_File, Outp);
   Ada.Text_IO.Put (OutF, "{""accepted"":");
   Ada.Text_IO.Put (OutF, "[");
   declare
      First : Boolean := True;
   begin
      for R of Results loop
         if R.Ok then
            if not First then Ada.Text_IO.Put (OutF, ","); end if;
            First := False;
            Ada.Text_IO.Put (OutF, "{""id"":" & Json (To_String (R.Id)) & ",""source"":" & Json (To_String (R.Source)) & ",""server"":" & Json (To_String (R.Server)) & ",""midpoint"":" & To_String (R.Midpoint) & ",""radius"":" & To_String (R.Radius) & "}");
         end if;
      end loop;
   end;
   Ada.Text_IO.Put (OutF, "],""rejected"":[");
   declare
      First : Boolean := True;
   begin
      for R of Results loop
         if not R.Ok then
            if not First then Ada.Text_IO.Put (OutF, ","); end if;
            First := False;
            Ada.Text_IO.Put (OutF, "{""id"":" & Json (To_String (R.Id)) & ",""source"":" & Json (To_String (R.Source)) & ",""reason"":" & Json (To_String (R.Reason)) & "}");
         end if;
      end loop;
   end;
   Ada.Text_IO.Put_Line (OutF, "],""summary"":{""accepted"":" & Natural'Image (Accepted) & ",""rejected"":" & Natural'Image (Rejected) & "}}");
   Ada.Text_IO.Close (OutF);
end Rtgate;
