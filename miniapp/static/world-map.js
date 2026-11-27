/* PETS WORLD 2.0 — illustrated fictional world, not a geographic map.
   Only existing navigation routes are actionable. No game state writes. */
(function(root){
  "use strict";
  const places=Object.freeze([
    {id:"farm",name:"Ферма",route:"farm",icon:"🌾",description:"Покупайте семена, поливайте грядки, собирайте урожай и расширяйте хозяйство."},
    {id:"forest",name:"Лес",route:"trips",icon:"🌲",description:"Отправляйтесь за ресурсами, исследуйте маршруты и проходите лесную тропу."},
    {id:"lake",name:"Озеро",route:"fishing",icon:"🐟",description:"Забрасывайте удочку, ловите рыбу и пополняйте общую коллекцию."},
    {id:"house",name:"Домик",route:"house",icon:"🏡",description:"Ваш уютный дом: комнаты, улучшения и полезные эффекты."},
    {id:"club",name:"Клуб",route:"club",icon:"🏆",description:"Лига питомцев, сезоны, испытания и награды."},
    {id:"business",name:"Tycoon",route:"business",icon:"🏭",description:"Ручное производство, оборудование, работники и заказы."},
    {id:"shop",name:"Магазин",route:"shop",icon:"🛍️",description:"Еда, аксессуары и улучшения за ириски."},
    {id:"market",name:"Рынок",route:"market",icon:"🏪",description:"Покупайте и продавайте ресурсы и товары с игроками своего чата."},
    {id:"arcade",name:"Аркады",kind:"future",icon:"🎮",description:"Короткие мини-игры для питомцев.",stage:"Будущий этап · аркадные игры"}
  ].map(Object.freeze));
  const get=id=>places.find(x=>x.id===id)||places.find(x=>x.id==="house");
  const valid=id=>places.some(x=>x.id===id);
  const esc=x=>String(x??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function status(place,p,now,config={}){
    if(place.kind==="future")return {text:"Скоро",tone:"future"};
    if(place.id==="house")return p.house?{text:"Ваш дом",tone:"open"}:{text:"Построить",tone:"open"};
    if(place.id==="forest"){
      if(p.trip)return Number(p.trip.finish_at)<=now?{text:"Награда",tone:"reward"}:{text:"В походе",tone:"active"};
      if(!p.best_streak)return {text:"После вылупления",tone:"open"};
      if(p.chapter&&p.chapter.stage<3)return {text:"Лесная тропа",tone:"active"};
      return {text:"Маршруты",tone:"open"};
    }
    if(place.id==="farm"){
      const plots=p.countryside?.plots||[];
      if(plots.some(x=>x.crop&&Number(x.ready_at)<=now))return {text:"Урожай готов",tone:"reward"};
      if(plots.some(x=>x.crop))return {text:"Растёт",tone:"active"};
      return {text:p.best_streak?"Посадить":"После вылупления",tone:"open"};
    }
    if(place.id==="lake"){
      const cast=p.countryside?.cast;
      if(cast&&now>=cast.bite_at&&now<=cast.expires_at)return {text:"Клюёт!",tone:"reward"};
      if(cast&&now<cast.bite_at)return {text:"Ждём клёва",tone:"active"};
      return {text:p.best_streak?"Рыбалка":"После вылупления",tone:"open"};
    }
    if(place.id==="business"){
      const units=p.businesses?.units||{};
      const entries=Object.entries(units);
      const ready=entries.find(([,u])=>u.job&&Number(u.job.finish_at)<=now);
      if(ready)return {text:"Партия готова",tone:"reward",businessCode:ready[0]};
      const active=entries.find(([,u])=>u.job);
      if(active)return {text:"В работе",tone:"active",businessCode:active[0]};
      const order=entries.find(([code,u])=>{
        const def=config.businesses?.find(b=>b.code===code),qty=config.tycoon_rules?.order_quantity||2;
        return def&&u.level>0&&!u.order_done&&u.stock>=qty&&p.businesses.sales_coins_remaining>=def.sell_price*qty;
      });
      if(order)return {text:"Заказ готов",tone:"reward",businessCode:order[0]};
      return {text:entries.some(([,u])=>u.level>0)?"Предприятия":"Построить",tone:"open"};
    }
    return {text:"Открыто",tone:"open"};
  }
  function recommended(p,now,config){
    for(const id of ["lake","farm","forest","business"]){
      if(status(get(id),p,now,config).tone==="reward")return id;
    }
    return "house";
  }
  function scenery(){
    // All art is inline SVG, with original vector shapes and a reusable tree.
    return '<svg class="map-land" viewBox="0 0 360 450" preserveAspectRatio="none" aria-hidden="true" focusable="false">'+
      '<defs><linearGradient id="map-grass" x2="0.8" y2="1"><stop stop-color="#b5d68e"/><stop offset="1" stop-color="#5a9a73"/></linearGradient>'+
      '<linearGradient id="map-water" x2="0.6" y2="1"><stop stop-color="#a8e7d8"/><stop offset="1" stop-color="#438da0"/></linearGradient>'+
      '<g id="map-pine"><ellipse cx="0" cy="17" rx="10" ry="4" fill="#32694e" opacity=".3"/><path d="M-2 8h4v11h-4z" fill="#795640"/><path d="M0-17-12 5h6l-9 10h30L6 5h6Z" fill="#317b56"/><path d="M0-17-12 5h12l-6 10h6Z" fill="#51986b"/></g>'+
      '<g id="map-rock"><path d="m-9 2 4-9 9-2 7 11-5 5H-5Z" fill="#8da7a1"/><path d="m-5-7 9-2 7 11-11-3Z" fill="#b5c3af"/></g></defs>'+
      '<rect width="360" height="450" rx="25" fill="url(#map-grass)"/>'+
      '<path d="M0 0H360V38L318 53 273 27 237 54 192 22 150 45 100 19 47 46 0 33Z" fill="#789d88"/>'+
      '<path d="M0 0H360L314 31 277 16 237 32 193 8 151 25 100 7 46 25 0 14Z" fill="#c6d9ac"/>'+
      '<path d="M98 11Q148 7 201 36T236 125Q204 154 136 135T85 68Z" fill="#4d9469" opacity=".65"/>'+
      '<path d="M245 5Q335 5 355 55L350 139Q327 170 280 153T243 103Q214 72 245 5Z" fill="#e0deb0" stroke="#90b78c" stroke-width="4"/>'+
      '<path d="M255 16Q326 9 345 58L341 128Q327 151 287 142T252 99Q226 69 255 16Z" fill="url(#map-water)"/>'+
      '<g class="map-ripples" fill="none" stroke="#e0faf0" stroke-width="2" opacity=".6"><path d="M266 45q12-4 24 0m13 59q12-4 24 0M260 118q9-3 18 0m35-80q8-2 16 0"/></g>'+
      '<path d="M13 18 84 26 98 99 30 134 8 109Z" fill="#dfce86" stroke="#ac9f68" stroke-width="2"/>'+
      '<g stroke="#947444" stroke-width="4" opacity=".6"><path d="m20 43 57 9m-54 8 61 10m-57 8 62 9m-61 7 54 8"/></g>'+
      '<path d="M0 174Q43 160 101 174T160 200Q130 242 39 252L0 239Z" fill="#8dbd83"/>'+
      '<path d="M247 168Q291 145 345 172L357 269Q318 282 253 260Z" fill="#8ba69a"/>'+
      '<path d="M4 315Q74 289 133 322T220 320Q274 285 355 318V450H0Z" fill="#78aa75"/>'+
      '<path d="M27 326h87l-4 94H20Z" fill="#a5bb8b"/><path d="M134 326h95v95h-95Z" fill="#c3bf91"/><path d="M249 322h100v99h-100Z" fill="#9eb698"/>'+
      '<g fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M60 75Q74 157 60 225Q78 277 60 375M180 75V225Q172 280 180 375M300 75Q281 150 300 225V375M60 225Q120 235 180 225T300 225M60 375Q123 358 180 375T300 375M60 75Q110 94 180 75T300 75" stroke="#6e946b" stroke-width="19"/>'+
      '<path d="M60 75Q74 157 60 225Q78 277 60 375M180 75V225Q172 280 180 375M300 75Q281 150 300 225V375M60 225Q120 235 180 225T300 225M60 375Q123 358 180 375T300 375M60 75Q110 94 180 75T300 75" stroke="#e0cba0" stroke-width="13"/>'+
      '<path d="M60 225Q120 235 180 225T300 225M180 75V225Q172 280 180 375" stroke="#f6e3ba" stroke-width="2" stroke-dasharray="4 10"/></g>'+
      '<g><use href="#map-pine" x="115" y="34"/><use href="#map-pine" x="145" y="31"/><use href="#map-pine" x="214" y="30"/><use href="#map-pine" x="226" y="65"/><use href="#map-pine" x="112" y="105"/><use href="#map-pine" x="205" y="124"/><use href="#map-pine" x="16" y="150"/><use href="#map-pine" x="103" y="190"/><use href="#map-pine" x="17" y="271"/><use href="#map-pine" x="340" y="288"/><use href="#map-pine" x="117" y="283"/><use href="#map-pine" x="224" y="288"/><use href="#map-pine" x="20" y="436"/><use href="#map-pine" x="336" y="433"/><use href="#map-rock" x="227" y="154"/><use href="#map-rock" x="131" y="255"/><use href="#map-rock" x="235" y="416"/></g>'+
      '<g fill="#f3e5a5" opacity=".9"><circle cx="37" cy="159" r="3"/><circle cx="49" cy="151" r="2"/><circle cx="94" cy="272" r="3"/><circle cx="107" cy="266" r="2"/><circle cx="323" cy="304" r="3"/><circle cx="316" cy="295" r="2"/><circle cx="141" cy="436" r="3"/><circle cx="150" cy="428" r="2"/></g>'+
      '<g class="map-night-stars" fill="#fff6c1"><circle cx="25" cy="23" r="1.7"/><circle cx="178" cy="13" r="2"/><circle cx="336" cy="26" r="1.6"/><path d="M127 146v9m-4.5-4.5h9M232 312v9m-4.5-4.5h9" stroke="#fff6c1" stroke-width="1.3"/></g>'+
      '</svg>';
  }
  function marker(place,p,now,config){
    const s=status(place,p,now,config);
    return '<button type="button" class="map-place place-'+place.id+' tone-'+s.tone+'" data-map-location="'+place.id+'" aria-pressed="false" aria-label="'+esc(place.name+" — "+s.text)+'">'+
      '<span class="map-place-icon" aria-hidden="true">'+place.icon+'</span><span class="map-place-title">'+place.name+'</span>'+
      '<small data-map-status="'+place.id+'">'+esc(s.text)+'</small></button>';
  }
  function renderMap(p,now,config,phase){
    return '<div class="map-heading"><div><div class="eyebrow">ВАША ИСТОРИЯ</div><h1>Долина PETS</h1><p>Выбери место на карте</p></div><div class="map-heading-compass" aria-hidden="true">✦</div></div>'+
      '<section class="world-map map-phase-'+esc(["morning","day","evening","night"].includes(phase)?phase:"day")+'" aria-label="Интерактивная карта мира">'+scenery()+
      '<div class="map-grid">'+places.map(x=>marker(x,p,now,config)).join("")+'</div></section>'+
      '<div class="map-legend"><span><i class="legend-open"></i> Доступно</span><span><i class="legend-reward"></i> Готово</span><span><i class="legend-future"></i> Скоро</span></div>';
  }
  function renderDetail(id,p,now,config){
    const place=get(id),s=status(place,p,now,config);
    let note="";
    if(place.id==="forest"&&p.trip)note=s.tone==="reward"?"Поход завершён. Заберите награду.":"Питомец ещё в пути. Можно посмотреть маршрут.";
    if(place.id==="business"&&s.businessCode){
      const def=config.businesses?.find(b=>b.code===s.businessCode);
      if(def)note=def.name+": "+s.text.toLowerCase()+".";
    }
    return '<section class="panel map-detail"><div class="map-detail-main"><span class="map-detail-icon" aria-hidden="true">'+place.icon+'</span>'+
      '<div><div class="eyebrow">'+(place.kind==="future"?"В РАЗРАБОТКЕ":"ЛОКАЦИЯ")+'</div><h2>'+place.name+'</h2>'+
      '<p>'+esc(place.description)+'</p></div></div>'+
      (note?'<p class="map-detail-note">'+esc(note)+'</p>':"")+
      (place.route?'<button type="button" class="btn btn-primary btn-wide" data-map-open="'+place.id+'">'+(s.text==="Клюёт!"?"К удочке":s.text==="Урожай готов"?"К урожаю":s.text==="Заказ готов"?"Перейти к заказу":s.text==="Партия готова"?"Забрать товары":s.tone==="reward"?"Перейти к награде":"Открыть локацию")+' →</button>':
      '<div class="map-future-note">🔒 '+esc(place.stage)+'</div>')+'</section>';
  }
  const api=Object.freeze({places,get,valid,status,recommended,renderMap,renderDetail});
  if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.PetsMap=api;
})(typeof window!=="undefined"?window:globalThis);
