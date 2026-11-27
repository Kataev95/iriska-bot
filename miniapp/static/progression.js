/* Long-term growth screen; only renders server-owned progression state. */
(function(root){
  "use strict";
  const esc=x=>String(x??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const branchNames={adventure:"🧭 Приключения",care:"💗 Забота",craft:"⚙️ Хозяйство"};
  const materialIcon={wood:"🪵",leaf:"🍃",stone:"🪨",crystal:"💎"};
  function percent(p){return p.next_xp==null?100:Math.max(0,Math.min(100,Math.round((p.xp-p.level_start_xp)/Math.max(1,p.next_xp-p.level_start_xp)*100)));}
  function skillStatus(p,skill){
    const state=p.progression,r=state.skills[skill.code]||0;
    if(r>=skill.max)return {rank:r,allowed:false,label:"Максимум"};
    if(!p.best_streak)return {rank:r,allowed:false,label:"После вылупления"};
    if(p.level<skill.level)return {rank:r,allowed:false,label:"Уровень "+skill.level};
    if(skill.requires&&(state.skills[skill.requires]||0)<skill.rank)return {rank:r,allowed:false,label:"Предыдущий навык · "+skill.rank};
    if(!state.points)return {rank:r,allowed:false,label:"Нет очков"};
    return {rank:r,allowed:true,label:"Улучшить · 1 очко"};
  }
  function section(title,html){return '<section class="panel growth-section"><h2>'+esc(title)+'</h2>'+html+'</section>';}
  function render(p,config){
    const s=p.progression,c=config.progression;
    if(!s||!c)return '<div class="panel">Обновите данные питомца.</div>';
    const stage=s.evolution,next=c.stages.find(x=>x.level>stage.level);
    const spec=c.specializations.find(x=>x.code===s.specialization);
    const materials=config.materials||[];
    const materialText=m=>Object.entries(m).map(([code,qty])=>'<span class="growth-material" title="'+esc(materials.find(x=>x.code===code)?.name||code)+'">'+(materialIcon[code]||"📦")+' '+qty+'</span>').join("");
    return '<div class="growth-banner"><div class="eyebrow">ПУТЬ ПИТОМЦА</div><div class="growth-banner-main"><span class="growth-emblem">'+esc(stage.icon)+'</span><div><h1>'+esc(stage.name)+'</h1><p>Уровень '+p.level+' / '+c.max_level+'</p></div></div>'+
      '<div class="growth-xp"><span>'+p.xp+' XP</span><span>'+(p.next_xp==null?"Максимум":"До следующего: "+Math.max(0,p.next_xp-p.xp)+' XP')+'</span></div>'+
      '<progress class="game-progress" max="100" value="'+percent(p)+'" aria-label="Опыт текущего уровня"></progress>'+
      '<p class="subtitle">'+(next?"Следующая стадия: "+esc(next.name)+" · "+next.level+" ур.":"Высшая стадия достигнута")+'</p></div>'+
      '<div class="growth-stats"><div><small>🔥 Серия</small><b>'+p.streak+'</b></div><div><small>🏅 Рекорд</small><b>'+p.best_streak+'</b></div><div><small>🧊 Заморозки</small><b>'+p.freezes+'</b></div></div>'+
      section("🌟 Эволюция",'<div class="evolution-track">'+c.stages.map(x=>'<div class="evolution-step '+(stage.level>=x.level?"unlocked":"locked")+(stage.level===x.level?" current":"")+'"><span>'+esc(x.icon)+'</span><div><b>'+esc(x.name)+'</b><small>Уровень '+x.level+'</small></div><strong>'+(stage.level===x.level?"Сейчас":stage.level>x.level?"✓":"🔒")+'</strong></div>').join("")+'</div>')+
      section("🎓 Профессия",p.profession?'<p class="notice">✅ '+esc(p.profession_title)+' · '+esc(config.professions.find(x=>x.code===p.profession)?.effect||"")+'</p>':
        '<p class="subtitle">Общий постоянный выбор на 5-м уровне.</p>'+config.professions.map(job=>'<div class="growth-option"><div><b>'+esc(job.name)+'</b><small>'+esc(job.effect)+'</small></div><button type="button" class="btn btn-primary" data-profession="'+esc(job.code)+'"'+(p.level<5||!p.best_streak?" disabled":"")+'>'+(p.level<5?"Ур. 5":"Выбрать")+'</button></div>').join(""))+
      section("🌳 Дерево навыков",'<div class="growth-points"><span>Свободные очки</span><b>'+s.points+' / '+s.earned_points+'</b></div><p class="subtitle">1 очко каждые 5 уровней. Выбор общий, сброса пока нет.</p>'+
        Object.keys(branchNames).map(branch=>'<div class="skill-branch"><h3>'+branchNames[branch]+'</h3>'+c.skills.filter(x=>x.branch===branch).map(skill=>{
          const st=skillStatus(p,skill);
          return '<div class="skill-node"><div class="skill-top"><span class="skill-icon">'+esc(skill.icon)+'</span><div><b>'+esc(skill.name)+'</b><small>'+esc(skill.effect)+'</small></div><span class="chip">'+st.rank+'/'+skill.max+'</span></div>'+
            '<div class="skill-pips" aria-hidden="true">'+Array.from({length:skill.max},(_,i)=>'<i'+(i<st.rank?' class="filled"':'')+'></i>').join("")+'</div>'+
            '<button type="button" class="btn btn-wide '+(st.allowed?'btn-primary':'btn-quiet')+'" data-skill="'+esc(skill.code)+'" data-rank="'+st.rank+'"'+(!st.allowed?' disabled':'')+'>'+esc(st.label)+'</button></div>';
        }).join("")+'</div>').join(""))+
      section("✨ Специализация",spec?'<div class="notice">'+esc(spec.icon+" "+spec.name+" · "+spec.effect)+'</div>':
        '<p class="subtitle">Одна дополнительная способность на '+c.specialization_level+'-м уровне. Профессия сохранится.</p>'+c.specializations.map(x=>'<div class="growth-option"><div><b>'+esc(x.icon+" "+x.name)+'</b><small>'+esc(x.effect)+'</small></div><button type="button" class="btn btn-primary" data-specialization="'+esc(x.code)+'"'+(p.level<c.specialization_level||!p.best_streak?' disabled':'')+'>'+(p.level<c.specialization_level?'Ур. '+c.specialization_level:'Выбрать')+'</button></div>').join(""))+
      section("🎁 Награды за уровни",'<p class="subtitle">Каждые 10 уровней — общие материалы. Получение один раз на питомца.</p>'+
        c.milestones.map(x=>{const done=s.claimed.includes(x.level),ready=p.best_streak&&p.level>=x.level&&!done;
          return '<div class="growth-milestone'+(ready?' ready':'')+'"><div><b>Уровень '+x.level+'</b><div class="growth-materials">'+materialText(x.materials)+'</div></div><button type="button" class="btn '+(ready?'btn-primary':'btn-quiet')+'" data-level-claim="'+x.level+'"'+(!ready?' disabled':'')+'>'+(done?'Получено':ready?'Забрать':'🔒')+'</button></div>';
        }).join(""))+
      '<button type="button" class="btn btn-quiet btn-wide" data-click="growth-back">← Назад</button>';
  }
  const api=Object.freeze({render,percent,skillStatus});
  if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.PetsProgression=api;
})(typeof window!=="undefined"?window:globalThis);
