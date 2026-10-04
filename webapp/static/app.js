/* Emoji Studio — мини-приложение. Без сборки и зависимостей, кроме Telegram SDK. */
(() => {
  'use strict';

  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    try { tg.setHeaderColor('#0b0b0d'); tg.setBackgroundColor('#0b0b0d'); tg.disableVerticalSwipes && tg.disableVerticalSwipes(); } catch (e) {}
  }
  let INIT = (tg && tg.initData) || '';
  try { INIT = INIT || localStorage.getItem('devInit') || ''; } catch (e) {}

  const TABS = ['create', 'shop', 'profile', 'more'];
  const SECS = { profile: ['me', 'friends', 'packs'], more: ['premium', 'about', 'learn'] };
  const STEPS = [
    { id: 'text', t: 'Надпись', s: 'Что написать внутри эмодзи' },
    { id: 'font', t: 'Шрифт', s: 'Каким почерком' },
    { id: 'kind', t: 'Тип набора', s: 'Эмодзи или стикеры' },
    { id: 'tpl', t: 'Шаблоны', s: 'Случайно или вручную' },
    { id: 'done', t: 'Итог', s: 'Проверь и оформляй' },
  ];

  const S = {
    tab: 'create',
    me: null,
    shop: null,
    packs: null,
    sel: new Set(),
    create: { text: '', font: null, kind: 'emoji', mode: 'random', count: 5, step: 0 },
    shopView: { q: '', sort: 'new', filter: 'all', page: 0, search: false },
    sec: { profile: 'me', more: 'premium' },
    packPage: 0,
  };

  /* ---------- утилиты ---------- */
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString('ru-RU');
  const GEM = '<i class="gem-i"></i>';
  const ico = (n, c = '') => `<svg class="i ${c}"><use href="#ic-${n}"/></svg>`;
  const lg = (n) => `<svg><use href="#${n}"/></svg>`;
  const wm = (c = '') => `<svg class="wm ${c}"><use href="#logo"/></svg>`;
  const haptic = (t = 'light') => { try { tg && tg.HapticFeedback && tg.HapticFeedback.impactOccurred(t); } catch (e) {} };

  /* рукописный заголовок: слова и буквы — для анимации */
  const hand = (text) => String(text).split(' ').map((w, wi) =>
    `<span class="w">${[...w].map((ch, i) => `<span class="l" style="--i:${wi * 4 + i}">${esc(ch)}</span>`).join('')}</span>`).join(' ');

  const head = (logo, title, sub, right = '') =>
    `<header class="phead"><span class="hlogo">${lg(logo)}</span>
      <div class="grow"><h1 class="hand">${hand(title)}</h1><p>${sub}</p></div>${right}</header>`;

  const secsNav = (g, items, cur) =>
    `<nav class="secs">${items.map(([id, t, ic]) => `<button class="${id === cur ? 'on' : ''}" data-action="sec" data-g="${g}" data-id="${id}">${ico(ic)}<span>${t}</span></button>`).join('')}</nav>`;

  let toastTimer;
  function toast(msg, isErr) {
    const el = $('#toast');
    el.textContent = msg;
    el.className = 'show' + (isErr ? ' err' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = ''; }, 2600);
    if (isErr) { try { tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred('error'); } catch (e) {} }
  }

  function burst() {
    const fx = $('#fx'), em = ['✨', '💎', '⭐', '🎉', '💖', '🔥', '🌈'];
    for (let i = 0; i < 30; i++) {
      const s = document.createElement('span');
      s.className = 'cf';
      s.textContent = em[i % em.length];
      s.style.setProperty('--x', ((Math.random() * 2 - 1) * 170).toFixed(0) + 'px');
      s.style.setProperty('--y', (-120 - Math.random() * 380).toFixed(0) + 'px');
      s.style.setProperty('--r', ((Math.random() * 2 - 1) * 260).toFixed(0) + 'deg');
      s.style.animationDelay = (Math.random() * 0.25).toFixed(2) + 's';
      fx.appendChild(s);
      setTimeout(() => s.remove(), 2000);
    }
  }

  function countUp(el, to, ms = 900) {
    const t0 = performance.now();
    const tick = (t) => {
      const p = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(Math.round(to * e));
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  function runCounts(root, quiet) {
    root.querySelectorAll('[data-count]').forEach((el) => {
      const v = Number(el.dataset.count) || 0;
      if (quiet || v === 0) el.textContent = fmt(v); else countUp(el, v);
    });
    root.querySelectorAll('[data-bar]').forEach((el) => { requestAnimationFrame(() => requestAnimationFrame(() => { el.style.width = el.dataset.bar + '%'; })); });
  }

  async function api(path, opts = {}) {
    const headers = { 'X-Init-Data': INIT };
    let body;
    if (opts.form) { body = opts.form; }
    else if (opts.body !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.body); }
    let res;
    try {
      res = await fetch(path, { method: opts.method || (body ? 'POST' : 'GET'), headers, body });
    } catch (e) {
      throw new Error('Нет связи с сервером');
    }
    if (opts.blob && res.ok) return res.blob();
    let data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok || data.ok === false) throw new Error(data.error || 'Ошибка ' + res.status);
    return data;
  }

  const cfg = () => (S.me ? S.me.config : {});
  const orderLimit = () => (S.me && (S.me.premium.active || S.me.is_admin) ? cfg().premium_limit : cfg().free_limit);
  const createCount = () => (S.create.mode === 'random' ? S.create.count : S.sel.size);

  /* ---------- загрузка ---------- */
  async function loadMe() {
    const d = await api('/api/me');
    const prev = S.me;
    S.me = d;
    if (!S.create.font) S.create.font = d.config.default_font;
    updateChips(prev);
    return d;
  }
  function updateChips(prev) {
    if (!S.me) return;
    const set = (id, v, was) => {
      const el = $(id);
      el.textContent = fmt(v);
      if (was !== undefined && was !== v) { const chip = el.closest('.chip'); chip.classList.remove('bump'); void chip.offsetWidth; chip.classList.add('bump'); }
    };
    set('#chipBalance', S.me.balance, prev ? prev.balance : undefined);
    set('#chipCrystals', S.me.crystals, prev ? prev.crystals : undefined);
    if (S.me.bot) $('#botName').textContent = S.me.bot;
  }
  async function loadShop(force) {
    if (S.shop && !force) return S.shop;
    const d = await api('/api/shop');
    S.shop = d.items;
    return S.shop;
  }

  /* ---------- страницы и переходы ---------- */
  function paint(html, dir, quiet, cls = '') {
    const view = $('#view');
    view.querySelectorAll('.page.leaving').forEach((n) => n.remove());
    const old = view.querySelector('.page');
    const el = document.createElement('div');
    el.className = 'page ' + cls + (quiet ? ' quiet' : '') + (dir ? (dir > 0 ? ' from-r' : ' from-l') : '');
    el.innerHTML = html;
    if (old) {
      if (dir) { old.classList.add('leaving', dir > 0 ? 'to-l' : 'to-r'); setTimeout(() => old.remove(), 400); }
      else old.remove();
    }
    view.appendChild(el);
    runCounts(el, quiet);
    return el;
  }

  function markTabs() {
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('on', t.dataset.tab === S.tab));
  }

  function setTab(tab) {
    if (tab === S.tab) return;
    const dir = Math.sign(TABS.indexOf(tab) - TABS.indexOf(S.tab));
    S.tab = tab;
    markTabs();
    haptic();
    render(dir);
  }

  function render(dir = 0, quiet = false) {
    if (!S.me) { paint(loaderHtml(), dir, true); return; }
    if (S.me.maintenance) { paint(lockHtml('Технические работы', 'Скоро всё заработает. Загляните чуть позже.'), dir, quiet); return; }
    if (S.me.gate && S.me.gate.length) { paint(gateHtml(), dir, quiet); return; }
    ({ create: renderCreate, shop: renderShop, profile: renderProfile, more: renderMore })[S.tab](dir, quiet);
  }
  const rr = () => render(0, true);

  const loaderHtml = () => `<div class="lock"><div class="loadlogo">${lg('logo')}</div><p class="muted">Загружаю…</p></div>`;
  const lockHtml = (h, p, extra = '') => `<div class="lock"><div class="elogo">${lg('logo')}</div><h2>${h}</h2><p class="muted">${p}</p>${extra}</div>`;
  const emptyHtml = (title, text) => `<div class="empty"><div class="elogo">${lg('logo')}</div><b>${title}</b>${text || ''}</div>`;

  function gateHtml() {
    const links = S.me.gate.map((c) => `<button class="btn block" style="margin-top:10px" data-action="openlink" data-url="${esc(c.url)}">${ico('send')} ${esc(c.title)}</button>`).join('');
    return lockHtml('Подпишитесь', 'Чтобы пользоваться приложением, подпишитесь на каналы и нажмите «Проверить».',
      links + '<button class="btn primary block" style="margin-top:14px" data-action="recheck">Проверить подписку</button>');
  }

  /* ======================= СОЗДАТЬ ======================= */
  function createDock() {
    const c = S.create, k = cfg(), n = createCount();
    const last = c.step === STEPS.length - 1;
    return `<div class="pdock" id="cdock"><div class="sum"><b>${n} ${last ? 'шт.' : 'эмодзи'}</b><span>${fmt(n * k.price)} ${ico('star')}${last ? '' : ` · ${fmt(n * k.crystals_per_emoji)} ${GEM}`}</span></div>
      ${c.step > 0 ? `<button class="btn icon sm" data-action="prev" aria-label="Назад">${ico('chev', 'flip')}</button>` : ''}
      ${last
        ? `<button class="btn icon sm" data-action="preview" aria-label="Превью">${ico('eye')}</button><button class="btn primary" data-action="checkout">Оформить</button>`
        : `<button class="btn primary" data-action="next">Далее ${ico('chev')}</button>`}
    </div>`;
  }
  function refreshCreateDock() { const d = $('#cdock'); if (d) d.outerHTML = createDock(); }

  function setStep(i) {
    const c = S.create;
    i = Math.max(0, Math.min(STEPS.length - 1, i));
    if (i === c.step) return;
    const dir = i > c.step ? 1 : -1;
    c.step = i;
    haptic();
    render(dir);
  }
  function nextStep() {
    const c = S.create;
    if (c.step === 0 && !c.text.trim()) return toast('Введите надпись для эмодзи', true);
    if (c.step === 3 && createCount() < 1) return toast('Выберите хотя бы один шаблон', true);
    setStep(c.step + 1);
  }

  function stepBody(id) {
    const c = S.create, m = S.me, k = cfg();
    const limit = orderLimit();
    const maxRandom = Math.min(limit, k.templates_builtin);

    if (id === 'text') {
      const quick = [];
      if (m.user.username) quick.push('@' + m.user.username);
      if (m.user.name) quick.push(m.user.name);
      const q = quick.map((v) => `<button class="pill" data-action="fill" data-v="${esc(v)}">${esc(v)}</button>`).join('');
      return `<section class="card">${wm()}
          <div class="lab"><span>${ico('text')}Надпись</span><span class="tiny" id="textCount">${c.text.length}/${k.text_max}</span></div>
          <input id="textInput" class="input big" maxlength="${k.text_max}" placeholder="Ваш ник" value="${esc(c.text)}" autocomplete="off">
          <div class="field-meta tiny"><span>Эта надпись появится внутри каждого эмодзи</span></div>
        </section>
        ${q ? `<section class="card">${wm('tl')}<div class="lab"><span>${ico('create')}Быстро вставить</span></div><div class="wrapx">${q}</div></section>` : ''}
        <div class="notice">${ico('info')}<span>Короткие надписи читаются лучше: 3–8 символов. Можно писать кириллицей и латиницей.</span></div>`;
    }

    if (id === 'font') {
      const fonts = k.fonts.map((f) => `<button class="fcard ${f.id === c.font ? 'on' : ''}" data-action="font" data-id="${f.id}">
          <div class="aa">Аа</div><div class="nm">${esc(f.label)}</div></button>`).join('');
      return `<div class="fgrid">${fonts}</div>`;
    }

    if (id === 'kind') {
      const kinds = Object.entries(k.kinds).map(([kid, v], i) => `<button class="kcard ${kid === c.kind ? 'on' : ''}" data-action="kind" data-id="${kid}">
          <span class="kl">${lg(i === 0 ? 'lg-create' : 'lg-more')}</span>
          <span class="grow"><b>${esc(v.title)}</b><span class="small muted">${esc(v.note)}</span></span></button>`).join('');
      return `<div class="stack">${kinds}</div>`;
    }

    if (id === 'tpl') {
      const mode = `<div class="seg">
          <button class="${c.mode === 'random' ? 'on' : ''}" data-action="mode" data-id="random">Случайно</button>
          <button class="${c.mode === 'manual' ? 'on' : ''}" data-action="mode" data-id="manual">Выбрать</button></div>`;
      let inner;
      if (c.mode === 'random') {
        const presets = [5, 10, 20, 50, 100].filter((n) => n <= maxRandom).map((n) => `<button class="pill ${n === c.count ? 'on' : ''}" data-action="count" data-n="${n}">${n}</button>`).join('');
        inner = `<div class="lab"><span>${ico('dice')}Сколько эмодзи</span></div>
          <div class="stepper"><button class="round" data-action="step" data-d="-1">−</button><div class="count" id="countNum">${c.count}</div><button class="round" data-action="step" data-d="1">+</button></div>
          <div class="wrapx" style="margin-top:14px;justify-content:center">${presets}</div>`;
      } else {
        const picked = [...S.sel].slice(0, 12).map((n) => `<img src="/api/preview/${n}.png" alt="">`).join('');
        inner = `<div class="lab"><span>${ico('cursor')}Выбрано</span><span class="num" style="font-size:30px" id="countNum">${S.sel.size}</span></div>
          ${S.sel.size ? `<div class="thumbs">${picked}</div>` : '<p class="small muted">Откройте магазин и отметьте нужные шаблоны — они появятся здесь.</p>'}
          <div class="row" style="margin-top:14px"><button class="btn grow" data-action="goto" data-tab="shop">${ico('shop')}В магазин</button>
          ${S.sel.size ? `<button class="btn ghost" data-action="clearsel">Сбросить</button>` : ''}</div>`;
      }
      const note = m.premium.active || m.is_admin ? '' :
        `<div class="notice">${ico('crown')}<span>Без премиума — до <b>${k.free_limit}</b> эмодзи за заказ. <a href="#" data-action="goto" data-tab="more">Снять лимит</a></span></div>`;
      return `${mode}<section class="card">${wm()}${inner}</section>${note}`;
    }

    /* итог */
    const f = k.fonts.find((x) => x.id === c.font);
    const n = createCount();
    return `<section class="card">${wm()}
        <div class="lab"><span>${ico('check')}Твой заказ</span></div>
        <div class="sumrow"><span>${ico('text')}Надпись</span><b>${esc(c.text.trim() || '—')}</b></div>
        <div class="sumrow"><span>${ico('font')}Шрифт</span><b>${esc(f ? f.label : c.font)}</b></div>
        <div class="sumrow"><span>${ico('stack')}Тип</span><b>${esc(k.kinds[c.kind].title)}</b></div>
        <div class="sumrow"><span>${ico('dice')}Шаблоны</span><b>${c.mode === 'random' ? 'случайные' : 'свои'} · ${n}</b></div>
      </section>
      ${m.promo ? `<div class="notice accent">${ico('tag')}<span>Промокод <b>${esc(m.promo.code)}</b>: скидка <b>${m.promo.percent}%</b> на этот заказ</span></div>` : ''}
      <div class="notice">${ico('info')}<span>Нажми «Превью», чтобы увидеть эмодзи до оплаты. Оплатить можно балансом, кристаллами или звёздами.</span></div>`;
  }

  function renderCreate(dir, quiet) {
    const c = S.create, k = cfg();
    const maxRandom = Math.min(orderLimit(), k.templates_builtin);
    if (c.count > maxRandom) c.count = maxRandom;
    if (c.count < 1) c.count = 1;
    const st = STEPS[c.step];
    const bars = STEPS.map((s, i) => `<button class="${i < c.step ? 'done' : ''} ${i === c.step ? 'on' : ''}" data-action="wstep" data-s="${i}" aria-label="${s.t}"></button>`).join('');
    const el = paint(`${head('lg-create', 'Создать', `Шаг ${c.step + 1} из ${STEPS.length}`)}
      <div class="steps">${bars}</div>
      <div class="stepinfo"><b>${esc(st.t)}</b><span>${esc(st.s)}</span></div>
      <div class="body stg">${stepBody(st.id)}</div>${createDock()}`, dir, quiet);

    const inp = $('#textInput', el);
    if (inp) inp.addEventListener('input', () => { c.text = inp.value; $('#textCount').textContent = c.text.length + '/' + k.text_max; });
  }

  function orderPayload() {
    const c = S.create;
    const base = { text: c.text.trim(), font: c.font, kind: c.kind };
    return c.mode === 'random' ? { ...base, mode: 'random', count: c.count } : { ...base, mode: 'manual', numbers: [...S.sel] };
  }

  function validateLocal() {
    if (!S.create.text.trim()) { toast('Введите надпись для эмодзи', true); return false; }
    if (createCount() < 1) { toast('Выберите хотя бы один шаблон', true); return false; }
    if (createCount() > orderLimit()) { toast('Лимит заказа — ' + orderLimit() + ' эмодзи', true); return false; }
    return true;
  }

  async function showPreview() {
    if (!validateLocal()) return;
    openSheet(`<h2>Предпросмотр</h2><div class="center"><span class="spinner"></span><p class="muted small" style="margin-top:10px">Собираю эмодзи…</p></div>`);
    try {
      const blob = await api('/api/order/preview', { body: orderPayload(), blob: true });
      if (!(blob instanceof Blob)) throw new Error('Не удалось собрать предпросмотр');
      const url = URL.createObjectURL(blob);
      const more = createCount() > 24 ? `<p class="tiny muted" style="margin-top:8px">Показаны первые 24 из ${createCount()}</p>` : '';
      setSheet(`<h2>Предпросмотр</h2><p class="muted small" style="margin-bottom:12px">Так будут выглядеть ваши эмодзи</p><img class="preview-img" src="${url}" alt="">${more}
        <button class="btn primary block" style="margin-top:14px" data-action="closesheet-checkout">Оформить</button>`);
    } catch (e) {
      setSheet(`<h2>Предпросмотр</h2><p class="muted" style="margin:10px 0 16px">${esc(e.message)}</p><button class="btn block" data-action="closesheet">Закрыть</button>`);
    }
  }

  let currentOrder = null;
  async function checkout() {
    if (!validateLocal()) return;
    openSheet(`<h2>Оформление</h2><div class="center"><span class="spinner"></span></div>`);
    try {
      const o = await api('/api/order', { body: orderPayload() });
      currentOrder = o;
      renderCheckout(o);
    } catch (e) {
      setSheet(`<h2>Не получилось</h2><p class="muted" style="margin:10px 0 16px">${esc(e.message)}</p><button class="btn block" data-action="closesheet">Закрыть</button>`);
    }
  }

  function renderCheckout(o) {
    const price = o.free ? 'Бесплатно (админ)' :
      (o.promo ? `<span class="strike">${fmt(o.base_amount)}</span>` : '') + `${fmt(o.amount)} ⭐`;
    const canBal = o.free || o.balance >= o.amount;
    const canGem = !o.free && o.crystals >= o.crystals_cost;
    setSheet(`
      <h2>Оформление заказа</h2>
      <p class="muted small" style="margin-bottom:14px">${o.count} эмодзи · «${esc(S.create.text.trim())}»${o.promo ? ` · промокод <b>${esc(o.promo)}</b>` : ''}</p>
      <div class="card" style="margin-bottom:14px"><div class="row between"><span class="muted">К оплате</span><b class="num" style="font-size:38px">${price}</b></div></div>
      <div class="stack">
        <button class="pay-opt ${canBal ? '' : 'off'}" data-action="pay" data-m="balance"><span class="ico">${ico('wallet')}</span><span class="grow"><b>С баланса</b><span class="tiny muted">Доступно ${fmt(o.balance)} ⭐</span></span>${ico('chev')}</button>
        ${o.free ? '' : `<button class="pay-opt ${canGem ? '' : 'off'}" data-action="pay" data-m="crystals"><span class="ico">${GEM}</span><span class="grow"><b>Кристаллами · ${fmt(o.crystals_cost)}</b><span class="tiny muted">Доступно ${fmt(o.crystals)}</span></span>${ico('chev')}</button>
        <button class="pay-opt" data-action="pay" data-m="stars"><span class="ico">${ico('star')}</span><span class="grow"><b>Звёздами Telegram</b><span class="tiny muted">Счёт на ${fmt(o.amount)} ⭐</span></span>${ico('chev')}</button>`}
      </div>
      ${!canBal && !o.free ? '<button class="btn block ghost" style="margin-top:12px" data-action="topup">Пополнить баланс</button>' : ''}
    `);
  }

  async function pay(method) {
    if (!currentOrder) return;
    const o = currentOrder;
    setSheet(`<div class="center"><span class="spinner"></span><p class="muted small" style="margin-top:10px">Обрабатываю…</p></div>`);
    try {
      const r = await api(`/api/order/${o.order_id}/pay`, { body: { method } });
      if (r.invoice) {
        if (!tg) throw new Error('Оплата доступна только в Telegram');
        tg.openInvoice(r.invoice, (status) => {
          if (status === 'paid') trackOrder(o.order_id);
          else { renderCheckout(o); if (status === 'failed') toast('Платёж не прошёл', true); }
        });
        return;
      }
      trackOrder(o.order_id);
    } catch (e) {
      toast(e.message, true);
      renderCheckout(o);
    }
  }

  let trackToken = 0;
  async function trackOrder(orderId) {
    const token = ++trackToken;
    setSheet(`<div class="center"><span class="spinner"></span><h2 style="margin-top:14px">Заказ принят</h2><p class="muted small" style="margin-top:6px">Собираю набор. Ссылка придёт в чат с ботом — это может занять несколько минут.</p></div>
      <button class="btn block" style="margin-top:10px" data-action="closesheet">Свернуть</button>`);
    S.sel.clear();
    loadMe().then(() => S.tab !== 'create' || refreshCreateDock()).catch(() => {});
    for (let i = 0; i < 200 && token === trackToken; i++) {
      await new Promise((r) => setTimeout(r, 3500));
      if (token !== trackToken) return;
      let st;
      try { st = (await api('/api/order/' + orderId)).status; } catch (e) { continue; }
      if (st === 'done') {
        S.packs = null;
        loadMe().catch(() => {});
        burst();
        haptic('medium');
        if (!$('.overlay:not(.closing)')) return toast('Набор готов — ссылка в чате');
        setSheet(`<div class="center"><div class="elogo" style="width:84px;height:84px;margin:0 auto 6px;animation:float 3s ease-in-out infinite">${lg('logo')}</div><h2 style="margin-top:8px">Набор готов!</h2><p class="muted small" style="margin-top:6px">Ссылка отправлена в чат с ботом. Все наборы — в профиле.</p></div>
          <button class="btn primary block" data-action="mysets">Мои наборы</button>`);
        return;
      }
      if (st === 'failed' || st === 'refunded') {
        loadMe().catch(() => {});
        if ($('.overlay:not(.closing)')) setSheet(`<div class="center"><h2>Не удалось собрать</h2><p class="muted small" style="margin-top:6px">${st === 'refunded' ? 'Оплата возвращена.' : 'Напишите в поддержку — мы разберёмся.'}</p></div><button class="btn block" data-action="closesheet">Закрыть</button>`);
        return;
      }
    }
  }

  /* ======================= МАГАЗИН ======================= */
  async function renderShop(dir, quiet) {
    if (!S.shop) {
      paint(loaderHtml(), dir, true);
      try { await loadShop(); }
      catch (e) {
        if (S.tab !== 'shop') return;
        paint(lockHtml('Не загрузилось', esc(e.message), '<button class="btn primary" data-action="reload">Повторить</button>'), 0, true);
        return;
      }
      if (S.tab !== 'shop') return;
      dir = 0; quiet = false;
    }
    drawShop(dir, quiet);
  }

  function filteredShop() {
    const v = S.shopView;
    let items = S.shop.slice();
    if (v.filter === 'user') items = items.filter((i) => i.kind === 'user');
    if (v.filter === 'mine') items = items.filter((i) => i.mine);
    if (v.q.trim()) {
      const q = v.q.trim().toLowerCase().replace('#', '');
      items = items.filter((i) => String(i.n).includes(q) || String(i.title).toLowerCase().includes(q));
    }
    if (v.sort === 'popular') items.sort((a, b) => b.downloads - a.downloads || a.n - b.n);
    else if (v.sort === 'old') items.reverse();
    return items;
  }

  function tileHtml(i, k) {
    const label = i.n >= 1000 ? '' : '#' + String(i.n).padStart(3, '0');
    return `<div class="tile ${S.sel.has(i.n) ? 'sel' : ''}" style="--k:${k}" data-action="open" data-n="${i.n}">
      <img src="/api/preview/${i.n}.png" loading="lazy" alt=""><span class="no">${label}</span>
      ${i.kind === 'user' ? `<span class="own">${i.mine ? 'Мой' : 'Юзер'}</span>` : ''}
      <span class="dl">⬇ ${fmt(i.downloads)}</span><span class="check">${ico('check')}</span></div>`;
  }

  const selbarInner = () => {
    const n = S.sel.size, k = cfg();
    return `<div class="sum"><b>Выбрано: ${n}</b><span>${fmt(n * k.price)} ${ico('star')}</span></div>
      <button class="btn icon sm" data-action="clearsel" aria-label="Сбросить выбор">${ico('close')}</button>
      <button class="btn primary" data-action="tocreate">Создать ${ico('chev')}</button>`;
  };

  function drawShop(dir, quiet) {
    const v = S.shopView;
    const sorts = [['new', 'Новые'], ['popular', 'Популярные'], ['old', 'Старые']];
    const filters = [['all', 'Все', 'shop'], ['user', 'От юзеров', 'users'], ['mine', 'Мои', 'heart']];
    const right = `<button class="btn icon sm" data-action="togglesearch" aria-label="Поиск">${ico('search')}</button>
      <button class="btn icon sm primary" data-action="addtpl" aria-label="Добавить шаблон">${ico('plus')}</button>`;
    const el = paint(`${head('lg-shop', 'Магазин', `${fmt(S.shop.length)} шаблонов`, `<div class="row" style="gap:6px">${right}</div>`)}
      ${v.search ? `<div class="shopbar"><input id="shopSearch" class="input" placeholder="Номер или название" value="${esc(v.q)}" autocomplete="off"></div>` : ''}
      <nav class="secs">${filters.map(([id, t, ic]) => `<button class="${v.filter === id ? 'on' : ''}" data-action="filter" data-id="${id}">${ico(ic)}<span>${t}</span></button>`).join('')}</nav>
      <div class="scroll-x sortrow">${sorts.map(([id, t]) => `<button class="pill ${v.sort === id ? 'on' : ''}" data-action="sort" data-id="${id}">${t}</button>`).join('')}</div>
      <div class="gridwrap" id="gw"></div>
      <div class="pager" id="pager"></div>
      <div class="pdock ${S.sel.size ? '' : 'hide'}" id="selbar">${selbarInner()}</div>`, dir, quiet, 'stg');

    const inp = $('#shopSearch', el);
    if (inp) {
      let t;
      inp.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { v.q = inp.value; v.page = 0; fillGrid(); }, 220); });
    }
    const gw = $('#gw', el);
    let x0 = null;
    gw.addEventListener('touchstart', (e) => { x0 = e.touches[0].clientX; }, { passive: true });
    gw.addEventListener('touchend', (e) => {
      if (x0 === null) return;
      const dx = e.changedTouches[0].clientX - x0; x0 = null;
      if (Math.abs(dx) > 55) shopPage(dx < 0 ? 1 : -1);
    }, { passive: true });
    requestAnimationFrame(() => fillGrid());
  }

  function fillGrid(dirCls) {
    const gw = $('#gw');
    if (!gw || !S.shop || S.tab !== 'shop') return;
    const v = S.shopView, items = filteredShop();
    const W = gw.clientWidth, H = gw.clientHeight, gap = 10;
    const cols = W >= 460 ? 4 : 3;
    const tile = (W - gap * (cols - 1)) / cols;
    const rows = Math.max(1, Math.floor((H + gap) / (tile + gap)));
    const per = cols * rows;
    const pages = Math.max(1, Math.ceil(items.length / per));
    v.page = Math.max(0, Math.min(v.page, pages - 1));
    S.shopPages = pages;
    const pager = $('#pager');
    if (!items.length) {
      gw.innerHTML = emptyHtml('Ничего не найдено', '<p class="small">Попробуйте другой запрос или фильтр</p>');
      pager.innerHTML = '';
      return;
    }
    const slice = items.slice(v.page * per, (v.page + 1) * per);
    gw.innerHTML = `<div class="grid ${dirCls || ''}" style="grid-template-columns:repeat(${cols},1fr)">${slice.map((i, k) => tileHtml(i, k)).join('')}</div>`;
    pager.innerHTML = pages > 1
      ? `<button class="btn icon sm" data-action="pgprev" ${v.page <= 0 ? 'disabled' : ''}>${ico('chev', 'flip')}</button>
         <div class="pg">${v.page + 1} / ${pages}</div>
         <button class="btn icon sm" data-action="pgnext" ${v.page >= pages - 1 ? 'disabled' : ''}>${ico('chev')}</button>`
      : `<div class="pg" style="font-size:20px;opacity:.7">${items.length} шт.</div>`;
  }

  function shopPage(d) {
    const v = S.shopView, np = v.page + d;
    if (np < 0 || np >= (S.shopPages || 1)) return;
    v.page = np;
    haptic();
    fillGrid(d > 0 ? '' : 'sl');
  }

  function refreshTileSel() {
    document.querySelectorAll('.tile').forEach((t) => t.classList.toggle('sel', S.sel.has(Number(t.dataset.n))));
    const bar = $('#selbar');
    if (!bar) return;
    const wasHidden = bar.classList.contains('hide');
    bar.innerHTML = selbarInner();
    bar.classList.toggle('hide', !S.sel.size);
    if (wasHidden !== !S.sel.size) requestAnimationFrame(() => { const g = $('#gw .grid'); fillGrid(g ? '' : undefined); });
  }

  let lottieLoader;
  function loadLottie() {
    if (window.lottie) return Promise.resolve();
    if (!lottieLoader) {
      lottieLoader = new Promise((res, rej) => {
        const s = document.createElement('script');
        s.src = 'https://cdnjs.cloudflare.com/ajax/libs/lottie-web/5.12.2/lottie.min.js';
        s.onload = res; s.onerror = rej;
        document.head.appendChild(s);
      });
    }
    return lottieLoader;
  }

  function openTemplate(n) {
    const item = S.shop.find((i) => i.n === n);
    if (!item) return;
    const picked = S.sel.has(n);
    openSheet(`
      <div class="anim-box" id="animBox"><img src="/api/preview/${n}.png" alt=""></div>
      <div class="center" style="padding:0 0 14px">
        <h2>${esc(item.title)}</h2>
        <p class="muted small">${item.kind === 'user' ? (item.mine ? 'Ваш шаблон' : 'Добавлен пользователем') : 'Встроенный шаблон'} · ⬇ ${fmt(item.downloads)}</p>
      </div>
      <div class="stack">
        <button class="btn ${picked ? '' : 'primary'} block" data-action="toggle" data-n="${n}">${picked ? 'Убрать из набора' : 'Добавить в набор'}</button>
        ${item.mine || (S.me && S.me.is_admin && item.kind === 'user') ? `<button class="btn danger block ghost" data-action="deltpl" data-n="${n}">${ico('trash')}Удалить шаблон</button>` : ''}
      </div>`);
    loadLottie().then(() => {
      const box = $('#animBox');
      if (!box || !window.lottie) return;
      box.innerHTML = '';
      window.lottie.loadAnimation({ container: box, renderer: 'svg', loop: true, autoplay: true, path: `/api/lottie/${n}.json` });
    }).catch(() => {});
  }

  function toggleSel(n) {
    if (S.sel.has(n)) S.sel.delete(n);
    else {
      if (S.sel.size >= orderLimit()) {
        toast(S.me.premium.active ? 'Максимум за заказ — ' + orderLimit() : 'Без премиума — до ' + orderLimit() + ' эмодзи за заказ', true);
        return false;
      }
      S.sel.add(n);
    }
    haptic();
    return true;
  }

  function addTemplateSheet() {
    const k = cfg();
    openSheet(`<h2>Добавить шаблон</h2>
      <p class="muted small" style="margin:4px 0 14px">Загрузите анимацию <b>.tgs</b> со слоем-надписью <b>TEXT</b> — в него бот подставит текст. Размер до 64 КБ, не более ${k.user_template_daily} в сутки.</p>
      <div class="stack">
        <input id="tplTitle" class="input" maxlength="40" placeholder="Название шаблона">
        <input id="tplFile" class="input" type="file" accept=".tgs,application/x-tgsticker,application/gzip">
        <button class="btn primary block" data-action="uploadtpl">Загрузить</button>
      </div>`);
  }

  async function uploadTemplate() {
    const title = $('#tplTitle').value.trim(), file = $('#tplFile').files[0];
    if (!title) return toast('Введите название', true);
    if (!file) return toast('Выберите файл .tgs', true);
    const fd = new FormData();
    fd.append('title', title);
    fd.append('file', file);
    const btn = $('[data-action=uploadtpl]');
    btn.disabled = true;
    try {
      await api('/api/templates', { form: fd });
      closeSheet();
      toast('Шаблон добавлен');
      S.shop = null;
      S.shopView.filter = 'mine';
      S.shopView.page = 0;
      if (S.tab === 'shop') render(0, true);
    } catch (e) { toast(e.message, true); btn.disabled = false; }
  }

  /* ======================= ПРОФИЛЬ ======================= */
  function renderProfile(dir, quiet) {
    const m = S.me, k = cfg(), sec = S.sec.profile;
    const nav = secsNav('profile', [['me', 'Я', 'profile'], ['friends', 'Друзья', 'users'], ['packs', 'Наборы', 'box']], sec);
    let body = '';
    const name = m.user.name || 'Пользователь';

    if (sec === 'me') {
      const avatar = m.user.photo ? `<img src="${esc(m.user.photo)}" alt="">` : esc(name.slice(0, 1).toUpperCase());
      const until = m.premium.active ? new Date(m.premium.until * 1000).toLocaleDateString('ru-RU') : '';
      body = `<div class="body stg">
        <section class="card">${wm()}
          <div class="row"><div class="avatar">${avatar}</div>
            <div class="grow"><b class="num" style="font-size:30px;line-height:1">${esc(name)}</b>
              <div class="muted small">${m.user.username ? '@' + esc(m.user.username) : 'ID ' + m.user.id}</div></div>
            ${m.premium.active ? `<span class="badge">${ico('crown')}Premium</span>` : '<span class="badge dim">Free</span>'}
          </div>
          ${m.premium.active ? `<p class="tiny muted" style="margin-top:8px">Премиум действует до ${until}</p>` : ''}
        </section>
        <section class="card">${wm('tl')}
          <div class="lab"><span>${ico('wallet')}Баланс</span><span class="tiny">Создано: ${fmt(m.emoji_created)}</span></div>
          <div class="row between"><div class="big">${ico('star')}<span data-count="${m.balance}">0</span></div>
            <button class="btn primary" data-action="topup">Пополнить</button></div>
        </section>
        <section class="card">
          <div class="lab"><span>${ico('tag')}Промокод</span></div>
          ${m.promo ? `<div class="notice accent" style="margin-bottom:10px"><span>Активен <b>${esc(m.promo.code)}</b> · скидка ${m.promo.percent}% на следующий заказ</span></div>` : ''}
          <div class="row"><input id="promoInput" class="input" placeholder="Введите промокод" autocapitalize="characters" autocomplete="off" maxlength="24">
            <button class="btn" data-action="promo">ОК</button></div>
        </section>
        <section class="card">
          <div class="row between"><div><b class="num" style="font-size:25px">Поддержка</b><div class="small muted">Вопросы по заказам и оплате</div></div>
            <button class="btn" data-action="support">${ico('chat')}${esc(k.support)}</button></div>
        </section></div>`;
    } else if (sec === 'friends') {
      const nxt = m.referral.next;
      const progress = nxt ? Math.min(100, Math.round((m.friends / nxt.need) * 100)) : 100;
      const tiers = m.referral.tiers.map((t) => `<div class="tier ${t.reached ? 'done' : ''}"><b>${t.total} ${GEM}</b><span>${t.need} друзей</span></div>`).join('');
      body = `<div class="body stg">
        <section class="card">${wm()}
          <div class="lab"><span>${ico('gem')}Кристаллы</span><span class="tiny">${k.crystals_per_emoji} ${GEM} = 1 эмодзи</span></div>
          <div class="row between" style="margin-bottom:12px"><div class="big"><i class="gem-i"></i><span data-count="${m.crystals}">0</span></div>
            <div class="tiny muted" style="text-align:right">Друзей приглашено<br><b class="num" style="font-size:34px;color:#fff">${m.friends}</b></div></div>
          <div class="bar"><i data-bar="${progress}"></i></div>
          <p class="tiny muted" style="margin-top:8px">${nxt ? `До награды ${nxt.total} ${GEM}: ещё ${nxt.need - m.friends} друзей` : 'Все ступени пройдены 🎉'}</p>
        </section>
        <section class="card">
          <div class="lab"><span>${ico('gift')}Пригласить друга</span></div>
          <p class="small muted" style="margin-bottom:10px">За каждого друга — <b style="color:#fff">${m.referral.reward} ${GEM}</b>. Награда приходит, когда друг запустит бота.</p>
          <div class="link-box"><span>${esc(m.referral.link)}</span></div>
          <div class="row" style="margin-top:10px"><button class="btn grow" data-action="copy">${ico('copy')}Копировать</button><button class="btn primary grow" data-action="share">${ico('send')}Позвать</button></div>
        </section>
        <section class="card">${wm('tl')}<div class="lab"><span>${ico('crown')}Ступени наград</span></div><div class="tiers">${tiers}</div></section></div>`;
    } else {
      body = `<div class="body fix"><div class="packs" id="packsBox"><div class="center"><span class="spinner"></span></div></div></div>`;
    }
    paint(`${head('lg-profile', 'Профиль', esc(name))}${nav}${body}`, dir, quiet);
    if (sec === 'packs') loadPacks();
  }

  async function loadPacks() {
    const box = $('#packsBox');
    try {
      if (!S.packs) S.packs = (await api('/api/packs')).packs;
    } catch (e) { if (box) box.innerHTML = `<p class="muted small">${esc(e.message)}</p>`; return; }
    drawPacks();
  }

  function drawPacks(dirCls) {
    const box = $('#packsBox');
    if (!box || !S.packs) return;
    if (!S.packs.length) { box.innerHTML = emptyHtml('Пока пусто', '<p class="small">Вы ещё не создавали наборы</p>'); return; }
    box.innerHTML = '<div class="plist" id="plist"></div><div class="pager" id="ppager"></div>';
    const lh = $('#plist').clientHeight;
    const per = Math.max(2, Math.floor((lh + 8) / 76));
    const pages = Math.max(1, Math.ceil(S.packs.length / per));
    S.packPage = Math.max(0, Math.min(S.packPage, pages - 1));
    S.packPages = pages;
    const slice = S.packs.slice(S.packPage * per, (S.packPage + 1) * per);
    $('#plist').innerHTML = slice.map((p, i) => `
      <button class="list-item" style="--k:${i}" data-action="openlink" data-url="${esc(p.url)}">
        <span class="ico">${lg(p.kind === 'emoji' ? 'lg-create' : 'lg-more')}</span>
        <span class="grow"><b>${esc(p.title)}</b><div class="tiny muted">${p.count} шт. · ${p.kind === 'emoji' ? 'эмодзи' : 'стикеры'}</div></span>${ico('chev')}
      </button>`).join('');
    $('#ppager').innerHTML = pages > 1
      ? `<button class="btn icon sm" data-action="ppprev" ${S.packPage <= 0 ? 'disabled' : ''}>${ico('chev', 'flip')}</button>
         <div class="pg">${S.packPage + 1} / ${pages}</div>
         <button class="btn icon sm" data-action="ppnext" ${S.packPage >= pages - 1 ? 'disabled' : ''}>${ico('chev')}</button>` : '';
  }

  function topupSheet() {
    const k = cfg();
    S.topup = { amount: k.topup_presets[1] || 100, method: 'stars' };
    drawTopup();
  }
  function drawTopup() {
    const k = cfg(), t = S.topup;
    const chips = k.topup_presets.map((n) => `<button class="pill ${n === t.amount ? 'on' : ''}" data-action="tpreset" data-n="${n}">${n} ⭐</button>`).join('');
    openSheet(`<h2>Пополнить баланс</h2>
      <p class="muted small" style="margin:4px 0 14px">Звёзды зачисляются на баланс и списываются при заказах.</p>
      <div class="wrapx">${chips}</div>
      <div class="gap"></div>
      <input id="topupInput" class="input" inputmode="numeric" placeholder="Своя сумма (${k.min_topup}–${k.max_topup})" value="${t.amount}">
      ${k.crypto ? `<div class="seg" style="margin-top:12px"><button class="${t.method === 'stars' ? 'on' : ''}" data-action="tmethod" data-id="stars">⭐ Звёзды</button><button class="${t.method === 'crypto' ? 'on' : ''}" data-action="tmethod" data-id="crypto">₿ Крипта</button></div>` : ''}
      <button class="btn primary block" style="margin-top:14px" data-action="dotopup">Оплатить</button>`);
    const inp = $('#topupInput');
    inp.addEventListener('input', () => { S.topup.amount = parseInt(inp.value, 10) || 0; document.querySelectorAll('[data-action=tpreset]').forEach((b) => b.classList.toggle('on', Number(b.dataset.n) === S.topup.amount)); });
  }

  async function doTopup() {
    const t = S.topup;
    try {
      const r = await api('/api/topup', { body: { amount: t.amount, method: t.method } });
      if (r.invoice) {
        tg.openInvoice(r.invoice, (st) => { if (st === 'paid') { closeSheet(); waitBalance(S.me.balance); } });
      } else if (r.url) {
        closeSheet();
        tg.openLink(r.url);
        toast('После оплаты баланс обновится сам');
        waitBalance(S.me.balance, 60);
      }
    } catch (e) { toast(e.message, true); }
  }

  async function waitBalance(before, tries = 10) {
    for (let i = 0; i < tries; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      try { await loadMe(); } catch (e) { continue; }
      if (S.me.balance !== before) { toast('Баланс пополнен'); burst(); haptic('medium'); if (S.tab === 'profile') rr(); return; }
    }
  }

  async function applyPromo() {
    const code = $('#promoInput').value.trim();
    if (!code) return toast('Введите промокод', true);
    try {
      const r = await api('/api/promo', { body: { code } });
      toast(`Промокод ${r.code}: скидка ${r.percent}%`);
      haptic('medium');
      await loadMe();
      rr();
    } catch (e) { toast(e.message, true); }
  }

  /* ======================= ПРОЧЕЕ ======================= */
  function renderMore(dir, quiet) {
    const m = S.me, k = cfg(), sec = S.sec.more;
    const nav = secsNav('more', [['premium', 'Premium', 'crown'], ['about', 'О боте', 'info'], ['learn', 'Обучение', 'play']], sec);
    let body = '';

    if (sec === 'premium') {
      const active = m.premium.active;
      const until = active ? new Date(m.premium.until * 1000).toLocaleDateString('ru-RU') : '';
      body = `<div class="body stg">
        <section class="card premium">${wm()}
          <div class="row between"><span class="badge">${ico('crown')}Premium</span>${active ? '<span class="tiny muted">Активен до ' + until + '</span>' : ''}</div>
          <div class="price" style="margin-top:6px"><span>${fmt(k.premium_price)}</span>${ico('star')}</div>
          <p class="muted small" style="margin-bottom:10px">на ${k.premium_days} дней</p>
          <div class="perk"><i>${ico('check')}</i><span>Любое количество эмодзи в заказе — до ${k.premium_limit} вместо ${k.free_limit}</span></div>
          <div class="perk"><i>${ico('check')}</i><span>Значок Premium в профиле</span></div>
        </section>
        <div class="stack">
          <button class="btn primary block" data-action="buyprem" data-m="stars">${active ? 'Продлить' : 'Оформить'} за ${fmt(k.premium_price)} ⭐</button>
          <button class="btn block" data-action="buyprem" data-m="balance">${ico('wallet')}С баланса (${fmt(m.balance)} ⭐)</button>
        </div></div>`;
    } else if (sec === 'about') {
      body = `<div class="body stg">
        <section class="card">${wm()}
          <div class="lab"><span>${ico('info')}О боте</span></div>
          <p style="margin-bottom:8px">Вы выбираете анимированные шаблоны, шрифт и надпись — бот подставляет текст в каждую анимацию и собирает из них набор премиум-эмодзи или стикеров Telegram.</p>
          <p class="muted small">Готовый набор приходит ссылкой в чат с ботом. Свои шаблоны можно добавить в магазине — ими смогут пользоваться все.</p>
        </section>
        <section class="card">${wm('tl')}
          <div class="lab"><span>${ico('bolt')}Сейчас в боте</span></div>
          <div class="stat-row">
            <div class="stat"><span class="muted tiny">Шаблонов</span><b>${fmt(k.templates_total)}</b></div>
            <div class="stat"><span class="muted tiny">Цена 1 эмодзи</span><b>${fmt(k.price)} ⭐</b></div>
          </div>
          <p class="tiny muted" style="margin-top:10px">Или ${k.crystals_per_emoji} ${GEM} за одно эмодзи — кристаллы дают за приглашённых друзей.</p>
        </section></div>`;
    } else {
      body = `<div class="body stg">
        <section class="card">${wm()}
          <div class="lab"><span>${ico('play')}Как это работает</span></div>
          <div class="steps3">
            <div><span>Напишите надпись и выберите шрифт</span></div>
            <div><span>Выберите шаблоны — случайные или вручную</span></div>
            <div><span>Оплатите и получите ссылку на набор</span></div>
          </div>
        </section>
        <button class="btn primary block" data-action="intro">${ico('play')}Смотреть презентацию</button></div>`;
    }
    paint(`${head('lg-more', 'Прочее', 'Premium, информация, обучение')}${nav}${body}`, dir, quiet);
  }

  async function buyPremium(method) {
    try {
      const r = await api('/api/premium', { body: { method } });
      if (r.invoice) {
        tg.openInvoice(r.invoice, async (st) => {
          if (st !== 'paid') return;
          for (let i = 0; i < 8; i++) { await new Promise((x) => setTimeout(x, 1500)); await loadMe(); if (S.me.premium.active) break; }
          if (S.tab === 'more') rr();
          toast('Премиум активирован'); burst(); haptic('medium');
        });
      } else {
        await loadMe(); rr(); toast('Премиум активирован'); burst(); haptic('medium');
      }
    } catch (e) { toast(e.message, true); }
  }

  /* ======================= ЛИСТЫ ======================= */
  function openSheet(html) {
    const root = $('#sheetRoot');
    root.innerHTML = '';
    const o = document.createElement('div');
    o.className = 'overlay';
    o.innerHTML = `<div class="sheet"><svg class="swm"><use href="#logo"/></svg><div class="grab"></div><div class="sheet-body">${html}</div></div>`;
    o.addEventListener('click', (e) => { if (e.target === o) closeSheet(); });
    root.appendChild(o);
  }
  function setSheet(html) {
    const b = $('.overlay:not(.closing) .sheet-body');
    if (b) b.innerHTML = html; else openSheet(html);
  }
  function closeSheet() {
    const root = $('#sheetRoot'), o = root.firstElementChild;
    if (!o || o.classList.contains('closing')) return;
    o.classList.add('closing');
    setTimeout(() => { if (o.parentNode === root) o.remove(); }, 300);
  }

  /* ======================= ОБУЧЕНИЕ (ПРЕЗЕНТАЦИЯ) ======================= */
  function introSlides() {
    const k = cfg();
    const em = ['✨', '🔥', '💎', '🎉', '💖', '🚀', '🌈', '⚡', '🎧'];
    const tiles = em.map((e, i) => `<i style="--k:${i}">${e}<img src="/api/preview/${i + 1}.png" alt=""></i>`).join('');
    const orbit1 = ['🔥', '💖', '🎉', '⚡', '🚀'].map((e, i) => `<i style="--ang:${i * 72}deg">${e}</i>`).join('');
    const orbit2 = ['✨', '🌈', '💎'].map((e, i) => `<i style="--ang:${i * 120 + 30}deg">${e}</i>`).join('');
    return [
      { kick: 'Встречай', head: 'Эмодзи,<br><em>которых ещё</em><br>не было.', sub: 'Анимированные кастом-эмодзи с твоей надписью — всего за пару касаний.',
        vis: `<div class="orbit"><div class="ring">${orbit1}</div><div class="ring r2">${orbit2}</div><div class="core">${lg('logo')}</div></div>` },
      { kick: 'Просто', head: 'Пишешь.<br>Выбираешь.<br><em>Готово.</em>', sub: 'Надпись, шрифт и шаблоны — остальное бот соберёт сам.',
        vis: `<div class="typedemo"><div class="tl">Надпись</div><div class="tx" id="typeDemo"></div><div class="res"><i style="--k:0">✨</i><i style="--k:1">🔥</i><i style="--k:2">💖</i><i style="--k:3">🚀</i></div></div>` },
      { kick: 'Каталог', head: `<em>${fmt(k.templates_total)}</em> анимаций.<br>Одно касание.`, sub: 'Листай магазин, отмечай любимые — или доверь выбор случайности.',
        vis: `<div class="tgrid">${tiles}</div>` },
      { kick: 'Стиль', head: 'Шрифт<br><em>с характером.</em>', sub: 'Выбери почерк, который скажет за тебя — даже без слов.',
        vis: `<div class="fontcycle"><span>Привет!</span><span>Привет!</span><span>Привет!</span><span>Привет!</span></div>` },
      { kick: 'Оплата', head: 'Звёзды.<br>Кристаллы.<br><em>Баланс.</em>', sub: 'Платишь так, как удобно. Скидка по промокоду — сразу в заказе.',
        vis: `<div class="coins"><i style="--k:0">⭐</i><i style="--k:1">💎</i><i style="--k:2">💼</i></div>` },
      { kick: 'Вместе', head: 'Зови друзей —<br><em>получай 💎</em>', sub: 'За каждого друга — кристаллы на заказы. Чем больше друзей, тем больше награда.',
        vis: `<div class="bignum">+<span data-count="${S.me.referral.reward}">0</span><small>💎 за друга</small></div>` },
    ];
  }

  function openIntro() {
    const root = $('#introRoot');
    if (root.firstChild || !S.me) return;
    const sl = introSlides();
    root.innerHTML = `<div id="intro">
      <div class="iwall"><i class="blob b1"></i><i class="blob b2"></i><i class="blob b3"></i><svg id="doodle2"><rect width="100%" height="100%" fill="url(#pat)"/></svg></div>
      <button class="iskip" data-action="introclose">Пропустить</button>
      <div class="idots">${sl.map((_, i) => `<i class="${i ? '' : 'on'}"></i>`).join('')}</div>
      <div class="slides" id="slides">${sl.map((s, i) => `<section class="slide" data-i="${i}">
        <div class="txt"><span class="kick rv2">${s.kick}</span><h2 class="head rv2">${s.head}</h2><p class="subt rv2">${s.sub}</p></div>
        <div class="vis rv2">${s.vis}</div></section>`).join('')}</div>
      <button class="btn primary inext" id="inext" data-action="intronext"><span>Дальше</span>${ico('chev')}</button>
    </div>`;

    const slides = $('#slides'), items = [...slides.children], dots = [...root.querySelectorAll('.idots i')], btn = $('#inext');
    let cur = 0, ticking = false, typeTimer;
    const last = items.length - 1;

    function typeDemo() {
      const el = $('#typeDemo'); if (!el) return;
      clearTimeout(typeTimer);
      const words = ['Привет', 'Мой ник', 'Люблю ♥', 'Огонь'];
      let wi = 0, ci = 0, del = false;
      const step = () => {
        const w = words[wi];
        el.textContent = w.slice(0, ci);
        if (!del && ci < w.length) { ci++; typeTimer = setTimeout(step, 110); }
        else if (!del) { del = true; typeTimer = setTimeout(step, 1300); }
        else if (ci > 0) { ci--; typeTimer = setTimeout(step, 55); }
        else { del = false; wi = (wi + 1) % words.length; typeTimer = setTimeout(step, 300); }
      };
      step();
    }

    function sync() {
      const h = slides.clientHeight, st = slides.scrollTop;
      items.forEach((s, i) => {
        const p = Math.max(-1, Math.min(1, (i * h - st) / h));
        s.style.setProperty('--p', p.toFixed(3));
        s.style.setProperty('--a', Math.abs(p).toFixed(3));
      });
      const idx = Math.max(0, Math.min(last, Math.round(st / h)));
      if (idx !== cur) {
        cur = idx;
        dots.forEach((d, i) => d.classList.toggle('on', i === idx));
        btn.dataset.action = idx === last ? 'introclose' : 'intronext';
        btn.firstElementChild.textContent = idx === last ? 'Начать' : 'Дальше';
        haptic();
      }
      ticking = false;
    }
    slides.addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(sync); } }, { passive: true });

    const seen = new Set();
    const io = new IntersectionObserver((es) => es.forEach((e) => {
      e.target.classList.toggle('in', e.isIntersecting);
      if (e.isIntersecting) {
        const i = Number(e.target.dataset.i);
        if (i === 1) typeDemo();
        if (i === last && !seen.has(i)) { seen.add(i); const n = e.target.querySelector('[data-count]'); if (n) countUp(n, Number(n.dataset.count) || 0, 1200); }
      }
    }), { root: slides, threshold: 0.55 });
    items.forEach((s) => io.observe(s));
    sync();
    root._cleanup = () => { io.disconnect(); clearTimeout(typeTimer); };
  }

  function closeIntro() {
    const root = $('#introRoot'), el = $('#intro');
    if (!el) return;
    try { localStorage.setItem('seenIntro', '1'); } catch (e) {}
    el.classList.add('closing');
    setTimeout(() => { root._cleanup && root._cleanup(); root.innerHTML = ''; }, 420);
  }
  function nextIntro() {
    const sl = $('#slides'); if (!sl) return;
    const h = sl.clientHeight, idx = Math.round(sl.scrollTop / h);
    sl.scrollTo({ top: (idx + 1) * h, behavior: 'smooth' });
  }

  /* ======================= СОБЫТИЯ ======================= */
  document.addEventListener('load', (e) => { if (e.target.tagName === 'IMG') e.target.classList.add('ready'); }, true);
  document.addEventListener('error', (e) => { if (e.target.tagName === 'IMG') e.target.style.visibility = 'hidden'; }, true);

  /* волна при нажатии */
  document.addEventListener('pointerdown', (e) => {
    const t = e.target.closest('.btn, .tab');
    if (!t) return;
    const r = t.getBoundingClientRect(), s = Math.max(r.width, r.height);
    const d = document.createElement('i');
    d.className = 'rip';
    d.style.cssText = `width:${s}px;height:${s}px;left:${e.clientX - r.left - s / 2}px;top:${e.clientY - r.top - s / 2}px`;
    t.appendChild(d);
    setTimeout(() => d.remove(), 650);
  }, { passive: true });

  document.addEventListener('click', async (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    const a = el.dataset.action, d = el.dataset;
    if (el.tagName === 'A') e.preventDefault();
    const c = S.create;

    switch (a) {
      case 'goto': closeSheet(); setTab(d.tab); break;
      /* создать */
      case 'wstep': setStep(Number(d.s)); break;
      case 'next': nextStep(); break;
      case 'prev': setStep(c.step - 1); break;
      case 'fill': c.text = d.v.slice(0, cfg().text_max); haptic(); rr(); break;
      case 'font': c.font = d.id; haptic(); rr(); break;
      case 'kind': c.kind = d.id; haptic(); rr(); break;
      case 'mode': c.mode = d.id; haptic(); rr(); break;
      case 'count': c.count = Number(d.n); haptic(); rr(); break;
      case 'step': {
        const max = Math.min(orderLimit(), cfg().templates_builtin);
        c.count = Math.max(1, Math.min(max, c.count + Number(d.d)));
        if (c.count === max && Number(d.d) > 0 && !S.me.premium.active && !S.me.is_admin) toast('Лимит без премиума — ' + max);
        haptic();
        const num = $('#countNum');
        num.textContent = c.count; num.classList.remove('pop'); void num.offsetWidth; num.classList.add('pop');
        refreshCreateDock();
        document.querySelectorAll('[data-action=count]').forEach((b) => b.classList.toggle('on', Number(b.dataset.n) === c.count));
        break;
      }
      case 'clearsel': S.sel.clear(); S.tab === 'shop' ? refreshTileSel() : rr(); break;
      case 'tocreate': c.mode = 'manual'; c.step = 3; setTab('create'); break;
      case 'preview': showPreview(); break;
      case 'checkout': checkout(); break;
      case 'closesheet': closeSheet(); break;
      case 'closesheet-checkout': closeSheet(); setTimeout(checkout, 200); break;
      case 'pay': pay(d.m); break;
      case 'topup': topupSheet(); break;
      case 'tpreset': S.topup.amount = Number(d.n); drawTopup(); break;
      case 'tmethod': S.topup.method = d.id; drawTopup(); break;
      case 'dotopup': doTopup(); break;
      case 'promo': applyPromo(); break;
      case 'mysets': closeSheet(); S.sec.profile = 'packs'; if (S.tab === 'profile') render(0, false); else setTab('profile'); break;
      case 'buyprem': buyPremium(d.m); break;
      case 'support': tg ? tg.openTelegramLink(cfg().support_url) : window.open(cfg().support_url); break;
      case 'openlink': (/t\.me/.test(d.url) && tg) ? tg.openTelegramLink(d.url) : (tg ? tg.openLink(d.url) : window.open(d.url)); break;
      case 'recheck': try { await loadMe(); render(); } catch (err) { toast(err.message, true); } break;
      case 'reload': S.shop = null; render(); break;
      case 'copy':
        try { await navigator.clipboard.writeText(S.me.referral.link); }
        catch (err) { const t = document.createElement('textarea'); t.value = S.me.referral.link; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); }
        toast('Ссылка скопирована'); haptic(); break;
      case 'share': {
        const url = 'https://t.me/share/url?url=' + encodeURIComponent(S.me.referral.link) + '&text=' + encodeURIComponent('Делаю анимированные эмодзи с надписью — заходи!');
        tg ? tg.openTelegramLink(url) : window.open(url);
        break;
      }
      /* разделы внутри страницы */
      case 'sec': {
        const order = SECS[d.g], cur = S.sec[d.g];
        if (cur === d.id) break;
        const dir = Math.sign(order.indexOf(d.id) - order.indexOf(cur));
        S.sec[d.g] = d.id; haptic(); render(dir); break;
      }
      /* магазин */
      case 'sort': S.shopView.sort = d.id; S.shopView.page = 0; haptic(); drawShop(0, true); break;
      case 'filter': S.shopView.filter = d.id; S.shopView.page = 0; haptic(); drawShop(0, true); break;
      case 'pgprev': shopPage(-1); break;
      case 'pgnext': shopPage(1); break;
      case 'ppprev': S.packPage--; drawPacks(); break;
      case 'ppnext': S.packPage++; drawPacks(); break;
      case 'togglesearch': {
        const v = S.shopView; v.search = !v.search; if (!v.search) v.q = ''; v.page = 0; drawShop(0, true);
        const si = $('#shopSearch'); if (si) si.focus();
        break;
      }
      case 'open': openTemplate(Number(d.n)); break;
      case 'toggle': {
        const n = Number(d.n);
        if (toggleSel(n) !== false) { closeSheet(); if (S.tab === 'shop') refreshTileSel(); toast(S.sel.has(n) ? 'Добавлено в набор' : 'Убрано'); }
        break;
      }
      case 'addtpl': addTemplateSheet(); break;
      case 'uploadtpl': uploadTemplate(); break;
      case 'deltpl':
        try { await api('/api/templates/' + d.n, { method: 'DELETE' }); S.sel.delete(Number(d.n)); S.shop = null; closeSheet(); toast('Шаблон удалён'); render(); }
        catch (err) { toast(err.message, true); }
        break;
      /* обучение */
      case 'intro': openIntro(); break;
      case 'introclose': closeIntro(); break;
      case 'intronext': nextIntro(); break;
    }
  });

  window.addEventListener('resize', () => { if (S.tab === 'shop') fillGrid(); if (S.tab === 'profile' && S.sec.profile === 'packs') drawPacks(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden && S.me) loadMe().then(() => { if (S.tab === 'profile' && S.sec.profile !== 'packs') rr(); }).catch(() => {}); });

  /* ======================= СТАРТ ======================= */
  (async function init() {
    markTabs();
    paint(loaderHtml(), 0, true);
    if (!INIT) {
      paint(lockHtml('Откройте из Telegram', 'Это приложение работает только внутри бота.'), 0, true);
      return;
    }
    try {
      await loadMe();
    } catch (e) {
      paint(lockHtml('Не удалось войти', esc(e.message), '<button class="btn primary" onclick="location.reload()">Повторить</button>'), 0, true);
      return;
    }
    render();
    try { if (!localStorage.getItem('seenIntro') && !S.me.maintenance && !(S.me.gate && S.me.gate.length)) setTimeout(openIntro, 700); } catch (e) {}
  })();
})();
