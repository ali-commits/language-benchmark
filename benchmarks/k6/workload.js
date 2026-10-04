import {SharedArray} from 'k6/data';
import exec from 'k6/execution';
import {choice,operation} from './lib/selection.js';
import {perform} from './lib/requests.js';
const cfg=JSON.parse(open(__ENV.SCENARIO_CONFIG||'/inputs/scenario.json'));
const path=__ENV.LOAD_DATA||'/inputs/load-data.json';
const actors=new SharedArray('actors',()=>JSON.parse(open(path)).actors);
const posts=new SharedArray('posts',()=>JSON.parse(open(path)).posts);
const pairs=new SharedArray('pairs',()=>JSON.parse(open(path)).like_pairs);
const expiry=cfg.expires_at;
if(!cfg.rate||cfg.rate<1||!cfg.vus||cfg.vus<1||cfg.duration<1)throw new Error('invalid bounded scenario');
if(expiry*1000<Date.now()+(cfg.duration+60)*1000)throw new Error('tokens expire during run');
if(pairs.length<cfg.rate*cfg.duration+cfg.vus)throw new Error('mutation pairs insufficient for offered operations');
export const options={scenarios:{capacity:{executor:'constant-arrival-rate',rate:cfg.rate,timeUnit:'1s',duration:cfg.duration+'s',preAllocatedVUs:cfg.vus,maxVUs:cfg.vus,gracefulStop:'20s'}},summaryTrendStats:['avg','med','p(50)','p(95)','p(99)','max'],systemTags:['status','method','name','scenario'],thresholds:{operation_duration:['p(95)<200','p(99)<500'],operations_failed:['count==0'],dropped_iterations:['count==0']}};
let pendingUnlike=null;
export default function() {
 const i=exec.scenario.iterationInTest;const n=choice(i,cfg.seed||20261004);
 let actor=actors[n%actors.length];const post=posts[n%posts.length];
 const op=cfg.operation==='mixed'?operation(n):cfg.operation;
 if(op==='like') {
  const pair=pendingUnlike||pairs[i];actor=actors[pair[0]];
  const removing=!!pendingUnlike;
  const ok=perform(op,actor,post,null,__ENV.BASE_URL,pair[1],removing);
  // Unique initial pairs are never reused for PUT; DELETE only follows a successful PUT in this VU.
  if(ok)pendingUnlike=removing?null:pair;
 } else perform(op,actor,post,(i%4===0)?actor.cursor:null,__ENV.BASE_URL,null,false);
}
export function handleSummary(data) {return {'/output/summary.json':JSON.stringify(data,null,2)};}
