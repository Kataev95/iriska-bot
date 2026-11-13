const TG = window.Telegram?.WebApp;
const app = document.getElementById("app");
const nav = document.getElementById("bottom-nav");
const wallet = document.getElementById("wallet");
const modal = document.getElementById("modal");
const toast = document.getElementById("toast");

// Telegram owns the native Close / Collapse controls in Fullscreen.
 // We cannot move those buttons; keep PETS' own header below their safe area.
function syncSafeArea() {
  if (!TG) return;
  const root = document.documentElement.style;
  const apply = (obj, side, name) => {
    const px = obj?.[side];
    if (Number.isFinite(px) && px >= 0 && px < 500) root.setProperty(name, px + "px");
  };
  apply(TG.safeAreaInset, "top", "--pets-sdk-safe-top");
  apply(TG.safeAreaInset, "bottom", "--pets-sdk-safe-bottom");
  apply(TG.contentSafeAreaInset, "top", "--pets-sdk-content-top");
  apply(TG.contentSafeAreaInset, "bottom", "--pets-sdk-content-bottom");
}
for (const event of ["safeAreaChanged", "contentSafeAreaChanged", "fullscreenChanged", "viewportChanged"]) {
  try { TG?.onEvent?.(event, syncSafeArea); } catch { /* old Telegram WebView */ }
}
syncSafeArea();

const ui = { config: null, pet: null, pets: [], selected: null, tab: "home",
             group: "food", busy: false, syncing: false, lastSync: 0,
             syncSerial: 0, clockDelta: 0, toastTimer: 0, picker: false,
             returnTab: "home", league: null, leagueLoading:false, leagueFetchedAt:0, leagueRequest:0,
             market: null, marketLoading:false, marketFetchedAt:0,
             marketRequest:0, marketSelection:{kind:"material",code:"leaf",quantity:1,price:2} };
const tabs = [
  ["home","🏠","Главная"], ["trips","🧭","Походы"],
  ["tasks","📋","Задания"], ["shop","🛍","Магазин"],
  ["growth","⭐","Развитие"],
];
const emoji = {food:"🍪",wear:"🎀",freeze:"🧊",house:"🏠"};
const resourceQuantity = (p, code) => Math.max(0,Number(p.materials?.[code] || 0));
const safe = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
}[c]));
const value = n => Math.max(0, Math.round(Number(n || 0)));
const stat = (icon,title,amount) => '<div class="stat '+(value(amount)<30?'is-low':value(amount)<60?'is-medium':'')+'"><span>'+icon+' '+title+'</span>'+
  '<strong>'+value(amount)+'%</strong><div class="progress"><i style="width:'+Math.min(value(amount),100)+'%"></i></div></div>';
const serverNow = () => (Date.now() + ui.clockDelta) / 1000;
const syncClock = p => {if(Number.isFinite(Number(p.server_time))) ui.clockDelta = p.server_time*1000-Date.now();};
function duration(seconds) {
  const total = Math.max(0,Math.ceil(seconds/60));
  if (total>=60) return Math.floor(total/60)+' ч '+(total%60)+' мин';
  return Math.max(1,total)+' мин';
}
function haptic(type="light") {
  try {
    if(type==="success" || type==="error") TG?.HapticFeedback?.notificationOccurred(type);
    else TG?.HapticFeedback?.impactOccurred(type);
  } catch {}
}
function tap() { haptic("light"); }
function showToast(text) {
  toast.textContent = String(text || "");
  toast.classList.add("visible");
  clearTimeout(ui.toastTimer);
  ui.toastTimer = setTimeout(() => toast.classList.remove("visible"), 3500);
}
async function api(path, payload) {
  if (!TG?.initData) throw new Error("Открой игру через кнопку в Telegram.");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const result = await fetch(path, {
      method: payload ? "POST" : "GET",
      headers: {"X-Telegram-Init-Data": TG.initData, ...(payload ? {"Content-Type":"application/json"} : {})},
      body: payload ? JSON.stringify(payload) : undefined,
      cache:"no-store", signal: controller.signal,
    });
    let data;
    try { data = await result.json(); } catch { throw new Error("Некорректный ответ сервера"); }
    if (!result.ok) throw new Error(data.error || data.message || "Не удалось выполнить запрос");
    return data;
  } catch (e) {
    if (e.name==="AbortError") {
      throw new Error(payload
        ? "Сервер не ответил. Сначала обнови данные, прежде чем повторять покупку или действие."
        : "Сервер не отвечает. Проверь интернет и повтори.");
    }
    throw e;
  } finally {clearTimeout(timer);}
}

/* PETS 1.2 — decorative UI feedback only (no change to XP, energy or currency). */
const effects = document.getElementById("effects");
const careSymbols = {feed:"🍖",play:"🎾",sleep:"💤"};
let effectTimer = null;
function playEffect(label, type="sparkle") {
  if (document.hidden || !effects) return;
  effects.replaceChildren();
  const card = document.createElement("div");
  card.className = "effect-card effect-"+type;
  card.textContent = String(label);
  effects.append(card);
  // Only twelve short-lived, compositor-friendly particles; no canvas loop.
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    for (let i=0; i<12; i++) {
      const particle = document.createElement("span");
      particle.className = "effect-particle";
      particle.textContent = (i%3===0 ? "✦" : i%3===1 ? "💖" : "✨");
      particle.style.setProperty("--particle-index", String(i));
      particle.style.setProperty("--particle-angle", (i*30)+"deg");
      particle.style.setProperty("--particle-distance", (72+(i%4)*16)+"px");
      effects.append(particle);
    }
  }
  clearTimeout(effectTimer);
  effectTimer = setTimeout(() => effects.replaceChildren(), 1900);
}
function petGreeting() {
  const pet = ui.pet;
  if (!pet || ui.busy) return;
  haptic("soft");
  playEffect("💚 "+pet.name+" рад встрече!", "greeting");
  const hero = app.querySelector(".hero");
  if (!hero) return;
  hero.classList.remove("pet-waving");
  // Restart the short CSS animation for another intentional tap.
  void hero.offsetWidth;
  hero.classList.add("pet-waving");
  setTimeout(() => hero.classList.remove("pet-waving"), 700);
}
function showActionEffect(kind, code, before, after, message) {
  if (!before || !after || before.id !== after.id) return;
  if (after.level > before.level) {
    playEffect("🎉 Новый уровень! "+after.level+" / 20", "level");
    haptic("success");
    return;
  }
  // Announce only changes confirmed in the authoritative DB snapshot.
  if (kind === "care" && (message.startsWith("✅") || message.startsWith("💕"))) {
    playEffect((careSymbols[code]||"💗")+" "+after.name+" доволен!", "care");
    return;
  }
  if (kind === "buy" && after.balance < before.balance) {
    playEffect("🛍 Покупка успешна", "purchase");
    return;
  }
  if (kind === "wear" && before.worn.join(",") !== after.worn.join(",")) {
    playEffect("🎀 Образ обновлён", "care");
    return;
  }
  if (kind === "profession" && before.profession !== after.profession) {
    playEffect("🎓 Новая профессия!", "level");
    return;
  }
  if (kind === "craft" && !(before.crafts||[]).includes(code) && (after.crafts||[]).includes(code)) {
    const recipe = ui.config.recipes.find(x=>x.code===code);
    playEffect((recipe?.icon || "🎁")+" Новый сувенир!", "reward");
    return;
  }
  if (kind === "build_room" &&
      Number(after.house_rooms?.[code]||0) > Number(before.house_rooms?.[code]||0)) {
    const room = ui.config.house_rooms.find(r=>r.code===code);
    playEffect((room?.icon || "🏡")+" Комната улучшена!", "level");
    return;
  }
  if (kind === "room_use" && message.startsWith("✅") &&
      !(before.house_used||[]).includes(code) && (after.house_used||[]).includes(code)) {
    playEffect(message, "care");
    return;
  }
  if (kind==="business_build" &&
      (after.businesses?.units?.[code]?.level||0)>(before.businesses?.units?.[code]?.level||0)) {
    playEffect("🏪 Предприятие улучшено!", "level");
    return;
  }
  if (kind==="business_start" && !before.businesses?.units?.[code]?.job &&
      after.businesses?.units?.[code]?.job) {
    playEffect("⚙️ Производство запущено", "care");
    return;
  }
  if (kind==="business_collect" &&
      (after.businesses?.units?.[code]?.stock||0)>(before.businesses?.units?.[code]?.stock||0)) {
    playEffect("📦 Товары на складе!", "reward");
    return;
  }
  if (kind==="business_sell" && after.balance>before.balance) {
    playEffect("🍬 +"+(after.balance-before.balance)+" ирисок!", "reward");
    return;
  }
  if (kind.startsWith("market_") && message.startsWith("✅")) {
    playEffect(kind==="market_create"?"📣 Объявление размещено":kind==="market_buy"?
       "🛍 Сделка совершена!":"↩️ Товары возвращены",kind==="market_buy"?"reward":"care");
    return;
  }
  let success = false;
  if (kind === "claim") {
    success = Boolean(before.trip && !after.trip);
  } else if (kind === "daily") {
    const previous = before.daily.filter(t => t.claimed).length;
    success = after.daily.filter(t => t.claimed).length > previous;
  }
  if (success) {
    const xp = Math.max(0,after.xp-before.xp);
    const coins = Math.max(0,after.balance-before.balance);
    const resources = ui.config.materials
      .filter(m => resourceQuantity(after,m.code)>resourceQuantity(before,m.code))
      .map(m => m.icon+" +"+(resourceQuantity(after,m.code)-resourceQuantity(before,m.code)));
    const gain = [xp>0?"⭐ +"+xp+" XP":"",coins>0?"🍬 +"+coins:"",...resources]
      .filter(Boolean).join("  ");
    playEffect(gain || "🎁 Награда получена!", "reward");
  }
}

function renderPicker() {
  if (ui.pets.length < 2) return "";
  const options = ui.picker ? '<div>'+ui.pets.map(p=>
    '<button class="picker-item" data-pet="'+p.id+'">🐾 '+safe(p.name)+' · 🔥 '+p.streak+'</button>'
  ).join("")+'</div>' : "";
  return '<div class="picker-container"><button class="picker" data-click="picker">🐾 '+
    safe(ui.pet?.name || "Питомцы")+' ▾</button>'+options+'</div>';
}
function hero(p) {
  const target = p.next_xp ?? p.xp;
  const prior = Number.isFinite(p.level_start_xp) ? p.level_start_xp : 25*p.level*(p.level-1);
  const progress = p.next_xp == null ? 100 :
    Math.min(100,Math.max(0,Math.round((p.xp-prior)/Math.max(1,target-prior)*100)));
  return '<div class="hero"><div class="hero-overlay">✨ Уровень '+p.level+' / 20</div>'+
    '<img src="'+safe(p.image)+'" alt="Питомец '+safe(p.name)+'" decoding="async">'+
    '<button type="button" class="pet-touch" data-click="pet-greet" aria-label="Поздороваться с питомцем — визуальный эффект, без затрат ирисок">'+
      '<span class="pet-hint" aria-hidden="true">✨ Поприветствовать</span></button></div>'+
    '<div class="panel"><div class="eyebrow">МОЙ ПИТОМЕЦ</div>'+
    '<div class="row"><div><div class="hero-title">'+safe(p.name)+'</div>'+
    '<div class="subtitle">🤝 Вместе с '+safe(p.partner)+'</div></div>'+
    '<div class="chip chip-purple">🔥 '+p.streak+' дн.</div></div>'+
    '<div class="stat-grid">'+stat("🍖","Сытость",p.food)+stat("😊","Настроение",p.joy)+stat("⚡","Энергия",p.energy)+'</div>'+
    '<div class="xp-line"><b>⭐ '+p.xp+' XP</b><div class="progress" role="progressbar" aria-label="Опыт текущего уровня" aria-valuenow="'+progress+'" aria-valuemin="0" aria-valuemax="100"><i style="width:'+progress+'%"></i></div>'+
    '<small>'+(p.next_xp == null ? "MAX":p.next_xp+' XP')+'</small></div>'+
    '<p class="mood-caption">'+safe(p.mood||"🐾 У питомца всё хорошо")+'</p></div>';
}
function home(p) {
  const actions = ui.config.actions.map(action => {
    const state = p.care_state?.[action.code] || {};
    const wait = Math.max(0,(state.ready_at||0)-serverNow());
    const blocked = Boolean(state.blocked_reason);
    const disabled = blocked || wait>0;
    const hint = wait>0 ? "через "+duration(wait) : (blocked ? "Недоступно" : "Можно сейчас");
    return '<button class="btn care-button" data-action="care" data-code="'+safe(action.code)+'" '+
      (disabled?'disabled ':'')+'title="'+safe(state.blocked_reason||hint)+'">'+
      '<span>'+safe(action.name)+'</span><small>'+(disabled?safe(hint):'✓ Готово')+'</small></button>';
  }).join("");
  return hero(p)+'<div class="panel"><div class="eyebrow">ЕЖЕДНЕВНЫЙ УХОД</div>'+
    '<h2 class="h2">Позаботьтесь о '+safe(p.name)+'</h2>'+
    '<div class="buttons">'+actions+'</div></div>'+
    '<div class="notice">'+(p.today_fed
      ? "✅ Сегодня ваша пара уже поддержала серию!"
      : "🕊 Напишите друг другу в чате, чтобы поддержать серию общения.")+'</div>'+
    '<div class="panel"><div class="row"><strong>🎀 Гардероб</strong>'+
    '<button class="btn btn-quiet" data-click="wardrobe">Открыть →</button></div>'+
    '<p class="subtitle">'+p.items.length+' вещей · '+p.worn.length+' надето'+
    (p.house ? ' · 🏠 Домик' : '')+'</p></div>'+
    '<div class="panel home-link"><div class="row"><div><div class="h2">🏡 Дом '+safe(p.name)+'</div>'+
    '<p class="subtitle">'+(p.house
      ? 'Улучшено '+Object.values(p.house_rooms||{}).reduce((sum,n)=>sum+n,0)+' / 6 уровней комнат'
      : 'Постройте свой уютный домик')+'</p></div>'+
    '<button class="btn btn-primary" data-click="house">Открыть →</button></div></div>'+
    '<div class="panel business-link"><div class="row"><div><div class="h2">🏪 Семейные бизнесы</div>'+
    '<p class="subtitle">'+(p.businesses?.starts_remaining ?? 4)+' производства осталось сегодня · '+
    '🍬 '+(p.businesses?.sales_coins_remaining ?? 12)+' к продаже</p></div>'+
    '<button class="btn btn-primary" data-click="business">Открыть →</button></div></div>'+
    '<div class="panel league-link"><div class="row"><div><div class="h2">🏆 Лига питомцев</div>'+
    '<p class="subtitle">Достижения и еженедельный рейтинг вашего чата</p></div>'+
    '<button class="btn btn-primary" data-click="league">Открыть →</button></div></div>'+
    '<div class="panel market-link"><div class="row"><div><div class="h2">🛒 Рынок питомцев</div>'+
    '<p class="subtitle">Покупайте и продавайте предметы в вашем Telegram-чате</p></div>'+
    '<button class="btn btn-primary" data-click="market">Открыть →</button></div></div>'+
    '<div class="panel backpack-link"><div class="row"><div><div class="h2">🎒 Общий рюкзак</div>'+
    '<p class="subtitle">'+(p.crafts||[]).length+' сувениров · '+
    ui.config.materials.reduce((sum,m)=>sum+resourceQuantity(p,m.code),0)+' материалов</p></div>'+
    '<button class="btn btn-primary" data-click="inventory">Открыть →</button></div>'+
    ((p.crafts||[]).length ?
      '<div class="trophy-strip">'+ui.config.recipes.filter(r=>p.crafts.includes(r.code))
        .map(r=>'<span class="chip chip-purple">'+safe(r.icon)+' '+safe(r.name)+'</span>').join('')+'</div>' : '')+
    '</div>';
}
function routeLoot(route, p) {
  if (!route) return '<span class="muted">Нет данных</span>';
  const loot = p?.house_route_loot?.[route.code] || route.loot || {};
  return ui.config.materials.filter(m => loot[m.code] > 0).map(m =>
    '<span class="chip material-chip '+safe(m.rarity)+'">'+safe(m.icon)+' +'+
    value(loot[m.code])+' '+safe(m.name)+'</span>'
  ).join("");
}
function trips(p) {
  if (p.trip) {
    const r = ui.config.routes.find(x => x.code === p.trip.route);
    const left = Math.max(0,p.trip.finish_at-serverNow());
    const title = r ? r.name : "Экспедиция";
    const timeText = left ? Math.ceil(left/60)+' мин до возвращения' : 'Готово к получению';
    return '<div class="section-heading">🧭 Приключения</div><p class="section-lead">Каждый поход приносит опыт и ириски.</p>'+
      '<div class="panel"><div class="eyebrow">ТЕКУЩИЙ ПОХОД</div>'+
      '<div class="card-row"><div class="card-icon">🗺️</div><div class="card-text"><strong>'+safe(title)+'</strong>'+
      '<small data-trip-countdown>⏳ '+timeText+'</small></div></div>'+
      '<div class="route-info"><span class="chip">⭐ '+p.trip.xp+' XP</span><span class="chip">🍬 '+p.trip.coins+'</span></div>'+
      '<p class="route-loot-title">🎒 Материалы после возвращения</p>'+
      '<div class="route-loot">'+routeLoot(r,p)+'</div>'+
      (left ? '<button class="btn btn-wide" data-click="refresh">🔄 Обновить</button>' :
        '<button class="btn btn-primary btn-wide" data-action="claim" data-trip="'+p.trip.id+'">🎁 Забрать награду</button>')+
      '</div>';
  }
  return '<div class="section-heading">🧭 Приключения</div>'+
    '<p class="section-lead">Выбирай маршрут, собирай награды и повышай уровень питомца.</p>'+
    '<div class="notice">🎟 Походов осталось сегодня: <b>'+value(p.trips_remaining ?? 3)+'/3</b></div>'+
    '<button class="btn btn-quiet btn-wide" data-click="inventory">🎒 Рюкзак и мастерская →</button>'+
    ui.config.routes.map(r => {
      const bonusXp = p.profession === "explorer" ? Math.floor(r.xp*1.2) : r.xp;
      const energy = r.energy-(p.profession==="artisan" ? 3:0);
      const coins = r.coins+(p.profession==="merchant" ? 1:0);
      const time = r.seconds<3600 ? r.seconds/60+' мин' : r.seconds/3600+' ч';
      const limited = p.trips_remaining === 0;
      const locked = limited || p.food<20 || p.energy<energy;
      return '<div class="panel route"><div class="card-row"><div class="card-icon">'+
        safe(r.name.split(" ")[0])+'</div><div class="card-text"><strong>'+
        safe(r.name)+'</strong><small>Приключение · '+time+'</small></div></div>'+
        '<div class="route-info"><span class="chip">⚡ '+energy+'</span>'+
        '<span class="chip">⭐ '+bonusXp+' XP</span><span class="chip">🍬 '+coins+'</span></div>'+
        '<div class="route-loot">🎒 '+routeLoot(r,p)+'</div>'+
        '<button class="btn btn-primary" data-action="start" data-code="'+safe(r.code)+'" '+
        (locked?'disabled':'')+'>'+(limited?'🔒 Лимит походов на сегодня':locked?'🔒 Мало энергии или еды':'🧭 Отправиться')+'</button></div>';
    }).join("");
}
function tasks(p) {
  const claimable = p.daily.some(x=>x.done&&!x.claimed);
  const done = p.daily.filter(x=>x.done).length;
  const count = p.daily.length;
  const percent = count ? Math.round(done/count*100) : 0;
  return '<div class="section-heading">📋 Ежедневные задания</div>'+
    '<p class="section-lead">Выполняйте действия вдвоём. Опыт общий у питомца.</p>'+
    '<div class="panel task-summary"><div class="row"><strong>🌟 Прогресс дня</strong>'+
       '<span class="chip chip-green">'+done+' / '+count+'</span></div>'+
       '<div class="progress task-progress" role="progressbar" aria-label="Выполненные задания" aria-valuemin="0" aria-valuemax="'+count+'" aria-valuenow="'+done+'">'+
       '<i style="width:'+percent+'%"></i></div>'+
       '<p class="subtitle">'+(done===count?'Все задания выполнены!':'Продолжай ухаживать и путешествовать')+'</p></div>'+
    p.daily.map(t=>'<div class="panel"><div class="card-row"><div class="card-icon">'+
      (t.claimed?'🎁':t.done?'✅':'▫️')+'</div><div class="card-text"><strong>'+
      safe(t.name)+'</strong><small>'+ (t.claimed?'Награда получена':t.done?'Готово к получению':'Ещё не выполнено')+
      '</small></div><div class="card-end">+'+t.xp+' XP</div></div></div>').join("")+
    (claimable?'<button class="btn btn-cyan btn-wide" data-action="daily">🎁 Забрать весь опыт</button>':
    '<div class="notice">'+(p.daily.every(t=>t.claimed)
      ? "✅ Все награды получены. Новые задания будут завтра."
      : "✨ Выполняй задания, чтобы получить опыт. Новые задания — завтра.")+'</div>');
}
function shop(p) {
  const groups = [["food","🍪 Еда"],["wear","🎀 Аксессуары"],["extras","🏠 Улучшения"]];
  const category = group => group==="extras" ? ["freeze","house"] : [group];
  return '<div class="section-heading">🛍 Магазин</div>'+
    '<p class="section-lead">Покупки за ириски. Баланс: 🍬 '+p.balance+'</p>'+
    '<div class="tabs">'+groups.map(([g,t])=>'<button class="pill '+(ui.group===g?'active':'')+
      '" data-group="'+g+'">'+t+'</button>').join("")+'</div>'+
    ui.config.shop.filter(item=>category(ui.group).includes(item.kind)).map(item=>{
      const owned = item.kind==="wear"&&p.items.includes(item.code)||item.kind==="house"&&p.house;
      return '<div class="panel"><div class="card-row"><div class="card-icon">'+(emoji[item.kind]||'🎁')+
        '</div><div class="card-text"><strong>'+safe(item.name)+'</strong><small>'+
        safe(item.info)+'</small></div><div class="card-end">'+(owned?'✅':'🍬 '+item.price)+'</div></div>'+
        '<button class="btn btn-wide '+(owned?'btn-quiet':'btn-primary')+
        '" '+(owned||p.balance<item.price?'disabled':'')+
        ' data-buy="'+safe(item.code)+'">'+(owned?'Куплено':p.balance<item.price?'Не хватает ирисок':'Купить →')+'</button></div>';
    }).join("");
}
function growth(p) {
  const target=p.next_xp;
  return '<div class="section-heading">⭐ Развитие</div>'+
    '<p class="section-lead">Уровень и профессия сохраняются навсегда.</p>'+
    '<div class="panel"><div class="eyebrow">ПРОГРЕСС</div><div class="title">Уровень '+p.level+' / 20</div>'+
    '<p class="muted">'+p.xp+' XP · '+(target==null?'Максимальный уровень':'до следующего '+(target-p.xp)+' XP')+'</p>'+
    '<div class="stat-grid"><div class="stat"><span>🔥 Серия</span><strong>'+p.streak+'</strong></div>'+
    '<div class="stat"><span>🏅 Рекорд</span><strong>'+p.best_streak+'</strong></div>'+
    '<div class="stat"><span>🧊 Заморозки</span><strong>'+p.freezes+'</strong></div></div></div>'+
    '<div class="panel"><div class="eyebrow">СПЕЦИАЛИЗАЦИЯ</div><div class="h2">🎓 Профессия</div>'+
    (p.profession?'<div class="notice">✅ '+safe(p.profession_title)+' · выбрана для обоих хозяев</div>':
      p.level<5?'<div class="notice">🔒 Откроется на 5-м уровне.</div>':
      ui.config.professions.map(job=>'<div class="card-row" style="margin-top:14px">'+
        '<div class="card-text"><strong>'+safe(job.name)+'</strong><small>'+safe(job.effect)+'</small></div>'+
        '<button class="btn btn-primary" data-profession="'+safe(job.code)+'">Выбрать</button></div>').join("")
    )+'</div><button class="btn btn-wide" data-click="wardrobe">🎀 Гардероб →</button>'+
    '<button class="btn btn-quiet btn-wide" data-click="inventory">🎒 Рюкзак и мастерская →</button>'+
    '<button class="btn btn-quiet btn-wide" data-click="house">🏡 Мой дом →</button>'+
    '<button class="btn btn-quiet btn-wide" data-click="business">🏪 Бизнесы и производство →</button>'+
    '<button class="btn btn-quiet btn-wide" data-click="market">🛒 Рынок и торговля →</button>'+
    '<button class="btn btn-quiet btn-wide" data-click="league">🏆 Достижения и рейтинги →</button>';
}
function inventory(p) {
  const stock = p.materials || {};
  const owned = p.crafts || [];
  const total = ui.config.materials.reduce((count,m)=>count+resourceQuantity(p,m.code),0);
  const rows = ui.config.materials.map(m => {
    const count = resourceQuantity(p,m.code);
    return '<div class="resource-card '+safe(m.rarity)+'">'+
      '<div class="resource-icon">'+safe(m.icon)+'</div>'+
      '<div class="resource-title">'+safe(m.name)+'</div>'+
      '<div class="resource-stock">'+count+' шт.</div>'+
      '<small>'+safe(m.description)+'</small></div>';
  }).join('');
  const recipes = ui.config.recipes.map(r => {
    const done=owned.includes(r.code);
    const costs=r.ingredients.map(i=>{
      const m=ui.config.materials.find(x=>x.code===i.code);
      const have=resourceQuantity(p,i.code);
      return '<span class="material-cost '+(have>=i.quantity?'enough':'missing')+'">'+
        safe(m?.icon||'📦')+' '+have+'/'+i.quantity+'</span>';
    }).join('');
    const canMake = r.ingredients.every(i=>resourceQuantity(p,i.code)>=i.quantity);
    return '<div class="panel recipe-card"><div class="card-row">'+
      '<div class="card-icon craft-icon">'+safe(r.icon)+'</div>'+
      '<div class="card-text"><strong>'+safe(r.name)+'</strong><small>'+safe(r.description)+'</small></div>'+
      (done?'<span class="chip chip-green">✅ Готово</span>':'')+'</div>'+
      '<div class="recipe-costs">'+costs+'</div>'+
      '<button class="btn btn-wide '+(done?'btn-quiet':'btn-primary')+'" '+
      (!canMake||done?'disabled ':'')+'data-craft="'+safe(r.code)+'">'+
      (done?'В коллекции':canMake?'🛠 Создать':'🔒 Нужно больше материалов')+'</button></div>';
  }).join('');
  return '<div class="section-heading">🎒 Рюкзак '+safe(p.name)+'</div>'+
    '<p class="section-lead">Материалы и сувениры принадлежат двум хозяевам. '+
    'Находки появляются только после получения награды похода.</p>'+
    '<div class="panel inventory-total"><div><div class="eyebrow">СОБРАНО</div>'+
      '<div class="title">'+total+' материалов</div></div>'+
      '<span class="chip chip-purple">🏆 '+owned.length+' / '+ui.config.recipes.length+' сувениров</span></div>'+
    '<h2 class="h2 inventory-heading">📦 Материалы</h2>'+
    '<div class="resource-grid">'+rows+'</div>'+
    '<h2 class="h2 inventory-heading">🛠 Мастерская</h2>'+
    '<p class="section-lead">Создавайте коллекционные сувениры из найденных вещей. '+
      'Пока они не дают бонусов к XP или ирискам.</p>'+recipes+
    '<button class="btn btn-quiet btn-wide" data-click="business">🏪 Предприятия и товары →</button>'+
    '<button class="btn btn-quiet btn-wide" data-click="market">🛒 Торговая площадка →</button>'+
    '<button class="btn btn-quiet btn-wide" data-click="league">🏆 Лига питомцев →</button>'+
    '<button class="btn btn-quiet btn-wide" data-click="inventory-back">← Назад</button>';
}


function wardrobe(p) {
  return '<div class="section-heading">🎀 Гардероб</div>'+
    '<p class="section-lead">Коллекция аксессуаров '+safe(p.name)+'. Изменения общие для пары.</p>'+
    (p.items.length?p.items.map(code=>{
      const item=ui.config.shop.find(x=>x.code===code);if(!item) return "";
      return '<div class="panel"><div class="card-row"><div class="card-icon">🎀</div>'+
        '<div class="card-text"><strong>'+safe(item.name)+'</strong>'+
        '<small>'+(p.worn.includes(code)?'✅ Надето':'Сейчас не надето')+'</small></div>'+
        '<button class="btn" data-action="wear" data-code="'+safe(code)+'">'+
        (p.worn.includes(code)?'Снять':'Надеть')+'</button></div></div>';
    }).join(""):'<div class="panel">Пока нет вещей. Загляни в магазин!</div>')+
    '<button class="btn btn-wide btn-quiet" data-tab="home">← К питомцу</button>';
}
function render() {
  if (!ui.pet) return;
  const p=ui.pet;
  wallet.innerHTML='🍬 <b>'+p.balance+'</b>';
  nav.classList.remove("hidden");
  nav.innerHTML=tabs.map(([id,icon,label])=>'<button class="nav-btn '+
    (ui.tab===id || ['wardrobe','inventory','house','business','market','league'].includes(ui.tab)&&ui.returnTab===id?'active':'')+'" data-tab="'+id+'"><span>'+icon+'</span>'+label+'</button>').join("");
  app.innerHTML=renderPicker()+({
    home:home,trips:trips,tasks:tasks,shop:shop,growth:growth,wardrobe:wardrobe,inventory:inventory,
    house: p => window.PetsHouse.renderHouse(p,ui.config),
    business: p => window.PetsBusiness.renderBusiness(p,ui.config,serverNow()),
    market: p => window.PetsMarket.renderMarket(
      ui.market,p,ui.config,ui.marketSelection,serverNow()),
    league: p => window.PetsLeague.renderLeague(ui.league,p,serverNow())
  }[ui.tab]||home)(p);
}
function setPet(snapshot) {
  ui.pet = snapshot;
  syncClock(snapshot);
  ui.lastSync = Date.now();
  render();
}

// Prevent stale GET results replacing an action or a more recent pet selection.
// A background GET is never allowed to overwrite the result of a POST.
async function loadPet(id) {
  if (ui.busy) {showToast("Сначала заверши текущее действие"); return;}
  const serial = ++ui.syncSerial;
  ui.syncing = true;
  try {
    const pet = await api('/api/pet/'+id);
    if (serial !== ui.syncSerial) return;
    ui.selected = id;
    ui.picker = false;
    ui.market=null; ui.marketFetchedAt=0; ++ui.marketRequest;
    ui.league=null; ui.leagueFetchedAt=0; ++ui.leagueRequest;
    setPet(pet);
  } finally {ui.syncing = false;}
}
async function refreshPet({force=false,quiet=true} = {}) {
  if (!ui.selected || !TG?.initData || ui.busy || ui.syncing || document.hidden ||
      !modal.classList.contains("hidden")) return;
  if (!force && Date.now()-ui.lastSync < 55000) return;
  const id = ui.selected, serial = ++ui.syncSerial;
  ui.syncing = true;
  try {
    const pet = await api('/api/pet/'+id);
    if (serial === ui.syncSerial && ui.selected === id && !ui.busy) {
      setPet(pet);
    }
  } catch(e) {if(!quiet) showToast(e.message);}
  finally {ui.syncing = false;}
}
async function loadMarket({force=false,quiet=true}={}) {
  if(!ui.selected || !TG?.initData || document.hidden || (ui.marketLoading&&!force)) return;
  if(!force && ui.market && Date.now()-ui.marketFetchedAt<60000) return;
  const id=ui.selected, serial=++ui.marketRequest;
  ui.marketLoading=true;
  try {
    const result=await api('/api/market/'+id);
    if(serial===ui.marketRequest && ui.selected===id) {
      ui.market=result; ui.marketFetchedAt=Date.now();
      if(ui.tab==="market") render();
    }
  } catch(e) {
    if(!quiet) showToast(e.message);
  } finally {ui.marketLoading=false;}
}
async function loadLeague({force=false,quiet=true}={}) {
  if(!ui.selected || !TG?.initData || document.hidden || (ui.leagueLoading&&!force)) return;
  if(!force && ui.league && Date.now()-ui.leagueFetchedAt<60000) return;
  const id=ui.selected, serial=++ui.leagueRequest;
  ui.leagueLoading=true;
  try {
    const data=await api('/api/league/'+id);
    if(serial===ui.leagueRequest&&ui.selected===id) {
      ui.league=data;ui.leagueFetchedAt=Date.now();
      if(ui.tab==='league') render();
    }
  } catch(e) {
    if(!quiet) showToast(e.message);
  } finally {ui.leagueLoading=false;}
}
function syncBackButton() {
  try {
    if (ui.tab !== "home" || !modal.classList.contains("hidden")) TG?.BackButton?.show();
    else TG?.BackButton?.hide();
  } catch {}
}
function goToTab(tab) {
  if (["wardrobe","inventory","house","business","market","league"].includes(tab) && !["wardrobe","inventory","house","business","market","league"].includes(ui.tab)) {
    ui.returnTab = ui.tab;
  }
  ui.tab = tab;
  render();
  syncBackButton();
  window.scrollTo(0,0);
  if(tab==="market") loadMarket();
  if(tab==="league") loadLeague();
}
function closeModal() {
  modal.classList.add("hidden");
  syncBackButton();
}
function goBack() {
  if (!modal.classList.contains("hidden")) closeModal();
  else if (["wardrobe","inventory","house","business","market","league"].includes(ui.tab)) goToTab(ui.returnTab || "home");
  else if (ui.tab !== "home") goToTab("home");
}
try {TG?.BackButton?.onClick(goBack);} catch {}
document.addEventListener("keydown", e => {if(e.key === "Escape") goBack();});
function syncTimers() {
  if (!ui.pet || ui.busy) return;
  if(ui.tab==='league') loadLeague();
  if(ui.tab==="market") {
    for(const badge of app.querySelectorAll("[data-market-expiry]")) {
      badge.textContent="⏳ "+window.PetsMarket.duration(Number(badge.dataset.marketExpires)-serverNow());
    }
    loadMarket();
  }
  if(ui.tab==="business") {
    for(const label of app.querySelectorAll("[data-business-countdown]")) {
      const code=label.dataset.businessCountdown;
      const job=ui.pet.businesses?.units?.[code]?.job;
      if(!job) continue;
      const remaining=Math.max(0,job.finish_at-serverNow());
      label.textContent=remaining>0?"⏳ Осталось "+duration(remaining):"✅ Товары готовы!";
      const button=app.querySelector('[data-business-collect="'+code+'"]');
      if(button) {
        button.disabled=remaining>0;
        button.textContent=remaining>0?"⏳ Производится":"📦 Забрать готовую партию";
      }
    }
  }
  if (ui.tab === "trips" && ui.pet.trip) {
    const remaining = Math.max(0, ui.pet.trip.finish_at - serverNow());
    const label = app.querySelector("[data-trip-countdown]");
    if (label) {
      label.textContent = remaining ? "⏳ "+duration(remaining)+" до возвращения" : "🎁 Поход завершён";
      if (remaining===0 && app.querySelector('[data-click="refresh"]')) render();
    }
  }
  if (ui.tab === "home") {
    for (const btn of app.querySelectorAll('[data-action="care"]')) {
      const info = ui.pet.care_state?.[btn.dataset.code];
      if (!info) continue;
      const remaining = Math.max(0, (info.ready_at||0) - serverNow());
      const disabled = remaining>0 || Boolean(info.blocked_reason);
      btn.disabled = disabled;
      const hint = btn.querySelector("small");
      if (hint) hint.textContent = remaining>0 ? "через "+duration(remaining)
        : info.blocked_reason ? "Недоступно" : "✓ Готово";
      btn.title = info.blocked_reason || (remaining>0 ? "Повтори позже" : "Можно сейчас");
    }
  }
}
function confirmDialog(title, info, handler) {
  modal.innerHTML='<div class="modal"><h3>'+safe(title)+'</h3><p>'+safe(info)+'</p>'+
    '<div class="modal-buttons"><button class="btn btn-quiet" id="cancel">Отмена</button>'+
    '<button class="btn btn-cyan" id="confirm">Подтвердить</button></div></div>';
  modal.classList.remove("hidden");
  syncBackButton();
  modal.querySelector("#cancel").addEventListener("click", closeModal);
  modal.querySelector("#confirm").addEventListener("click", async ()=>{
    closeModal(); await handler();
  });
}
async function perform(kind, code="", extra={}) {
  if (ui.busy) return;
  const id = ui.selected;
  ui.busy = true;
  // Invalidate a pending background GET before making changes.
  ++ui.syncSerial;
  app.classList.add("is-busy");
  tap();
  try {
    const before = ui.pet;
    const result = await api('/api/action', {pet_id:id,kind,code,...extra});
    if (ui.selected === id) {
      setPet(result.pet);
      showActionEffect(kind, code, before, result.pet, result.message);
      if(kind.startsWith("market_")) {
        ui.market=null;ui.marketFetchedAt=0;
        await loadMarket({force:true,quiet:false});
      }
    }
    showToast(result.message);
    if (result.message.startsWith("✅") || result.message.startsWith("💕")) haptic("success");
  } catch(e) {
    haptic("error");
    showToast(e.message);
  } finally {
    ui.busy = false;
    app.classList.remove("is-busy");
  }
}
document.addEventListener("click", async e=>{
  const target=e.target.closest("button");if(!target) return;
  if (target.dataset.tab) {tap();goToTab(target.dataset.tab);return;}
  if (target.dataset.group) {ui.group=target.dataset.group;tap();render();return;}
  if (target.dataset.pet) {try{await loadPet(Number(target.dataset.pet));}catch(err){showToast(err.message);}return;}
  if (target.dataset.click==="picker") {ui.picker=!ui.picker;render();return;}
  if (target.dataset.click==="pet-greet") {petGreeting();return;}
  if (target.dataset.click==="wardrobe") {tap();goToTab("wardrobe");return;}
  if (target.dataset.click==="house") {tap();goToTab("house");return;}
  if (target.dataset.click==="house-back") {tap();goToTab(ui.returnTab||"home");return;}
  if (target.dataset.click==="business") {tap();goToTab("business");return;}
  if (target.dataset.click==="business-back") {tap();goToTab(ui.returnTab||"home");return;}
  if (target.dataset.click==="market") {tap();goToTab("market");return;}
  if (target.dataset.click==="market-back") {tap();goToTab(ui.returnTab||"home");return;}
  if (target.dataset.click==="league") {tap();goToTab("league");return;}
  if (target.dataset.click==="league-back") {tap();goToTab(ui.returnTab||"home");return;}
  if (target.dataset.leagueRefresh) {await loadLeague({force:true,quiet:false});return;}
  if (target.dataset.marketRefresh) {await loadMarket({force:true,quiet:false});return;}
  if (target.dataset.marketKind) {
    const kind=target.dataset.marketKind;
    if(kind==="material"||kind==="good") {
      ui.marketSelection.kind=kind;
      ui.marketSelection.code=kind==="material"?ui.config.materials[0]?.code:
        ui.config.businesses[0]?.code;
      render();
    }
    return;
  }
  if (target.dataset.marketCode) {
    const code=target.dataset.marketCode;
    if(window.PetsMarket.allItems(ui.config).some(i=>
       i.kind===ui.marketSelection.kind&&i.code===code)) {
      ui.marketSelection.code=code;render();
    }
    return;
  }
  if (target.dataset.marketQuantity) {
    const qty=Number(target.dataset.marketQuantity);
    if([1,2,3,5].includes(qty)) {ui.marketSelection.quantity=qty;render();}
    return;
  }
  if (target.dataset.marketPrice) {
    const price=Number(target.dataset.marketPrice);
    if([1,2,3,5,8,13,20].includes(price)) {ui.marketSelection.price=price;render();}
    return;
  }
  if (target.dataset.marketPost) {
    const selected=window.PetsMarket.allItems(ui.config).find(i=>
      i.kind===ui.marketSelection.kind&&i.code===ui.marketSelection.code);
    if(selected) {
      const qty=ui.marketSelection.quantity,price=ui.marketSelection.price;
      confirmDialog("Разместить предложение?",
        selected.icon+" "+selected.name+" ×"+qty+" за "+(qty*price)+" 🍬. "+
        "Предметы будут зарезервированы на 48 часов. Ириски получит автор объявления.",
        ()=>perform("market_create",selected.code,
          {item_kind:selected.kind,quantity:qty,unit_price:price}));
    }
    return;
  }
  if (target.dataset.marketCancel) {
    const id=Number(target.dataset.marketCancel);
    if(Number.isSafeInteger(id)&&id>0) {
      confirmDialog("Снять объявление?",
        "Все зарезервированные предметы вернутся на общий склад.",
        ()=>perform("market_cancel","",{listing_id:id}));
    }
    return;
  }
  if (target.dataset.marketBuy) {
    const id=Number(target.dataset.marketBuy);
    const listing=ui.market?.listings?.find(l=>l.id===id);
    if(listing&&Number.isSafeInteger(id)&&id>0) {
      const info=window.PetsMarket.allItems(ui.config).find(i=>
        i.kind===listing.kind&&i.code===listing.code);
      confirmDialog("Купить предложение?",
        (info?.icon||"📦")+" "+(info?.name||"Предмет")+" ×"+listing.quantity+
        " за "+listing.total_price+" 🍬. Продавцу начислим ириски, "+
        "предметы попадут в рюкзак общего питомца.",
        ()=>perform("market_buy","",{listing_id:id}));
    }
    return;
  }
  if (target.dataset.businessBuild) {
    const unit=ui.config.businesses.find(b=>b.code===target.dataset.businessBuild);
    if(unit) {
      const level=Number(ui.pet.businesses?.units?.[unit.code]?.level||0);
      const tier=unit.tiers[level];
      if(tier) {
        const cost=tier.costs.map(item=>{
          const m=ui.config.materials.find(x=>x.code===item.code);
          return (m?.icon||"📦")+" "+item.quantity;
        }).join(", ");
        confirmDialog(level===0?"Построить "+unit.name+"?":"Улучшить "+unit.name+"?",
          "Будут списаны общие ресурсы: "+cost+". Ириски не тратятся.",
          ()=>perform("business_build",unit.code,{expected_level:level}));
      }
    }
    return;
  }
  if (target.dataset.businessStart) {
    const unit=ui.config.businesses.find(b=>b.code===target.dataset.businessStart);
    if(unit) {
      const cost=unit.inputs.map(item=>{
        const m=ui.config.materials.find(x=>x.code===item.code);
        return (m?.icon||"📦")+" "+item.quantity;
      }).join(", ");
      confirmDialog("Запустить производство?",
        "Ресурсы с общего склада: "+cost+". Товары можно забрать через "+
        duration(unit.production_seconds)+".",
        ()=>perform("business_start",unit.code));
    }
    return;
  }
  if (target.dataset.businessCollect) {
    const code=target.dataset.businessCollect;
    const job=Number(target.dataset.jobId);
    if(Number.isSafeInteger(job)&&job>0) await perform("business_collect",code,{job_id:job});
    return;
  }
  if (target.dataset.businessSell) {
    const unit=ui.config.businesses.find(b=>b.code===target.dataset.businessSell);
    if(unit) confirmDialog("Продать товары?",
      "Сервер продаст максимум из доступных товаров в пределах дневного лимита. "+
      "Ириски начисляются лично тому, кто продаёт, без вывода из бота.",
      ()=>perform("business_sell",unit.code));
    return;
  }
  if (target.dataset.buildRoom) {
    const room=ui.config.house_rooms.find(r=>r.code===target.dataset.buildRoom);
    if (room) {
      const tier=room.tiers[Number(ui.pet.house_rooms?.[room.code]||0)];
      if (tier) {
        const costText=tier.costs.map(c=>{
          const material=ui.config.materials.find(m=>m.code===c.code);
          return (material?.icon||'📦')+' '+c.quantity;
        }).join(', ');
        confirmDialog('Улучшить '+room.name+'?',
          'Общие материалы будут потрачены: '+costText+'. Улучшение для двоих.',
          ()=>perform("build_room",room.code,
            {expected_level:Number(ui.pet.house_rooms?.[room.code]||0)}));
      }
    }
    return;
  }
  if (target.dataset.click==="inventory") {tap();goToTab("inventory");return;}
  if (target.dataset.click==="inventory-back") {tap();goToTab(ui.returnTab||"home");return;}
  if (target.dataset.craft) {
    const recipe=ui.config.recipes.find(x=>x.code===target.dataset.craft);
    if (recipe) {
      const needed=recipe.ingredients.map(i=>{
        const item=ui.config.materials.find(m=>m.code===i.code);
        return (item?.icon||'📦')+' '+i.quantity;
      }).join(", ");
      confirmDialog('Создать '+recipe.name+'?',
        'Будут потрачены общие материалы: '+needed+'. Сувенир один на двоих.',
        ()=>perform("craft",recipe.code));
    }
    return;
  }
  if (target.dataset.click==="refresh") {await refreshPet({force:true,quiet:false});return;}
  if(target.dataset.buy) {
    const item=ui.config.shop.find(i=>i.code===target.dataset.buy);
    if(item)confirmDialog('Купить '+item.name+'?','С баланса спишется '+item.price+' 🍬. '+item.info,
      ()=>perform("buy",item.code));return;
  }
  if(target.dataset.profession) {
    const job=ui.config.professions.find(j=>j.code===target.dataset.profession);
    if(job)confirmDialog('Профессия '+job.name,'Выбор для двоих. Изменить пока нельзя.',
      ()=>perform("profession",job.code));return;
  }
  if(target.dataset.action) {
    const kind=target.dataset.action,code=target.dataset.code||"";
    const handler=()=>perform(kind,code,
      kind==="claim"?{trip_id:Number(target.dataset.trip)}:{});
    if(kind==="start")confirmDialog("Отправить питомца в поход?",
      "Поход потратит сытость и энергию. Награда ждёт возвращения.",handler);
    else handler();
  }
});
async function boot() {
  try {
    TG?.ready();
    TG?.expand();
    syncSafeArea();
    if(!TG?.initData) throw new Error("Открой Mini App через кнопку бота в Telegram.");
    ui.config=await api("/api/bootstrap");
    ui.pets=ui.config.pets;
    if(!ui.pets.length) {
      app.innerHTML='<div class="error-page"><div class="icon">🥚</div><h2>Питомцев пока нет</h2>'+
        '<p class="muted">Ответь другу словом «питомец» в групповом чате, чтобы завести вашего первого питомца.</p></div>';
      return;
    }
    const started=(TG.initDataUnsafe?.start_param||"").match(/^p_(\d+)$/);
    let id=started?Number(started[1]):ui.pets[0].id;
    if(!ui.pets.some(p=>p.id===id)) id=ui.pets[0].id;
    await loadPet(id);
  } catch(e) {
    app.innerHTML='<div class="error-page"><div class="icon">🔒</div><h2>Не удалось открыть игру</h2>'+
      '<p class="muted">'+safe(e.message)+'</p>'+
      '<button class="btn btn-wide" id="retry">Повторить</button></div>';
    document.querySelector("#retry")?.addEventListener("click", boot);
  }
}
// Refresh shared pet state when returning from another Telegram tab / chat.
try { TG?.onEvent?.("activated", () => refreshPet({force:true})); } catch {}
document.addEventListener("visibilitychange", () => {
  if(!document.hidden) {
    refreshPet();
    if(ui.tab==="market") loadMarket({force:true});
    if(ui.tab==="league") loadLeague({force:true});
  }
});
window.addEventListener("online", () => refreshPet({force:true,quiet:false}));
window.addEventListener("offline", () => showToast("Нет подключения к интернету"));
setInterval(() => {
  syncTimers();
  refreshPet();
}, 15000);
boot();
