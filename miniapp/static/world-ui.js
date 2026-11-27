/* PETS WORLD 2.0 — presentation helpers. No network, storage or game writes. */
(function(root){
  "use strict";
  const paths=Object.freeze({
    paw:'<ellipse cx="7" cy="8" rx="2.6" ry="3.3" transform="rotate(-25 7 8)"/><ellipse cx="17" cy="8" rx="2.6" ry="3.3" transform="rotate(25 17 8)"/><ellipse cx="3.5" cy="13" rx="2.1" ry="2.8" transform="rotate(-30 3.5 13)"/><ellipse cx="20.5" cy="13" rx="2.1" ry="2.8" transform="rotate(30 20.5 13)"/><path d="M12 12c-3 0-3.8 2.8-6 4.7C2.8 20 7 23 12 20.5 17 23 21.2 20 18 16.7 15.8 14.8 15 12 12 12Z"/>',
    world:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c-5 5-5 13 0 18M12 3c5 5 5 13 0 18"/>',
    games:'<path d="M7 6h10c3 0 4 2 5 8l.5 3c.4 3-2.6 4-4.4 2l-3.4-3h-5.4l-3.4 3c-1.8 2-4.8 1-4.4-2l.5-3C3 8 4 6 7 6Z"/><path d="M7 10v6m-3-3h6m6-1h.1m3 3h.1"/>',
    trade:'<path d="M3 4h3l3 11h10l3-8H7M10 19h.1m8 0h.1"/><circle cx="10" cy="20" r="1"/><circle cx="18" cy="20" r="1"/>',
    club:'<path d="M7 3h10v6a5 5 0 0 1-10 0V3ZM7 5H3v3c0 3 2 4 5 4m9-7h4v3c0 3-2 4-5 4m-4 2v5m-5 2h10"/>',
    bag:'<rect x="5" y="6" width="14" height="15" rx="4"/><path d="M9 6V4a3 3 0 0 1 6 0v2M5 11h14m-10 4h6v6"/>',
    candy:'<path d="m8 7-5-3 1 6-2 4 6-1m8 4 5 3-1-6 2-4-6 1"/><rect x="6" y="6" width="12" height="12" rx="6" transform="rotate(-35 12 12)"/><path d="m10 8 5 7"/>',
    feed:'<path d="M5 4v7m3-7v7m3-7v7c0 3-6 3-6 0m3 2v8m10-17c-4 0-5 9 0 9h1m0-9v17"/>',
    play:'<circle cx="12" cy="12" r="9"/><path d="M5 6c5 1 8 4 9 9m5 3c-5-1-8-4-9-9"/>',
    sleep:'<path d="M19.5 15.6A8.5 8.5 0 0 1 8.4 4.5a8.6 8.6 0 1 0 11.1 11.1Z"/><path d="M17 3v5m-2.5-2.5h5"/>',
    heart:'<path d="M12 20 4 12C-2 5 7 0 12 7c5-7 14-2 8 5Z"/>',
    bolt:'<path d="m14 2-9 12h6l-1 8 9-12h-6Z"/>',
    star:'<path d="m12 2 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1Z"/>',
    flame:'<path d="M12 2c1 5 7 7 7 13a7 7 0 0 1-14 0c0-3 2-6 4-8 0 4 2 5 3 5 2-3 1-6 0-10Z"/>',
    task:'<rect x="5" y="4" width="14" height="18" rx="3"/><path d="M9 4V2h6v2m-6 8 2 2 4-4m-6 7h6"/>',
    profile:'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2Z"/>',
    compass:'<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5Z"/>',
    house:'<path d="m2 11 10-9 10 9M5 9v12h14V9M10 21v-7h4v7"/>',
    business:'<path d="M3 21V8l7 4V6l7 4V3h4v18H3Zm4-5v2m5-2v2m5-2v2"/>',
    shop:'<path d="M4 10v11h16V10M2 10l3-7h14l3 7M2 10c0 5 5 5 5 0 0 5 5 5 5 0 0 5 5 5 5 0 0 5 5 5 5 0M10 21v-5h4v5"/>',
    wardrobe:'<path d="M9 4 3 7l2 5 3-1v10h8V11l3 1 2-5-6-3c0 4-6 4-6 0Z"/>',
    studio:'<path d="M4 20 17 7l3 3L7 23H3Zm11-15 2-2 4 4-2 2M5 3v6m-3-3h6m12 10v5m-2.5-2.5h5"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 2v6m10-6v6M3 11h18m-13 5h2m4 0h2"/>',
    sun:'<circle cx="12" cy="12" r="4"/><path d="M12 1v3m0 16v3M1 12h3m16 0h3M4 4l2 2m12 12 2 2M4 20l2-2M18 6l2-2"/>',
    check:'<path d="m5 12 4 4 10-10"/>',
    arrow:'<path d="m9 5 7 7-7 7"/>'
  });
  const routes=Object.freeze({home:"paw",world:"world",games:"games",trade:"trade",club:"club",
    profile:"profile",growth:"star",wardrobe:"wardrobe",studio:"studio",trips:"compass",
    house:"house",business:"business",tasks:"task",shop:"shop",market:"trade",league:"club",season:"calendar",inventory:"bag"});
  function icon(name){
    const body=Object.prototype.hasOwnProperty.call(paths,name)?paths[name]:paths.star;
    return '<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'+body+'</svg>';
  }
  function routeIcon(route){return icon(Object.prototype.hasOwnProperty.call(routes,route)?routes[route]:"star");}
  function phase(hour){
    if(!Number.isInteger(hour)||hour<0||hour>23)return "day";
    return hour<8?"night":hour<11?"morning":hour<18?"day":hour<22?"evening":"night";
  }
  const labels=Object.freeze({morning:"Доброе утро",day:"Солнечная долина",evening:"Тихий вечер",night:"Звёздная ночь"});
  function phaseLabel(hour){return labels[phase(hour)];}
  function percent(v){const n=Number(v);return Number.isFinite(n)?Math.min(100,Math.max(0,Math.round(n))):0;}
  function needs(value,label,kind){
    const n=percent(value);
    return '<div class="need need-'+kind+(n<30?' is-low':n<60?' is-medium':'')+'">'+
      '<span class="need-label">'+icon(kind)+' '+label+'</span><b>'+n+'%</b>'+
      '<progress class="game-progress" max="100" value="'+n+'" aria-label="'+label+'">'+n+'%</progress></div>';
  }
  function landscape(){
    return '<svg class="scene-landscape" viewBox="0 0 480 300" preserveAspectRatio="xMidYMax slice" aria-hidden="true" focusable="false">'+
      '<circle class="scene-orb" cx="375" cy="66" r="33"/>'+
      '<path class="scene-cloud cloud-a" d="M-50 64Q-15 38 14 56Q38 22 73 52Q108 39 126 67Z"/>'+
      '<path class="scene-cloud cloud-b" d="M332 114Q352 84 380 106Q401 75 427 102Q457 86 483 118Z"/>'+
      '<path class="scene-hill-far" d="M0 167Q56 90 132 153Q204 83 288 155Q370 80 480 162V300H0Z"/>'+
      '<path class="scene-hill-mid" d="M0 207Q77 155 164 203Q258 135 360 195Q429 158 480 199V300H0Z"/>'+
      '<path class="scene-hill-near" d="M0 251Q105 206 232 245Q375 199 480 239V300H0Z"/>'+
      '<g class="scene-trees"><path d="M30 158 4 217h15L0 250h59l-20-33h17Z"/><path d="M448 153 421 214h14l-20 35h66l-21-35h14Z"/><path d="M77 186 58 229h10l-16 25h49l-16-25h10Z"/><path d="M408 186 390 229h9l-15 25h48l-15-25h9Z"/></g>'+
      '<g class="scene-stars"><circle cx="42" cy="38" r="1.7"/><circle cx="119" cy="72" r="2"/><circle cx="225" cy="31" r="1.5"/><circle cx="301" cy="58" r="1.3"/><circle cx="441" cy="34" r="2"/><path d="M68 104v10m-5-5h10M315 99v8m-4-4h8"/></g>'+
      '</svg>';
  }
  const api=Object.freeze({icon,routeIcon,phase,phaseLabel,percent,needs,landscape});
  if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.PetsWorldUI=api;
})(typeof window!=="undefined"?window:globalThis);
