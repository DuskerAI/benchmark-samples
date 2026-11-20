with Interfaces;

package Crypto_Hash is
   subtype U8 is Interfaces.Unsigned_8;
   type Byte_Array is array (Natural range <>) of U8;

   procedure SHA1_Hash (Input : in Byte_Array; Output : out Byte_Array);
   procedure SHA256_Hash (Input : in Byte_Array; Output : out Byte_Array);
end Crypto_Hash;
