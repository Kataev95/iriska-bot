/* PETS TYCOON — shared production. All inventory, time and rewards are server-owned. */
(function () {
  const esc = x => String(x ?? "").replace(/[&<>"']/g,c=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
  const n = x => Math.max(0,Math.floor(Number(x)||0));
  function duration(seconds) {
    const total=Math.max(0,Math.ceil(Number(seconds)||0));
    const h=Math.floor(total/3600),m=Math.floor(total%3600/60),s=total%60;
    return h?h+" ч "+m+" мин":m?m+" мин "+String(s).padStart(2,"0")+" с":s+" с";
  }
  function materials(config, costs, stock) {
    return '<div class="recipe-costs">'+costs.map(req=>{
      const m=config.materials.find(x=>x.code===req.code);
      const current=n(stock[req.code]);
      return '<span class="material-cost '+(current>=req.quantity?"enough":"missing")+'" title="'+esc(m?.name||"Материал")+'">'+
        esc(m?.icon || "📦")+" "+current+"/"+n(req.quantity)+"</span>";
    }).join("")+"</div>";
  }
  function canAfford(costs, stock) {
    return costs.every(x=>n(stock[x.code])>=x.quantity);
  }
  function workerDuration(unit, state, rules) {
    const percent=rules.worker_duration_percent?.[n(state.workers)]??100;
    return Math.floor(unit.production_seconds*percent/100);
  }
  function workState(job, rules, now) {
    const left=Math.max(0,Number(job.finish_at)-now);
    const taps=n(job.taps), max=n(rules.max_taps||30);
    return {left,taps,max,ready:left<=0,canTap:left>0&&taps<max&&now>=Number(job.tap_ready_at||0)};
  }
  function batchCard(b,state,config,p,starts,now){
    const rules=config.tycoon_rules||{}, job=state.job, lv=n(state.level);
    const tapSeconds=n(rules.tap_seconds?.[lv-1]||60);
    if(!job){
      const canStart=starts>0&&canAfford(b.inputs,p.materials||{});
      return '<div class="tycoon-machine tycoon-idle"><div class="machine-art" aria-hidden="true"><span>⚙️</span><strong>'+esc(b.good_icon)+'</strong><span>⚙️</span></div>'+
        '<div class="eyebrow">ПРОИЗВОДСТВО</div><h2 class="h2">Подготовь партию</h2>'+
        '<p class="subtitle">'+duration(workerDuration(b,state,rules))+' · '+esc(b.good_icon)+' '+n(b.outputs[lv-1])+' шт.</p>'+
        materials(config,b.inputs,p.materials||{})+
        '<button class="btn btn-primary btn-wide" data-business-start="'+esc(b.code)+'" '+(!canStart?'disabled ':'')+'>'+
        (starts<=0?'Лимит запусков на сегодня':canStart?'▶ Запустить партию':'Не хватает материалов')+'</button>'+
        '<p class="subtitle">Материалы списываются один раз. После запуска ускоряй работу нажатиями или дождись готовности.</p></div>';
    }
    const w=workState(job,rules,now);
    return '<div class="tycoon-machine'+(w.ready?' tycoon-ready':'')+'" data-tycoon-machine="'+esc(b.code)+'">'+
      '<div class="row"><span class="eyebrow">ПАРТИЯ В РАБОТЕ</span><span class="chip">'+esc(b.good_icon)+' '+n(job.quantity)+' шт.</span></div>'+
      '<div class="business-job"><div><strong data-business-countdown="'+esc(b.code)+'">'+(w.ready?'Готово!':duration(w.left))+'</strong>'+
      '<small data-tycoon-status aria-live="polite">'+(w.ready?'Можно забрать партию':'Работайте вместе или дождитесь таймера')+'</small></div></div>'+
      '<button class="tycoon-tap" data-tycoon-tap="'+esc(b.code)+'" data-job-id="'+n(job.id)+'" '+(!w.canTap?'disabled ':'')+
      'aria-label="Ускорить производство на '+tapSeconds+' секунд"><span class="machine-art" aria-hidden="true"><span>⚙️</span><strong>'+esc(b.good_icon)+'</strong><span>⚙️</span></span>'+
      '<b>'+ (w.ready?'Партия готова':'РАБОТАТЬ')+'</b><small>−'+tapSeconds+' с за нажатие</small></button>'+
      '<div class="row tycoon-tap-meter"><small>Ручная работа</small><b data-tycoon-taps>'+w.taps+' / '+w.max+'</b></div>'+
      '<progress class="game-progress" data-tycoon-progress max="'+Math.max(1,w.max)+'" value="'+w.taps+'" aria-label="Ручная работа"></progress>'+
      '<button class="btn btn-cyan btn-wide" data-business-collect="'+esc(b.code)+'" data-job-id="'+n(job.id)+'" '+(w.ready?'':'disabled ')+'>'+
      (w.ready?'📦 Забрать партию':'Производится…')+'</button></div>';
  }
  function renderBusiness(p, config, serverNow, selected) {
    const economy=p.businesses || {units:{},starts_remaining:0,sales_coins_remaining:0};
    const starts=n(economy.starts_remaining), allowed=n(economy.sales_coins_remaining);
    const maxStarts=n(config.business_limits?.production_starts || 4);
    const maxCoins=n(config.business_limits?.sales_coins || 12);
    const rules=config.tycoon_rules||{};
    const units=config.businesses||[];
    const b=units.find(x=>x.code===selected)||units[0];
    const head='<div class="tycoon-banner business-hero"><div class="tycoon-banner-text"><span class="eyebrow">СЕМЕЙНОЕ ДЕЛО</span><h2>PETS TYCOON</h2><p>Построй. Производи. Улучшай.</p></div>'+
      '<div class="tycoon-banner-art" aria-hidden="true">🏭</div></div>'+
      '<div class="tycoon-summary"><div><small>Партий осталось</small><b>'+starts+' / '+maxStarts+'</b></div><div><small>Лимит продаж</small><b>🍬 '+allowed+' / '+maxCoins+'</b></div></div>'+
      '<div class="tycoon-select" aria-label="Предприятия">'+units.map(x=>{
        const state=economy.units?.[x.code]||{};
        return '<button class="tycoon-unit'+(x.code===b?.code?' active':'')+'" data-tycoon-select="'+esc(x.code)+'" aria-pressed="'+(x.code===b?.code)+'">'+
          '<span aria-hidden="true">'+esc(x.icon)+'</span><strong>'+esc(x.name)+'</strong><small>'+
          (state.level?'Ур. '+n(state.level)+' · 📦 '+n(state.stock):'Не построено')+'</small></button>';
      }).join("")+'</div>';
    if(!b)return head;
    const state=economy.units?.[b.code]||{level:0,stock:0,workers:0,job:null};
    const lv=n(state.level), qty=n(state.stock), workers=n(state.workers);
    const stock=p.materials||{}, tier=b.tiers[lv], worker=b.worker_tiers?.[workers];
    const canBuild=tier&&canAfford(tier.costs,stock)&&p.best_streak>0;
    const equipment='<section class="panel tycoon-upgrade"><div class="row"><strong>⚙️ Оборудование</strong><span class="chip">Ур. '+lv+' / 2</span></div>'+
      '<p class="subtitle">'+(lv>0?'Выход: '+n(b.outputs[lv-1])+' шт. · −'+n(rules.tap_seconds?.[lv-1]||60)+' с за нажатие':esc(b.description))+'</p>'+
      (tier?materials(config,tier.costs,stock)+'<button class="btn btn-primary btn-wide" data-business-build="'+esc(b.code)+'" '+(!canBuild?'disabled ':'')+'>'+
        (canBuild?(lv===0?'Построить предприятие':'Улучшить оборудование'):'Не хватает материалов / не вылупился')+'</button>':'<div class="business-done">Максимальный уровень</div>')+'</section>';
    if(!lv)return head+equipment+'<div class="notice">После постройки откроются ручная работа, команда, общий склад и заказы.</div><button class="btn btn-quiet btn-wide" data-click="business-back">← Назад</button>';
    const canHire=worker&&workers<lv&&canAfford(worker.costs,stock);
    const workersCard='<section class="panel tycoon-upgrade"><div class="row"><strong>🧑‍🔧 Команда</strong><span class="chip chip-green">'+workers+' / 2</span></div>'+
      '<p class="subtitle">Работники ускоряют новые партии даже вне Mini App. Время: '+duration(workerDuration(b,state,rules))+'.</p>'+
      (worker?'<p class="subtitle">После найма: '+duration(Math.floor(b.production_seconds*worker.duration_percent/100))+'</p>'+
        materials(config,worker.costs,stock)+'<button class="btn btn-wide" data-tycoon-hire="'+esc(b.code)+'" '+(!canHire?'disabled ':'')+'>'+
        (workers>=lv?'Сначала улучшите оборудование':canHire?'Нанять работника':'Не хватает материалов')+'</button>':'<div class="business-done">Команда полностью укомплектована</div>')+'</section>';
    const sellable=Math.min(qty,Math.floor(allowed/Math.max(1,n(b.sell_price))));
    const orderQty=n(rules.order_quantity||2), orderCoins=orderQty*n(b.sell_price);
    const warehouse='<section class="panel tycoon-warehouse"><div class="row"><strong>📦 Общий склад</strong><span class="chip chip-green">'+qty+' шт.</span></div>'+
      '<p class="subtitle">'+esc(b.good_icon)+' '+esc(b.good_title)+' · '+n(b.sell_price)+' 🍬 / шт.</p>'+
      '<div class="tycoon-order"><div><div class="eyebrow">ЗАКАЗ ПОКУПАТЕЛЯ</div><strong>'+orderQty+' × '+esc(b.good_title)+'</strong><small>Оплата: '+orderCoins+' 🍬</small></div>'+
      '<button class="btn btn-primary" data-tycoon-order="'+esc(b.code)+'" '+(sellable<orderQty?'disabled ':'')+'>Сдать заказ</button></div>'+
      '<button class="btn btn-quiet btn-wide" data-business-sell="'+esc(b.code)+'" '+(sellable<=0?'disabled ':'')+'>'+
      (sellable?'Продать '+sellable+' шт. за '+(sellable*b.sell_price)+' 🍬':'Нет доступных продаж')+'</button></section>';
    return head+batchCard(b,state,config,p,starts,serverNow)+warehouse+equipment+workersCard+
      '<div class="notice">Товары, оборудование и работники общие. Ириски получает продавец. Все заказы и продажи используют один дневной лимит. Новую партию запускаете сами.</div>'+
      '<button class="btn btn-quiet btn-wide" data-click="inventory">🎒 Рюкзак и ресурсы →</button>'+
      '<button class="btn btn-quiet btn-wide" data-click="business-back">← Назад</button>';
  }
  window.PetsBusiness={renderBusiness,workState,duration,workerDuration};
})();
