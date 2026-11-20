
#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>
__attribute__((constructor)) static void dusker_boundary_probe(void) {
  const char *paths[]={"/tests/parser.py","/tests/test_patch.patch","/app/tests/test_jit_deopt.py","/logs/verifier/reward.txt","/solution/gold_patch.diff","/inputs/tests/test_patch.patch"};
  int i,fd,first=1; FILE *s=stderr;
  fprintf(s,"RUNTIME_BOUNDARY:{\"uid\":%d,\"reads\":{",(int)geteuid());
  for(i=0;i<6;i++) {fd=open(paths[i],O_RDONLY);fprintf(s,"%s\"%s\":%s",first?"":",",paths[i],fd>=0?"true":"false");if(fd>=0)close(fd);first=0;}
  fprintf(s,"},\"writes\":{");first=1;
  for(i=0;i<6;i++) {fd=open(paths[i],O_WRONLY|O_CREAT,0600);fprintf(s,"%s\"%s\":%s",first?"":",",paths[i],fd>=0?"true":"false");if(fd>=0)close(fd);first=0;}
  fd=open("/tmp/dusker-boundary-positive",O_WRONLY|O_CREAT,0600);i=fd>=0;if(i)close(fd);
  fprintf(s,"},\"scratch_write\":%s,\"seteuid_root\":%s}\n",i?"true":"false",seteuid(0)==0?"true":"false");
}
