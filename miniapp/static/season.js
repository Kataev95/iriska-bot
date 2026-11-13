/* PETS WORLD 1.8: seasonal challenges and permanent cosmetic titles.
 * Only the server can grant a title. Buttons submit the challenge code,
 * never a score, price, timestamp or currency amount.
 */
(function () {
  const esc=x=>String(x??"").replace(/[&<>"']/g,c=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
  const count=x=>Math.max(0,Math.floor(Number(x)||0));
  const percent=(value,total)=>total>0?Math.min(100,Math.round(count(value)/count(total)*100)):0;
  function remaining(seconds) {
    const s=Math.max(0,Math.ceil(Number(seconds)||0));
    if(s===0) return "Сезон завершён";
    const d=Math.floor(s/86400),h=Math.floor(s%86400/3600);
    if(d) return d+" дн. "+h+" ч";
    const m=Math.ceil(s%3600/60);
    return h?h+" ч "+m+" мин":m+" мин";
  }
  function renderSeason(data,pet,now) {
    const header='<div class="section-heading">🎟 Сезон PETS WORLD</div>'+
      '<p class="section-lead">Выполняйте испытания вместе, получайте редкие титулы '+
      'и пополняйте общую коллекцию питомца.</p>';
    if(!data) return header+'<div class="panel season-loading">'+
      '<div class="spinner"></div><p>Загружаем испытания сезона…</p>'+
      '<button class="btn btn-quiet btn-wide" data-season-refresh="1">🔄 Повторить</button></div>'+
      '<button class="btn btn-quiet btn-wide" data-click="season-back">← Назад</button>';
    const s=data.season||{},challenges=Array.isArray(data.challenges)?data.challenges:[];
    const titles=Array.isArray(data.titles)?data.titles:[];
    const earned=challenges.filter(c=>c.claimed).length;
    const possible=challenges.filter(c=>c.ready&&!c.claimed).length;
    const timeLabel=remaining(Number(s.end_ts||0)>0?s.end_ts-now:s.seconds_left);
    const hero='<div class="panel season-hero">'+
      '<div class="season-stars" aria-hidden="true">✨ 🏆 ✨</div>'+
      '<div class="eyebrow">СЕЗОН '+count(s.number)+'</div>'+
      '<h2 class="season-heading">Новые испытания</h2>'+
      '<p class="subtitle">До '+esc(s.end||"конца сезона")+' · '+
      '<span data-season-time>'+esc(timeLabel)+'</span></p>'+
      '<div class="season-hero-stats">'+
      '<span>⭐ <b>'+count(data.my_score)+'</b> очков</span>'+
      '<span>🎖 <b>'+earned+'/'+challenges.length+'</b> титулов</span>'+
      '<span>🏆 <b>'+(data.my_rank?('#'+count(data.my_rank)):'—')+'</b> место</span></div>'+
      '<progress class="season-meter" value="'+earned+'" max="'+Math.max(1,challenges.length)+
      '" aria-label="Получено сезонных наград"></progress></div>';
    const rows=challenges.map(c=>{
      const progress=count(c.progress),goal=count(c.goal),ready=Boolean(c.ready);
      const claimed=Boolean(c.claimed);
      const label=claimed?'✅ Получено':ready?'🎁 Забрать титул':'🔒 '+progress+' / '+goal;
      const cls=claimed?'claimed':ready?'ready':'locked';
      return '<div class="season-challenge '+cls+'"><div class="season-challenge-top">'+
        '<span class="season-challenge-icon">'+esc(c.icon||"🏅")+'</span>'+
        '<div class="season-challenge-text"><strong>'+esc(c.title)+'</strong>'+
        '<small>'+esc(c.description)+'</small></div>'+
        (claimed?'<span class="chip chip-green">✓</span>':'')+'</div>'+
        '<div class="season-progress-row"><progress class="season-meter" '+
        'value="'+Math.min(progress,Math.max(1,goal))+'" max="'+Math.max(1,goal)+
        '" aria-label="Прогресс испытания"></progress>'+
        '<b>'+progress+'/'+goal+'</b></div>'+
        '<div class="season-prize">🎁 Награда: '+esc(c.reward)+'</div>'+
        '<button class="btn '+(ready&&!claimed?'btn-primary':'btn-quiet')+
        ' btn-wide" data-season-claim="'+esc(c.code)+'" '+(!ready||claimed?'disabled':'')+
        '>'+label+'</button></div>';
    }).join('');
    const challengesView='<div class="panel season-tasks">'+
      '<div class="row"><div><div class="eyebrow">ВЫПОЛНЯЙТЕ ВДВОЁМ</div>'+
      '<h2 class="h2">🎯 Испытания</h2></div><span class="chip chip-purple">'+
      earned+'/'+challenges.length+'</span></div>'+
      (possible?'<div class="notice">🎁 Доступно наград: <b>'+possible+
      '</b> — заберите их до конца сезона.</div>':'')+
      (rows||'<p class="subtitle">Пока нет испытаний.</p>')+'</div>';
    const collection=titles.map(t=>{
      const equipped=Boolean(t.equipped);
      return '<div class="season-title-item '+(equipped?'equipped':'')+'"><div>'+
        '<strong>'+esc(t.title)+'</strong>'+
        '<small>Получен в сезоне от '+esc(t.season_start)+'</small></div>'+
        '<button class="btn '+(equipped?'btn-quiet':'btn-cyan')+
        '" data-season-equip="'+esc(t.code)+'" data-season-start="'+
        esc(t.season_start)+'" '+(equipped?'disabled':'')+'>'+
        (equipped?'✅ Надет':'Надеть')+'</button></div>';
    }).join('');
    const collectionView='<div class="panel season-collection"><div class="row">'+
      '<h2 class="h2">👑 Коллекция титулов</h2>'+
      '<span class="chip chip-green">'+titles.length+'</span></div>'+
      '<p class="section-lead">Титулы сохраняются навсегда после окончания сезона. '+
      'Один титул виден у обоих хозяев питомца.</p>'+
      (collection||'<div class="notice">Пока пусто. Завершите испытание и нажмите «Забрать титул».</div>')+
      (data.equipped_title?'<button class="btn btn-quiet btn-wide" '+
      'data-season-clear="1">✕ Снять текущий титул</button>':'')+'</div>';
    const leaders=Array.isArray(data.top)?data.top:[];
    const rowsBoard=leaders.map((r,i)=>{
      const rank=count(r.rank)||i+1;
      const icon=rank===1?'🥇':rank===2?'🥈':rank===3?'🥉':'🏅';
      const mine=Number(r.pet_id)===Number(pet.id);
      return '<div class="season-leader '+(mine?'mine':'')+'"><span>'+icon+' '+rank+'</span>'+
        '<div><strong>'+esc(r.pet_name||"Питомец")+'</strong><small>'+
        (mine?'🐾 Ваш питомец':'Участник чата')+'</small></div>'+
        '<b>'+count(r.score)+' ⭐</b></div>';
    }).join('');
    const leaderboard='<div class="panel season-leaderboard"><div class="row">'+
      '<h2 class="h2">🏆 Рейтинг сезона</h2>'+
      '<button class="btn btn-quiet" data-season-refresh="1">🔄</button></div>'+
      '<p class="section-lead">'+count(data.participants)+' питомцев участвуют в рейтинге вашего чата.</p>'+
      (rowsBoard||'<p class="subtitle">Пока ни один питомец не набрал сезонных очков.</p>')+
      '<p class="subtitle">Очки считаются только по подтверждённым игровым действиям. '+
      'Титулы не дают ирисок, XP или преимущество в рейтинге.</p></div>';
    return header+hero+challengesView+collectionView+leaderboard+
      '<button class="btn btn-quiet btn-wide" data-click="league">🏅 Недельная лига →</button>'+
      '<button class="btn btn-quiet btn-wide" data-click="season-back">← Назад</button>';
  }
  window.PetsSeason={renderSeason,remaining};
})();
