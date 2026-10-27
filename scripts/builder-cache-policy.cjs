// Pure closed-period policy; no network, credentials or filesystem access.
function localDate(at,zone){try{return new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).format(at);}catch{return null;}}
function periodClosed(end,zone,at=new Date()){const day=zone&&localDate(at,zone);return typeof day==='string'&&day>end;}
function reusableCache(cache,end,zone,complete,{force=process.env.AI_DASHBOARD_FORCE_REFRESH==='1',now=new Date()}={}){
 if(force)return false;const captured=new Date(cache.fetchedAt);if(!Number.isFinite(captured.valueOf()))return false;
 const age=now-captured;if(age>=0&&age<900000)return true;
 return(cache.status||'ready')==='ready'&&complete&&periodClosed(end,zone,captured)&&periodClosed(end,zone,now);
}
module.exports={periodClosed,reusableCache};
