// Plain transport: expected outcomes stay with the trusted parent.
const fs=require('node:fs');
const {pathToFileURL}=require('node:url');
async function main(){
 const request=JSON.parse(fs.readFileSync(0,'utf8'));
 const api=request.format==='esm'?await import(pathToFileURL('/app/compiled/esm/index.mjs').href):require('/app/compiled/cjs/index.cjs');
 const system=request.format==='esm'?await import(pathToFileURL('/app/compiled/esm/system.mjs').href):require('/app/compiled/cjs/system.cjs');
 const events=[];
 const encode=value=>value===undefined?{$undefined:true}:Array.isArray(value)?value.map(encode):value;
 try {
  new Function('api','system','emit',request.program)(api,system,(...args)=>{events.push(args.map(encode));});
  process.stdout.write(JSON.stringify({events}));
 }catch(error){process.stdout.write(JSON.stringify({events,uncaught:String(error?.stack||error)}));}
}
main().catch(error=>{process.stdout.write(JSON.stringify({transport_error:String(error.stack)}));process.exitCode=1;});
