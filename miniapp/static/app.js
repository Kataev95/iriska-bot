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
             group: "food", businessCode: "florist", mapLocation: null, seedCode: "wheat", busy: false, syncing: false, lastSync: 0,
             syncSerial: 0, clockDelta: 0, toastTimer: 0, picker: false,
             studioMode:"frames",
             studio:null, studioLoading:false, studioFetchedAt:0, studioRequest:0,
             season:null, seasonLoading:false,
             seasonFetchedAt:0, seasonRequest:0,
             league: null, leagueLoading:false, leagueFetchedAt:0, leagueRequest:0,
             market: null, marketLoading:false, marketFetchedAt:0,
             marketRequest:0, marketSelection:{kind:"material",code:"leaf",quantity:1,price:2} };
const navigation = window.PetsNavigation.create();
const tabs = window.PetsNavigation.tabs;
const design = window.PetsWorldUI;
const worldMap = window.PetsMap;
const country = window.PetsCountryside;
const arcade = window.PetsArcade;
const progression = window.PetsProgression;
const icon = design.icon;
const life = window.PetsLife;
const reactionGate = life.createGate();
let reactionTimer = null;
let inactive = false;
const motionHidden = () => document.hidden || inactive;
const emoji = {food:"🍪",wear:"🎀",freeze:"🧊",house:"🏠"};
const resourceQuantity = (p, code) => Math.max(0,Number(p.materials?.[code] || 0));
const safe = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
}[c]));
const value = n => Math.max(0, Math.round(Number(n || 0)));
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
  if (motionHidden() || !effects) return;
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
function clearPetReaction() {
  clearTimeout(reactionTimer);
  reactionTimer=null;
  reactionGate.reset();
  const scene=app.querySelector(".pet-scene");
  if(!scene)return;
  delete scene.dataset.reaction;
  scene.querySelector(".pet-expression")?.remove();
  const layer=scene.querySelector(".pet-reaction-layer");
  if(layer)layer.replaceChildren();
  const speech=scene.querySelector(".pet-speech");
  if(speech)speech.textContent="";
}
function reactPet(p, kind, confirmed=false) {
  if(!p||motionHidden()||ui.tab!=="home"||ui.pet?.id!==p.id)return false;
  const scene=app.querySelector(".pet-scene");
  if(!scene)return false;
  const reaction=life.reaction(p,kind,confirmed);
  if(!reaction)return false;
  const token=reactionGate.begin(p.id,Date.now(),reaction.ms,confirmed);
  if(token===null)return false;
  clearTimeout(reactionTimer);
  scene.querySelector(".pet-expression")?.remove();
  scene.dataset.reaction=reaction.motion;
  const layer=scene.querySelector(".pet-reaction-layer");
  const speech=scene.querySelector(".pet-speech");
  if(speech)speech.textContent=reaction.text;
  if(layer){
    const prop=document.createElement("span");
    prop.className="pet-reaction-prop";
    prop.textContent=reaction.symbol;
    layer.replaceChildren(prop);
  }
  const path=life.expressionPath(p,reaction.expression);
  const reduced=window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if(path&&path!==p.image&&!reduced){
    const img=new Image();
    img.className="pet-expression";
    img.alt="";
    img.setAttribute("aria-hidden","true");
    img.onload=()=>{
      if(reactionGate.current(token,p.id)&&!motionHidden()&&scene.isConnected){
        scene.querySelector(".pet-motion")?.append(img);
      }
    };
    // Broken/missing assets leave the authoritative portrait untouched.
    img.src=path;
  }
  reactionTimer=setTimeout(()=>{
    if(reactionGate.finish(token))clearPetReaction();
  },reaction.ms);
  return true;
}
function petGreeting() {
  if(!ui.pet||ui.busy)return;
  if(reactPet(ui.pet,"pat"))haptic("soft");
}
function showActionEffect(kind, code, before, after, message) {
  if (!before || !after || before.id !== after.id) return;
  const careConfirmed=kind==="care"&&(message.startsWith("✅")||message.startsWith("💕"));
  const localCare=careConfirmed&&reactPet(after,code,true);
  if (after.level > before.level) {
    playEffect("🎉 Новый уровень! "+after.level+" / "+(after.max_level||ui.config.progression?.max_level||100), "level");
    haptic("success");
    return;
  }
  // Announce only changes confirmed in the authoritative DB snapshot.
  if (careConfirmed) {
    if(!localCare)playEffect((careSymbols[code]||"💗")+" Уход выполнен", "care");
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
  if (["skill_learn","specialization","level_claim"].includes(kind)&&message.startsWith("✅")) {
    playEffect(message,kind==="level_claim"?"reward":"level");return;
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
  if (["farm_harvest","fish_reel","country_upgrade","country_sell"].includes(kind)&&message.startsWith("✅")) {
    playEffect(message,kind==="country_upgrade"?"level":"reward");return;
  }
  if (kind==="tycoon_hire" && (after.businesses?.units?.[code]?.workers||0)>(before.businesses?.units?.[code]?.workers||0)) {
    playEffect("🧑‍🔧 Новый работник!", "level");return;
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
  if ((kind==="business_sell" || kind==="tycoon_order") && after.balance>before.balance) {
    playEffect("🍬 +"+(after.balance-before.balance)+" ирисок!", "reward");
    return;
  }
  if (kind==="studio_equip" && message.startsWith("✅")) {
    playEffect("🎨 Новый образ питомца!", "care");return;
  }
  if (kind==="season_claim" && message.startsWith("✅")) {
    playEffect("🎖 Новый титул в коллекции!", "reward");return;
  }
  if (kind==="season_equip" && message.startsWith("✅")) {
    playEffect("👑 Титул обновлён!", "care");return;
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
  const frameCodes=["classic","grove","neon","atelier","champion","cosmos","trailkeeper"];
  const stickerIcons={paw:"🐾",compass:"🧭",heart:"💞",medal:"🎯",crystal:"💎",crown:"👑"};
  const frame=frameCodes.includes(p.style?.frame)?p.style.frame:"classic";
  const sticker=stickerIcons[p.style?.sticker]||"🐾";
  const phase=design.phase(p.world_hour);
  const pose=life.pose(p), state=life.state(p);
  return '<section class="hero pet-scene phase-'+phase+' pose-'+pose+'" data-pet-id="'+p.id+'" aria-label="Долина питомца">'+design.landscape()+
    '<div class="scene-top"><span class="scene-badge">'+icon(phase==="night"?"sleep":"sun")+
    safe(design.phaseLabel(p.world_hour))+'</span><span class="scene-badge scene-level">'+icon("star")+
    'Ур. '+p.level+' / '+(p.max_level||100)+'</span></div>'+
    '<div class="pet-portrait studio-frame studio-frame-'+frame+'">'+
    '<div class="pet-motion"><img class="pet-art" src="'+safe(p.image)+'" alt="Питомец '+safe(p.name)+'" decoding="async" fetchpriority="high"></div>'+
    '<span class="studio-hero-sticker" aria-hidden="true">'+sticker+'</span>'+
    '<button type="button" class="pet-touch" data-click="pet-greet" aria-label="Поздороваться с питомцем — визуальный эффект, без затрат ирисок">'+
    '<span class="pet-hint" aria-hidden="true">'+safe(state.hint)+' ✨</span></button></div>'+
    '<div class="pet-idle-prop" aria-hidden="true">'+state.symbol+'</div>'+
    '<div class="pet-reaction-layer" aria-hidden="true"></div>'+
    '<div class="pet-speech" role="status" aria-live="polite"></div>'+
    '<div class="scene-caption">'+icon("paw")+' '+safe(state.label)+'</div></section>';
}
function petDashboard(p){
  const target=p.next_xp??p.xp;
  const prior=Number.isFinite(p.level_start_xp)?p.level_start_xp:25*p.level*(p.level-1);
  const progress=p.next_xp==null?100:design.percent((p.xp-prior)/Math.max(1,target-prior)*100);
  return '<section class="panel pet-dashboard"><div class="pet-identity"><div class="pet-identity-main">'+
    '<div class="hero-title">'+safe(p.name)+'</div><div class="pet-partner">'+icon("heart")+
    'Вместе с '+safe(p.partner)+'</div>'+
    (p.progression?.evolution?'<div class="chip chip-green">'+safe(p.progression.evolution.icon+" "+p.progression.evolution.name)+'</div>':'')+
    (p.season_title?'<div class="chip chip-purple season-name-tag">'+safe(p.season_title)+'</div>':'')+
    '</div><div class="streak-pill" aria-label="Серия '+p.streak+' дней">'+icon("flame")+p.streak+' дн.</div></div>'+
    '<div class="need-grid">'+design.needs(p.food,"Сытость","feed")+design.needs(p.joy,"Настроение","heart")+
    design.needs(p.energy,"Энергия","bolt")+'</div>'+
    '<div class="xp-hud"><b>'+icon("star")+p.xp+' XP</b><small>'+
    (p.next_xp==null?"Макс. уровень":"До следующего: "+Math.max(0,target-p.xp)+' XP')+'</small>'+
    '<progress class="game-progress" max="100" value="'+progress+'" aria-label="Опыт текущего уровня">'+progress+'%</progress></div>'+
    '<p class="mood-caption">'+safe(life.pose(p)==="egg"?life.state(p).idle:p.mood||"У питомца всё хорошо")+'</p></section>';
}
function weeklyChapter(p) {
  const chapter = p.chapter;
  if (!chapter) return "";
  const trip = p.trip;
  let action;
  if (chapter.stage === 3) {
    action = '<p>✅ Тропа открыта.'+(chapter.shared?" В альбоме есть отметка о совместном походе.":"")+'</p>'+
      '<button class="btn btn-quiet" data-click="studio">Открыть студию</button>';
  } else if (chapter.stage === 2) {
    action = '<button class="btn btn-primary" data-action="chapter_finish" data-code="'+safe(chapter.code)+'">Установить указатель</button>';
  } else if (chapter.stage === 1) {
    const material = ui.config.materials.find(item=>item.code===chapter.ingredient);
    const stocked = resourceQuantity(p, chapter.ingredient)>0;
    action = '<p class="subtitle">На указатель уйдёт 1 материал: '+safe(material?.name||"")+'.</p>'+
      (stocked
        ? '<button class="btn btn-primary" data-action="chapter_craft" data-code="'+safe(chapter.code)+'">Собрать в мастерской</button>'
        : '<p class="subtitle">Выберите поход за нужным материалом ниже.</p>');
  } else if (trip && trip.id === chapter.trip_id) {
    const left = Math.max(0,trip.finish_at-serverNow());
    action = left
      ? '<p class="subtitle" data-chapter-countdown>⏳ '+duration(left)+' до возвращения</p>'
      : '<button class="btn btn-primary" data-action="claim" data-trip="'+trip.id+'">Забрать добычу</button>';
  } else if (trip) {
    action = '<p class="subtitle">Сначала завершите текущий поход ниже.</p>';
  } else if (!p.best_streak) {
    action = '<p class="subtitle">Приключение откроется, когда питомец вылупится.</p>';
  } else {
    const routes = {park:"Короткий за древесиной",ruins:"Дальний за кристаллом"};
    action = '<div class="chapter-routes">'+ui.config.routes.filter(route=>route.code in routes).map(route=>{
      const energy = Math.max(1,route.energy-(p.progression?.effects.trip_energy??(p.profession==="artisan"?3:0)));
      const locked = p.trips_remaining===0 || p.food<20 || p.energy<energy;
      return '<button class="btn btn-primary" data-action="chapter_start" data-code="'+safe(route.code)+
        '" data-chapter="'+safe(chapter.code)+'" '+(locked?'disabled':'')+'>'+
        safe(routes[route.code])+' · '+duration(route.seconds)+'</button>';
    }).join("")+'</div>'+
      '<p class="subtitle">'+(p.trips_remaining===0?"Лимит походов на сегодня исчерпан.":"Для выхода нужно 20 сытости и достаточно энергии с учётом навыков.")+'</p>';
  }
  return '<section class="panel weekly-chapter"><div class="eyebrow">ПРИКЛЮЧЕНИЕ НЕДЕЛИ</div>'+
    '<h2 class="h2">🍂 '+safe(chapter.title)+'</h2>'+
    '<p class="subtitle">Помогите питомцу открыть старую тропу. Награда — рамка «Хранитель тропы» и запись в альбоме.</p>'+
    '<ol class="chapter-steps" aria-label="Ход приключения">'+
    ["Добыть материал","Собрать указатель","Открыть тропу"].map((label,index)=>
      '<li '+(index===chapter.stage?'aria-current="step"':'')+'>'+(index<chapter.stage?'✅ ':'')+label+'</li>').join("")+
    '</ol>'+action+'</section>';
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
      '<span class="care-emblem" aria-hidden="true">'+icon(action.code)+'</span>'+
    '<span class="care-name">'+safe(({feed:"Покормить",play:"Поиграть",sleep:"Уложить"})[action.code]||action.name)+'</span>'+
    '<small>'+(disabled?safe(hint):'Готово')+'</small></button>';
  }).join("");
  return petDashboard(p)+'<section class="care-dock"><div class="care-dock-title"><strong>Ежедневный уход</strong><small>Забота для двоих</small></div>'+
    '<div class="buttons">'+actions+'</div></section>'+
    '<div class="pair-status'+(p.today_fed?' pair-complete':'')+'">'+icon(p.today_fed?"check":"heart")+
    '<span>'+(p.today_fed?"Сегодня вы уже поддержали серию!":
      p.partner_wrote_today?"Партнёр уже написал сегодня. Ответь в чате, чтобы поддержать серию.":
      p.user_cared?"Ты уже написал сегодня. Ждём ответа партнёра.":
      "Напишите друг другу в чате, чтобы поддержать серию.")+'</span></div>'+
    '<div class="home-shortcuts">'+
    hubCard("tasks","📋","Задания дня",p.daily.filter(t=>t.done&&!t.claimed).length
      ? "Есть награды к получению" : "Три совместных задания")+
    hubCard("profile","✨","Профиль","Развитие, внешний вид и альбом")+
    ((p.trip || (p.chapter && p.chapter.stage<3))
      ? hubCard("trips","🧭","Приключения",p.trip
        ? (p.trip.finish_at<=serverNow()?"Награда ждёт вас":"Питомец в пути")
        : "Продолжить лесную тропу") : "")+
    '</div>';
}
function hubCard(route, icon, title, detail) {
  return '<button type="button" class="hub-card" data-tab="'+safe(route)+'">'+
    '<span class="hub-icon" aria-hidden="true">'+design.routeIcon(route)+'</span>'+
    '<span class="hub-copy"><strong>'+safe(title)+'</strong><small>'+safe(detail)+'</small></span>'+
    '<span class="hub-arrow" aria-hidden="true">'+design.icon("arrow")+'</span></button>';
}
function hub(title, description, cards) {
  return '<div class="section-heading">'+safe(title)+'</div><p class="section-lead">'+
    safe(description)+'</p><div class="hub-list">'+cards.join("")+'</div>';
}
function profile(p) {
  return hub("✨ Профиль питомца","Всё о вашем общем питомце",[
    hubCard("growth","⭐","Развитие","Уровень "+p.level+" · профессия и серия"),
    hubCard("wardrobe","🎀","Гардероб","Аксессуары · "+p.items.length+" вещей"),
    hubCard("studio","🎨","Студия и альбом","Рамки, значки и история")
  ]);
}
function world(p) {
  return worldMap.renderMap(p,serverNow(),ui.config,design.phase(p.world_hour));
}
function syncWorldMap() {
  if(ui.tab!=="world"||!ui.pet)return;
  const p=ui.pet, now=serverNow();
  if(!worldMap.valid(ui.mapLocation))ui.mapLocation=worldMap.recommended(p,now,ui.config);
  const scene=app.querySelector(".world-map");
  if(!scene)return;
  const phase="map-phase-"+design.phase(p.world_hour);
  if(!scene.classList.contains(phase)){
    scene.classList.remove("map-phase-morning","map-phase-day","map-phase-evening","map-phase-night");
    scene.classList.add(phase);
  }
  for(const button of scene.querySelectorAll("[data-map-location]")){
    const place=worldMap.get(button.dataset.mapLocation), status=worldMap.status(place,p,now,ui.config);
    const pressed=String(place.id===ui.mapLocation);
    if(button.getAttribute("aria-pressed")!==pressed)button.setAttribute("aria-pressed",pressed);
    const tone="tone-"+status.tone;
    if(!button.classList.contains(tone)){
      button.classList.remove("tone-open","tone-active","tone-reward","tone-future");
      button.classList.add(tone);
    }
    const label=place.name+" — "+status.text;
    if(button.getAttribute("aria-label")!==label)button.setAttribute("aria-label",label);
    const badge=button.querySelector("[data-map-status]");
    if(badge&&badge.textContent!==status.text)badge.textContent=status.text;
  }
  patchHome("[data-world-detail]",worldMap.renderDetail(ui.mapLocation,p,now,ui.config));
}
function selectWorldLocation(id) {
  if(ui.busy||ui.tab!=="world"||!worldMap.valid(id))return;
  ui.mapLocation=id;
  syncWorldMap();
  tap();
  // On small screens the detail may be just below the fold. Keep selection
  // visible and bring only the description/action into view on explicit taps.
  app.querySelector("[data-world-detail]")?.scrollIntoView?.({block:"nearest",behavior:"auto"});
}
function openWorldLocation(id) {
  if(ui.busy||ui.tab!=="world"||!worldMap.valid(id)||id!==ui.mapLocation)return;
  const place=worldMap.get(id);
  if(!place.route)return;
  const status=worldMap.status(place,ui.pet,serverNow(),ui.config);
  if(place.route==="business"&&status.businessCode&&ui.config.businesses.some(b=>b.code===status.businessCode)){
    ui.businessCode=status.businessCode;
  }
  tap();
  goToTab(place.route);
}
function games(p) {
  return hub("🎮 Игры","Развивайтесь вместе и выполняйте задания",[
    hubCard("arcade","🎮","PETS Arcade","Четыре мини-игры, рекорды и опыт"),
    hubCard("business","🏪","PETS Tycoon","Ручная работа, команда, склад и заказы"),
    hubCard("tasks","📋","Задания дня",p.daily.filter(t=>t.claimed).length+" / "+p.daily.length+" наград получено")
  ]);
}
function trade(p) {
  return hub("🛒 Торговля","Покупки и обмен в вашем чате",[
    hubCard("shop","🛍","Магазин","Еда, аксессуары и улучшения"),
    hubCard("market","🏪","Рынок","Предложения игроков и ваши объявления")
  ]);
}
function club(p) {
  return hub("🏆 Клуб","Коллекции и соревнования питомцев",[
    hubCard("league","🏅","Лига","Достижения и рейтинг недели"),
    hubCard("season","🎟","Сезон","Испытания, титулы и рейтинг сезона")
  ]);
}
function quickAccess() {
  return '<div class="quick-access"><button type="button" class="btn btn-quiet" data-tab="inventory">'+icon("bag")+' Рюкзак</button></div>';
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
  return weeklyChapter(p)+tripRoutes(p);
}
function tripRoutes(p) {
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
      (p.chapter?.trip_id===p.trip.id
        ? '<p class="subtitle">Награда этого похода доступна в карточке главы выше.</p>'
        : left ? '<button class="btn btn-wide" data-click="refresh">🔄 Обновить</button>' :
        '<button class="btn btn-primary btn-wide" data-action="claim" data-trip="'+p.trip.id+'">🎁 Забрать награду</button>')+
      '</div>';
  }
  return '<div class="section-heading">🧭 Приключения</div>'+
    '<p class="section-lead">Выбирай маршрут, собирай награды и повышай уровень питомца.</p>'+
    '<div class="notice">🎟 Походов осталось сегодня: <b>'+value(p.trips_remaining ?? 3)+'/3</b></div>'+

    ui.config.routes.map(r => {
      const bonusXp = Math.floor(r.xp*(p.progression?.effects.trip_xp_percent??(p.profession==="explorer"?120:100))/100);
      const energy = Math.max(1,r.energy-(p.progression?.effects.trip_energy??(p.profession==="artisan"?3:0)));
      const coins = r.coins+(p.progression?.effects.trip_coins??(p.profession==="merchant"?1:0));
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
  return '<div class="section-heading">📋 Ежедневные задания</div>'+
    '<p class="section-lead">Выполняйте действия вдвоём. Опыт общий у питомца.</p>'+
    '<div class="panel task-summary"><div class="row"><strong>🌟 Прогресс дня</strong>'+
       '<span class="chip chip-green">'+done+' / '+count+'</span></div>'+
       '<progress class="game-progress task-progress" aria-label="Выполненные задания" max="'+Math.max(1,count)+'" value="'+done+'">'+done+' / '+count+'</progress>'+
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
  return progression.render(p,ui.config);
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
    '<button class="btn btn-wide btn-quiet" data-click="wardrobe-back">← Назад</button>';
}
function render() {
  if (!ui.pet) return;
  const p=ui.pet;
  const balanceMarkup=icon("candy")+'<b>'+p.balance+'</b>';
  if(wallet.innerHTML!==balanceMarkup) wallet.innerHTML=balanceMarkup;
  nav.classList.remove("hidden");
  const navMarkup=tabs.map(([id,_emoji,label])=>'<button class="nav-btn '+
    (navigation.activeRoot()===id?'active':'')+'" data-root="'+id+'"'+
    (navigation.activeRoot()===id?' aria-current="page"':'')+'><span class="nav-symbol">'+design.routeIcon(id)+'</span>'+label+'</button>').join("");
  if(nav.dataset.rendered!==navMarkup){nav.innerHTML=navMarkup;nav.dataset.rendered=navMarkup;}
  const tools='<div class="game-tools">'+renderPicker()+(ui.tab==="inventory"?"":quickAccess())+'</div>';
  if(ui.tab==="home"){
    // Keep an unchanged scene mounted: routine snapshots must not interrupt
    // breathing, clouds, keyboard focus on the pet, or an active reaction.
    if(!app.querySelector("[data-home-scene]")){
      clearPetReaction();
      app.innerHTML='<div data-home-tools></div><div data-home-scene></div><div data-home-body></div>';
    }
    const scene=app.querySelector("[data-home-scene]");
    const sceneMarkup=hero(p);
    if(scene.dataset.markup!==sceneMarkup){
      clearPetReaction();
      scene.innerHTML=sceneMarkup;
      scene.dataset.markup=sceneMarkup;
    }
    patchHome("[data-home-tools]",tools);
    patchHome("[data-home-body]",home(p));
    return;
  }
  if(ui.tab==="world"){
    if(!app.querySelector("[data-world-scene]")){
      clearPetReaction();
      app.innerHTML='<div data-world-tools></div><div data-world-scene></div><div data-world-detail aria-live="polite"></div>';
      app.querySelector("[data-world-scene]").innerHTML=world(p);
    }
    patchHome("[data-world-tools]",tools);
    syncWorldMap();
    return;
  }
  clearPetReaction();
  app.innerHTML=tools+({
    profile,games,trade,club,trips,tasks,shop,growth,wardrobe,inventory,
    house: p => window.PetsHouse.renderHouse(p,ui.config),
    farm: p => country.farm(p,ui.config.countryside,serverNow(),ui.seedCode),
    fishing: p => country.fishing(p,ui.config.countryside,serverNow()),
    arcade: p => arcade.render(p,ui.config.arcade,serverNow()),
    business: p => window.PetsBusiness.renderBusiness(p,ui.config,serverNow(),ui.businessCode),
    market: p => window.PetsMarket.renderMarket(
      ui.market,p,ui.config,ui.marketSelection,serverNow()),
    league: p => window.PetsLeague.renderLeague(ui.league,p,serverNow()),
    season: p => window.PetsSeason.renderSeason(ui.season,p,serverNow()),
    studio: p => window.PetsStudio.renderStudio(ui.studio,p,ui.studioMode)
  }[ui.tab]||home)(p);
}
function patchHome(selector,markup){
  const node=app.querySelector(selector);
  if(node.dataset.markup===markup)return;
  node.innerHTML=markup;
  node.dataset.markup=markup;
}
function setPet(snapshot) {
  if(ui.pet?.id!==snapshot.id)ui.mapLocation=null;
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
    ui.season=null; ui.seasonFetchedAt=0; ++ui.seasonRequest;
    ui.studio=null; ui.studioFetchedAt=0; ++ui.studioRequest;
    setPet(pet);
  } finally {ui.syncing = false;}
}
async function refreshPet({force=false,quiet=true} = {}) {
  if (!ui.selected || !TG?.initData || ui.busy || ui.syncing || motionHidden() ||
      !modal.classList.contains("hidden")) return;
  if(!force&&ui.tab==="arcade"&&ui.pet?.arcade?.active?.mine&&serverNow()<=ui.pet.arcade.active.expires_at)return;
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
async function refreshCountryside(){
  if(!ui.selected||!TG?.initData||ui.busy||ui.syncing||motionHidden()||
     !modal.classList.contains("hidden")||!["farm","fishing"].includes(ui.tab))return;
  const id=ui.selected,serial=++ui.syncSerial;
  ui.syncing=true;
  try{
    const result=await api("/api/countryside/"+id);
    if(serial!==ui.syncSerial||ui.selected!==id||ui.busy||!["farm","fishing"].includes(ui.tab))return;
    if(JSON.stringify(ui.pet.countryside)!==JSON.stringify(result.countryside)){
      ui.pet={...ui.pet,countryside:result.countryside};
      syncClock(result);
      render();
    }else syncClock(result);
    syncCountryside();
  }catch{/* The next refresh retries; do not interrupt a fishing session with a toast. */}
  finally{ui.syncing=false;}
}
async function refreshArcade(){
  if(!ui.selected||!TG?.initData||ui.busy||ui.syncing||motionHidden()||
     !modal.classList.contains("hidden")||ui.tab!=="arcade")return;
  const id=ui.selected,serial=++ui.syncSerial;
  ui.syncing=true;
  try{
    const result=await api("/api/arcade/"+id);
    if(serial!==ui.syncSerial||ui.selected!==id||ui.busy||ui.tab!=="arcade")return;
    syncClock(result);
    if(JSON.stringify(ui.pet.arcade)!==JSON.stringify(result.arcade)){
      ui.pet={...ui.pet,arcade:result.arcade};render();
    }
    syncArcade();
  }catch{/* Retry on next entry or refresh. */}
  finally{ui.syncing=false;}
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
async function loadSeason({force=false,quiet=true}={}) {
  if(!ui.selected || !TG?.initData || document.hidden || (ui.seasonLoading&&!force)) return;
  if(!force && ui.season && Date.now()-ui.seasonFetchedAt<60000) return;
  const id=ui.selected,serial=++ui.seasonRequest;
  ui.seasonLoading=true;
  try {
    const data=await api('/api/season/'+id);
    if(serial===ui.seasonRequest && ui.selected===id) {
      ui.season=data;ui.seasonFetchedAt=Date.now();
      if(ui.tab==='season') render();
    }
  } catch(e) {
    if(!quiet) showToast(e.message);
  } finally {ui.seasonLoading=false;}
}
async function loadStudio({force=false,quiet=true}={}) {
  if(!ui.selected || !TG?.initData || document.hidden || (ui.studioLoading&&!force)) return;
  if(!force && ui.studio && Date.now()-ui.studioFetchedAt<60000) return;
  const id=ui.selected,serial=++ui.studioRequest;
  ui.studioLoading=true;
  try {
    const data=await api('/api/studio/'+id);
    if(serial===ui.studioRequest && ui.selected===id) {
      ui.studio=data;ui.studioFetchedAt=Date.now();
      if(ui.tab==="studio") render();
    }
  } catch(e) {
    if(!quiet) showToast(e.message);
  } finally {ui.studioLoading=false;}
}
function syncBackButton() {
  try {
    if (ui.tab !== "home" || !modal.classList.contains("hidden")) TG?.BackButton?.show();
    else TG?.BackButton?.hide();
  } catch {}
}
function displayRoute(tab) {
  ui.tab=tab;
  ui.picker=false;
  render();
  syncBackButton();
  window.scrollTo(0,0);
  if(tab==="market") loadMarket();
  if(tab==="league") loadLeague();
  if(tab==="season") loadSeason({quiet:false});
  if(tab==="studio") loadStudio({quiet:false});
  if(tab==="farm"||tab==="fishing")refreshCountryside();
  if(tab==="arcade"){syncArcade();refreshArcade();}
}
function goToTab(tab, root=false) {
  if(ui.busy) return;
  displayRoute(root ? navigation.select(tab) : navigation.open(tab));
}
function closeModal() {
  modal.classList.add("hidden");
  syncBackButton();
}
function goBack() {
  if (!modal.classList.contains("hidden")) closeModal();
  else if (!ui.busy && ui.pet) displayRoute(navigation.back());
}
try {TG?.BackButton?.onClick(goBack);} catch {}
document.addEventListener("keydown", e => {if(e.key === "Escape") goBack();});
function syncTimers() {
  if (!ui.pet || ui.busy) return;
  if(ui.tab==='league') loadLeague();
  if(ui.tab==='studio') loadStudio();
  if(ui.tab==='season') {
    const label=app.querySelector("[data-season-time]");
    if(label && ui.season?.season?.end_ts) {
      label.textContent=window.PetsSeason.remaining(
        Number(ui.season.season.end_ts)-serverNow());
    }
    loadSeason();
  }
  if(ui.tab==="market") {
    for(const badge of app.querySelectorAll("[data-market-expiry]")) {
      badge.textContent="⏳ "+window.PetsMarket.duration(Number(badge.dataset.marketExpires)-serverNow());
    }
    loadMarket();
  }
  if(ui.tab==="business") syncTycoon();
  if(ui.tab==="world") syncWorldMap();
  if (ui.tab === "trips" && ui.pet.trip) {
    const remaining = Math.max(0, ui.pet.trip.finish_at - serverNow());
    const label = app.querySelector("[data-trip-countdown]");
    if (label) {
      label.textContent = remaining ? "⏳ "+duration(remaining)+" до возвращения" : "🎁 Поход завершён";
      if (remaining===0 && app.querySelector('[data-click="refresh"]')) render();
    }
  }
  if (ui.tab === "trips") {
    const countdown = app.querySelector("[data-chapter-countdown]");
    if (countdown && ui.pet.trip) {
      const left = ui.pet.trip.finish_at-serverNow();
      if (left<=0) render();
      else countdown.textContent = "⏳ "+duration(left)+" до возвращения";
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
        : info.blocked_reason ? "Недоступно" : "Готово";
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
function syncTycoon(message="", accepted=false) {
  if(ui.tab!=="business"||motionHidden()||!ui.pet)return;
  for(const machine of app.querySelectorAll("[data-tycoon-machine]")){
    const code=machine.dataset.tycoonMachine;
    const job=ui.pet.businesses?.units?.[code]?.job;
    const button=machine.querySelector("[data-tycoon-tap]");
    if(!job||!button||Number(button.dataset.jobId)!==job.id)continue;
    const state=window.PetsBusiness.workState(job,ui.config.tycoon_rules||{},serverNow());
    machine.classList.toggle("tycoon-ready",state.ready);
    button.disabled=ui.busy||!state.canTap;
    const caption=button.querySelector("b");
    if(caption)caption.textContent=state.ready?"Партия готова":state.taps>=state.max?"Лимит работы":"РАБОТАТЬ";
    const label=machine.querySelector("[data-business-countdown]");
    if(label)label.textContent=state.ready?"Готово!":window.PetsBusiness.duration(state.left);
    const meter=machine.querySelector("[data-tycoon-taps]");
    if(meter)meter.textContent=state.taps+" / "+state.max;
    const progress=machine.querySelector("[data-tycoon-progress]");
    if(progress)progress.value=state.taps;
    const collect=machine.querySelector("[data-business-collect]");
    if(collect){
      collect.disabled=ui.busy||!state.ready;
      collect.textContent=state.ready?"📦 Забрать партию":"Производится…";
    }
    const status=machine.querySelector("[data-tycoon-status]");
    if(status&&(message||state.ready)){
      const text=message||(state.ready?"Можно забрать партию":"");
      if(status.textContent!==text)status.textContent=text;
    }
    if(accepted){
      machine.classList.remove("tycoon-hit");
      machine.classList.add("tycoon-hit");
      setTimeout(()=>machine.classList.remove("tycoon-hit"),300);
    }
  }
}
async function performTycoonTap(code, jobId) {
  if(ui.busy||motionHidden()||ui.tab!=="business")return;
  const id=ui.selected, unit=ui.pet?.businesses?.units?.[code], job=unit?.job;
  if(!job||job.id!==jobId||!window.PetsBusiness.workState(job,ui.config.tycoon_rules||{},serverNow()).canTap)return;
  ui.busy=true;
  ++ui.syncSerial;
  syncTycoon();
  let refresh=false, message="", accepted=false;
  try {
    const result=await api("/api/tycoon/tap",{pet_id:id,code,job_id:job.id,expected_taps:job.taps||0});
    if(ui.selected!==id)return;
    syncClock(result);
    if(result.job?.id===job.id){
      unit.job=result.job;
      message=result.message;
      accepted=Boolean(result.accepted);
      if(accepted)haptic("light");
    } else {
      refresh=true;
      showToast(result.message);
    }
  } catch(e) {
    refresh=true;
    showToast(e.message);
  } finally {
    ui.busy=false;
    syncTycoon(message,accepted);
    if(refresh)await refreshPet({force:true,quiet:false});
    else if(unit.job?.tap_ready_at){
      const wait=Math.max(0,Math.ceil((unit.job.tap_ready_at-serverNow())*1000));
      setTimeout(()=>syncTycoon(),Math.min(wait+30,2000));
    }
  }
}

function syncCountryside(){
  if(ui.busy||motionHidden()||!ui.pet?.countryside)return;
  const s=ui.pet.countryside, now=serverNow();
  if(ui.tab==="farm"){
    for(const node of app.querySelectorAll("[data-country-plot]")){
      const plot=s.plots.find(x=>x.slot===Number(node.dataset.countryPlot));
      if(!plot?.crop)continue;
      const state=country.plotState(plot,now),ready=state==="ready";
      node.classList.toggle("plot-ready",ready);
      node.classList.toggle("plot-growing",state==="growing");
      node.classList.toggle("plot-watered",state==="watered");
      const badge=node.querySelector("[data-plot-status]");
      const text=ready?"Готово":plot.watered?"Полита":"Растёт";
      if(badge&&badge.textContent!==text)badge.textContent=text;
      const clock=node.querySelector("[data-plot-clock]");
      if(clock)clock.textContent=ready?"Можно собирать":country.duration(plot.ready_at-now);
      const art=node.querySelector("[data-plot-art]");
      if(art&&ready)art.textContent=ui.config.countryside.crops.find(x=>x.code===plot.crop)?.icon||"🌾";
      const water=node.querySelector("[data-farm-water]"),harvest=node.querySelector("[data-farm-harvest]");
      if(water)water.disabled=ready||Boolean(plot.watered)||!ui.pet.best_streak;
      if(harvest)harvest.disabled=!ready||!ui.pet.best_streak;
    }
  }
  if(ui.tab==="fishing"){
    const node=app.querySelector("[data-fish-game]");
    if(!node)return;
    const state=country.castState(s.cast,now);
    for(const name of ["idle","waiting","bite","missed"])node.classList.toggle("fish-"+name,state===name);
    const heading=node.querySelector("[data-fish-heading]");
    const title=state==="waiting"?"Ждём клёва…":state==="bite"?"КЛЮЁТ!":state==="missed"?"Рыба сорвалась":"Забросьте удочку";
    if(heading&&heading.textContent!==title){heading.textContent=title;if(state==="bite")haptic("medium");}
    const clock=node.querySelector("[data-fish-clock]");
    if(clock)clock.textContent=state==="waiting"?"Не подсекайте слишком рано":state==="bite"?country.duration(s.cast.expires_at-now)+" до схода":state==="missed"?"Можно начать заново":"Заброс бесплатный";
    const reel=node.querySelector("[data-fish-reel]"),cast=node.querySelector("[data-fish-cast]");
    if(reel)reel.disabled=state!=="bite"||!ui.pet.best_streak;
    if(cast)cast.disabled=!ui.pet.best_streak||s.casts_remaining<=0||state==="waiting"||state==="bite";
  }
}

function syncArcade(){
  if(ui.tab!=="arcade"||ui.busy||motionHidden()||!ui.pet?.arcade)return;
  const run=ui.pet.arcade.active, now=serverNow();
  const node=app.querySelector("[data-arcade-run]");
  if(!run||!node||Number(node.dataset.arcadeRun)!==run.id)return;
  const phase=arcade.phase(run,now);
  if(phase==="expired"){render();refreshArcade();return;}
  const timer=node.querySelector("[data-arcade-time]");
  const text=arcade.seconds(run.expires_at-now);
  if(timer&&timer.textContent!==text)timer.textContent=text;
  if(!run.mine||!run.state)return;
  const s=run.state,status=node.querySelector("[data-arcade-status]"),message=arcade.status(run,now);
  if(status&&status.textContent!==message)status.textContent=message;
  if(run.game==="memory"){
    const memory=node.querySelector("[data-arcade-memory]");
    if(phase==="recall"&&memory&&memory.dataset.hidden!=="yes"){
      for(const tile of memory.children)tile.textContent="?";
      memory.dataset.hidden="yes";
    }
    for(const key of node.querySelectorAll("[data-arcade-move]"))key.disabled=phase!=="recall";
  }else if(run.game==="catch"){
    const item=node.querySelector("[data-arcade-falling]");
    if(item){
      const frac=Math.max(0,Math.min(1,(now-s.start)/(s.ready-s.start)));
      item.style.setProperty("--fall",Math.round(12+60*frac)+"%");
      item.hidden=phase==="late";
    }
    for(const key of node.querySelectorAll("[data-arcade-move]")){
      key.disabled=key.dataset.arcadeMove==="-1"?phase!=="late":phase!=="ready";
    }
  }else if(run.game==="reaction"){
    const key=node.querySelector("[data-arcade-move]");
    if(key&&!key.classList.contains("signal-"+phase)){
      for(const name of ["waiting","ready","late"])key.classList.toggle("signal-"+name,name===phase);
      key.querySelector("[data-arcade-signal]").textContent=phase==="ready"?"⚡":phase==="late"?"↻":"●";
      key.querySelector("[data-arcade-signal-label]").textContent=phase==="ready"?"ЖМИ!":phase==="late"?"ДАЛЬШЕ":"ЖДИ";
    }
  }
}
async function performArcade(action,game="",move=null){
  if(ui.busy||motionHidden()||ui.tab!=="arcade"||!ui.pet?.arcade)return;
  const id=ui.selected,state=ui.pet.arcade,run=state.active;
  const payload={pet_id:id,action,game};
  if(action==="start"){
    if(!ui.config.arcade.games.some(d=>d.code===game))return;
    payload.expected_last=state.last_id;
  }else{
    if(!run?.mine||serverNow()>run.expires_at){syncArcade();return;}
    payload.run_id=run.id;payload.version=run.version;
    if(action==="move")payload.move=move;
  }
  ui.busy=true;++ui.syncSerial;app.classList.add("is-busy");
  const before=ui.pet;
  let refresh=false;
  try{
    const result=await api("/api/arcade/action",payload);
    if(ui.selected!==id)return;
    syncClock(result);
    if(result.pet){
      setPet(result.pet);
      showActionEffect("arcade",game,before,result.pet,result.message);
      if(result.pet.level<=before.level&&result.arcade.result?.xp)playEffect("⭐ +"+result.arcade.result.xp+" XP","reward");
      haptic("success");
    }else{
      ui.pet={...ui.pet,arcade:result.arcade};render();
    }
    const feedback=app.querySelector("[data-arcade-feedback]");
    if(action==="move"&&feedback&&!result.pet)feedback.textContent=result.message;
    else showToast(result.message);
  }catch(e){refresh=true;showToast(e.message);}
  finally{
    ui.busy=false;app.classList.remove("is-busy");syncArcade();
    if(refresh)refreshArcade();
  }
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
      ++ui.leagueRequest; ui.league=null; ui.leagueFetchedAt=0;
      if(kind.startsWith("season_")) {
        ++ui.seasonRequest; ui.season=null; ui.seasonFetchedAt=0;
      }
      if(kind==="studio_equip" || kind==="chapter_finish") {
        ++ui.studioRequest; ui.studio=null; ui.studioFetchedAt=0;
      }
      setPet(result.pet);
      showActionEffect(kind, code, before, result.pet, result.message);
      if(kind.startsWith("market_")) {
        ui.market=null;ui.marketFetchedAt=0;
        await loadMarket({force:true,quiet:false});
      }
      if(kind.startsWith("season_")) await loadSeason({force:true,quiet:false});
      if(kind==="studio_equip") await loadStudio({force:true,quiet:false});
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
  if (target.dataset.root) {tap();goToTab(target.dataset.root,true);return;}
  if (target.dataset.tab) {tap();goToTab(target.dataset.tab);return;}
  if (target.dataset.mapLocation) {selectWorldLocation(target.dataset.mapLocation);return;}
  if (target.dataset.mapOpen) {openWorldLocation(target.dataset.mapOpen);return;}
  if (target.dataset.click==="arcade-back") {tap();goBack();return;}
  if(ui.tab==="arcade"){
    if(ui.busy)return;
    if(target.dataset.arcadeStart){await performArcade("start",target.dataset.arcadeStart);return;}
    if(target.dataset.arcadeRefresh){await refreshArcade();return;}
    if(target.dataset.arcadeCancel){
      confirmDialog("Завершить игру?","Попытка будет потрачена, награды не будет.",()=>performArcade("cancel"));return;
    }
    if(target.dataset.arcadeMove!==undefined&&!target.disabled){
      const move=target.dataset.arcadeType==="number"?Number(target.dataset.arcadeMove):target.dataset.arcadeMove;
      await performArcade("move","",move);return;
    }
  }
  if (target.dataset.click==="country-back") {tap();goBack();return;}
  if (ui.busy && (ui.tab==="farm"||ui.tab==="fishing")) return;
  if (target.dataset.countrySeed && ui.tab==="farm") {
    if(ui.config.countryside.crops.some(d=>d.code===target.dataset.countrySeed)){
      ui.seedCode=target.dataset.countrySeed;tap();render();
    }
    return;
  }
  if (target.dataset.farmPlant && ui.tab==="farm") {
    const code=target.dataset.farmPlant, crop=ui.config.countryside.crops.find(d=>d.code===code);
    const slot=Number(target.dataset.slot), version=Number(target.dataset.version);
    if(crop&&Number.isSafeInteger(slot)&&Number.isSafeInteger(version)){
      confirmDialog("Посадить "+crop.name+"?","С твоего баланса спишется "+crop.price+" 🍬. Урожай будет общим.",
        ()=>perform("farm_plant",code,{slot,version}));
    }
    return;
  }
  if ((target.dataset.farmWater||target.dataset.farmHarvest) && ui.tab==="farm") {
    await perform(target.dataset.farmWater?"farm_water":"farm_harvest","",{slot:Number(target.dataset.slot),version:Number(target.dataset.version)});
    return;
  }
  if (target.dataset.fishCast!==undefined && ui.tab==="fishing") {
    await perform("fish_cast","",{expected_last:Number(target.dataset.fishCast)});return;
  }
  if (target.dataset.fishReel && ui.tab==="fishing") {
    await perform("fish_reel","",{cast_id:Number(target.dataset.fishReel)});return;
  }
  if (target.dataset.countryUpgrade && ["farm","fishing"].includes(ui.tab)) {
    const kind=target.dataset.countryUpgrade, level=Number(target.dataset.level);
    const tier=ui.config.countryside.upgrades[kind]?.[level-1];
    if(tier){
      const cost=tier.costs.map(x=>(ui.config.materials.find(m=>m.code===x.code)?.name||x.code)+": "+x.quantity).join(", ");
      confirmDialog("Улучшить до уровня "+(level+1)+"?","Общие материалы: "+cost+".",
        ()=>perform("country_upgrade",kind,{expected_level:level}));
    }
    return;
  }
  if (target.dataset.countrySell && ["farm","fishing"].includes(ui.tab)) {
    const kind=target.dataset.goodsKind, code=target.dataset.countrySell, stock=Number(target.dataset.stock);
    const d=(kind==="crop"?ui.config.countryside.crops:ui.config.countryside.fish).find(x=>x.code===code);
    if(d){
      const qty=Math.min(stock,Math.floor(ui.pet.countryside.sales_remaining/d.sell));
      if(qty>0)confirmDialog("Продать "+d.name+"?","Со склада уйдёт до "+qty+" шт. Ты получишь до "+qty*d.sell+" 🍬 в пределах лимита.",
        ()=>perform("country_sell",code,{goods_kind:kind,expected_stock:stock}));
    }
    return;
  }
  if (["house-back","business-back","market-back","studio-back","season-back",
       "league-back","inventory-back","wardrobe-back"].includes(target.dataset.click)) {
    tap();goBack();return;
  }
  if (target.dataset.group) {ui.group=target.dataset.group;tap();render();return;}
  if (target.dataset.pet) {try{await loadPet(Number(target.dataset.pet));}catch(err){showToast(err.message);}return;}
  if (target.dataset.click==="picker") {ui.picker=!ui.picker;render();return;}
  if (target.dataset.click==="pet-greet") {petGreeting();return;}
  if (target.dataset.click==="wardrobe") {tap();goToTab("wardrobe");return;}
  if (target.dataset.click==="house") {tap();goToTab("house");return;}
  if (target.dataset.click==="business") {tap();goToTab("business");return;}
  if (target.dataset.click==="market") {tap();goToTab("market");return;}
  if (target.dataset.click==="studio") {tap();goToTab("studio");return;}
  if (target.dataset.studioMode) {
    const mode=target.dataset.studioMode;
    if(["frames","stickers","album"].includes(mode)){
      ui.studioMode=mode;tap();render();
    }
    return;
  }
  if (target.dataset.studioRefresh) {await loadStudio({force:true,quiet:false});return;}
  if (target.dataset.studioEquip) {
    const slot=target.dataset.studioSlot,code=target.dataset.studioEquip;
    const source=slot==="frame"?ui.studio?.frames:
      slot==="sticker"?ui.studio?.stickers:[];
    const item=source?.find(x=>x.code===code);
    if(item?.unlocked&&!item.equipped) await perform("studio_equip",code,{slot});
    return;
  }
  if (target.dataset.click==="season") {tap();goToTab("season");return;}
  if (target.dataset.seasonRefresh) {await loadSeason({force:true,quiet:false});return;}
  if (target.dataset.seasonClaim) {
    const item=ui.season?.challenges?.find(c=>c.code===target.dataset.seasonClaim);
    if(item?.ready&&!item.claimed) await perform("season_claim",item.code);
    return;
  }
  if (target.dataset.seasonEquip) {
    const code=target.dataset.seasonEquip,start=target.dataset.seasonStart;
    const item=ui.season?.titles?.find(t=>t.code===code&&t.season_start===start);
    if(item&&!item.equipped) {
      confirmDialog("Надеть титул?",
        "Титул «"+item.title+"» появится на главной странице общего питомца для обоих хозяев.",
        ()=>perform("season_equip",code,{season_start:start}));
    }
    return;
  }
  if (target.dataset.seasonClear) {
    confirmDialog("Снять титул?",
      "Титул останется в коллекции и его можно будет надеть снова.",
      ()=>perform("season_equip","",{season_start:""}));
    return;
  }
  if (target.dataset.click==="league") {tap();goToTab("league");return;}
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
  if (target.dataset.tycoonSelect) {
    if(!ui.busy&&ui.config.businesses.some(b=>b.code===target.dataset.tycoonSelect)){
      ui.businessCode=target.dataset.tycoonSelect;tap();render();
    }
    return;
  }
  if (target.dataset.tycoonTap) {
    const job=Number(target.dataset.jobId);
    if(Number.isSafeInteger(job)&&job>0)await performTycoonTap(target.dataset.tycoonTap,job);
    return;
  }
  if (target.dataset.tycoonHire) {
    const unit=ui.config.businesses.find(b=>b.code===target.dataset.tycoonHire);
    if(unit){
      const level=Number(ui.pet.businesses?.units?.[unit.code]?.workers||0);
      const tier=unit.worker_tiers?.[level];
      if(tier){
        const cost=tier.costs.map(item=>{
          const m=ui.config.materials.find(x=>x.code===item.code);
          return (m?.icon||"📦")+" "+item.quantity;
        }).join(", ");
        confirmDialog("Нанять работника?","Общие материалы: "+cost+". Новые партии станут быстрее; текущая не изменится.",
          ()=>perform("tycoon_hire",unit.code,{expected_level:level}));
      }
    }
    return;
  }
  if (target.dataset.tycoonOrder) {
    const unit=ui.config.businesses.find(b=>b.code===target.dataset.tycoonOrder);
    if(unit){
      const qty=ui.config.tycoon_rules?.order_quantity||2;
      const expectedStock=Number(ui.pet.businesses?.units?.[unit.code]?.stock||0);
      confirmDialog("Выполнить заказ?","С общего склада спишется "+qty+" шт. Ты получишь "+(qty*unit.sell_price)+" 🍬 в пределах дневного лимита.",
        ()=>perform("tycoon_order",unit.code,{expected_stock:expectedStock}));
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
        window.PetsBusiness.duration(window.PetsBusiness.workerDuration(unit,ui.pet.businesses?.units?.[unit.code]||{},ui.config.tycoon_rules||{},ui.pet.progression?.effects.production_percent??100))+". Можно ускорить партию нажатиями.",
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
  if(target.dataset.click==="growth-back"){tap();goBack();return;}
  if(ui.tab==="growth"&&target.dataset.skill&&!target.disabled){
    const code=target.dataset.skill,rank=Number(target.dataset.rank);
    const skill=ui.config.progression.skills.find(x=>x.code===code);
    if(skill)confirmDialog("Улучшить "+skill.name+"?","Потратить 1 общее очко навыков. Сброс пока недоступен.",()=>perform("skill_learn",code,{expected_rank:rank}));
    return;
  }
  if(ui.tab==="growth"&&target.dataset.specialization&&!target.disabled){
    const spec=ui.config.progression.specializations.find(x=>x.code===target.dataset.specialization);
    if(spec)confirmDialog("Выбрать "+spec.name+"?","Общий постоянный выбор. Профессия сохранится.",()=>perform("specialization",spec.code));
    return;
  }
  if(ui.tab==="growth"&&target.dataset.levelClaim&&!target.disabled){
    await perform("level_claim","",{level:Number(target.dataset.levelClaim)});return;
  }
  if(target.dataset.profession) {
    const job=ui.config.professions.find(j=>j.code===target.dataset.profession);
    if(job)confirmDialog('Профессия '+job.name,'Выбор для двоих. Изменить пока нельзя.',
      ()=>perform("profession",job.code));return;
  }
  if(target.dataset.action) {
    const kind=target.dataset.action,code=target.dataset.code||"";
    const handler=()=>perform(kind,code,
      kind==="claim"?{trip_id:Number(target.dataset.trip)}:
      kind==="chapter_start"?{chapter:target.dataset.chapter}:{});
    if(kind==="start" || kind==="chapter_start")confirmDialog("Отправить питомца в поход?",
      "Поход потратит сытость и энергию. Награда ждёт возвращения.",handler);
    else if(kind==="chapter_craft") confirmDialog(
      "Собрать указатель?", "Один материал спишется из общего рюкзака.", handler);
    else handler();
  }
});
async function boot() {
  try {
    TG?.ready();
    TG?.expand();
    try {TG?.setHeaderColor?.("#213b33");TG?.setBackgroundColor?.("#101d20");TG?.setBottomBarColor?.("#172c27");}catch{}
    syncSafeArea();
    const brandMark=document.querySelector(".brand-mark");
    if(brandMark) brandMark.innerHTML=icon("paw");
    if(!TG?.initData) throw new Error("Открой Mini App через кнопку бота в Telegram.");
    ui.config=await api("/api/bootstrap");
    ui.pets=ui.config.pets;
    if(!ui.pets.length) {
      app.innerHTML='<div class="error-page"><div class="icon">🥚</div><h2>Питомцев пока нет</h2>'+
        '<p class="muted">Ответь другу словом «питомец» в групповом чате, чтобы завести вашего первого питомца.</p></div>';
      return;
    }
    const started=(TG.initDataUnsafe?.start_param||"").match(/^p_(\d+)$/);
    let id=started?Number(started[1]):ui.config.chapter_pet_id??ui.pets[0].id;
    if(!ui.pets.some(p=>p.id===id)) id=ui.pets[0].id;
    if (ui.config.chapter_pet_id===id && !started) {
      navigation.select("world");
      ui.tab=navigation.open("trips");
    }
    await loadPet(id);
    syncBackButton();
  } catch(e) {
    app.innerHTML='<div class="error-page"><div class="icon">🔒</div><h2>Не удалось открыть игру</h2>'+
      '<p class="muted">'+safe(e.message)+'</p>'+
      '<button class="btn btn-wide" id="retry">Повторить</button></div>';
    document.querySelector("#retry")?.addEventListener("click", boot);
  }
}
// Refresh shared pet state when returning from another Telegram tab / chat.
try {
  TG?.onEvent?.("activated", () => {inactive=false;syncMotion();refreshPet({force:true});});
  TG?.onEvent?.("deactivated", () => {inactive=true;syncMotion();});
} catch {}
function syncMotion() {
  document.documentElement.classList.toggle("motion-paused",motionHidden());
  if(motionHidden()){
    clearPetReaction();
    clearTimeout(effectTimer);
    effects?.replaceChildren();
  }
}
syncMotion();
document.addEventListener("visibilitychange", () => {
  syncMotion();
  if(!document.hidden) {
    refreshPet();
    if(ui.tab==="market") loadMarket({force:true});
    if(ui.tab==="league") loadLeague({force:true});
    if(ui.tab==="season") loadSeason({force:true});
    if(ui.tab==="studio") loadStudio({force:true});
  }
});
window.addEventListener("online", () => refreshPet({force:true,quiet:false}));
window.addEventListener("offline", () => showToast("Нет подключения к интернету"));
// Fast lightweight updates only while the arcade is visible and running.
setInterval(()=>{
  if(!motionHidden()&&!ui.busy&&ui.tab==="arcade"&&ui.pet?.arcade?.active)syncArcade();
},100);
// Cheap countdown updates, only on the visible game screen.
setInterval(()=>{
  if(motionHidden()||ui.busy)return;
  if(ui.tab==="business")syncTycoon();
  if(ui.tab==="world")syncWorldMap();
  if(ui.tab==="farm"||ui.tab==="fishing")syncCountryside();
},1000);
// A short state-only poll lets the partner see a cast before its bite window closes.
setInterval(()=>{
  if(ui.tab==="fishing")refreshCountryside();
  if(ui.tab==="arcade"&&(!ui.pet?.arcade?.active?.mine||serverNow()>ui.pet.arcade.active.expires_at))refreshArcade();
},4000);
setInterval(() => {
  if(motionHidden()) return;
  syncTimers();
  refreshPet();
}, 15000);
boot();
