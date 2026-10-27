const {test}=require('node:test'),assert=require('node:assert/strict');
const {periodClosed,reusableCache}=require('../scripts/builder-cache-policy.cjs');
test('a complete daily partition list captured Monday is not a closed weekly snapshot',()=>{
 const cache={status:'ready',fetchedAt:'2026-10-26T15:00:00Z'},now=new Date('2026-10-27T09:00:00Z');
 assert.equal(reusableCache(cache,'2026-10-26','America/Los_Angeles',true,{now}),false);
});
test('force refresh bypasses fresh and historical complete cache',()=>{
 const cache={status:'ready',fetchedAt:'2026-10-27T08:00:00Z'},now=new Date('2026-10-27T09:00:00Z');
 assert.equal(reusableCache(cache,'2026-10-26','America/Los_Angeles',true,{now}),true);
 assert.equal(reusableCache(cache,'2026-10-26','America/Los_Angeles',true,{now,force:true}),false);
});
test('ET start time does not imply the Pacific account Monday has closed',()=>{
 const first=new Date('2026-10-27T05:00:00Z');
 assert.equal(periodClosed('2026-10-26','America/New_York',first),true);
 assert.equal(periodClosed('2026-10-26','America/Los_Angeles',first),false);
 assert.equal(periodClosed('2026-10-26','America/Los_Angeles',new Date('2026-10-27T07:00:00Z')),true);
});
