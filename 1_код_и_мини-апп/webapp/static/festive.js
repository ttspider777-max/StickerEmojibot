/* Новогодний слой Emoji Studio: кот, гирлянды, снег, настройки, обратный отсчёт.
   Подключается до app.js и отдаёт ему window.Festive. Работает без зависимостей. */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const NS = 'http://www.w3.org/2000/svg';
  const tg = window.Telegram && window.Telegram.WebApp;
  const reduced = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ============================ НАСТРОЙКИ ============================ */
  /* при «уменьшении движения» в системе снег выключен по умолчанию, огни ровные, кот не анимируется */
  const DEF = { snow: !reduced, garland: true, cat: true, catAnim: !reduced, lights: reduced ? 'steady' : 'twinkle', density: reduced ? 'low' : 'normal', sound: false, haptics: true };
  let P = { ...DEF };
  try { P = { ...DEF, ...JSON.parse(localStorage.getItem('es_prefs') || '{}') }; } catch (e) {}
  const save = () => { try { localStorage.setItem('es_prefs', JSON.stringify(P)); } catch (e) {} };

  function applyPrefs() {
    const b = document.body, c = b.classList;
    c.toggle('no-snow', !P.snow);
    c.toggle('no-garland', !P.garland);
    c.toggle('no-cat', !P.cat);
    c.toggle('no-cat-anim', !P.catAnim);
    ['twinkle', 'steady', 'chase', 'wave'].forEach((m) => c.toggle('lights-' + m, P.lights === m));
    snowResize();
  }

  /* ============================ КОТ ============================ */
  /* Рыжий полосатый кот в шапке Санты. Один набор разметки на всё:
     — «logo» (голова) для значков и логотипа;
     — «cat-sit» (весь кот с шарфом и подарком) для заставок и пустых экранов;
     — живой кот над таб-баром: следит за пальцем, моргает, мяукает, засыпает. */
  /* Плоская иллюстрация с тушевым контуром. Палитра (6 цветов):
     чернила #2b1608 · рыжий #f28c28 · тень #d9691a · сливочный #ffe6bd · розовый #ff8fa3 · красный #e5384c (+ тень #b21f38) */
  const INK = '#2b1608', OR = '#f28c28', ORD = '#d9691a', CR = '#ffe6bd', PK = '#ff8fa3', RD = '#e5384c', RDD = '#b21f38', STR = '#9a420c';
  const ol = `stroke="${INK}" stroke-width="2.3" stroke-linejoin="round" stroke-linecap="round"`;

  function eye(cx, side) {
    const k = side === 'l' ? 1 : -1;
    return `<g class="eye-${side}">
    <g class="pupils">
      <ellipse cx="${cx}" cy="62" rx="6.4" ry="8.4" fill="${INK}"/>
      <circle cx="${cx + 2.2}" cy="58.6" r="2.7" fill="#fff"/>
      <circle cx="${cx - 2}" cy="65.4" r="1.2" fill="#fff"/>
    </g>
    <ellipse class="lid ${side}" cx="${cx}" cy="62" rx="8.4" ry="10.4" fill="${OR}"/>
    <path d="M${cx - 8.4 * k} ${side === 'l' ? 49.6 : 47.2}l${15 * k} ${side === 'l' ? -2.2 : 1.8}" stroke="${STR}" stroke-width="2.4" stroke-linecap="round" fill="none"/></g>`;
  }

  function headInner() {
    return `
  <g class="head-g">
   <g class="ear-l">
    <path d="M20 49C13 35 11 20 15.5 7L47 28z" fill="${OR}" ${ol}/>
    <path d="M22.6 39C20 30 19 21 21 14.6L36 27.4z" fill="${PK}"/>
   </g>
   <g class="ear-r">
    <path d="M101 48C106 39 107.5 28 102.6 15.5L97.5 19.4 98.4 8.5 72 28z" fill="${OR}" ${ol}/>
    <path d="M97.4 38C99.6 31 99.4 25 97.8 21.5L95 24.6 95.4 15.5 82 27.4z" fill="${PK}"/>
   </g>
   <path d="M60 22C86 22 106 38 106 62c0 11-5 18-9 22l5 6-10-1 2 7-10-4c-7 5-15 8-24 8s-17-3-24-8l-10 4 2-7-10 1 5-6c-4-4-9-11-9-22C14 38 34 22 60 22z" fill="${OR}"/>
   <path d="M106 62c0 11-5 18-9 22l5 6-10-1 2 7-10-4c-7 5-15 8-24 8 18 0 36-12 41-28 2-4 3-7 5-10z" fill="${ORD}"/>
   <path d="M60 22C86 22 106 38 106 62c0 11-5 18-9 22l5 6-10-1 2 7-10-4c-7 5-15 8-24 8s-17-3-24-8l-10 4 2-7-10 1 5-6c-4-4-9-11-9-22C14 38 34 22 60 22z" fill="none" ${ol}/>
   <g stroke="${STR}" stroke-width="3.2" stroke-linecap="round" fill="none">
    <path d="M60 38v10"/><path d="M52 40.4q1.2 5 3.4 7.6"/><path d="M68.4 40q-.8 3.6-.8 6.4"/><path d="M44.6 43q2 3.2 2.4 6"/>
    <path d="M15.4 60q8 1 13 4.4"/><path d="M17 69q7-1 11.6 1.4"/>
    <path d="M104.6 59q-8.4 1-13.4 5"/><path d="M103 68.4q-7-1.4-11.6 1"/><path d="M99.4 76q-5-2-8.6-.8"/>
   </g>
   <ellipse cx="49.5" cy="80.4" rx="13.4" ry="9.4" fill="${CR}"/><ellipse cx="70.5" cy="80.4" rx="13.4" ry="9.4" fill="${CR}"/><ellipse cx="60" cy="91" rx="9.6" ry="6" fill="${CR}"/>
   <path d="M55 96q5 2.4 10 0" stroke="${ORD}" stroke-width="1.4" stroke-linecap="round" fill="none" opacity=".0"/>
   <g fill="${STR}"><circle cx="45" cy="80" r="1"/><circle cx="49.4" cy="82.6" r="1"/><circle cx="43.4" cy="84" r="1"/><circle cx="75.6" cy="80.4" r="1"/><circle cx="71" cy="82.8" r="1"/></g>
   ${eye(41, 'l')}${eye(79, 'r')}
   <path d="M54.6 73.4h10.8q.6 2-1.6 4.4L60 81.4l-3.8-3.6q-2.2-2.4-1.6-4.4z" fill="${PK}" stroke="${INK}" stroke-width="1.8" stroke-linejoin="round"/>
   <path d="M57.6 75.2h3" stroke="#fff" stroke-width="1.5" stroke-linecap="round" opacity=".85"/>
   <g class="mouth-closed"><path d="M60 81.6v3.4M60 85c-2.6 3.6-7.4 3.2-8.8-.4M60 85c2.6 3.6 7.4 3.2 8.8-.4" stroke="${INK}" stroke-width="1.9" stroke-linecap="round" fill="none"/></g>
   <g class="mouth-open"><path d="M52.4 84.6c2.2-1.8 4.8-2.6 7.6-2.6s5.4.8 7.6 2.6c-.8 7-4.4 10.6-7.6 10.6s-6.8-3.6-7.6-10.6z" fill="${INK}"/><path d="M55.4 91.4c1.4-2 3-2.8 4.6-2.8s3.2.8 4.6 2.8c-1 1.8-2.8 2.8-4.6 2.8s-3.6-1-4.6-2.8z" fill="${PK}"/></g>
   <g class="whiskers" stroke="${CR}" stroke-width="1.5" stroke-linecap="round" fill="none">
    <path d="M37 77C27 73 16 72.4 4 75.4"/><path d="M36.4 81.6C26 82 15 84.4 2.6 89"/><path d="M38 85.4C29 89.6 20 94 11 99"/>
    <path d="M83 77C93 72.6 104 71.6 116.6 73.4"/><path d="M83.6 81.6C94 81.2 105 82.6 117.6 86"/>
   </g>
   <g class="hat" transform="translate(0 -3) rotate(-4 60 30)">
    <path d="M33 35C33 17 49 5 68 4c17-1 29 9 36 25L33 37z" fill="${RD}"/>
    <path d="M70 4.6C86 5 97 14 104 29L85 32C85 19 79.4 9.4 70 4.6z" fill="${RDD}"/>
    <path d="M33 35C33 17 49 5 68 4c17-1 29 9 36 25" fill="none" ${ol}/>
    <path d="M52 22q8-2 14 1" stroke="${RDD}" stroke-width="2" stroke-linecap="round" fill="none"/>
    <path d="M29 35.5h68a6.5 6.5 0 0 1 0 13H29a6.5 6.5 0 0 1 0-13z" fill="#fff"/>
    <path d="M30 42.4q31 5.6 66 0v1.4a6.4 6.4 0 0 1-6.4 6.4H36.4A6.4 6.4 0 0 1 30 43.8z" fill="#d3ddf0"/>
    <path d="M29 35.5h68a6.5 6.5 0 0 1 0 13H29a6.5 6.5 0 0 1 0-13z" fill="none" ${ol}/>
    <g class="pom"><circle cx="105.6" cy="30.4" r="8.4" fill="#fff" ${ol}/><path d="M101 33.6q3.6 3.4 8.4 1.2" stroke="#d3ddf0" stroke-width="2.2" stroke-linecap="round" fill="none"/></g>
   </g>
  </g>`;
  }

  const PAWS = `
  <g class="paws">
   ${[30, 90].map((x, i) => `<g transform="translate(${x} ${i ? 106 : 105})">
     <ellipse rx="15" ry="10.4" fill="${CR}" ${ol}/>
     <path d="M-5 1.4v6.4M0 2v6.4M5 1.4v6.4" stroke="${ORD}" stroke-width="1.8" stroke-linecap="round"/></g>`).join('')}
  </g>`;

  function sitInner() {
    return `
  <ellipse cx="120" cy="268" rx="108" ry="11" fill="#e4ecff"/>
  <g transform="translate(14 233) rotate(-3 25 20)">
   <rect x="2" y="10" width="46" height="30" rx="3" fill="${RD}" ${ol}/>
   <rect x="0" y="4" width="50" height="11" rx="3" fill="#f2566b" ${ol}/>
   <path d="M21 4h8v36h-8z" fill="#ffc933" ${ol}/>
   <path d="M25 4C15-7 7 3 21 4zM25 4C35-7 43 3 29 4z" fill="#ffc933" ${ol}/>
  </g>
  <g class="tail-g">
   <path d="M170 246C216 252 238 214 221 180C214 164 224 152 233 158" stroke="${INK}" stroke-width="22" stroke-linecap="round" fill="none"/>
   <path d="M170 246C216 252 238 214 221 180C214 164 224 152 233 158" stroke="${OR}" stroke-width="17.5" stroke-linecap="round" fill="none"/>
   <path d="M170 246C216 252 238 214 221 180C214 164 224 152 233 158" stroke="${STR}" stroke-width="17.5" stroke-dasharray="4.5 14" stroke-dashoffset="3" fill="none"/>
   <circle cx="233" cy="158" r="9.4" fill="${CR}" ${ol}/>
  </g>
  <g class="breath">
   <ellipse cx="78" cy="240" rx="27" ry="30" fill="${ORD}" ${ol}/><ellipse cx="162" cy="240" rx="27" ry="30" fill="${ORD}" ${ol}/>
   <path d="M76 264C56 238 60 176 90 146h60c30 30 34 92 14 118z" fill="${OR}" ${ol}/>
   <path d="M150 146c30 30 34 92 14 118h-22c14-28 12-80-12-118z" fill="${ORD}"/>
   <ellipse cx="116" cy="216" rx="26" ry="44" fill="${CR}"/>
   <g stroke="${STR}" stroke-width="4.4" stroke-linecap="round" fill="none">
    <path d="M64 196q11 5 20 3"/><path d="M62 214q12 6 22 3"/><path d="M63 232q10 4 19 1"/><path d="M80 177q8 3 13 1"/>
    <path d="M177 200q-10 4-18 2"/><path d="M178 220q-11 5-20 2"/>
   </g>
   <rect x="96" y="188" width="19" height="72" rx="9.5" fill="${OR}" ${ol}/><rect x="125" y="188" width="19" height="72" rx="9.5" fill="${OR}" ${ol}/>
   <path d="M97 206h13M97 220h13M130 206h13" stroke="${STR}" stroke-width="3" stroke-linecap="round"/>
   ${[105.5, 134.5].map((x) => `<g transform="translate(${x} 262)"><ellipse rx="15" ry="8.6" fill="${CR}" ${ol}/><path d="M-5 0v6M0 1v6M5 0v6" stroke="${ORD}" stroke-width="1.7" stroke-linecap="round"/></g>`).join('')}
   <path d="M130 166l17-3 7 42-24 5z" fill="${RD}" ${ol}/>
   <path d="M131 180l19-3M133 194l19-3M135 208l19-3" stroke="#fff" stroke-width="3.4" stroke-linecap="round"/>
   <path d="M82 144C102 163 138 163 158 144L164 161C140 182 100 182 76 161z" fill="${RD}" ${ol}/>
   <path d="M92 155l-5 15M106 162l-4 16M120 165v17M134 162l4 16M148 155l5 15" stroke="#fff" stroke-width="3.4" stroke-linecap="round"/>
  </g>
  <g transform="translate(60 45)">${headInner()}</g>`;
  }

  const DEFS = `
  <defs>
    <clipPath id="ceL"><ellipse cx="41" cy="62" rx="10.4" ry="11.4"/></clipPath>
    <clipPath id="ceR"><ellipse cx="79" cy="62" rx="10.4" ry="11.4"/></clipPath>
    <radialGradient id="gSnow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#ffffff"/><stop offset=".65" stop-color="#dfeaff" stop-opacity=".95"/><stop offset="1" stop-color="#9fb8e8" stop-opacity="0"/></radialGradient>
  </defs>`;

  function injectCat() {
    const sprite = $('#sprite');
    if (!sprite) return;
    sprite.insertAdjacentHTML('beforeend', `${DEFS}
      <symbol id="logo" viewBox="0 0 120 104" class="cat-svg">${headInner()}</symbol>
      <symbol id="cat-sit" viewBox="-4 0 256 284" class="cat-svg">${sitInner()}</symbol>`);
  }

  /* ---- живой кот ---- */
  const PHRASES = [
    'Мяу!', 'Мурр…', 'С наступающим! 🎄', 'Я охраняю подарки', 'Где мой подарок?', 'Хочу снежок!',
    'Ещё эмодзи! Ещё!', 'Погладь меня 🐾', 'Ёлка — мой трон', 'Мррр-мяу!', 'Я не спал. Честно.',
  ];
  let cat, bubble, heads = [], pupilEls = [], headG, lastActive = Date.now(), bubbleT, meowT;

  function mountCat() {
    cat = $('#cat');
    if (!cat) return;
    cat.innerHTML = `<svg class="cat-svg" viewBox="0 0 120 116" aria-hidden="true">${headInner()}${PAWS}</svg>
      <span class="bubble"></span><span class="zz">Z z z</span>`;
    bubble = $('.bubble', cat);
    pupilEls = [...cat.querySelectorAll('.pupils')];
    headG = $('.head-g', cat);
    cat.addEventListener('click', (e) => { e.stopPropagation(); wake(); tapCat(); });
    window.addEventListener('pointermove', (e) => { wake(); look(e.clientX, e.clientY); }, { passive: true });
    window.addEventListener('pointerdown', (e) => { wake(); look(e.clientX, e.clientY); }, { passive: true });
    window.addEventListener('keydown', wake, { passive: true });
    setInterval(() => { if (!document.hidden && Date.now() - lastActive > 35000) cat.classList.add('sleep'); }, 4000);
    setTimeout(() => say('С наступающим! 🎄'), 1600);
  }

  function look(x, y) {
    if (!cat || cat.classList.contains('sleep')) return;
    const r = cat.getBoundingClientRect();
    const cx = r.left + r.width * 0.5, cy = r.top + r.height * 0.52;
    const dx = x - cx, dy = y - cy, d = Math.hypot(dx, dy) || 1, k = Math.min(1, d / 170);
    const mx = (3.4 * k * dx) / d, my = (3.6 * k * dy) / d;
    pupilEls.forEach((p) => { p.style.transform = `translate(${mx.toFixed(2)}px,${my.toFixed(2)}px)`; });
    if (headG) headG.style.transform = `rotate(${Math.max(-5, Math.min(5, (dx / Math.max(window.innerWidth, 1)) * 9)).toFixed(2)}deg)`;
  }

  function wake() {
    lastActive = Date.now();
    if (cat && cat.classList.contains('sleep')) { cat.classList.remove('sleep'); say('М-м? Я не спал!'); }
  }

  function say(text, ms = 2300) {
    if (!bubble || !P.cat) return;
    bubble.textContent = text;
    bubble.classList.add('show');
    clearTimeout(bubbleT);
    bubbleT = setTimeout(() => bubble.classList.remove('show'), ms);
  }

  function openMouth(ms = 650) {
    if (!cat) return;
    cat.classList.add('meow');
    clearTimeout(meowT);
    meowT = setTimeout(() => cat.classList.remove('meow'), ms);
  }

  function anim(cls) {
    if (!cat) return;
    cat.classList.remove('jump', 'nudge');
    void cat.offsetWidth;
    cat.classList.add(cls);
    setTimeout(() => cat.classList.remove(cls), 800);
  }

  function hearts(n = 6) {
    if (!cat) return;
    const r = cat.getBoundingClientRect();
    for (let i = 0; i < n; i++) {
      const h = document.createElement('span');
      h.className = 'heart-fx';
      h.textContent = ['❤️', '💖', '✨', '🧡'][i % 4];
      h.style.left = r.left + r.width * (0.2 + Math.random() * 0.6) + 'px';
      h.style.top = r.top + 8 + 'px';
      h.style.setProperty('--hx', ((Math.random() * 2 - 1) * 34).toFixed(0) + 'px');
      h.style.setProperty('--hr', ((Math.random() * 2 - 1) * 30).toFixed(0) + 'deg');
      h.style.animationDelay = (i * 0.07).toFixed(2) + 's';
      document.body.appendChild(h);
      setTimeout(() => h.remove(), 1700);
    }
  }

  function haptic(t = 'light') { if (!P.haptics) return; try { tg && tg.HapticFeedback && tg.HapticFeedback.impactOccurred(t); } catch (e) {} }

  let taps = 0, tapT;
  function tapCat() {
    taps++;
    clearTimeout(tapT);
    tapT = setTimeout(() => { taps = 0; }, 900);
    anim('jump');
    openMouth();
    meowSound();
    haptic(taps >= 3 ? 'medium' : 'light');
    if (taps >= 3) { say('Мррррр… 💤→💖', 2600); hearts(8); taps = 0; return; }
    say(PHRASES[(Math.random() * PHRASES.length) | 0]);
  }

  /* простой синтез «мяу»: два осциллятора с формантным фильтром */
  let actx;
  function meowSound() {
    if (!P.sound) return;
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      const t = actx.currentTime, dur = 0.62;
      const o = actx.createOscillator(), o2 = actx.createOscillator(), f = actx.createBiquadFilter(), g = actx.createGain();
      o.type = 'sawtooth'; o2.type = 'triangle';
      const base = 430 + Math.random() * 90;
      [o, o2].forEach((x, i) => {
        x.frequency.setValueAtTime(base * (i ? 2 : 1), t);
        x.frequency.exponentialRampToValueAtTime(base * (i ? 2.9 : 1.45), t + 0.16);
        x.frequency.exponentialRampToValueAtTime(base * (i ? 1.5 : 0.75), t + dur);
      });
      f.type = 'bandpass'; f.Q.value = 5;
      f.frequency.setValueAtTime(900, t);
      f.frequency.exponentialRampToValueAtTime(2100, t + 0.2);
      f.frequency.exponentialRampToValueAtTime(1000, t + dur);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.32, t + 0.07);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.connect(f); o2.connect(f); f.connect(g); g.connect(actx.destination);
      o.start(t); o2.start(t); o.stop(t + dur + 0.05); o2.stop(t + dur + 0.05);
    } catch (e) {}
  }

  function react(kind, text) {
    if (!cat || !P.cat) return;
    wake();
    if (kind === 'tab') { anim('nudge'); }
    else if (kind === 'ok') { anim('jump'); openMouth(900); hearts(9); meowSound(); say(text || 'Готово! Мррр 🎉', 2800); }
    else if (kind === 'err') { anim('nudge'); say(text || 'Ой… попробуй ещё раз', 2400); }
    else if (kind === 'say') { say(text, 2600); openMouth(500); }
  }

  /* ============================ ГИРЛЯНДА ============================ */
  /* Плоская рукотворная гирлянда: тёмный шнур, пушистые еловые веточки пучками,
     ретро-лампочки C7 с контуром, красные банты. 4 цвета ламп, без градиентов и бликов-«глянца». */
  const BULBS = ['#ff4757', '#ffc933', '#2ed573', '#3aa0ff'];
  const mulberry = (a) => () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const INK2 = '#14090a';

  function swag(x0, x1, y0, sag) {
    const cx = (x0 + x1) / 2, cy = y0 + sag * 2, N = 140, pts = [];
    let len = 0, px = x0, py = y0;
    for (let i = 0; i <= N; i++) {
      const t = i / N, a = (1 - t) * (1 - t), b = 2 * (1 - t) * t, c = t * t;
      const x = a * x0 + b * cx + c * x1, y = a * y0 + b * cy + c * y0;
      if (i) len += Math.hypot(x - px, y - py);
      const dx = 2 * (1 - t) * (cx - x0) + 2 * t * (x1 - cx), dy = 2 * (1 - t) * (cy - y0) + 2 * t * (y0 - cy);
      pts.push({ x, y, l: len, a: Math.atan2(dy, dx) });
      px = x; py = y;
    }
    const at = (s) => { let i = 0; while (i < N && pts[i].l < s) i++; return pts[Math.min(i, N)]; };
    return { d: `M${x0.toFixed(1)} ${y0}Q${cx.toFixed(1)} ${cy.toFixed(1)} ${x1.toFixed(1)} ${y0}`, len, at };
  }

  /* еловая веточка: веер из трёх игл + короткая «ножка» */
  function sprig(x, y, ang, s, dark) {
    const g = dark ? '#1d6b3f' : '#38b26b';
    const n = (a, l) => `<path d="M0 0L${(Math.cos(a) * l).toFixed(1)} ${(Math.sin(a) * l).toFixed(1)}" stroke="${g}" stroke-width="2.6" stroke-linecap="round"/>`;
    return `<g transform="translate(${x.toFixed(1)} ${y.toFixed(1)}) rotate(${ang.toFixed(0)}) scale(${s.toFixed(2)})">${n(-0.75, 9)}${n(0, 11)}${n(0.75, 9)}</g>`;
  }

  function bow(x, y, r) {
    return `<g transform="translate(${x.toFixed(1)} ${(y + 1).toFixed(1)}) rotate(${r})">
      <path d="M0 0C-11-9-15 3-4 3.6z" fill="${RD}" stroke="${INK}" stroke-width="1.5" stroke-linejoin="round"/>
      <path d="M0 0C11-9 15 3 4 3.6z" fill="${RD}" stroke="${INK}" stroke-width="1.5" stroke-linejoin="round"/>
      <path d="M-1.4 3.4l-4.6 11 4-2 1.6 3.6zM1.4 3.4l4.6 11-4-2-1.6 3.6z" fill="${RDD}" stroke="${INK}" stroke-width="1.3" stroke-linejoin="round"/>
      <circle r="3" fill="${RD}" stroke="${INK}" stroke-width="1.5"/></g>`;
  }

  function ornament(x, y, c) {
    const col = ['#ff4757', '#ffc933', '#3aa0ff'][c % 3];
    return `<g transform="translate(${x.toFixed(1)} ${y.toFixed(1)})"><path d="M0 0v6" stroke="${INK2}" stroke-width="1.2"/>
      <rect x="-2.6" y="5.4" width="5.2" height="3.4" rx="1" fill="#ffc933" stroke="${INK}" stroke-width="1.2"/>
      <circle cy="15" r="6.6" fill="${col}" stroke="${INK}" stroke-width="1.5"/>
      <path d="M-4 12.6q1.2-2.4 3.4-3" stroke="#fff" stroke-width="1.6" stroke-linecap="round" fill="none" opacity=".9"/>
      <path d="M-6 16.4q6 3 12 0" stroke="${INK}" stroke-width="1" fill="none" opacity=".3"/></g>`;
  }

  function buildGarland() {
    const svg = $('#garland');
    if (!svg) return;
    const W = Math.max(280, window.innerWidth), H = 64, rnd = mulberry(23);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('width', W); svg.setAttribute('height', H);
    const n = Math.max(3, Math.round(W / 118));
    const xs = Array.from({ length: n + 1 }, (_, i) => -6 + (i * (W + 12)) / n + (i && i < n ? (rnd() - 0.5) * 8 : 0));

    let rope = '', pine = '', berries = '', lights = '', orns = '', bows = '', li = 0;
    const y0 = 4;
    for (let i = 0; i < n; i++) {
      const sag = 15 + (i % 2) * 7 + rnd() * 4, s = swag(xs[i], xs[i + 1], y0, sag);
      rope += `<path d="${s.d}" stroke="${INK}" stroke-width="9.4" stroke-linecap="round" fill="none"/><path d="${s.d}" stroke="#1d6b3f" stroke-width="6" stroke-linecap="round" fill="none"/>`;
      // еловые веточки пучками, по обе стороны, чередуя тёмные и светлые
      for (let l = 5, k = 0; l < s.len - 3; l += 7.5 + rnd() * 2.5, k++) {
        const p = s.at(l), side = k % 2 ? 1 : -1, a = (p.a * 180) / Math.PI + side * (62 + rnd() * 18);
        pine += sprig(p.x, p.y + side * 1.2, a, 0.85 + rnd() * 0.35, k % 3 === 0);
      }
      for (let l = 20; l < s.len - 10; l += 40 + rnd() * 10) {
        const p = s.at(l);
        berries += `<g fill="${RD}" stroke="${INK}" stroke-width="1.1"><circle cx="${p.x.toFixed(1)}" cy="${(p.y + 0.4).toFixed(1)}" r="2.5"/><circle cx="${(p.x + 4.4).toFixed(1)}" cy="${(p.y + 2).toFixed(1)}" r="2.2"/><circle cx="${(p.x - 3.6).toFixed(1)}" cy="${(p.y + 2.6).toFixed(1)}" r="2.2"/></g>`;
      }
      const m = s.at(s.len / 2);
      orns += ornament(m.x, m.y + 3, i);
      // отдельный тонкий шнур с лампочками чуть ниже ветвей
      const wd = `M${xs[i].toFixed(1)} ${y0 + 3}Q${((xs[i] + xs[i + 1]) / 2).toFixed(1)} ${(y0 + 3 + sag * 2 + 2).toFixed(1)} ${xs[i + 1].toFixed(1)} ${y0 + 3}`;
      rope += `<path d="${wd}" stroke="${INK2}" stroke-width="1.4" fill="none"/>`;
      for (let l = 16; l < s.len - 8; l += 24 + rnd() * 3) {
        if (Math.abs(l - s.len / 2) < 11) continue;
        const p = s.at(l), ci = li % BULBS.length, c = BULBS[ci];
        const rot = ((p.a * 180) / Math.PI) * 0.25 + (rnd() - 0.5) * 5;
        const d = (rnd() * 2.6).toFixed(2), per = (1.7 + rnd() * 1.5).toFixed(2);
        lights += `<g transform="translate(${p.x.toFixed(1)} ${(p.y + 4).toFixed(1)})" style="--d:${d}s;--p:${per}s;--dc:${(li * 0.16).toFixed(2)}s;--dw:${((p.x / W) * 2.4).toFixed(2)}s;--sd:${(rnd() * 3).toFixed(1)}s">
          <g class="pivot" style="--r0:${(rot - 3).toFixed(1)}deg;--r1:${(rot + 3).toFixed(1)}deg">
          <circle class="halo" cy="12.5" r="10" fill="${c}" fill-opacity=".5"/>
          <rect x="-2.6" y="1" width="5.2" height="5.4" rx="1" fill="#3b3b44" stroke="${INK}" stroke-width="1.2"/>
          <path d="M-2.6 3.8h5.2" stroke="${INK}" stroke-width=".9"/>
          <path d="M0 6C-4.8 7.6-6.4 12.4-4.2 16.2-2.8 18.8 2.8 18.8 4.2 16.2 6.4 12.4 4.8 7.6 0 6z" fill="${c}" opacity=".42"/>
          <path class="on" d="M0 6C-4.8 7.6-6.4 12.4-4.2 16.2-2.8 18.8 2.8 18.8 4.2 16.2 6.4 12.4 4.8 7.6 0 6z" fill="${c}"/>
          <path d="M0 6C-4.8 7.6-6.4 12.4-4.2 16.2-2.8 18.8 2.8 18.8 4.2 16.2 6.4 12.4 4.8 7.6 0 6z" fill="none" stroke="${INK}" stroke-width="1.3" stroke-linejoin="round"/>
          <path d="M-2.4 10.2q.2-2 1.6-2.8" stroke="#fff" stroke-width="1.5" stroke-linecap="round" fill="none"/></g></g>`;
        li++;
      }
    }
    xs.forEach((x, i) => { bows += bow(x, y0 + 1, (i % 2 ? 6 : -5) + (rnd() - 0.5) * 6); });
    svg.innerHTML = `<g class="wire-sway">${rope}${pine}${berries}${orns}${lights}${bows}</g>`;
  }

  /* ============================ СНЕГ ============================ */
  const DENS = { low: [38, 5], normal: [86, 10], high: [150, 18] };
  let snowReady = false, wind = 0, windT = 0, flakeSprites = [], layers = [];

  function makeSprites() {
    flakeSprites = [];
    for (let v = 0; v < 4; v++) {
      const c = document.createElement('canvas'); c.width = c.height = 64;
      const x = c.getContext('2d');
      if (v < 3) {
        const g = x.createRadialGradient(32, 32, 0, 32, 32, 30);
        g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(0.35, 'rgba(255,255,255,.85)'); g.addColorStop(1, 'rgba(255,255,255,0)');
        x.fillStyle = g; x.beginPath(); x.arc(32, 32, 30, 0, 7); x.fill();
      } else {
        x.translate(32, 32); x.strokeStyle = 'rgba(255,255,255,.95)'; x.lineWidth = 3; x.lineCap = 'round';
        for (let k = 0; k < 6; k++) {
          x.rotate(Math.PI / 3);
          x.beginPath(); x.moveTo(0, 0); x.lineTo(0, -26); x.moveTo(0, -15); x.lineTo(-7, -22); x.moveTo(0, -15); x.lineTo(7, -22); x.stroke();
        }
      }
      flakeSprites.push(c);
    }
  }

  function initLayer(id, front) {
    const cv = $(id);
    if (!cv) return null;
    return { cv, ctx: cv.getContext('2d'), front, flakes: [], w: 0, h: 0 };
  }

  function fill(L) {
    const cnt = (DENS[P.density] || DENS.normal)[L.front ? 1 : 0];
    L.flakes = Array.from({ length: cnt }, () => spawn(L, true));
  }
  function spawn(L, any) {
    const z = Math.random();
    return {
      x: Math.random() * (L.w || innerWidth), y: any ? Math.random() * (L.h || innerHeight) : -20,
      z, ph: Math.random() * 6.28, sp: 0.6 + Math.random() * 0.8, rot: Math.random() * 6.28, vr: (Math.random() - 0.5) * 1.2,
      s: flakeSprites[L.front ? (Math.random() < 0.3 ? 3 : 2) : (z > 0.8 && Math.random() < 0.25 ? 3 : (Math.random() * 2) | 0)],
    };
  }

  function snowResize() {
    if (!snowReady) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    layers.forEach((L) => {
      L.w = innerWidth; L.h = innerHeight;
      L.cv.width = Math.round(L.w * dpr); L.cv.height = Math.round(L.h * dpr);
      L.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      fill(L);
    });
  }

  let last = 0;
  function frame(t) {
    requestAnimationFrame(frame);
    if (document.hidden || !P.snow) return;
    if (t - last < 30) return;
    const dt = Math.min(0.06, (t - last) / 1000 || 0.03);
    last = t;
    wind += (windT - wind) * 0.04; windT *= 0.985;
    layers.forEach((L) => {
      const { ctx, flakes, w, h, front } = L;
      ctx.clearRect(0, 0, w, h);
      for (const f of flakes) {
        const depth = front ? 0.9 + f.z * 0.4 : 0.25 + f.z * 0.75;
        f.y += (front ? 52 + f.z * 40 : 16 + f.z * 52) * f.sp * dt;
        f.x += (Math.sin(f.ph + t * 0.0008 * f.sp) * (front ? 22 : 12) + wind * depth * 0.6) * dt;
        f.rot += f.vr * dt;
        if (f.y > h + 20) { Object.assign(f, spawn(L, false)); f.x = Math.random() * w; }
        if (f.x > w + 20) f.x = -20; else if (f.x < -20) f.x = w + 20;
        const size = front ? 6 + f.z * 8 : 2.4 + f.z * 6.2;
        ctx.globalAlpha = front ? 0.2 + f.z * 0.2 : 0.28 + f.z * 0.6;
        if (f.s === flakeSprites[3]) { ctx.save(); ctx.translate(f.x, f.y); ctx.rotate(f.rot); ctx.drawImage(f.s, -size, -size, size * 2, size * 2); ctx.restore(); }
        else ctx.drawImage(f.s, f.x - size, f.y - size, size * 2, size * 2);
      }
    });
    ctx_reset();
  }
  function ctx_reset() { layers.forEach((L) => { L.ctx.globalAlpha = 1; }); }

  function initSnow() {
    makeSprites();
    layers = [initLayer('#snowBack', false), initLayer('#snowFront', true)].filter(Boolean);
    snowReady = true;
    snowResize();
    requestAnimationFrame(frame);
    let px = null;
    window.addEventListener('pointermove', (e) => { if (px !== null) windT = Math.max(-120, Math.min(120, windT + (e.clientX - px) * 0.6)); px = e.clientX; }, { passive: true });
    window.addEventListener('pointerup', () => { px = null; }, { passive: true });
  }
  function gust(dir) { windT = (dir || (Math.random() < 0.5 ? -1 : 1)) * 110; }

  /* ============================ ОТСЧЁТ ДО НОВОГО ГОДА ============================ */
  function nyInfo(now = new Date()) {
    const y = now.getFullYear(), jan = now.getMonth() === 0 && now.getDate() <= 8;
    if (jan) return { done: true, year: y };
    const target = new Date(y + 1, 0, 1, 0, 0, 0), start = new Date(y, 11, 1);
    const left = Math.max(0, target - now), p = Math.min(1, Math.max(0, (now - start) / (target - start)));
    return {
      done: false, year: y + 1, p,
      d: Math.floor(left / 864e5), h: Math.floor(left / 36e5) % 24, m: Math.floor(left / 6e4) % 60, s: Math.floor(left / 1e3) % 60,
    };
  }
  const two = (n) => String(n).padStart(2, '0');
  function nyHtml() {
    const i = nyInfo();
    if (i.done) return `<section class="ny done"><div class="ny-top"><svg class="ny-ico"><use href="#logo"/></svg><div><b>С Новым ${i.year} годом! 🎉</b><span>Пусть эмодзи будут анимированными, а подарки — крупными</span></div></div></section>`;
    return `<section class="ny" data-ny>
      <div class="ny-top"><svg class="ny-ico"><use href="#logo"/></svg><div><b>До Нового ${i.year} года</b><span>Успей собрать праздничный набор</span></div></div>
      <div class="ny-cells">
        <div class="ny-cell"><b data-k="d">${i.d}</b><span>дней</span></div>
        <div class="ny-cell"><b data-k="h">${two(i.h)}</b><span>часов</span></div>
        <div class="ny-cell"><b data-k="m">${two(i.m)}</b><span>минут</span></div>
        <div class="ny-cell sec"><b data-k="s">${two(i.s)}</b><span>секунд</span></div>
      </div>
      <div class="bar"><i data-bar="${Math.round(i.p * 100)}"></i></div></section>`;
  }
  setInterval(() => {
    document.querySelectorAll('[data-ny]').forEach((el) => {
      const i = nyInfo();
      if (i.done) return;
      const set = (k, v) => { const n = el.querySelector(`[data-k=${k}]`); if (n && n.textContent !== String(v)) n.textContent = v; };
      set('d', i.d); set('h', two(i.h)); set('m', two(i.m)); set('s', two(i.s));
    });
  }, 1000);

  /* ============================ ИЗБРАННОЕ И ИСТОРИЯ (localStorage) ============================ */
  const store = {
    get(k, d) { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };

  /* ============================ СТАРТ ============================ */
  function boot() {
    injectCat();
    applyPrefs();
    buildGarland();
    mountCat();
    initSnow();
    let rt;
    window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => { buildGarland(); snowResize(); }, 200); });
  }

  window.Festive = {
    prefs: P,
    set(k, v) { P[k] = v; save(); applyPrefs(); if (k === 'density') snowResize(); if (k === 'lights') buildGarland(); },
    toggle(k) { this.set(k, !P[k]); return P[k]; },
    react, say, hearts, gust, haptic, nyHtml, store, meowSound,
    emojis: ['❄️', '🎄', '⭐', '🎁', '✨', '🔔', '🐱', '💖'],
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
