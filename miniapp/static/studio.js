/* PETS WORLD 1.9 — style atelier and private game-history album. */
(function(){
  const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
  const number=v=>Math.max(0,Math.floor(Number(v)||0));
  const pct=(progress,goal)=>goal>0?Math.min(100,Math.round(number(progress)*100/number(goal))):100;
  const FRAMES=new Set(["classic","grove","neon","atelier","champion","cosmos"]);
  const STICKERS=new Set(["paw","compass","heart","medal","crystal","crown"]);
  function safeFrame(code){return FRAMES.has(code)?code:"classic";}
  function safeSticker(code){return STICKERS.has(code)?code:"paw";}
  function renderStyleItem(x,slot){
    const ready=Boolean(x.unlocked),equipped=Boolean(x.equipped);
    const code=String(x.code||"");
    const own=slot==="frame"?FRAMES:STICKERS;
    if(!own.has(code))return "";
    const progress=number(x.progress),goal=number(x.goal);
    const line=goal>0
      ?'<div class="studio-progress"><div class="progress" role="progressbar" '+
       'aria-label="Прогресс украшения" aria-valuemin="0" aria-valuemax="'+goal+
       '" aria-valuenow="'+Math.min(progress,goal)+'">'+
       '<i style="width:'+pct(progress,goal)+'%"></i></div>'+
       '<span>'+Math.min(progress,goal)+' / '+goal+'</span></div>'
      :'<div class="studio-free">🎁 Открыто каждому</div>';
    return '<div class="studio-style-item '+(equipped?'equipped':ready?'ready':'locked')+'">'+
      '<div class="studio-style-symbol '+(slot==="frame"?"studio-frame-swatch-"+code:"")+
      '">'+esc(x.icon||"✨")+'</div>'+
      '<div class="studio-style-info"><strong>'+esc(x.title)+'</strong>'+
      '<small>'+esc(x.description)+'</small>'+line+'</div>'+
      '<button type="button" class="btn '+(equipped?'btn-quiet':ready?'btn-primary':'btn-quiet')+
      '" data-studio-equip="'+esc(code)+'" data-studio-slot="'+slot+'" '+
      (equipped||!ready?'disabled':'')+'>'+
      (equipped?'✅ Надето':ready?'Надеть':'🔒')+'</button></div>';
  }
  function renderAlbum(events){
    const timeline=events.map((event,i)=>
      '<div class="studio-memory"><div class="studio-memory-icon">'+esc(event.icon||"🐾")+'</div>'+
      '<div class="studio-memory-body"><strong>'+esc(event.title||"Воспоминание")+'</strong>'+
      '<small>📅 '+esc(event.day||"")+'</small></div></div>'
    ).join("");
    return '<div class="panel studio-album">'+
      '<div class="eyebrow">ВАША ИСТОРИЯ</div><h2 class="h2">📖 Альбом приключений</h2>'+
      '<p class="section-lead">Реальные события вашего общего питомца. '+
      'Записи сохраняются в игре и не требуют покупки.</p>'+
      (timeline||'<div class="notice">Здесь появятся ваши первые приключения.</div>')+
      '<p class="subtitle">Показаны последние 24 события из истории питомца.</p></div>';
  }
  function renderStudio(data,pet,mode){
    const heading='<div class="section-heading">🎨 Студия питомца</div>'+
      '<p class="section-lead">Создавайте индивидуальный образ, играя вместе. '+
      'Украшения не стоят ирисок и не влияют на характеристики.</p>';
    if(!data)return heading+'<div class="panel studio-loading"><div class="spinner"></div>'+
      '<p>Готовим студию и альбом приключений…</p>'+
      '<button class="btn btn-quiet btn-wide" data-studio-refresh="1">🔄 Обновить</button></div>'+
      '<button class="btn btn-quiet btn-wide" data-click="studio-back">← Назад</button>';
    const equipped=data.equipped||{},frame=safeFrame(equipped.frame);
    const sticker=safeSticker(equipped.sticker);
    const icon=({paw:"🐾",compass:"🧭",heart:"💞",medal:"🎯",crystal:"💎",crown:"👑"})[sticker];
    const album=Array.isArray(data.album)?data.album:[];
    const frames=Array.isArray(data.frames)?data.frames:[];
    const stickers=Array.isArray(data.stickers)?data.stickers:[];
    const tabs=[["frames","🖼️ Рамки"],["stickers","✨ Значки"],["album","📖 Альбом"]].map(([key,title])=>
      '<button class="pill '+(mode===key?'active':'')+'" data-studio-mode="'+key+
      '" aria-pressed="'+(mode===key?'true':'false')+'">'+title+'</button>').join('');
    const preview='<div class="panel studio-preview-panel">'+
      '<div class="eyebrow">ВАШ ОБРАЗ</div>'+
      '<div class="studio-preview studio-frame studio-frame-'+frame+'">'+
      '<img src="'+esc(pet.image)+'" alt="Питомец '+esc(pet.name)+'" loading="lazy">'+
      '<span class="studio-preview-sticker" aria-hidden="true">'+icon+'</span></div>'+
      '<div class="studio-preview-name">'+esc(pet.name)+'</div>'+
      '<p class="subtitle">🤝 Общий образ для обоих хозяев</p></div>';
    let main;
    if(mode==="album"){
      main='<div class="panel studio-coop"><strong>💞 Вместе сильнее</strong>'+
        '<p class="subtitle">Дней совместного ухода: <b>'+number(data.metrics?.days_both)+'</b>. '+
        'Оба хозяина должны выполнять уход в одни сутки.</p></div>'+renderAlbum(album);
    } else {
      const list=mode==="stickers"?stickers:frames;
      const slot=mode==="stickers"?"sticker":"frame";
      const collected=list.filter(x=>x.unlocked).length;
      main='<div class="panel studio-catalog"><div class="row">'+
        '<h2 class="h2">'+(slot==="frame"?"🖼️ Рамки профиля":"✨ Значки питомца")+'</h2>'+
        '<span class="chip chip-green">'+collected+'/'+list.length+'</span></div>'+
        '<p class="section-lead">Выполняйте игровые условия, чтобы открывать новые стили.</p>'+
        list.map(x=>renderStyleItem(x,slot)).join('')+'</div>';
    }
    return heading+preview+'<div class="studio-tabs">'+tabs+'</div>'+main+
      '<button class="btn btn-quiet btn-wide" data-studio-refresh="1">🔄 Обновить</button>'+
      '<button class="btn btn-quiet btn-wide" data-click="studio-back">← Назад</button>';
  }
  window.PetsStudio={renderStudio};
})();
