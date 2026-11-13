/* PETS WORLD 1.7 — read-only achievements and weekly league. */
(function () {
  const esc=x=>String(x??"").replace(/[&<>"']/g,c=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
  const num=x=>Math.max(0,Math.floor(Number(x)||0));
  const pct=(current,target)=>target>0?Math.min(100,Math.round(num(current)/num(target)*100)):0;
  const asList=value=>Array.isArray(value)?value:[];

  function normalize(data) {
    const v=data?.league || data || {};
    return {
      week: v.week || {start: v.week_start, end: v.week_end},
      achievements: asList(v.achievements || v.badges || v.milestones),
      board: asList(v.leaderboard || v.ranking || v.top || v.standings || v.leaders),
      mine: v.mine || v.my_rank || v.your_rank || null,
      rules: v.rules || {},
    };
  }
  function formatWeek(week) {
    if(typeof week==="string") return week;
    if(week.label) return week.label;
    if(week.start && week.end) return week.start+" — "+week.end;
    if(week.week_start && week.week_end) return week.week_start+" — "+week.week_end;
    return "Понедельник — воскресенье";
  }
  function renderLeague(data,pet,serverNow) {
    const header='<div class="section-heading">🏆 Лига питомцев</div>'+
      '<p class="section-lead">Соревнуйтесь с другими питомцами своего чата. '+
      'Засчитываются только реальные действия, подтверждённые сервером.</p>'+
      '<div class="league-scene" aria-hidden="true">⭐ <span>🏆</span> 🐲</div>';
    if(!data) {
      return header+'<div class="panel league-loading"><div class="spinner"></div>'+
        '<p>Получаем достижения и рейтинг чата…</p>'+
        '<button type="button" class="btn btn-quiet btn-wide" data-league-refresh="1">🔄 Обновить</button></div>'+
        '<button type="button" class="btn btn-quiet btn-wide" data-click="league-back">← Назад</button>';
    }
    const state=normalize(data);
    const achieved=state.achievements.filter(a=>Boolean(
      a.unlocked || a.earned || a.completed || a.achieved || a.done
    )).length;
    const week=formatWeek(state.week);
    const summary='<div class="panel league-summary"><div class="row"><div>'+
      '<div class="eyebrow">СОРЕВНОВАНИЕ НЕДЕЛИ</div>'+
      '<div class="title">🏅 Рейтинг питомцев</div></div>'+
      '<span class="chip chip-purple">⏳ '+esc(week)+'</span></div>'+
      '<p class="subtitle">Баллы начисляются за подтверждённую активность в игре. '+
      'Рейтинг не создаёт новые ириски.</p></div>';
    const badges=state.achievements.map(a=>{
      const unlocked=Boolean(a.unlocked || a.earned || a.completed || a.achieved || a.done);
      const title=a.name || a.title || "Достижение";
      const icon=a.icon || a.emoji || "🏅";
      const description=a.description || a.info || a.hint || "";
      const current=num(a.current??a.progress??a.value??0);
      const target=num(a.target??a.goal??a.required??0);
      return '<div class="league-badge '+(unlocked?'unlocked':'locked')+'">'+
        '<span class="league-badge-icon">'+esc(icon)+'</span>'+
        '<div class="league-badge-main"><strong>'+esc(title)+'</strong>'+
          '<small>'+esc(description)+'</small>'+
          (target>0?'<div class="progress league-badge-progress" role="progressbar" '+
            'aria-label="Прогресс достижения" aria-valuemin="0" aria-valuemax="'+target+
            '" aria-valuenow="'+Math.min(current,target)+'">'+
            '<i style="width:'+pct(current,target)+'%"></i></div>'+
            '<span class="league-badge-counter">'+Math.min(current,target)+' / '+target+'</span>':'')+
        '</div><span class="league-badge-status">'+(unlocked?'✅':'🔒')+'</span></div>';
    }).join('');
    const rankRows=state.board.map((r,index)=>{
      const rank=num(r.rank)||index+1;
      const icon=rank===1?'🥇':rank===2?'🥈':rank===3?'🥉':'🏅';
      const name=r.pet_name || r.name || r.title || ('Питомец #'+num(r.pet_id||r.id));
      const score=num(r.score??r.points??r.weekly_score??r.total??0);
      const own=Boolean(r.is_mine || r.mine || r.own || Number(r.pet_id||r.id)===Number(p.id));
      const scoreLabel=String(r.score_label||'баллов');
      return '<div class="league-rank-row '+(own?'mine':'')+'">'+
        '<span class="league-rank-place">'+icon+' '+rank+'</span>'+
        '<div class="league-rank-name"><strong>'+esc(name)+'</strong>'+
        '<small>'+(own?'🐾 Ваш питомец':'Участник чата')+'</small></div>'+
        '<div class="league-rank-score">'+score+'<small>'+esc(scoreLabel)+'</small></div></div>';
    }).join('');
    const myRank=state.mine && typeof state.mine==='object'
      ? ('Место: '+num(state.mine.rank||state.mine.place||0)+
        ' · '+num(state.mine.score??state.mine.points??0)+' баллов')
      : (typeof state.mine==='number'?'Место: '+num(state.mine):'');
    return header+summary+
      '<div class="panel season-link"><div class="row"><div>'+
      '<div class="h2">🎟 Сезонные испытания</div>'+
      '<p class="subtitle">Призы-титулы за приключения, задания и бизнесы</p></div>'+
      '<button class="btn btn-primary" data-click="season">Открыть →</button></div></div>'+
      '<div class="panel league-collection"><div class="row"><div>'+
        '<div class="eyebrow">КОЛЛЕКЦИЯ</div>'+
        '<h2 class="h2">🎖 Достижения '+esc(p.name)+'</h2></div>'+
        '<span class="chip chip-green">'+achieved+'/'+state.achievements.length+'</span></div>'+
        '<p class="section-lead">Открывайте достижения, играя вместе. Награды не нужно покупать.</p>'+
        (badges||'<p class="subtitle">Здесь появятся ваши достижения.</p>')+'</div>'+
      '<div class="panel league-leaderboard"><div class="row"><strong>📊 Таблица лидеров</strong>'+
        '<button type="button" class="btn btn-quiet" data-league-refresh="1">🔄</button></div>'+
        (myRank?'<p class="league-mine">'+esc(myRank)+'</p>':'')+
        (rankRows||'<div class="notice">Пока нет участников с результатами за эту неделю.</div>')+'</div>'+
      '<div class="notice">🔒 Лига показывает только питомцев вашего Telegram-чата. '+
      'Таблица обновляется после реальных игровых действий.</div>'+
      '<button type="button" class="btn btn-quiet btn-wide" data-click="league-back">← Назад</button>';
  }
  window.PetsLeague={renderLeague};
})();
