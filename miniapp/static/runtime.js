/* Visibility-aware scheduler and request ownership. Pure helpers, no game writes. */
(function(root){
  "use strict";
  function createScheduler({run,delay,active,schedule,cancel}){
    let timer=null, generation=0;
    function stop(){
      generation++;
      if(timer!==null){cancel(timer);timer=null;}
    }
    function arm(ms,token){
      if(!active()||token!==generation)return;
      timer=schedule(()=>{
        timer=null;
        if(!active()||token!==generation)return;
        try{run();}
        finally{if(token===generation&&active()&&timer===null)arm(delay(),token);}
      },ms);
    }
    return Object.freeze({wake(){stop();arm(0,generation);},stop,pending:()=>timer!==null});
  }
  function cadence(tab,active,mine,online=true){
    return {tick:tab==="arcade"&&active&&mine?100:1000,
      feature:!online?null:tab==="fishing"?(active?4000:8000):
        tab==="arcade"?(active?(mine?null:4000):15000):tab==="farm"?15000:null};
  }
  function createReadGate(){
    let owner=0,serial=0;
    return Object.freeze({
      begin(){owner=++serial;return owner;},
      finish(token){if(owner===token)owner=0;},
      busy(){return owner!==0;}
    });
  }
  const api=Object.freeze({createScheduler,cadence,createReadGate});
  if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.PetsRuntime=api;
})(typeof window!=="undefined"?window:globalThis);
