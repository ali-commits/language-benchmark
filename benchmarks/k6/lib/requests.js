import http from 'k6/http';
import {check} from 'k6';
import {attempted,completed,succeeded,failed,duration,failureRate} from './metrics.js';

export function perform(op,actor,post,cursor,base,pair,remove) {
 const tags={operation:op,name:op}; const options={headers:{Authorization:'Bearer '+actor.token,'Content-Type':'application/json'},tags,timeout:'15s'};
 attempted.add(1,tags);const start=Date.now();let r;let expected=200;
 if(op==='feed')r=http.get(base+'/api/v1/feed?limit=20'+(cursor?'&cursor='+encodeURIComponent(cursor):''),options);
 else if(op==='comments')r=http.get(base+'/api/v1/posts/'+post+'/comments',options);
 else if(op==='like') {expected=204;r=remove?http.del(base+'/api/v1/posts/'+pair+'/like',null,options):http.put(base+'/api/v1/posts/'+pair+'/like',null,options);}
 else if(op==='post') {expected=201;r=http.post(base+'/api/v1/posts',JSON.stringify({content:'A realistic update from a benchmark user'}),options);}
 else if(op==='comment') {expected=201;r=http.post(base+'/api/v1/posts/'+post+'/comments',JSON.stringify({content:'Thanks for sharing this update'}),options);}
 else if(op==='login')r=http.post(base+'/api/v1/auth/login',JSON.stringify({username:actor.username,password:actor.password}),{...options,headers:{'Content-Type':'application/json'}});
 else throw new Error('unknown operation '+op);
 let ok=r.status===expected;
 if(ok&&expected!==204) {
  try {const b=r.json();ok=op==='login'?typeof b.access_token==='string':(op==='feed'||op==='comments')?Array.isArray(b.items):Number.isInteger(b.id)&&typeof b.content==='string';}catch(_){ok=false;}
 }
 failureRate.add(!ok,tags);
 check(r,{'operation response valid':()=>ok},tags);
 duration.add(Date.now()-start,tags);completed.add(1,tags);if(ok)succeeded.add(1,tags);else failed.add(1,tags);
 return ok;
}
