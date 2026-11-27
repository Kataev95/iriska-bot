/* Server-owned arcade challenges; the browser renders, never awards points. */
(function(root){
  "use strict";
  const esc=x=>String(x??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const score=x=>x==null?"—":Math.max(0,Math.min(100,Number(x)||0))+"/100";
  const seconds=x=>Math.max(0,Math.ceil(x))+" с";
  function phase(run,now){
    if(!run||now>run.expires_at)return "expired";
    if(!run.mine||!run.state)return "partner";
    const s=run.state;
    if(run.game==="memory")return now<s.reveal_until?"reveal":"recall";
    if(run.game==="maze")return "maze";
    return now<s.ready?"waiting":now<=s.until?"ready":"late";
  }
  function status(run,now){
    const p=phase(run,now),s=run?.state||{};
    if(p==="expired")return "Время истекло";
    if(p==="partner")return "Сейчас играет второй владелец";
    if(run.game==="memory")return p==="reveal"?"Запомни порядок! "+seconds(s.reveal_until-now):"Повтори символ "+(s.round+1)+" из 6";
    if(run.game==="catch")return p==="waiting"?"Предмет летит…":p==="ready"?"Лови сейчас!":"Предмет упал. Следующий раунд.";
    if(run.game==="reaction")return p==="waiting"?"Жди зелёного сигнала":p==="ready"?"НАЖИМАЙ!":"Сигнал погас";
    return "Ходов: "+s.moves+" · выход 🏁";
  }
  function controls(run,now,config){
    const s=run.state,p=phase(run,now);
    if(run.game==="catch"){
      return '<div class="arcade-lanes" data-arcade-lanes>'+[0,1,2].map(i=>'<div class="arcade-lane"><div class="arcade-track">'+
        (i===s.lane?'<span class="arcade-falling" data-arcade-falling aria-hidden="true">🍎</span>':"")+
        '<span class="arcade-basket" aria-hidden="true">🧺</span></div><button type="button" class="btn arcade-lane-btn" data-arcade-move="'+i+'" data-arcade-type="number"'+(p!=="ready"?" disabled":"")+' aria-label="Ловить, дорожка '+(i+1)+'">'+(i+1)+'</button></div>').join("")+'</div>'+
        '<button type="button" class="btn btn-quiet btn-wide" data-arcade-move="-1" data-arcade-type="number"'+(p!=="late"?" disabled":"")+'>Следующий раунд</button>';
    }
    if(run.game==="memory"){
      return '<div class="arcade-memory" data-arcade-memory aria-label="Последовательность для запоминания">'+
        (p==="reveal"&&s.sequence?s.sequence.map(i=>'<span>'+esc(config.symbols[i])+'</span>').join(""):"<span>?</span>".repeat(6))+'</div>'+
        '<div class="arcade-memory-keys">'+config.symbols.map((symbol,i)=>'<button type="button" class="btn" data-arcade-move="'+i+'" data-arcade-type="number"'+(p!=="recall"?" disabled":"")+' aria-label="Символ '+esc(symbol)+'">'+esc(symbol)+'</button>').join("")+'</div>';
    }
    if(run.game==="reaction"){
      return '<button type="button" data-arcade-move="tap" class="arcade-signal signal-'+p+'" aria-label="Проверка реакции"><span data-arcade-signal>'+ (p==="ready"?"⚡":p==="late"?"↻":"●")+'</span><b data-arcade-signal-label>'+ (p==="ready"?"ЖМИ!":p==="late"?"ДАЛЬШЕ":"ЖДИ")+'</b></button>';
    }
    const cells=s.grid.flatMap((row,r)=>Array.from(row,(cell,c)=>{
      const player=r===s.row&&c===s.col,exit=r===5&&c===5;
      return '<span class="maze-cell '+(cell==="#"?"wall":"path")+(player?" player":"")+(exit?" exit":"")+'">'+(player?"🐾":exit?"🏁":"")+'</span>';
    })).join("");
    const moves=[["up","↑",-1,0],["left","←",0,-1],["down","↓",1,0],["right","→",0,1]];
    return '<div class="arcade-maze" role="img" aria-label="Лабиринт, позиция '+(s.row+1)+', '+(s.col+1)+', выход 6, 6">'+cells+'</div>'+
      '<div class="arcade-directions">'+moves.map(([key,label,dr,dc])=>'<button type="button" class="btn dir-'+key+'" data-arcade-move="'+key+'"'+(s.grid[s.row+dr]?.[s.col+dc]!=="."?" disabled":"")+' aria-label="'+({up:"Вверх",down:"Вниз",left:"Влево",right:"Вправо"}[key])+'">'+label+'</button>').join("")+'</div>';
  }
  function render(p,config,now){
    const a=p.arcade;
    if(!a||!config)return '<div class="panel">Игровой зал недоступен. Обновите игру.</div>';
    const run=a.active,active=run&&now<=run.expires_at;
    const game=active&&config.games.find(x=>x.code===run.game);
    let html='<div class="arcade-banner"><div><div class="eyebrow">ИГРОВОЙ ЗАЛ</div><h1>PETS Arcade</h1><p>Играй. Побеждай. Ставь рекорды.</p></div><span aria-hidden="true">🎮</span></div>'+
      '<div class="arcade-rewards"><span>⭐ Награды XP сегодня</span><strong>'+a.rewards_remaining+' / '+config.rewards+'</strong></div>';
    if(active&&game){
      const s=run.state;
      html+='<section class="panel arcade-game" data-arcade-run="'+run.id+'"><div class="arcade-row"><strong>'+esc(game.icon+" "+game.name)+'</strong><span class="chip" data-arcade-time>'+seconds(run.expires_at-now)+'</span></div>';
      if(!run.mine||!s){
        html+='<p class="subtitle">Второй владелец уже играет. Результат будет общим.</p><button type="button" class="btn btn-wide" data-arcade-refresh="1">Обновить</button>';
      }else{
        const total=run.game==="catch"?5:run.game==="reaction"?3:run.game==="memory"?6:null;
        const tally=run.game==="catch"?s.points:run.game==="memory"?Math.round(s.points*100/6):run.game==="reaction"&&s.round?Math.round(s.points/s.round):null;
        html+='<div class="arcade-round"><span>'+(total?"Раунд "+(s.round+1)+" / "+total:"Ходы "+s.moves+" / "+config.max_moves)+'</span>'+(tally!==null?'<b>'+score(tally)+'</b>':"")+'</div>'+
          '<progress class="game-progress arcade-progress" aria-label="Прогресс игры" max="'+(total||config.max_moves)+'" value="'+(total?s.round:s.moves)+'"></progress>'+
          '<div class="arcade-status" data-arcade-status aria-live="polite">'+esc(status(run,now))+'</div>'+
          controls(run,now,config)+
          '<p class="arcade-feedback" data-arcade-feedback aria-live="polite"></p>'+
          '<button type="button" class="btn btn-quiet btn-wide" data-arcade-cancel="1">Завершить без награды</button>';
      }
      html+='</section>';
    }else{
      if(a.result){
        const result=a.result,def=config.games.find(x=>x.code===result.game);
        html+='<section class="panel arcade-result"><div class="eyebrow">ПОСЛЕДНЯЯ ИГРА</div><strong>'+esc(def?.name||"Аркада")+'</strong><b>'+esc(result.outcome==="completed"?score(result.score):result.outcome==="expired"?"Время истекло":"Игра прервана")+'</b>'+
          (result.xp?'<span class="chip">+'+result.xp+' XP</span>':"")+'</section>';
      }
      html+='<div class="arcade-grid">'+config.games.map(def=>{
        const rec=a.records[def.code]||{};
        return '<section class="panel arcade-card"><span class="arcade-icon">'+esc(def.icon)+'</span><h2>'+esc(def.name)+'</h2><p>'+esc(def.description)+'</p>'+
          '<div class="arcade-record"><span>Сегодня <b>'+score(rec.today)+'</b></span><span>Рекорд <b>'+score(rec.best)+'</b></span></div>'+
          '<button type="button" class="btn btn-primary btn-wide" data-arcade-start="'+esc(def.code)+'"'+(!p.best_streak||!rec.remaining?" disabled":"")+'>'+
          (!p.best_streak?"После вылупления":rec.remaining?"Играть · "+rec.remaining+"/"+config.attempts:"Попытки закончились")+'</button></section>';
      }).join("")+'</div>';
    }
    return html+'<p class="notice">Бесплатно: '+config.attempts+' попытки в каждой игре за день на питомца. За результат от '+config.reward_score+'/100 — '+config.reward_xp+' XP, до '+config.rewards+' наград в день. Рекорды общие; ириски не начисляются. Время реакции зависит от соединения.</p><button type="button" class="btn btn-quiet btn-wide" data-click="arcade-back">← Назад</button>';
  }
  const api=Object.freeze({render,phase,status,seconds});
  if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.PetsArcade=api;
})(typeof window!=="undefined"?window:globalThis);
