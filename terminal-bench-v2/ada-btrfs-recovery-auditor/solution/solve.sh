#!/bin/bash
set -euo pipefail

cat > /app/btraudit/src/btraudit.adb <<'ADA'
with Ada.Command_Line;
with Ada.Directories;
with Ada.Strings;
with Ada.Strings.Fixed;
with Ada.Strings.Unbounded;
with Ada.Streams.Stream_IO;
with Ada.Text_IO;

procedure Btraudit is
   use Ada.Strings.Unbounded;
   use Ada.Text_IO;
   package SIO renames Ada.Streams.Stream_IO;

   Max_Files   : constant := 1200;
   Max_Extents : constant := 1200;
   Max_QGroups : constant := 200;
   Max_Subvols : constant := 200;
   Max_Vios    : constant := 1200;
   Max_Pending : constant := 1200;
   Max_Links   : constant := 400;
   Max_Txn     : constant := 64;

   type File_Rec is record
      Used : Boolean := False;
      Sv   : Integer := 0;
      Path : Unbounded_String;
      Ext  : Unbounded_String;
      Len  : Long_Integer := 0;
      Hash : Unbounded_String;
   end record;

   type Ext_Rec is record
      Used : Boolean := False;
      Id   : Unbounded_String;
      Len  : Long_Integer := 0;
      Hash : Unbounded_String;
      Have_Check : Boolean := False;
   end record;

   type Q_Rec is record
      Used       : Boolean := False;
      Id         : Integer := 0;
      Limit      : Long_Integer := 0;
      Referenced : Long_Integer := 0;
      Exclusive  : Long_Integer := 0;
   end record;

   type S_Rec is record
      Used : Boolean := False;
      Id   : Integer := 0;
      Qid  : Integer := 0;
   end record;

   type Link_Rec is record
      Used   : Boolean := False;
      Child  : Integer := 0;
      Parent : Integer := 0;
   end record;

   Files   : array (1 .. Max_Files) of File_Rec;
   Extents : array (1 .. Max_Extents) of Ext_Rec;
   QGroups : array (1 .. Max_QGroups) of Q_Rec;
   Subvols : array (1 .. Max_Subvols) of S_Rec;
   Vios    : array (1 .. Max_Vios) of Unbounded_String;
   Pending : array (1 .. Max_Pending) of Unbounded_String;
   Links   : array (1 .. Max_Links) of Link_Rec;
   Txn_Ids : array (1 .. Max_Txn) of Integer := (others => 0);
   VCount  : Natural := 0;
   PCount  : Natural := 0;
   TDepth  : Natural := 0;

   function Trimmed (S : String) return String is
   begin
      return Ada.Strings.Fixed.Trim (S, Ada.Strings.Both);
   end Trimmed;

   function Is_WS (C : Character) return Boolean is
   begin
      return C = ' ' or else C = ASCII.HT or else C = ASCII.LF or else C = ASCII.CR or else C = ASCII.VT or else C = ASCII.FF;
   end Is_WS;

   function Token (S : String; N : Positive) return String is
      I     : Integer := S'First;
      First : Integer := S'First;
      Count : Natural := 0;
   begin
      while I <= S'Last loop
         while I <= S'Last and then Is_WS (S (I)) loop
            I := I + 1;
         end loop;
         exit when I > S'Last;
         First := I;
         while I <= S'Last and then not Is_WS (S (I)) loop
            I := I + 1;
         end loop;
         Count := Count + 1;
         if Count = N then
            return S (First .. I - 1);
         end if;
      end loop;
      return "";
   end Token;

   procedure Add_Violation (S : String) is
   begin
      for I in 1 .. VCount loop
         if To_String (Vios (I)) = S then
            return;
         end if;
      end loop;
      if VCount < Max_Vios then
         VCount := VCount + 1;
         Vios (VCount) := To_Unbounded_String (S);
      end if;
   end Add_Violation;

   function Find_Q (Qid : Integer) return Natural is
   begin
      for I in QGroups'Range loop
         if QGroups (I).Used and then QGroups (I).Id = Qid then
            return I;
         end if;
      end loop;
      return 0;
   end Find_Q;

   function Ensure_Q (Qid : Integer) return Natural is
      Pos : Natural := Find_Q (Qid);
   begin
      if Pos /= 0 then
         return Pos;
      end if;
      for I in QGroups'Range loop
         if not QGroups (I).Used then
            QGroups (I).Used := True;
            QGroups (I).Id := Qid;
            return I;
         end if;
      end loop;
      return 0;
   end Ensure_Q;

    procedure Link_QGroup (Child, Parent : Integer) is
      Ign_Child  : Natural;
      Ign_Parent : Natural;
   begin
      Ign_Child := Ensure_Q (Child);
      Ign_Parent := Ensure_Q (Parent);
      for I in Links'Range loop
         if Links (I).Used and then Links (I).Child = Child and then Links (I).Parent = Parent then
            return;
         end if;
      end loop;
      for I in Links'Range loop
         if not Links (I).Used then
            Links (I).Used := True;
            Links (I).Child := Child;
            Links (I).Parent := Parent;
            return;
         end if;
       end loop;
    end Link_QGroup;

    procedure Unlink_QGroup (Child, Parent : Integer) is
    begin
       for I in Links'Range loop
          if Links (I).Used and then Links (I).Child = Child and then Links (I).Parent = Parent then
             Links (I).Used := False;
          end if;
       end loop;
    end Unlink_QGroup;

   function Is_Descendant (Child, Ancestor : Integer; Depth : Natural := 0) return Boolean is
   begin
      if Child = Ancestor then
         return True;
      elsif Depth > Max_Links then
         return False;
      end if;
      for I in Links'Range loop
         if Links (I).Used and then Links (I).Child = Child then
            if Is_Descendant (Links (I).Parent, Ancestor, Depth + 1) then
               return True;
            end if;
         end if;
      end loop;
      return False;
   end Is_Descendant;

   function Find_Subvol (Sid : Integer) return Natural is
   begin
      for I in Subvols'Range loop
         if Subvols (I).Used and then Subvols (I).Id = Sid then
            return I;
         end if;
      end loop;
      return 0;
   end Find_Subvol;

   procedure Set_Subvol (Sid, Qid : Integer) is
      Pos : Natural := Find_Subvol (Sid);
      Ign : Natural;
   begin
      Ign := Ensure_Q (Qid);
      if Pos = 0 then
         for I in Subvols'Range loop
            if not Subvols (I).Used then
               Pos := I;
               Subvols (I).Used := True;
               Subvols (I).Id := Sid;
               exit;
            end if;
         end loop;
      end if;
      if Pos /= 0 then
         Subvols (Pos).Qid := Qid;
      end if;
   end Set_Subvol;

   function Q_For_Subvol (Sid : Integer) return Integer is
      Pos : Natural := Find_Subvol (Sid);
   begin
      if Pos = 0 then
         return 0;
      end if;
      return Subvols (Pos).Qid;
   end Q_For_Subvol;

   function Find_Ext (Eid : String) return Natural is
   begin
      for I in Extents'Range loop
         if Extents (I).Used and then To_String (Extents (I).Id) = Eid then
            return I;
         end if;
      end loop;
      return 0;
   end Find_Ext;

   function Ensure_Ext (Eid : String; Len : Long_Integer; Hash : String; Is_Check : Boolean) return Natural is
      Pos : Natural := Find_Ext (Eid);
      Have : constant Boolean := Hash'Length > 0;
   begin
      if Pos = 0 then
         for I in Extents'Range loop
            if not Extents (I).Used then
               Pos := I;
               Extents (I).Used := True;
               Extents (I).Id := To_Unbounded_String (Eid);
               exit;
            end if;
         end loop;
      end if;
      if Pos /= 0 then
         if Len > 0 then
             Extents (Pos).Len := Len;
          end if;
          if Have then
             if Is_Check then
                if Length (Extents (Pos).Hash) > 0 and then To_String (Extents (Pos).Hash) /= Hash then
                   Add_Violation ("checksum " & Eid);
                end if;
                for I in Files'Range loop
                   if Files (I).Used and then To_String (Files (I).Ext) = Eid and then To_String (Files (I).Hash) /= Hash then
                      Add_Violation ("checksum " & Eid);
                   end if;
                end loop;
                Extents (Pos).Hash := To_Unbounded_String (Hash);
                Extents (Pos).Have_Check := True;
             elsif Extents (Pos).Have_Check then
                if To_String (Extents (Pos).Hash) /= Hash then
                   Add_Violation ("checksum " & Eid);
                end if;
             elsif Length (Extents (Pos).Hash) = 0 then
                Extents (Pos).Hash := To_Unbounded_String (Hash);
             elsif To_String (Extents (Pos).Hash) /= Hash then
                Add_Violation ("checksum " & Eid);
             end if;
          end if;
       end if;
       return Pos;
    end Ensure_Ext;

   function Find_File (Sid : Integer; Path : String) return Natural is
   begin
      for I in Files'Range loop
         if Files (I).Used and then Files (I).Sv = Sid and then To_String (Files (I).Path) = Path then
            return I;
         end if;
      end loop;
      return 0;
   end Find_File;

   procedure Upsert_File (Sid : Integer; Path, Eid : String; Len : Long_Integer; Hash : String) is
      Pos : Natural := Find_File (Sid, Path);
      Ign : Natural;
   begin
      Ign := Ensure_Ext (Eid, Len, Hash, False);
      if Pos = 0 then
         for I in Files'Range loop
            if not Files (I).Used then
               Pos := I;
               Files (I).Used := True;
               exit;
            end if;
         end loop;
      end if;
      if Pos /= 0 then
         Files (Pos).Sv := Sid;
         Files (Pos).Path := To_Unbounded_String (Path);
         Files (Pos).Ext := To_Unbounded_String (Eid);
         Files (Pos).Len := Len;
         Files (Pos).Hash := To_Unbounded_String (Hash);
      end if;
   end Upsert_File;

   procedure Rewrite_File (Sid : Integer; Path, Eid : String; Len : Long_Integer; Hash : String) is
      Pos : Natural := Find_File (Sid, Path);
      Ign : Natural;
   begin
      if Pos /= 0 then
         Ign := Ensure_Ext (Eid, Len, Hash, False);
         Files (Pos).Ext := To_Unbounded_String (Eid);
         Files (Pos).Len := Len;
         Files (Pos).Hash := To_Unbounded_String (Hash);
      end if;
   end Rewrite_File;

   procedure Move_File (Src_Sid : Integer; Src_Path : String; Dst_Sid : Integer; Dst_Path : String) is
      Src : Natural := Find_File (Src_Sid, Src_Path);
      Dst : Natural;
   begin
      if Src /= 0 and then Find_Subvol (Dst_Sid) /= 0 then
         Dst := Find_File (Dst_Sid, Dst_Path);
         if Dst /= 0 then
            Files (Dst).Used := False;
         end if;
         Files (Src).Sv := Dst_Sid;
         Files (Src).Path := To_Unbounded_String (Dst_Path);
      end if;
   end Move_File;

   procedure Scrub_Extent (Eid : String; Hash : String) is
      Pos : Natural := Find_Ext (Eid);
   begin
      if Pos /= 0 and then Length (Extents (Pos).Hash) > 0 and then To_String (Extents (Pos).Hash) /= Hash then
         Add_Violation ("checksum " & Eid);
      end if;
   end Scrub_Extent;

   procedure Apply (Line : String) is
      K   : constant String := Token (Line, 1);
      P   : Natural;
      Src : Natural;
   begin
      if K = "QGROUP" then
         P := Ensure_Q (Integer'Value (Token (Line, 2)));
         if P /= 0 then
            QGroups (P).Limit := Long_Integer'Value (Token (Line, 3));
         end if;
       elsif K = "LINK_QGROUP" then
          Link_QGroup (Integer'Value (Token (Line, 2)), Integer'Value (Token (Line, 3)));
      elsif K = "UNLINK_QGROUP" then
          Unlink_QGroup (Integer'Value (Token (Line, 2)), Integer'Value (Token (Line, 3)));
       elsif K = "SUBVOL" then
         Set_Subvol (Integer'Value (Token (Line, 2)), Integer'Value (Token (Line, 3)));
      elsif K = "SNAPSHOT" then
         if Integer'Value (Token (Line, 2)) /= Integer'Value (Token (Line, 3)) then
            for I in Files'Range loop
               if Files (I).Used and then Files (I).Sv = Integer'Value (Token (Line, 2)) then
                  Files (I).Used := False;
               end if;
            end loop;
         end if;
         Set_Subvol (Integer'Value (Token (Line, 2)), Integer'Value (Token (Line, 4)));
         for I in Files'Range loop
            if Files (I).Used and then Files (I).Sv = Integer'Value (Token (Line, 3)) then
               Upsert_File (Integer'Value (Token (Line, 2)), To_String (Files (I).Path), To_String (Files (I).Ext), Files (I).Len, To_String (Files (I).Hash));
            end if;
         end loop;
      elsif K = "CHECK" then
         P := Ensure_Ext (Token (Line, 2), 0, Token (Line, 3), True);
      elsif K = "SCRUB" then
         Scrub_Extent (Token (Line, 2), Token (Line, 3));
      elsif K = "WRITE" then
         Upsert_File (Integer'Value (Token (Line, 2)), Token (Line, 3), Token (Line, 4), Long_Integer'Value (Token (Line, 5)), Token (Line, 6));
      elsif K = "REWRITE" then
         Rewrite_File (Integer'Value (Token (Line, 2)), Token (Line, 3), Token (Line, 4), Long_Integer'Value (Token (Line, 5)), Token (Line, 6));
      elsif K = "CLONE" then
         Src := Find_File (Integer'Value (Token (Line, 2)), Token (Line, 3));
         if Src /= 0 then
            Upsert_File (Integer'Value (Token (Line, 2)), Token (Line, 4), To_String (Files (Src).Ext), Files (Src).Len, To_String (Files (Src).Hash));
         end if;
      elsif K = "MOVE" then
         Move_File (Integer'Value (Token (Line, 2)), Token (Line, 3), Integer'Value (Token (Line, 4)), Token (Line, 5));
      elsif K = "RENAME" then
         P := Find_File (Integer'Value (Token (Line, 2)), Token (Line, 3));
         if P /= 0 then
            declare
               Dst : Natural := Find_File (Integer'Value (Token (Line, 2)), Token (Line, 4));
            begin
               if Dst /= 0 then
                  Files (Dst).Used := False;
               end if;
            end;
            Files (P).Path := To_Unbounded_String (Token (Line, 4));
         end if;
      elsif K = "DELETE" then
         P := Find_File (Integer'Value (Token (Line, 2)), Token (Line, 3));
         if P /= 0 then
            Files (P).Used := False;
         end if;
      end if;
   end Apply;

   function Ref_Count (Eid : String) return Natural is
      C : Natural := 0;
   begin
      for I in Files'Range loop
         if Files (I).Used and then To_String (Files (I).Ext) = Eid then
            C := C + 1;
         end if;
      end loop;
      return C;
   end Ref_Count;

   procedure Compute_Quotas is
      File_Q : Integer;
      Any    : Boolean;
      All_In : Boolean;
   begin
      for I in QGroups'Range loop
         if QGroups (I).Used then
            QGroups (I).Referenced := 0;
            QGroups (I).Exclusive := 0;
         end if;
      end loop;

      for I in Files'Range loop
         if Files (I).Used then
            File_Q := Q_For_Subvol (Files (I).Sv);
            for Q in QGroups'Range loop
               if QGroups (Q).Used and then Is_Descendant (File_Q, QGroups (Q).Id) then
                  QGroups (Q).Referenced := QGroups (Q).Referenced + Files (I).Len;
               end if;
            end loop;
         end if;
      end loop;

      for E in Extents'Range loop
         if Extents (E).Used then
            for Q in QGroups'Range loop
               if QGroups (Q).Used then
                  Any := False;
                  All_In := True;
                  for I in Files'Range loop
                     if Files (I).Used and then To_String (Files (I).Ext) = To_String (Extents (E).Id) then
                        Any := True;
                        File_Q := Q_For_Subvol (Files (I).Sv);
                        if not Is_Descendant (File_Q, QGroups (Q).Id) then
                           All_In := False;
                        end if;
                     end if;
                  end loop;
                  if Any and then All_In then
                     QGroups (Q).Exclusive := QGroups (Q).Exclusive + Extents (E).Len;
                  end if;
               end if;
            end loop;
         end if;
      end loop;
   end Compute_Quotas;

   procedure Sort_Violations is
      T : Unbounded_String;
   begin
      for I in 1 .. VCount loop
         for J in I + 1 .. VCount loop
            if To_String (Vios (J)) < To_String (Vios (I)) then
               T := Vios (I);
               Vios (I) := Vios (J);
               Vios (J) := T;
            end if;
         end loop;
      end loop;
   end Sort_Violations;

   procedure Write_Output (Out_Dir : String) is
      F : File_Type;
      FC, EC, QC : Natural := 0;
   begin
      Ada.Directories.Create_Path (Out_Dir);
      Compute_Quotas;
      for I in Files'Range loop
         if Files (I).Used then FC := FC + 1; end if;
      end loop;
      for I in Extents'Range loop
         if Extents (I).Used and then Ref_Count (To_String (Extents (I).Id)) > 0 then EC := EC + 1; end if;
      end loop;
      for I in QGroups'Range loop
         if QGroups (I).Used then
            QC := QC + 1;
            if QGroups (I).Limit > 0 and then QGroups (I).Referenced > QGroups (I).Limit then
               Add_Violation ("quota-over " & Trimmed (Integer'Image (QGroups (I).Id)));
            end if;
         end if;
      end loop;
      Sort_Violations;
      Create (F, Out_File, Out_Dir & "/summary.json");
      Put_Line (F, "{""files"":" & Natural'Image (FC) & ",""extents"":" & Natural'Image (EC) & ",""qgroups"":" & Natural'Image (QC) & ",""violations"":" & Natural'Image (VCount) & "}");
      Close (F);
      Create (F, Out_File, Out_Dir & "/extents.tsv");
      Put_Line (F, "extent" & ASCII.HT & "len" & ASCII.HT & "hash" & ASCII.HT & "refs");
      declare
         Printed : array (Extents'Range) of Boolean := (others => False);
         Best    : Natural;
      begin
         loop
            Best := 0;
            for I in Extents'Range loop
               if Extents (I).Used and then not Printed (I) and then Ref_Count (To_String (Extents (I).Id)) > 0 then
                  if Best = 0 or else To_String (Extents (I).Id) < To_String (Extents (Best).Id) then
                     Best := I;
                  end if;
               end if;
            end loop;
            exit when Best = 0;
            Put_Line (F, To_String (Extents (Best).Id) & ASCII.HT & Trimmed (Long_Integer'Image (Extents (Best).Len)) & ASCII.HT & To_String (Extents (Best).Hash) & ASCII.HT & Trimmed (Natural'Image (Ref_Count (To_String (Extents (Best).Id)))));
            Printed (Best) := True;
         end loop;
      end;
      Close (F);
      Create (F, Out_File, Out_Dir & "/quotas.tsv");
      Put_Line (F, "qgroup" & ASCII.HT & "referenced" & ASCII.HT & "exclusive" & ASCII.HT & "limit" & ASCII.HT & "over");
      declare
         Printed : array (QGroups'Range) of Boolean := (others => False);
         Best    : Natural;
      begin
         loop
            Best := 0;
            for I in QGroups'Range loop
               if QGroups (I).Used and then not Printed (I) then
                  if Best = 0 or else QGroups (I).Id < QGroups (Best).Id then
                     Best := I;
                  end if;
               end if;
            end loop;
            exit when Best = 0;
            Put_Line (F, Trimmed (Integer'Image (QGroups (Best).Id)) & ASCII.HT & Trimmed (Long_Integer'Image (QGroups (Best).Referenced)) & ASCII.HT & Trimmed (Long_Integer'Image (QGroups (Best).Exclusive)) & ASCII.HT & Trimmed (Long_Integer'Image (QGroups (Best).Limit)) & ASCII.HT & (if QGroups (Best).Limit > 0 and then QGroups (Best).Referenced > QGroups (Best).Limit then "true" else "false"));
            Printed (Best) := True;
         end loop;
      end;
      Close (F);
      if VCount = 0 then
         declare
            Empty_File : SIO.File_Type;
         begin
            SIO.Create (Empty_File, SIO.Out_File, Out_Dir & "/violations.txt");
            SIO.Close (Empty_File);
         end;
      else
         Create (F, Out_File, Out_Dir & "/violations.txt");
         for I in 1 .. VCount loop
            Put_Line (F, To_String (Vios (I)));
         end loop;
         Close (F);
      end if;
   end Write_Output;

   procedure Replay_Record (Line : String) is
      K : constant String := Token (Line, 1);
      Id : Integer;
   begin
      if K = "BEGIN" then
         if TDepth < Max_Txn then
            TDepth := TDepth + 1;
            Txn_Ids (TDepth) := Integer'Value (Token (Line, 2));
         else
            TDepth := 0;
            PCount := 0;
         end if;
      elsif K = "COMMIT" then
         Id := Integer'Value (Token (Line, 2));
         if TDepth > 0 and then Txn_Ids (TDepth) = Id then
            TDepth := TDepth - 1;
            if TDepth = 0 then
               for I in 1 .. PCount loop
                  Apply (To_String (Pending (I)));
               end loop;
               PCount := 0;
            end if;
         else
            TDepth := 0;
            PCount := 0;
         end if;
      elsif K = "ABORT" then
         TDepth := 0;
         PCount := 0;
      elsif TDepth > 0 then
         if PCount < Max_Pending then
            PCount := PCount + 1;
            Pending (PCount) := To_Unbounded_String (Line);
         end if;
      else
         Apply (Line);
      end if;
   end Replay_Record;

   Case_Dir : Unbounded_String;
   Out_Dir  : Unbounded_String;
   Buffer   : Unbounded_String;
   Input    : File_Type;
begin
   for I in 1 .. Ada.Command_Line.Argument_Count loop
      if Ada.Command_Line.Argument (I) = "--case" and then I < Ada.Command_Line.Argument_Count then
         Case_Dir := To_Unbounded_String (Ada.Command_Line.Argument (I + 1));
      elsif Ada.Command_Line.Argument (I) = "--out" and then I < Ada.Command_Line.Argument_Count then
         Out_Dir := To_Unbounded_String (Ada.Command_Line.Argument (I + 1));
      end if;
   end loop;
    Open (Input, In_File, To_String (Case_Dir) & "/stream.rec");
    while not End_Of_File (Input) loop
       declare
          Raw : constant String := Get_Line (Input);
       begin
          for I in Raw'Range loop
             if Raw (I) = ';' then
                declare
                   Rec : constant String := Trimmed (To_String (Buffer));
                begin
                   if Rec'Length > 0 then
                      Replay_Record (Rec);
                   end if;
                end;
                Buffer := Null_Unbounded_String;
             else
                Append (Buffer, Raw (I));
             end if;
          end loop;
          Append (Buffer, ' ');
       end;
    end loop;
   Close (Input);
   Write_Output (To_String (Out_Dir));
exception
   when others =>
      Ada.Command_Line.Set_Exit_Status (1);
end Btraudit;
ADA

cd /app/btraudit
make clean
make
