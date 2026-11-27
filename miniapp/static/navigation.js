/* PETS WORLD 2.0 — pure navigation state; no DOM or game mutations. */
(function (root) {
  "use strict";
  const tabs = Object.freeze([
    ["home","🐾","Питомец"], ["world","🌍","Мир"],
    ["games","🎮","Игры"], ["trade","🛒","Торговля"], ["club","🏆","Клуб"]
  ]);
  const owners = Object.freeze({
    home:"home", profile:"home", growth:"home", wardrobe:"home", studio:"home",
    world:"world", trips:"world", house:"world", farm:"world", fishing:"world",
    games:"games", tasks:"games", business:"games",
    trade:"trade", shop:"trade", market:"trade",
    club:"club", season:"club", league:"club", inventory:null
  });
  const known = route => Object.prototype.hasOwnProperty.call(owners, route);
  const isRoot = route => tabs.some(([id]) => id === route);
  function create() {
    let path = ["home"];
    const current = () => path[path.length-1];
    return {
      current,
      activeRoot: () => {
        for (let i=path.length-1;i>=0;i--) {
          if (owners[path[i]]) return owners[path[i]];
        }
        return "home";
      },
      select(route) {
        if (isRoot(route)) path=[route];
        return current();
      },
      open(route) {
        if (!known(route)) return current();
        const previous=path.lastIndexOf(route);
        if (previous>=0) path=path.slice(0,previous+1);
        else path.push(route);
        return current();
      },
      back() {
        if (path.length>1) path.pop();
        else path=["home"];
        return current();
      }
    };
  }
  const api=Object.freeze({tabs,create});
  if (typeof module !== "undefined" && module.exports) module.exports=api;
  else root.PetsNavigation=api;
})(typeof window !== "undefined" ? window : globalThis);
