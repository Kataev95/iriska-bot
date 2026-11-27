/* PETS WORLD 2.0 / stage 3 — presentation-only pet reactions.
   No API calls, rewards, counters, storage or game-rule mutations. */
(function(root){
  "use strict";
  const poses=Object.freeze({
    egg:{label:"Малыш ещё в яйце",symbol:"🥚",hint:"Навестить",idle:"Яйцо ждёт вылупления"},
    sleep:{label:"Сладко спит",symbol:"💤",hint:"Навестить",idle:"Тихо, питомец отдыхает"},
    hungry:{label:"Хочет кушать",symbol:"🍖",hint:"Погладить",idle:"Пора покормить"},
    bored:{label:"Хочет поиграть",symbol:"🎾",hint:"Погладить",idle:"Скучает по вам"},
    happy:{label:"Очень счастлив",symbol:"💚",hint:"Погладить",idle:"Рад быть рядом"},
    idle:{label:"В хорошем настроении",symbol:"🐾",hint:"Погладить",idle:"Рад вашему визиту"}
  });
  const known=(obj,key)=>Object.prototype.hasOwnProperty.call(obj,key);
  function pose(p){
    // Use the server-selected portrait before rounded client-side stats.
    if(!p.best_streak || /\/egg\.jpg$/.test(p.image||""))return "egg";
    if(/_sleep\.jpg$/.test(p.image||""))return "sleep";
    if(/_hungry\.jpg$/.test(p.image||""))return "hungry";
    if(/_happy\.jpg$/.test(p.image||""))return "happy";
    return Number(p.joy)<30?"bored":"idle";
  }
  function state(p){return poses[pose(p)];}
  const actions=Object.freeze({
    feed:{motion:"feed",symbol:"🍖",text:"Время вкусняшек!",expression:"happy",ms:1800},
    play:{motion:"play",symbol:"🎾",text:"Вот это веселье!",expression:"happy",ms:1800},
    sleep:{motion:"sleep",symbol:"💤",text:"Спокойного отдыха!",expression:"sleep",ms:2000}
  });
  function reaction(p,kind,confirmed=false){
    const current=pose(p);
    if(kind==="pat"){
      if(current==="egg")return {motion:"egg",symbol:"✨",text:"Малыш ещё в яйце",ms:1500};
      if(current==="sleep")return {motion:"quiet",symbol:"💤",text:"Тсс… пусть отдыхает",ms:1500};
      if(current==="hungry")return {motion:"pat",symbol:"💛",text:"Спасибо за ласку! Хочется кушать.",ms:1600};
      return {motion:"pat",symbol:"💚",text:"Как приятно!",expression:"happy",ms:1600};
    }
    if(!confirmed||!known(actions,kind)||current==="egg")return null;
    const r={...actions[kind]};
    // Never use a happy portrait to suggest hunger or tiredness was resolved.
    if(r.expression==="happy"&&(current==="sleep"||current==="hungry"))delete r.expression;
    return r;
  }
  function createGate(){
    let serial=0, active=null;
    return {
      begin(petId,now,ms,force=false){
        if(!force&&active&&now<active.until)return null;
        active={token:++serial,petId,until:now+ms};
        return active.token;
      },
      current(token,petId){return Boolean(active&&active.token===token&&active.petId===petId);},
      finish(token){if(!active||active.token!==token)return false;active=null;return true;},
      reset(){active=null;++serial;}
    };
  }
  function expressionPath(p,name){
    if(name!=="happy"&&name!=="sleep")return null;
    const path=p.portrait_reactions?.[name];
    return typeof path==="string"&&/^\/assets\/pets\/[a-z_]+\.jpg$/.test(path)?path:null;
  }
  const api=Object.freeze({pose,state,reaction,createGate,expressionPath});
  if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.PetsLife=api;
})(typeof window!=="undefined"?window:globalThis);
