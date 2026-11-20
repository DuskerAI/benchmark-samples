with Ada.Command_Line; use Ada.Command_Line;
with Ada.Text_IO; use Ada.Text_IO;
with Ada.Strings; use Ada.Strings;
with Ada.Strings.Fixed; use Ada.Strings.Fixed;
with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;
with Ada.Characters.Handling; use Ada.Characters.Handling;

procedure Nft_Policy is
   Max_Tokens : constant := 256;
   Max_Chains : constant := 128;
   Max_Sets   : constant := 128;
   Max_Rules  : constant := 512;

   subtype U is Unbounded_String;

   type Token_Array is array (Positive range 1 .. Max_Tokens) of U;
   type Token_List is record
      Count : Natural := 0;
      Data  : Token_Array;
   end record;

   type Chain_Rec is record
      Family     : U;
      Table_Name : U;
      Chain_Name : U;
      Hook       : U;
      Priority   : Integer := 0;
      Policy     : U;
      Source_Pos : Natural := 0;
   end record;

   type Set_Rec is record
      Name  : U;
      Value : U;
   end record;

   type Define_Rec is record
      Name  : U;
      Value : U;
   end record;

   type Name_Class is (Address_Set, Port_Set, Interface_Zone);

   type Name_Rec is record
      Name  : U;
      Kind  : Name_Class := Address_Set;
      Value : U;
   end record;

   type Rule_Rec is record
      Family     : U;
      Table_Name : U;
      Chain_Name : U;
      Hook       : U;
      Priority   : Integer := 0;
      Policy     : U;
      Ct         : U;
      Saddr      : U;
      Sport      : U;
      Dport      : U;
      Proto      : U;
      Tcpflags   : U;
      Uid        : U;
      Mark       : U;
      Iif        : U;
      Oif        : U;
      Action     : U;
      Source_Pos : Natural := 0;
   end record;

   type Chain_Array is array (Positive range 1 .. Max_Chains) of Chain_Rec;
   type Set_Array   is array (Positive range 1 .. Max_Sets) of Set_Rec;
   type Define_Array is array (Positive range 1 .. Max_Sets) of Define_Rec;
   type Name_Array  is array (Positive range 1 .. Max_Sets) of Name_Rec;
   type Rule_Array  is array (Positive range 1 .. Max_Rules) of Rule_Rec;

   type Policy_Rec is record
      Chains      : Chain_Array;
      Chain_Count : Natural := 0;
      Sets        : Set_Array;
      Set_Count   : Natural := 0;
      Defines     : Define_Array;
      Define_Count : Natural := 0;
      Names       : Name_Array;
      Name_Count  : Natural := 0;
      Rules       : Rule_Array;
      Rule_Count  : Natural := 0;
   end record;

   function S (X : U) return String is (To_String (X));

   function Clean_Line (Line : String) return String is
      Last : Natural := Line'Last;
   begin
      for I in Line'Range loop
         if Line (I) = '#' then
            Last := I - 1;
            exit;
         end if;
      end loop;
      if Last < Line'First then
         return "";
      end if;
      return Trim (Line (Line'First .. Last), Both);
   end Clean_Line;

   function Tokenize (Line : String) return Token_List is
      T   : Token_List;
      Cur : U := Null_Unbounded_String;

      procedure Push is
      begin
         if Length (Cur) > 0 then
            T.Count := T.Count + 1;
            T.Data (T.Count) := Cur;
            Cur := Null_Unbounded_String;
         end if;
      end Push;
   begin
      for Ch of Line loop
         if Ch = ' ' or else Ch = ASCII.HT or else Ch = '{' or else Ch = '}' or else Ch = ',' or else Ch = '=' then
            Push;
         else
            Append (Cur, Ch);
         end if;
      end loop;
      Push;
      return T;
   end Tokenize;

   function Join_Tokens (T : Token_List; First, Last : Positive) return String is
      R : U := Null_Unbounded_String;
   begin
      if Last < First or else First > T.Count then
         return "";
      end if;
      for I in First .. Natural'Min (Last, T.Count) loop
         if Length (R) > 0 then
            Append (R, " ");
         end if;
         Append (R, S (T.Data (I)));
      end loop;
      return S (R);
   end Join_Tokens;

   function Lower (Text : String) return String is
      R : String := Text;
   begin
      for I in R'Range loop
         R (I) := To_Lower (R (I));
      end loop;
      return R;
   end Lower;

   function Img (Value : Integer) return String is
   begin
      return Trim (Integer'Image (Value), Both);
   end Img;

   function Parse_Priority (Text : String) return Integer is
       L : constant String := Lower (Trim (Text, Both));
      Offset_Pos : Natural := 0;
      Base       : Integer := 0;
   begin
      for I in L'Range loop
         if I > L'First and then (L (I) = '+' or else L (I) = '-') then
            Offset_Pos := I;
            exit;
         end if;
      end loop;
      if Offset_Pos /= 0 then
         Base := Parse_Priority (L (L'First .. Offset_Pos - 1));
         return Base + Integer'Value (L (Offset_Pos .. L'Last));
      end if;
      if L = "dstnat" then
         return -100;
      elsif L = "mangle" then
         return -150;
      elsif L = "filter" then
         return 0;
      elsif L = "srcnat" then
         return 100;
      else
         return Integer'Value (L);
      end if;
   end Parse_Priority;

   procedure Add_Define (P : in out Policy_Rec; T : Token_List) is
   begin
      if T.Count >= 3 then
         P.Define_Count := P.Define_Count + 1;
         P.Defines (P.Define_Count).Name := To_Unbounded_String (Lower (S (T.Data (2))));
         P.Defines (P.Define_Count).Value := To_Unbounded_String (Join_Tokens (T, 3, T.Count));
      end if;
   end Add_Define;

   function Nimg (Value : Natural) return String is
   begin
      return Trim (Natural'Image (Value), Both);
   end Nimg;

   function Strip_One_Hextet (Text : String) return String is
      P : Positive := Text'First;
   begin
      while P < Text'Last and then Text (P) = '0' loop
         P := P + 1;
      end loop;
      return Text (P .. Text'Last);
   end Strip_One_Hextet;

   function Normalize_IPv6_Text (Text : String) return String is
      L   : constant String := Lower (Text);
      R   : U := Null_Unbounded_String;
      Beg : Positive := L'First;
   begin
      for I in L'Range loop
         if L (I) = ':' then
            if I > Beg then
               Append (R, Strip_One_Hextet (L (Beg .. I - 1)));
            end if;
            Append (R, ":");
            Beg := I + 1;
         end if;
      end loop;
      if Beg <= L'Last then
         Append (R, Strip_One_Hextet (L (Beg .. L'Last)));
      end if;
      return S (R);
   end Normalize_IPv6_Text;

   function Normalize_Address (Text : String) return String is
      V : constant String := Lower (Trim (Text, Both));
      Dash : Natural := 0;
   begin
      for I in V'Range loop
         if V (I) = '-' then
            Dash := I;
            exit;
         end if;
      end loop;
      if Dash /= 0 then
         return Normalize_Address (V (V'First .. Dash - 1)) & "-" & Normalize_Address (V (Dash + 1 .. V'Last));
      elsif Index (V, ":") /= 0 then
         return Normalize_IPv6_Text (V);
      else
         return V;
      end if;
   end Normalize_Address;

   function Find_Set (P : Policy_Rec; Name : String) return String is
   begin
      for I in 1 .. P.Set_Count loop
         if S (P.Sets (I).Name) = Name then
            return S (P.Sets (I).Value);
         end if;
      end loop;
      return Name;
   end Find_Set;

   function Normalize_Address_List (Text : String; P : Policy_Rec) return String is
      T : constant Token_List := Tokenize (Text);
      R : U := Null_Unbounded_String;
      V : String := Trim (Text, Both);
   begin
      if V'Length = 0 then
         return "*";
      end if;
      if V (V'First) = '@' then
         return Find_Set (P, V (V'First + 1 .. V'Last));
      end if;
      for I in 1 .. T.Count loop
         if Length (R) > 0 then
            Append (R, ",");
         end if;
         Append (R, Normalize_Address (S (T.Data (I))));
      end loop;
      return S (R);
   end Normalize_Address_List;

   function Normalize_Ports (Text : String) return String is
      T : constant Token_List := Tokenize (Text);
      R : U := Null_Unbounded_String;
   begin
      if Trim (Text, Both)'Length = 0 then
         return "*";
      end if;
      for I in 1 .. T.Count loop
         if Length (R) > 0 then
            Append (R, ",");
         end if;
         Append (R, S (T.Data (I)));
      end loop;
      return S (R);
   end Normalize_Ports;

   function Normalize_Ct (Text : String) return String is
      T : constant Token_List := Tokenize (Lower (Text));
      R : U := Null_Unbounded_String;
   begin
      if Trim (Text, Both)'Length = 0 then
         return "*";
      end if;
      for I in 1 .. T.Count loop
         if Length (R) > 0 then
            Append (R, ",");
         end if;
         Append (R, S (T.Data (I)));
      end loop;
      return S (R);
   end Normalize_Ct;

   function Find_Chain (P : Policy_Rec; Family, Table_Name, Chain_Name : String) return Chain_Rec is
      C : Chain_Rec;
   begin
      for I in 1 .. P.Chain_Count loop
         if S (P.Chains (I).Family) = Family and then S (P.Chains (I).Table_Name) = Table_Name and then S (P.Chains (I).Chain_Name) = Chain_Name then
            return P.Chains (I);
         end if;
      end loop;
      C.Family := To_Unbounded_String (Family);
      C.Table_Name := To_Unbounded_String (Table_Name);
      C.Chain_Name := To_Unbounded_String (Chain_Name);
      C.Hook := To_Unbounded_String ("none");
      C.Policy := To_Unbounded_String ("accept");
      return C;
   end Find_Chain;

   procedure Add_Chain (P : in out Policy_Rec; T : Token_List; Seq : Natural) is
   begin
      if T.Count >= 10 then
         P.Chain_Count := P.Chain_Count + 1;
         P.Chains (P.Chain_Count).Family := To_Unbounded_String (Lower (S (T.Data (2))));
         P.Chains (P.Chain_Count).Table_Name := To_Unbounded_String (Lower (S (T.Data (3))));
         P.Chains (P.Chain_Count).Chain_Name := To_Unbounded_String (Lower (S (T.Data (4))));
         P.Chains (P.Chain_Count).Hook := To_Unbounded_String (Lower (S (T.Data (6))));
         P.Chains (P.Chain_Count).Priority := Parse_Priority (S (T.Data (8)));
         P.Chains (P.Chain_Count).Policy := To_Unbounded_String (Lower (S (T.Data (10))));
         P.Chains (P.Chain_Count).Source_Pos := Seq;
      end if;
   end Add_Chain;

   procedure Add_Set (P : in out Policy_Rec; T : Token_List) is
      Raw : constant String := Join_Tokens (T, 3, T.Count);
   begin
      if T.Count >= 3 then
         P.Set_Count := P.Set_Count + 1;
         P.Sets (P.Set_Count).Name := To_Unbounded_String (Lower (S (T.Data (2))));
         P.Sets (P.Set_Count).Value := To_Unbounded_String (Normalize_Address_List (Raw, P));
      end if;
   end Add_Set;

   procedure Add_Name (P : in out Policy_Rec; T : Token_List; Kind : Name_Class) is
      Raw : constant String := Join_Tokens (T, 3, T.Count);
   begin
      if T.Count >= 3 then
         P.Name_Count := P.Name_Count + 1;
         P.Names (P.Name_Count).Name := To_Unbounded_String (Lower (S (T.Data (2))));
         P.Names (P.Name_Count).Kind := Kind;
         P.Names (P.Name_Count).Value := To_Unbounded_String (Raw);
      end if;
   end Add_Name;

   function Next_Key_Index (T : Token_List; Start : Positive; Last : Natural) return Natural is
   begin
      for I in Start .. Last loop
         declare
            V : constant String := Lower (S (T.Data (I)));
         begin
             if V = "ct" or else V = "iif" or else V = "oif" or else V = "proto" or else V = "tcpflags" or else V = "saddr" or else V = "daddr" or else V = "sport" or else V = "dport" or else V = "uid" or else V = "mark" or else V = "jump" or else V = "goto" then
               return I;
            end if;
         end;
      end loop;
      return Last + 1;
   end Next_Key_Index;

   procedure Add_Rule (P : in out Policy_Rec; T : Token_List; Seq : Natural) is
      R : Rule_Rec;
      I : Natural := 5;
      N : constant Natural := T.Count;
      J : Natural;
      C : Chain_Rec;
   begin
      if T.Count < 5 then
         return;
      end if;
      R.Family := To_Unbounded_String (Lower (S (T.Data (2))));
      R.Table_Name := To_Unbounded_String (Lower (S (T.Data (3))));
      R.Chain_Name := To_Unbounded_String (Lower (S (T.Data (4))));
      C := Find_Chain (P, S (R.Family), S (R.Table_Name), S (R.Chain_Name));
      R.Hook := C.Hook;
      R.Priority := C.Priority;
       R.Policy := C.Policy;
       R.Ct := To_Unbounded_String ("*");
       R.Proto := To_Unbounded_String ("*");
       R.Tcpflags := To_Unbounded_String ("*");
       R.Uid := To_Unbounded_String ("*");
       R.Mark := To_Unbounded_String ("*");
      R.Iif := To_Unbounded_String ("*");
      R.Oif := To_Unbounded_String ("*");
       R.Saddr := To_Unbounded_String ("*");
       R.Sport := To_Unbounded_String ("*");
       R.Dport := To_Unbounded_String ("*");
      R.Action := To_Unbounded_String (Lower (S (T.Data (N))));
      while I < N loop
         declare
            K : constant String := Lower (S (T.Data (I)));
         begin
             if K = "ct" then
                J := Next_Key_Index (T, I + 1, N - 1);
                R.Ct := To_Unbounded_String (Normalize_Ct (Join_Tokens (T, I + 1, J - 1)));
                I := J;
            elsif K = "iif" then
               J := Next_Key_Index (T, I + 1, N - 1);
               R.Iif := To_Unbounded_String (Join_Tokens (T, I + 1, J - 1));
               I := J;
            elsif K = "oif" then
               J := Next_Key_Index (T, I + 1, N - 1);
               R.Oif := To_Unbounded_String (Join_Tokens (T, I + 1, J - 1));
               I := J;
            elsif K = "proto" then
               J := Next_Key_Index (T, I + 1, N - 1);
                R.Proto := To_Unbounded_String (Lower (Join_Tokens (T, I + 1, J - 1)));
                I := J;
             elsif K = "tcpflags" then
                J := Next_Key_Index (T, I + 1, N - 1);
                R.Tcpflags := To_Unbounded_String (Lower (Join_Tokens (T, I + 1, J - 1)));
                I := J;
            elsif K = "saddr" then
               J := Next_Key_Index (T, I + 1, N - 1);
               R.Saddr := To_Unbounded_String (Normalize_Address_List (Join_Tokens (T, I + 1, J - 1), P));
               I := J;
             elsif K = "sport" then
                J := Next_Key_Index (T, I + 1, N - 1);
                R.Sport := To_Unbounded_String (Normalize_Ports (Join_Tokens (T, I + 1, J - 1)));
                I := J;
             elsif K = "dport" then
                J := Next_Key_Index (T, I + 1, N - 1);
                 R.Dport := To_Unbounded_String (Normalize_Ports (Join_Tokens (T, I + 1, J - 1)));
                 I := J;
             elsif K = "uid" then
                J := Next_Key_Index (T, I + 1, N - 1);
                R.Uid := To_Unbounded_String (Normalize_Ports (Join_Tokens (T, I + 1, J - 1)));
                I := J;
            elsif K = "mark" then
               J := Next_Key_Index (T, I + 1, N - 1);
               R.Mark := To_Unbounded_String (Join_Tokens (T, I + 1, J - 1));
               I := J;
            else
               I := I + 1;
            end if;
         end;
      end loop;
      R.Source_Pos := Seq;
      P.Rule_Count := P.Rule_Count + 1;
      P.Rules (P.Rule_Count) := R;
   end Add_Rule;

   function Load_Policy (Path : String) return Policy_Rec is
      F    : File_Type;
      Line : String (1 .. 4096);
      Last : Natural;
      P    : Policy_Rec;
      Seq  : Natural := 0;
   begin
      Open (F, In_File, Path);
      while not End_Of_File (F) loop
         Get_Line (F, Line, Last);
         declare
            C : constant String := Clean_Line (Line (1 .. Last));
            T : constant Token_List := Tokenize (C);
         begin
            if T.Count > 0 then
               Seq := Seq + 1;
               declare
                  K : constant String := Lower (S (T.Data (1)));
               begin
                   if K = "define" then
                      Add_Define (P, T);
                   elsif K = "chain" then
                      Add_Chain (P, T, Seq);
                   elsif K = "set" then
                      Add_Set (P, T);
                      Add_Name (P, T, Address_Set);
                   elsif K = "ports" then
                      Add_Name (P, T, Port_Set);
                   elsif K = "zone" then
                      Add_Name (P, T, Interface_Zone);
                   elsif K = "rule" then
                     Add_Rule (P, T, Seq);
                  end if;
               end;
            end if;
         end;
      end loop;
      Close (F);
      return P;
   end Load_Policy;

   function Chain_Less (A, B : Chain_Rec) return Boolean is
   begin
      if S (A.Family) /= S (B.Family) then return S (A.Family) < S (B.Family); end if;
      if S (A.Table_Name) /= S (B.Table_Name) then return S (A.Table_Name) < S (B.Table_Name); end if;
      if S (A.Hook) /= S (B.Hook) then return S (A.Hook) < S (B.Hook); end if;
      if A.Priority /= B.Priority then return A.Priority < B.Priority; end if;
      return S (A.Chain_Name) < S (B.Chain_Name);
   end Chain_Less;

   function Rule_Less (A, B : Rule_Rec) return Boolean is
   begin
      if S (A.Family) /= S (B.Family) then return S (A.Family) < S (B.Family); end if;
      if S (A.Table_Name) /= S (B.Table_Name) then return S (A.Table_Name) < S (B.Table_Name); end if;
      if S (A.Chain_Name) /= S (B.Chain_Name) then return S (A.Chain_Name) < S (B.Chain_Name); end if;
      if S (A.Action) /= S (B.Action) then return S (A.Action) < S (B.Action); end if;
      return A.Source_Pos < B.Source_Pos;
   end Rule_Less;

   procedure Sort (P : in out Policy_Rec) is
      C : Chain_Rec;
      R : Rule_Rec;
   begin
      for I in 1 .. P.Chain_Count loop
         for J in I + 1 .. P.Chain_Count loop
            if Chain_Less (P.Chains (J), P.Chains (I)) then
               C := P.Chains (I); P.Chains (I) := P.Chains (J); P.Chains (J) := C;
            end if;
         end loop;
      end loop;
      for I in 1 .. P.Rule_Count loop
         for J in I + 1 .. P.Rule_Count loop
            if Rule_Less (P.Rules (J), P.Rules (I)) then
               R := P.Rules (I); P.Rules (I) := P.Rules (J); P.Rules (J) := R;
            end if;
         end loop;
      end loop;
   end Sort;

   function Same_Chain (A, B : Rule_Rec) return Boolean is
   begin
      return S (A.Family) = S (B.Family) and then S (A.Table_Name) = S (B.Table_Name) and then S (A.Chain_Name) = S (B.Chain_Name);
   end Same_Chain;

   procedure Write_Compiled (P0 : Policy_Rec; Output : String) is
      P : Policy_Rec := P0;
      F : File_Type;
      Pos : Natural := 0;
   begin
      Sort (P);
      Create (F, Out_File, Output);
      for I in 1 .. P.Chain_Count loop
         Put_Line (F, "chain family=" & S (P.Chains (I).Family) & " table=" & S (P.Chains (I).Table_Name) &
                     " chain=" & S (P.Chains (I).Chain_Name) & " hook=" & S (P.Chains (I).Hook) &
                     " priority=" & Img (P.Chains (I).Priority) & " policy=" & S (P.Chains (I).Policy));
      end loop;
      for I in 1 .. P.Rule_Count loop
         if I = 1 or else not Same_Chain (P.Rules (I), P.Rules (I - 1)) then
            Pos := 1;
         else
            Pos := Pos + 1;
         end if;
         Put_Line (F, "rule family=" & S (P.Rules (I).Family) & " table=" & S (P.Rules (I).Table_Name) &
                     " chain=" & S (P.Rules (I).Chain_Name) & " hook=" & S (P.Rules (I).Hook) &
                      " priority=" & Img (P.Rules (I).Priority) & " pos=" & Nimg (Pos) &
                      " ct=" & S (P.Rules (I).Ct) & " iif=" & S (P.Rules (I).Iif) & " oif=" & S (P.Rules (I).Oif) &
                      " proto=" & S (P.Rules (I).Proto) & " tcpflags=" & S (P.Rules (I).Tcpflags) & " saddr=" & S (P.Rules (I).Saddr) &
                      " dport=" & S (P.Rules (I).Dport) & " uid=" & S (P.Rules (I).Uid) & " mark=" & S (P.Rules (I).Mark) & " action=" & S (P.Rules (I).Action));
      end loop;
      Close (F);
   end Write_Compiled;

   function File_Text (Path : String) return String is
      F : File_Type;
      R : U := Null_Unbounded_String;
      Line : String (1 .. 4096);
      Last : Natural;
   begin
      Open (F, In_File, Path);
      while not End_Of_File (F) loop
         Get_Line (F, Line, Last);
         Append (R, Line (1 .. Last));
         Append (R, ASCII.LF);
      end loop;
      Close (F);
      return S (R);
   end File_Text;

   procedure Compile_File (Input, Output : String) is
      P : constant Policy_Rec := Load_Policy (Input);
   begin
      Write_Compiled (P, Output);
   end Compile_File;

   procedure Compare_Files (Left, Right, Output : String) is
      Ltmp : constant String := Output & ".left.tmp";
      Rtmp : constant String := Output & ".right.tmp";
      F    : File_Type;
   begin
      Compile_File (Left, Ltmp);
      Compile_File (Right, Rtmp);
      Create (F, Out_File, Output);
      if File_Text (Ltmp) = File_Text (Rtmp) then
         Put_Line (F, "equivalent");
      else
         Put_Line (F, "different");
      end if;
      Close (F);
   end Compare_Files;
begin
   if Argument_Count = 3 and then Argument (1) = "compile" then
      Compile_File (Argument (2), Argument (3));
   elsif Argument_Count = 4 and then Argument (1) = "compare" then
      Compare_Files (Argument (2), Argument (3), Argument (4));
   else
      Put_Line ("usage: nft-policy-tool compile INPUT OUTPUT | compare LEFT RIGHT OUTPUT");
      Set_Exit_Status (Failure);
   end if;
end Nft_Policy;
