/* PETS WORLD 1.4 — self-contained, data-driven house page. */
const esc = x => String(x ?? "").replace(/[&<>"']/g, c=>({
  "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
}[c]));
const count = n => Math.max(0,Math.round(Number(n)||0));

export function renderHouse(p, config) {
  const rooms = p.house_rooms || {};
  const total = Object.values(rooms).reduce((sum,n)=>sum+Number(n||0),0);
  const base = config.shop.find(x=>x.code==="house");
  const price = base?.price || 25;
  const scene = '<div class="house-scene" aria-hidden="true">'+
    '<div class="house-sky">✦ <span>☁️</span> ✦</div>'+
    '<div class="house-illustration">'+(p.house?'🏡':'🏚️')+'</div>'+
    '<div class="house-ground">🌿🌼🍄🌷🌿🌻🍀</div></div>';
  const top = '<div class="section-heading">🏡 Дом '+esc(p.name)+'</div>'+
    '<p class="section-lead">Стройте и улучшайте комнаты вместе. Материалы и дом общие для пары.</p>'+scene;

  if (!p.house) {
    return top+'<div class="panel house-locked"><div class="eyebrow">НАЧАЛО СТРОИТЕЛЬСТВА</div>'+
      '<h2 class="h2">🏠 Домик ещё не куплен</h2>'+
      '<p class="section-lead">Сначала купите обычный домик за ириски. '+
      'Его старый бонус (медленнее уменьшается сытость и настроение) сохранится. '+
      'После покупки сможете строить комнаты за материалы из походов.</p>'+
      '<button class="btn btn-primary btn-wide" data-buy="house" '+
      (p.balance<price?'disabled ':'')+'>'+
      (p.balance<price?'🔒 Недостаточно ирисок':'🏠 Купить за '+price+' 🍬')+'</button></div>'+
      '<button class="btn btn-quiet btn-wide" data-click="house-back">← Назад</button>';
  }
  const cards = config.house_rooms.map(room => {
    const lv = Math.min(2,Math.max(0,count(rooms[room.code])));
    const tier = room.tiers[lv] || null;
    const finished = lv>=2;
    const used = (p.house_used||[]).includes(room.code);
    const stock = p.materials || {};
    const needed = tier ? tier.costs.map(req => {
      const mat = config.materials.find(m=>m.code===req.code);
      const available = count(stock[req.code]);
      return '<span class="material-cost '+(available>=req.quantity?'enough':'missing')+'">'+
        esc(mat?.icon||'📦')+' '+available+'/'+count(req.quantity)+'</span>';
    }).join('') : '';
    const affordable = tier && tier.costs.every(req=>count(stock[req.code])>=req.quantity);
    const stat = room.code==="bedroom" ? count(p.energy) : count(p.joy);
    const daily = lv>0 && room.code!=="workshop" ?
      '<button class="btn btn-cyan btn-wide" data-action="room_use" data-code="'+esc(room.code)+'" '+
      (used||stat>=95?'disabled ':'')+'>'+
      (used?'✅ Сегодня уже использовано':stat>=95?'💚 Почти полный показатель':
        room.code==="bedroom"?'🌙 Отдохнуть в спальне':'🌸 Поиграть в саду')+
      '</button>' : '';
    return '<article class="panel house-room room-'+esc(room.code)+'">'+
      '<div class="card-row"><div class="room-icon">'+esc(room.icon)+'</div>'+
        '<div class="card-text"><strong>'+esc(room.name)+'</strong><small>'+esc(room.description)+'</small></div>'+
        '<span class="room-level">Ур. '+lv+'/2</span></div>'+
      '<div class="room-tiers"><span class="'+(lv>=1?'on':'')+'"></span>'+
      '<span class="'+(lv>=2?'on':'')+'"></span></div>'+
      '<div class="room-bonus">'+esc(lv>0?room.tiers[lv-1].effect:'🔒 Пока не построено')+'</div>'+
      daily+
      (finished?'<div class="room-complete">🏆 Максимальное улучшение</div>':
        '<div class="recipe-costs">'+needed+'</div>'+
        '<button class="btn btn-primary btn-wide" data-build-room="'+esc(room.code)+'" '+
        (!affordable?'disabled ':'')+'>'+
        (affordable?lv>0?'⬆️ Улучшить комнату':'🧱 Построить комнату':
          '🔒 Не хватает материалов')+'</button>')+'</article>';
  }).join('');
  return top+
    '<div class="panel house-overview"><div class="row"><div><div class="eyebrow">МОЙ ДОМ</div>'+
      '<div class="title">🏡 Уютное гнёздышко</div></div>'+
      '<span class="chip chip-purple">🧱 '+total+' / 6</span></div>'+
      '<div class="progress house-progress" role="progressbar" aria-label="Строительство домика" '+
      'aria-valuemin="0" aria-valuemax="6" aria-valuenow="'+total+'">'+
      '<i style="width:'+Math.round(total/6*100)+'%"></i></div>'+
      '<p class="subtitle">Домик куплен. Материалы добываются в приключениях.</p></div>'+
    '<h2 class="h2 house-rooms-title">✨ Комнаты</h2>'+cards+
    '<button class="btn btn-quiet btn-wide" data-click="inventory">🎒 Рюкзак и материалы →</button>'+
    '<button class="btn btn-quiet btn-wide" data-click="house-back">← Назад</button>';
}
