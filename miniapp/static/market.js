/* PETS WORLD 1.6 — chat-local escrow market, all interactions use buttons. */
(function () {
  const esc=x=>String(x??"").replace(/[&<>"']/g,c=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
  const n=x=>Math.max(0,Math.floor(Number(x)||0));
  const quantities=[1,2,3,5];
  const prices=[1,2,3,5,8,13,20];

  function allItems(config) {
    return [
      ...config.materials.map(m=>({kind:"material",code:m.code,icon:m.icon,
        name:m.name,rarity:m.rarity})),
      ...config.businesses.map(b=>({kind:"good",code:b.code,
        icon:b.good_icon,name:b.good_title,rarity:"common"})),
    ];
  }
  function stockOf(p,item) {
    return item.kind==="material" ? n(p.materials?.[item.code]) :
      n(p.businesses?.units?.[item.code]?.stock);
  }
  function catalogItem(items,kind,code) {
    return items.find(x=>x.kind===kind&&x.code===code) ||
      {kind,code,icon:"📦",name:"Предмет",rarity:"common"};
  }
  function label(code,list,active,attr) {
    return list.map(x=>'<button class="market-option '+(x===active?'on':'')+
      '" '+attr+'="'+esc(x)+'" type="button">'+esc(x)+'</button>').join('');
  }
  function duration(seconds) {
    const minutes=Math.ceil(Math.max(0,seconds)/60);
    if(minutes<=0) return "Истекло";
    if(minutes<60) return minutes+" мин";
    return Math.floor(minutes/60)+" ч "+minutes%60+" мин";
  }
  function renderMarket(market,p,config,choice,now) {
    const rules=config.market_rules||{};
    const items=allItems(config);
    const top='<div class="section-heading">🏪 Рынок питомцев</div>'+
      '<p class="section-lead">Обменивайтесь материалами и изготовленными товарами '+
      'с другими питомцами из того же Telegram-чата.</p>'+
      '<div class="market-scene" aria-hidden="true">🏕️ <span>🏪</span> 🎁</div>';
    if(!market) return top+'<div class="panel loading-market">'+
      '<div class="spinner"></div><p>Загружаем предложения вашего чата…</p>'+
      '<button class="btn btn-quiet btn-wide" data-market-refresh="1">Обновить</button></div>'+
      '<button class="btn btn-quiet btn-wide" data-click="market-back">← Назад</button>';
    const leftPosts=n(market.remaining_posts),leftActive=n(market.remaining_active),
          leftBuys=n(market.remaining_buys);
    const cur=items.find(x=>x.kind===choice.kind&&x.code===choice.code)||
      items.find(x=>x.kind==="material")||items[0];
    const stock=stockOf(p,cur);
    const quantity=quantities.includes(choice.quantity)?choice.quantity:1;
    const price=prices.includes(choice.price)?choice.price:2;
    const canPost=leftPosts>0&&leftActive>0&&stock>=quantity;
    const stockCards=items.filter(i=>i.kind===choice.kind).map(i=>
      '<button type="button" class="market-item '+(i.code===cur.code?'selected':'')+
      '" data-market-code="'+esc(i.code)+'">'+
      '<span>'+esc(i.icon)+'</span><strong>'+esc(i.name)+'</strong>'+
      '<small>На складе: '+stockOf(p,i)+'</small></button>').join('');
    const seller='<div class="panel market-seller"><div class="row">'+
      '<div><div class="eyebrow">МОИ ОБЪЯВЛЕНИЯ</div><h2 class="h2">📣 Выставить товар</h2></div>'+
      '<span class="chip chip-purple">'+leftActive+' свободно</span></div>'+
      '<p class="section-lead">Товары резервируются сразу. Покупатель платит '+
      'продавцу ирисками. Если не продано за 48 часов, всё возвращается на склад.</p>'+
      '<div class="market-kind-row"><button class="pill '+(choice.kind==="material"?"active":"")+
      '" data-market-kind="material">🍃 Материалы</button>'+
      '<button class="pill '+(choice.kind==="good"?"active":"")+
      '" data-market-kind="good">📦 Товары</button></div>'+
      '<div class="market-item-grid">'+stockCards+'</div>'+
      '<div class="market-qty-label">Количество <strong>'+quantity+' шт.</strong></div>'+
      '<div class="market-options">'+label("quantity",quantities,quantity,"data-market-quantity")+'</div>'+
      '<div class="market-qty-label">Цена за штуку <strong>'+price+' 🍬</strong></div>'+
      '<div class="market-options">'+label("price",prices,price,"data-market-price")+'</div>'+
      '<div class="market-total"><span>'+esc(cur.icon)+' '+quantity+' шт.</span>'+
        '<strong>Итого '+(quantity*price)+' 🍬</strong></div>'+
      '<button class="btn btn-cyan btn-wide" data-market-post="1" '+
      (!canPost?"disabled ":"")+'>'+
      (leftPosts<=0?"🔒 Лимит объявлений на сегодня":
       leftActive<=0?"🔒 Все места заняты":
       stock<quantity?"🔒 На складе недостаточно предметов":"📣 Разместить предложение")+
      '</button>'+
      '<p class="market-footnote">Можно разместить ещё '+leftPosts+' из '+
      n(rules.posts_per_day||5)+' объявлений за сегодня.</p></div>';
    const listings=(market.listings||[]).map(l=>{
      const info=catalogItem(items,l.kind,l.code);
      const remaining=l.expires_at-now;
      const expired=remaining<=0;
      const isMine=l.own;
      const canBuy=!isMine&&l.can_buy&&leftBuys>0&&p.balance>=l.total_price&&!expired;
      const button=isMine?
        '<button class="btn btn-quiet btn-wide" data-market-cancel="'+n(l.id)+'">'+
          '↩️ Снять объявление и вернуть товар</button>':
        '<button class="btn btn-primary btn-wide" data-market-buy="'+n(l.id)+'" '+
          (!canBuy?'disabled ':'')+'>'+
          (!l.can_buy?'🔒 Собственное предложение':
           leftBuys<=0?'🔒 Дневной лимит покупок':
           p.balance<l.total_price?'🔒 Не хватает ирисок':
           expired?'⏳ Срок объявления истёк':'🛒 Купить за '+n(l.total_price)+' 🍬')+
          '</button>';
      return '<article class="panel market-listing">'+
        '<div class="card-row"><div class="market-listing-icon">'+esc(info.icon)+'</div>'+
        '<div class="card-text"><strong>'+esc(info.name)+' × '+n(l.quantity)+'</strong>'+
        '<small>🐾 '+esc(l.pet_name)+(isMine?' · Ваш питомец':'')+'</small></div>'+
        '<div class="market-listing-price">🍬 '+n(l.total_price)+'</div></div>'+
        '<div class="row market-listing-meta"><span class="chip">'+n(l.unit_price)+
          ' 🍬 / шт.</span><span class="chip" data-market-expiry="'+n(l.id)+
          '" data-market-expires="'+Number(l.expires_at)+'">⏳ '+duration(remaining)+'</span></div>'+button+
        '</article>';
    }).join('');
    return top+'<div class="panel market-overview">'+
      '<div class="row"><strong>🔐 Торговля только в вашем чате</strong>'+
      '<span class="chip chip-green">🍬 '+n(p.balance)+'</span></div>'+
      '<div class="market-stats"><div><strong>'+leftPosts+'</strong><small>Объявлений сегодня</small></div>'+
      '<div><strong>'+leftBuys+'</strong><small>Покупок сегодня</small></div></div></div>'+
      seller+'<div class="section-heading market-listings-head">🛒 Предложения игроков</div>'+
      '<div class="row"><p class="section-lead">Другие питомцы из этого чата</p>'+
      '<button type="button" class="btn btn-quiet" data-market-refresh="1">🔄</button></div>'+
      (listings||'<div class="panel notice">Пока нет активных предложений. Станьте первым продавцом!</div>')+
      '<button class="btn btn-quiet btn-wide" data-click="market-back">← Назад</button>';
  }
  window.PetsMarket={renderMarket,duration,allItems,stockOf};
})();
