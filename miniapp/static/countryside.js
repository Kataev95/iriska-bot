/* Farm and lake UI: rendering and local countdowns only. All outcomes from server. */
(function(root){
  "use strict";
  const esc=x=>String(x??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const n=x=>Math.max(0,Math.floor(Number(x)||0));
  const duration=x=>{const s=Math.max(0,Math.ceil(x));return s>=60?Math.floor(s/60)+" мин "+String(s%60).padStart(2,"0")+" с":s+" с";};
  const rarities={common:"Обычная",rare:"Редкая",epic:"Эпическая",legendary:"Легендарная",mythic:"Мифическая"};
  function plotState(plot,now){
    if(!plot.crop)return "empty";
    return Number(plot.ready_at)<=now?"ready":plot.watered?"watered":"growing";
  }
  function castState(cast,now){
    if(!cast)return "idle";
    if(now<cast.bite_at)return "waiting";
    if(now<=cast.expires_at)return "bite";
    return "missed";
  }
  function button(label,attrs,disabled=false,extra="btn-primary"){
    return '<button type="button" class="btn '+extra+' btn-wide" '+attrs+(disabled?' disabled':'')+'>'+esc(label)+'</button>';
  }
  function upgrade(p,c,kind,title,info){
    const lv=n(p.countryside.levels[kind]),tier=c.upgrades[kind]?.[lv-1],stock=p.materials||{};
    const affordable=tier?.costs.every(x=>n(stock[x.code])>=x.quantity);
    return '<section class="panel country-upgrade"><div class="country-row"><strong>'+title+'</strong><span class="chip">Ур. '+lv+' / 3</span></div>'+
      '<p class="subtitle">'+esc(info)+'</p>'+
      (tier?'<div class="recipe-costs">'+tier.costs.map(x=>'<span class="material-cost '+(n(stock[x.code])>=x.quantity?'enough':'missing')+'">'+
        esc(x.code==="wood"?"🪵":x.code==="leaf"?"🍃":x.code==="stone"?"🪨":"💎")+' '+n(stock[x.code])+'/'+x.quantity+'</span>').join("")+'</div>'+
        button(affordable?"Улучшить до "+(lv+1):"Не хватает материалов",'data-country-upgrade="'+kind+'" data-level="'+lv+'"',!affordable||!p.best_streak):'<p class="subtitle">Максимальный уровень</p>')+'</section>';
  }
  function stockList(p,c,kind){
    const state=p.countryside, defs=kind==="crop"?c.crops:c.fish, stock=kind==="crop"?state.crops:state.fish;
    const items=defs.filter(d=>n(stock[d.code])>0);
    return '<section class="panel country-stock"><div class="country-row"><strong>📦 Общий склад</strong><span class="chip">🍬 '+state.sales_remaining+' / '+c.sales_daily+'</span></div>'+
      (items.length?items.map(d=>{
        const count=n(stock[d.code]),sellable=Math.min(count,Math.floor(state.sales_remaining/d.sell));
        return '<div class="country-stock-row"><div><strong>'+esc(d.icon+" "+d.name)+'</strong><small>'+count+' шт. · '+d.sell+' 🍬/шт.</small></div>'+
          '<button type="button" class="btn btn-quiet" data-country-sell="'+esc(d.code)+'" data-goods-kind="'+kind+'" data-stock="'+count+'"'+(!sellable||!p.best_streak?' disabled':'')+'>'+
          (sellable?'Продать '+sellable+' · '+sellable*d.sell+' 🍬':'Лимит')+'</button></div>';
      }).join(""):'<p class="subtitle">Пока пусто. Соберите '+(kind==="crop"?"урожай":"улов")+'.</p>')+
      '<p class="subtitle">Общий лимит продаж фермы и озера. Ириски получает продавец.</p></section>';
  }
  function farm(p,c,now,selected){
    const s=p.countryside,chosen=c.crops.find(d=>d.code===selected)||c.crops[0];
    const head='<div class="country-banner farm-banner"><div><div class="eyebrow">ВАША ЗЕМЛЯ</div><h1>Ферма</h1><p>Семена → урожай → ириски</p></div><span aria-hidden="true">🌾</span></div>'+
      '<div class="country-summary"><span>Грядки <b>'+s.plots.length+' / '+c.plot_counts.at(-1)+'</b></span><span>Посадки <b>'+s.plants_remaining+' / '+c.plants_daily+'</b></span></div>';
    const seeds='<section class="panel"><div class="country-row"><strong>Выберите семена</strong><span class="chip">🍬 '+p.balance+'</span></div><div class="seed-grid">'+c.crops.map(d=>
      '<button type="button" class="seed-card'+(d.code===chosen.code?' selected':'')+'" data-country-seed="'+esc(d.code)+'" aria-pressed="'+(d.code===chosen.code)+'"><span>'+esc(d.icon)+'</span><b>'+esc(d.name)+'</b><small>'+d.price+' 🍬 · '+Math.ceil(d.seconds/60)+' мин</small><small>Урожай '+d.quantity+' шт.</small></button>').join("")+'</div><p class="subtitle">Семена покупаются сразу при посадке за ваши ириски. Урожай общий.</p></section>';
    const plots='<div class="farm-grid">'+s.plots.map(plot=>{
      const state=plotState(plot,now),d=c.crops.find(x=>x.code===plot.crop);
      const attrs=' data-slot="'+plot.slot+'" data-version="'+plot.version+'"';
      return '<section class="farm-plot plot-'+state+'" data-country-plot="'+plot.slot+'"><div class="country-row"><small>ГРЯДКА '+plot.slot+'</small><span class="plot-state" data-plot-status>'+esc(state==="empty"?"Свободна":state==="ready"?"Готово":state==="watered"?"Полита":"Растёт")+'</span></div>'+
        '<div class="plot-art" aria-hidden="true"><div class="soil"></div><span data-plot-art>'+esc(d?state==="ready"?d.icon:"🌱":"·")+'</span></div>'+
        '<b>'+esc(d?.name||"Свободная грядка")+'</b><small data-plot-clock>'+ (d?state==="ready"?"Можно собирать":duration(plot.ready_at-now):"Выберите семена")+'</small>'+
        (d?button("💧 Полить",'data-farm-water="1"'+attrs,Boolean(plot.watered)||state==="ready"||!p.best_streak,"btn-quiet")+
           button("Собрать",'data-farm-harvest="1"'+attrs,state!=="ready"||!p.best_streak):
           button("Посадить · "+chosen.price+" 🍬",'data-farm-plant="'+esc(chosen.code)+'"'+attrs,!p.best_streak||s.plants_remaining<=0||p.balance<chosen.price))+'</section>';
    }).join("")+'</div>';
    return head+(!p.best_streak?'<div class="notice">Ферма станет доступна после вылупления питомца.</div>':"")+seeds+plots+
      stockList(p,c,"crop")+upgrade(p,c,"land","🧱 Расширение","Больше грядок: "+c.plot_counts.join(" → "))+
      upgrade(p,c,"tool","💧 Лейка","Полив один раз за посадку сокращает исходное время роста на "+c.water_percent[s.levels.tool-1]+"%.")+
      '<p class="notice">Урожай не портится. Семена стоят ириски, прибыль зависит от продаж и лимита. Вы можете собирать урожай вместе.</p>'+
      button("← Назад к карте",'data-click="country-back"',false,"btn-quiet");
  }
  function fishing(p,c,now){
    const s=p.countryside, state=castState(s.cast,now), rod=s.levels.rod, last=c.fish.find(d=>d.code===s.last_fish);
    return '<div class="country-banner lake-banner"><div><div class="eyebrow">ТИХАЯ БУХТА</div><h1>Рыбалка</h1><p>Дождитесь клёва и подсеките</p></div><span aria-hidden="true">🎣</span></div>'+
      '<div class="country-summary"><span>Забросы <b>'+s.casts_remaining+' / '+c.casts_daily+'</b></span><span>Коллекция <b>'+c.fish.filter(d=>n(s.collection[d.code])>0).length+' / '+c.fish.length+'</b></span></div>'+
      '<section class="panel fish-game fish-'+state+'" data-fish-game><div class="lake-art" aria-hidden="true"><div class="lake-hills"></div><div class="lake-waves"></div><span class="bobber">●</span><span class="fish-rod">🎣</span></div>'+
      '<h2 data-fish-heading>'+esc(state==="waiting"?"Ждём клёва…":state==="bite"?"КЛЮЁТ!":state==="missed"?"Рыба сорвалась":"Забросьте удочку")+'</h2>'+
      '<p data-fish-clock>'+esc(state==="waiting"?"Не подсекайте слишком рано":state==="bite"?duration(s.cast.expires_at-now)+" до схода":state==="missed"?"Можно начать заново":"Заброс бесплатный")+'</p>'+
      (s.cast?button("Подсечь!",'data-fish-reel="'+s.cast.id+'"',state!=="bite"||!p.best_streak):"")+
      button("Забросить удочку",'data-fish-cast="'+s.last_cast_id+'"',!p.best_streak||s.casts_remaining<=0||state==="waiting"||state==="bite","btn-primary")+
      (!p.best_streak?'<p class="subtitle">Дождитесь вылупления питомца.</p>':"")+
      (last?'<p class="subtitle">Последний улов: '+esc(last.icon+" "+last.name)+'</p>':"")+'</section>'+
      '<section class="panel"><div class="country-row"><strong>📖 Альбом рыб</strong><span class="chip">Удочка '+rod+'/3</span></div><div class="fish-collection">'+c.fish.map(d=>{
        const count=n(s.collection[d.code]),weight=n(d.weights[rod-1]);
        return '<div class="fish-card rarity-'+esc(d.rarity)+(count?" discovered":"")+'"><span aria-hidden="true">'+(count?esc(d.icon):"?")+'</span><div><b>'+esc(d.name)+'</b><small>'+esc(rarities[d.rarity]||"")+' · '+weight+'%</small></div><strong>'+count+'</strong></div>';
      }).join("")+'</div><p class="subtitle">Коллекция сохраняется после продажи рыбы. Шансы указаны для текущей удочки.</p></section>'+
      stockList(p,c,"fish")+upgrade(p,c,"rod","🎣 Удочка","Улучшение меняет шансы редкости и увеличивает время подсечки. Сейчас "+c.bite_windows[rod-1]+" с.")+
      '<p class="notice">Три бесплатных заброса в день на двоих. Сход рыбы расходует попытку. Следующий заброс можно сделать после завершения предыдущего.</p>'+
      button("← Назад к карте",'data-click="country-back"',false,"btn-quiet");
  }
  const api=Object.freeze({farm,fishing,plotState,castState,duration});
  if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.PetsCountryside=api;
})(typeof window!=="undefined"?window:globalThis);
