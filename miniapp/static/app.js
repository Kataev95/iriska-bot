const TG = window.Telegram?.WebApp;
const app = document.getElementById("app");
const nav = document.getElementById("bottom-nav");
const wallet = document.getElementById("wallet");
const modal = document.getElementById("modal");
const toast = document.getElementById("toast");

const ui = { config: null, pet: null, pets: [], selected: null, tab: "home",
             group: "food", busy: false, toastTimer: 0, picker: false };
const tabs = [
  ["home","🏠","Главная"], ["trips","🧭","Походы"],
  ["tasks","📋","Задания"], ["shop","🛍","Магазин"],
  ["growth","⭐","Развитие"],
];
const emoji = {food:"🍪",wear:"🎀",freeze:"🧊",house:"🏠"};
const safe = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
}[c]));
const value = n => Math.max(0, Math.round(Number(n || 0)));
const stat = (icon,title,amount) => '<div class="stat"><span>'+icon+' '+title+'</span>'+
  '<strong>'+value(amount)+'%</strong><div class="progress"><i style="width:'+Math.min(value(amount),100)+'%"></i></div></div>';
function tap() { try { TG?.HapticFeedback?.impactOccurred("light"); } catch {} }
function showToast(text) {
  toast.textContent = String(text || "");
  toast.classList.add("visible");
  clearTimeout(ui.toastTimer);
  ui.toastTimer = setTimeout(() => toast.classList.remove("visible"), 3500);
}
async function api(path, payload) {
  if (!TG?.initData) throw new Error("Открой игру через кнопку в Telegram.");
  const result = await fetch(path, {
    method: payload ? "POST" : "GET",
    headers: {"X-Telegram-Init-Data": TG.initData, ...(payload ? {"Content-Type":"application/json"} : {})},
    body: payload ? JSON.stringify(payload) : undefined,
    cache:"no-store",
  });
  let data;
  try { data = await result.json(); } catch { throw new Error("Ошибка сервера"); }
  if (!result.ok) throw new Error(data.error || data.message || "Не удалось выполнить запрос");
  return data;
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
  const prior = 25*p.level*(p.level-1);
  const progress = p.next_xp == null ? 100 :
    Math.min(100,Math.max(0,Math.round((p.xp-prior)/(target-prior)*100)));
  return '<div class="hero"><div class="hero-overlay">✨ Уровень '+p.level+' / 20</div>'+
    '<img src="'+safe(p.image)+'" alt="Питомец '+safe(p.name)+'"></div>'+
    '<div class="panel"><div class="eyebrow">МОЙ ПИТОМЕЦ</div>'+
    '<div class="row"><div><div class="hero-title">'+safe(p.name)+'</div>'+
    '<div class="subtitle">🤝 Вместе с '+safe(p.partner)+'</div></div>'+
    '<div class="chip chip-purple">🔥 '+p.streak+' дн.</div></div>'+
    '<div class="stat-grid">'+stat("🍖","Сытость",p.food)+stat("😊","Настроение",p.joy)+stat("⚡","Энергия",p.energy)+'</div>'+
    '<div class="xp-line"><b>⭐ '+p.xp+' XP</b><div class="progress"><i style="width:'+progress+'%"></i></div>'+
    '<small>'+(p.next_xp == null ? "MAX":p.next_xp+' XP')+'</small></div></div>';
}
function home(p) {
  const actions = ui.config.actions.map(action =>
    '<button class="btn" data-action="care" data-code="'+safe(action.code)+'">'+safe(action.name)+'</button>'
  ).join("");
  return hero(p)+'<div class="panel"><div class="eyebrow">ЕЖЕДНЕВНЫЙ УХОД</div>'+
    '<h2 class="h2">Позаботьтесь о '+safe(p.name)+'</h2>'+
    '<div class="buttons">'+actions+'</div></div>'+
    '<div class="notice">'+(p.today_fed
      ? "✅ Сегодня ваша пара уже поддержала серию!"
      : "🕊 Напишите друг другу в чате, чтобы поддержать серию общения.")+'</div>'+
    '<div class="panel"><div class="row"><strong>🎀 Гардероб</strong>'+
    '<button class="btn btn-quiet" data-click="wardrobe">Открыть →</button></div>'+
    '<p class="subtitle">'+p.items.length+' вещей · '+p.worn.length+' надето'+
    (p.house ? ' · 🏠 Домик' : '')+'</p></div>';
}
function trips(p) {
  if (p.trip) {
    const r = ui.config.routes.find(x => x.code === p.trip.route);
    const left = Math.max(0,p.trip.finish_at-Date.now()/1000);
    const title = r ? r.name : "Экспедиция";
    const timeText = left ? Math.ceil(left/60)+' мин до возвращения' : 'Готово к получению';
    return '<div class="section-heading">🧭 Приключения</div><p class="section-lead">Каждый поход приносит опыт и ириски.</p>'+
      '<div class="panel"><div class="eyebrow">ТЕКУЩИЙ ПОХОД</div>'+
      '<div class="card-row"><div class="card-icon">🗺️</div><div class="card-text"><strong>'+safe(title)+'</strong>'+
      '<small>⏳ '+timeText+'</small></div></div>'+
      '<div class="route-info"><span class="chip">⭐ '+p.trip.xp+' XP</span><span class="chip">🍬 '+p.trip.coins+'</span></div>'+
      (left ? '<button class="btn btn-wide" data-click="refresh">🔄 Обновить</button>' :
        '<button class="btn btn-primary btn-wide" data-action="claim" data-trip="'+p.trip.id+'">🎁 Забрать награду</button>')+
      '</div>';
  }
  return '<div class="section-heading">🧭 Приключения</div>'+
    '<p class="section-lead">Выбирай маршрут, собирай награды и повышай уровень питомца.</p>'+
    ui.config.routes.map(r => {
      const bonusXp = p.profession === "explorer" ? Math.floor(r.xp*1.2) : r.xp;
      const energy = r.energy-(p.profession==="artisan" ? 3:0);
      const coins = r.coins+(p.profession==="merchant" ? 1:0);
      const time = r.seconds<3600 ? r.seconds/60+' мин' : r.seconds/3600+' ч';
      const locked = p.food<20 || p.energy<energy;
      return '<div class="panel route"><div class="card-row"><div class="card-icon">'+
        safe(r.name.split(" ")[0])+'</div><div class="card-text"><strong>'+
        safe(r.name)+'</strong><small>Приключение · '+time+'</small></div></div>'+
        '<div class="route-info"><span class="chip">⚡ '+energy+'</span>'+
        '<span class="chip">⭐ '+bonusXp+' XP</span><span class="chip">🍬 '+coins+'</span></div>'+
        '<button class="btn btn-primary" data-action="start" data-code="'+safe(r.code)+'" '+
        (locked?'disabled':'')+'>'+(locked?'🔒 Мало энергии или еды':'🧭 Отправиться')+'</button></div>';
    }).join("");
}
function tasks(p) {
  const claimable = p.daily.some(x=>x.done&&!x.claimed);
  return '<div class="section-heading">📋 Ежедневные задания</div>'+
    '<p class="section-lead">Выполняйте действия вдвоём. Опыт общий у питомца.</p>'+
    p.daily.map(t=>'<div class="panel"><div class="card-row"><div class="card-icon">'+
      (t.claimed?'🎁':t.done?'✅':'▫️')+'</div><div class="card-text"><strong>'+
      safe(t.name)+'</strong><small>'+ (t.claimed?'Награда получена':t.done?'Готово к получению':'Ещё не выполнено')+
      '</small></div><div class="card-end">+'+t.xp+' XP</div></div></div>').join("")+
    (claimable?'<button class="btn btn-cyan btn-wide" data-action="daily">🎁 Забрать весь опыт</button>':
    '<div class="notice">Новые задания будут доступны завтра.</div>');
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
    )+'</div><button class="btn btn-wide" data-click="wardrobe">🎀 Гардероб →</button>';
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
    (ui.tab===id?'active':'')+'" data-tab="'+id+'"><span>'+icon+'</span>'+label+'</button>').join("");
  app.innerHTML=renderPicker()+({
    home:home,trips:trips,tasks:tasks,shop:shop,growth:growth,wardrobe:wardrobe
  }[ui.tab]||home)(p);
}
async function loadPet(id) {
  ui.selected=id;
  ui.pet=await api('/api/pet/'+id);
  ui.picker=false;
  render();
}
function confirmDialog(title, info, handler) {
  modal.innerHTML='<div class="modal"><h3>'+safe(title)+'</h3><p>'+safe(info)+'</p>'+
    '<div class="modal-buttons"><button class="btn btn-quiet" id="cancel">Отмена</button>'+
    '<button class="btn btn-cyan" id="confirm">Подтвердить</button></div></div>';
  modal.classList.remove("hidden");
  modal.querySelector("#cancel").addEventListener("click", ()=>modal.classList.add("hidden"));
  modal.querySelector("#confirm").addEventListener("click", async ()=>{
    modal.classList.add("hidden"); await handler();
  });
}
async function perform(kind, code="", extra={}) {
  if (ui.busy) return;
  ui.busy=true;tap();
  try {
    const result=await api('/api/action',{pet_id:ui.selected,kind,code,...extra});
    ui.pet=result.pet;render();showToast(result.message);
  } catch(e) { showToast(e.message); }
  finally {ui.busy=false;}
}
document.addEventListener("click", async e=>{
  const target=e.target.closest("button");if(!target) return;
  if (target.dataset.tab) {ui.tab=target.dataset.tab;tap();render();window.scrollTo(0,0);return;}
  if (target.dataset.group) {ui.group=target.dataset.group;tap();render();return;}
  if (target.dataset.pet) {try{await loadPet(Number(target.dataset.pet));}catch(err){showToast(err.message);}return;}
  if (target.dataset.click==="picker") {ui.picker=!ui.picker;render();return;}
  if (target.dataset.click==="wardrobe") {ui.tab="wardrobe";render();return;}
  if (target.dataset.click==="refresh") {try{await loadPet(ui.selected);}catch(err){showToast(err.message);}return;}
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
setInterval(()=>{if(ui.tab==="trips"&&ui.pet?.trip&&!ui.busy)render();},30000);
boot();
