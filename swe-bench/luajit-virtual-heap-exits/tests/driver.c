/* Trusted embedding. Expectations and grading stay in the Python parent. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"

typedef struct Meter { uint64_t allocations; lua_Alloc allocator; void *ud; } Meter;
static void *meter_alloc(void *ud, void *ptr, size_t osz, size_t nsz) {
  Meter *m=(Meter *)ud;
  if (!ptr && nsz) m->allocations++;
  return m->allocator(m->ud,ptr,osz,nsz);
}
static int meter_reset(lua_State *L) {
  void *ud; lua_getallocf(L,&ud); ((Meter *)ud)->allocations=0; return 0;
}
static int meter_read(lua_State *L) {
  void *ud; lua_getallocf(L,&ud);
  lua_pushnumber(L,(lua_Number)((Meter *)ud)->allocations); return 1;
}
int main(void) {
  Meter meter={0,NULL,NULL};
  lua_State *L=luaL_newstate();
  size_t cap=32768,n=0,got;
  char *program=(char *)malloc(cap);
  if (!L || !program) { fprintf(stderr,"EMBED_STARTUP: cannot create VM\n");return 70; }
  meter.allocator=lua_getallocf(L,&meter.ud);
  lua_setallocf(L,meter_alloc,&meter);
  while ((got=fread(program+n,1,cap-n,stdin))>0) {
    n+=got;
    if (n==cap) { char *p;cap*=2;if (cap>4*1024*1024)return 71;p=(char *)realloc(program,cap);if(!p)return 72;program=p; }
  }
  luaL_openlibs(L);
  lua_newtable(L);
  lua_pushcfunction(L,meter_reset);lua_setfield(L,-2,"reset");
  lua_pushcfunction(L,meter_read);lua_setfield(L,-2,"read");
  lua_setglobal(L,"meter");
  if (luaL_loadbuffer(L,program,n,"scenario") || lua_pcall(L,0,1,0)) {
    const char *message=lua_tostring(L,-1);
    fprintf(stderr,"LUA_ERROR: %s\n",message?message:"non-string error");
    lua_setallocf(L,meter.allocator,meter.ud);lua_close(L);free(program);return 2;
  }
  if (!lua_isstring(L,-1)) { fprintf(stderr,"scenario returned no observation\n");return 3; }
  puts(lua_tostring(L,-1));
  lua_setallocf(L,meter.allocator,meter.ud);lua_close(L);free(program);return 0;
}
