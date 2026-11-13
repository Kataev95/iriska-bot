/* PETS WORLD 1.5 business UI — rendering only, all state from signed API. */
(function () {
  const esc = x => String(x ?? "").replace(/[&<>"']/g,c=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
  const n = x => Math.max(0,Math.floor(Number(x)||0));
  function duration(seconds) {
    const mins=Math.ceil(Math.max(0,seconds)/60);
    return mins>=60?Math.floor(mins/60)+" ч "+mins%60+" мин":Math.max(mins,1)+" мин";
  }
  function materials(config, costs, stock) {
    return '<div class="recipe-costs">'+costs.map(req=>{
      const m=config.materials.find(x=>x.code===req.code);
      const current=n(stock[req.code]);
      return '<span class="material-cost '+(current>=req.quantity?"enough":"missing")+'">'+
        esc(m?.icon || "📦")+" "+current+"/"+n(req.quantity)+"</span>";
    }).join("")+"</div>";
  }
  function canAfford(costs, stock) {
    return costs.every(x=>n(stock[x.code])>=x.quantity);
  }
  function renderBusiness(p, config, serverNow) {
    const economy=p.businesses || {units:{},starts_remaining:0,sales_coins_remaining:0};
    const starts=n(economy.starts_remaining), allowed=n(economy.sales_coins_remaining);
    const maxStarts=n(config.business_limits?.production_starts || 4);
    const maxCoins=n(config.business_limits?.sales_coins || 12);
    const progress=Math.round((maxStarts-starts)/Math.max(1,maxStarts)*100);
    const head='<div class="section-heading">🏪 Бизнесы '+esc(p.name)+'</div>'+
      '<p class="section-lead">Ваша семейная экономика: добывайте материалы, запускайте '+
      'производство и продавайте товары за ириски внутри игры.</p>'+
      '<div class="business-hero" aria-hidden="true"><span>🌼</span><span>🏪</span><span>🧸</span>'+
      '<div class="business-ground">🍀🌿🌼🌳🌿</div></div>'+
      '<div class="panel business-limit"><div class="eyebrow">ОБЩИЕ ЛИМИТЫ СЕГОДНЯ</div>'+
      '<div class="business-limit-grid"><div><strong>'+starts+' / '+maxStarts+
      '</strong><small>Запусков осталось</small></div><div><strong>🍬 '+allowed+' / '+maxCoins+
      '</strong><small>Можно заработать</small></div></div>'+
      '<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="'+maxStarts+
      '" aria-valuenow="'+(maxStarts-starts)+'" aria-label="Запуски за день">'+
      '<i style="width:'+progress+'%"></i></div></div>';
    const cards=config.businesses.map(b=>{
      const state=economy.units?.[b.code] || {level:0,stock:0,job:null};
      const lv=n(state.level);
      const batch=state.job;
      const qty=n(state.stock);
      const tier=lv<2?b.tiers[lv]:null;
      const short=lv>0?'Ур. '+lv+'/2':'Не построено';
      const costs=tier?materials(config,tier.costs,p.materials||{}):'';
      const canBuild=tier&&canAfford(tier.costs,p.materials||{});
      const build=tier?
        '<button class="btn btn-primary btn-wide" data-business-build="'+esc(b.code)+'" '+
          (!canBuild?'disabled ':'')+'>'+
          (canBuild?(lv===0?'🧱 Построить лавку':'⬆️ Улучшить до '+(lv+1)+' уровня'):
           '🔒 Не хватает материалов')+'</button>':
        '<div class="business-done">🏆 Максимальный уровень предприятия</div>';
      let work='';
      if (lv>0) {
        let batchUi='';
        if (batch) {
          const remaining=Math.max(0,batch.finish_at-serverNow);
          batchUi='<div class="business-job"><div class="business-pulse">⚙️</div>'+
            '<div><strong>Партия '+n(batch.quantity)+' шт.</strong>'+
            '<small data-business-countdown="'+esc(b.code)+'">'+
              (remaining>0?'⏳ Осталось '+duration(remaining):'✅ Товары готовы!')+
            '</small></div></div>'+
            '<button class="btn btn-cyan btn-wide" data-business-collect="'+esc(b.code)+'" '+
            'data-job-id="'+n(batch.id)+'" '+(remaining>0?'disabled ':'')+'>'+
              (remaining>0?'⏳ Производится':'📦 Забрать готовую партию')+'</button>';
        } else {
          const canStart=starts>0&&canAfford(b.inputs,p.materials||{});
          batchUi='<div class="business-input"><strong>📋 Следующая партия</strong>'+
            '<span>⏳ '+duration(b.production_seconds)+' · '+
              esc(b.good_icon)+' '+n(b.outputs[lv-1])+' шт.</span></div>'+
            materials(config,b.inputs,p.materials||{})+
            '<button class="btn btn-primary btn-wide" data-business-start="'+esc(b.code)+'" '+
            (!canStart?'disabled ':'')+'>'+
              (starts<=0?'🔒 Лимит запусков на сегодня':canStart?'▶️ Запустить производство':
               '🔒 Не хватает материалов')+'</button>';
        }
        const sellable=Math.min(qty,Math.floor(allowed/n(b.sell_price)));
        work='<div class="business-work">'+batchUi+
          '<div class="business-warehouse"><div><strong>📦 Общий склад</strong>'+
            '<small>'+esc(b.good_icon)+' '+qty+' '+esc(b.good_title.toLowerCase())+' · '+
            n(b.sell_price)+' 🍬 / шт.</small></div>'+
            '<span class="chip chip-green">'+qty+' шт.</span></div>'+
          '<button class="btn btn-wide btn-quiet" data-business-sell="'+esc(b.code)+'" '+
            (sellable<=0?'disabled ':'')+'>'+
            (sellable>0?'💰 Продать '+sellable+' шт. за '+(sellable*b.sell_price)+' 🍬':
             qty<=0?'Склад пока пуст':'🔒 Лимит продаж на сегодня')+'</button></div>';
      }
      return '<section class="panel business-card"><div class="card-row">'+
        '<div class="business-icon">'+esc(b.icon)+'</div>'+
        '<div class="card-text"><strong>'+esc(b.name)+'</strong><small>'+
          esc(b.description)+'</small></div>'+
        '<span class="business-level">'+short+'</span></div>'+
        '<div class="business-tiers"><i class="'+(lv>=1?'on':'')+'"></i>'+
          '<i class="'+(lv>=2?'on':'')+'"></i></div>'+
        (lv>0?'<p class="business-bonus">📈 Выход: '+n(b.outputs[lv-1])+
          ' '+esc(b.good_title.toLowerCase())+' за партию</p>':'')+
        work+(!tier?'':costs+build)+'</section>';
    }).join('');
    return head+cards+
      '<div class="notice">🔐 Товары общие для двоих, ириски получает тот, кто нажал «Продать». '+
      'Лимиты заработка и запусков общие на питомца и обновляются завтра.</div>'+
      '<button class="btn btn-quiet btn-wide" data-click="inventory">🎒 Рюкзак и ресурсы →</button>'+
      '<button class="btn btn-quiet btn-wide" data-click="business-back">← Назад</button>';
  }
  window.PetsBusiness={renderBusiness};
})();
