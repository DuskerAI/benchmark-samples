with Interfaces.C;
with System;

package body Crypto_Hash is
   function C_SHA256 (Data : System.Address; Len : Interfaces.C.size_t; Md : System.Address) return System.Address;
   pragma Import (C, C_SHA256, "SHA256");

   procedure SHA256_Hash (Input : in Byte_Array; Output : out Byte_Array) is
      R : System.Address;
   begin
      R := C_SHA256 (Input (Input'First)'Address, Interfaces.C.size_t (Input'Length), Output (Output'First)'Address);
   end SHA256_Hash;
end Crypto_Hash;
