'use strict';

const S = {
  state: null,
  tab: 'ftk',
  sort: 'score',
  polling: null,
  timer: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* ---------------------------------------------------------------- 小工具 */

function yuan(cents) {
  if (cents === null || cents === undefined) return '—';
  const v = cents / 100;
  return v === 0 ? '免费' : '¥' + (Number.isInteger(v) ? v : v.toFixed(2));
}

function fmtAgo(ts) {
  if (!ts) return '未知';
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return '刚刚';
  if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
  if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
  return Math.floor(diff / 86400) + ' 天前';
}

function fmtLeft(endTs) {
  if (!endTs) return '';
  const left = endTs - Math.floor(Date.now() / 1000);
  if (left <= 0) return { text: '已结束', soon: true };
  const d = Math.floor(left / 86400);
  const h = Math.floor((left % 86400) / 3600);
  const m = Math.floor((left % 3600) / 60);
  let text;
  if (d > 0) text = `剩 ${d} 天 ${h} 小时`;
  else if (h > 0) text = `剩 ${h} 小时 ${m} 分`;
  else text = `剩 ${m} 分钟`;
  return { text, soon: left < 6 * 3600 };
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------------------------------------------------------------- 渲染 */

function currentList() {
  const st = S.state;
  if (!st) return [];
  const key = { ftk: 'ftk', deals: 'deals', wishlist: 'wishlist_deals', weekend: 'weekend' }[S.tab];
  let list = (st[key] || []).slice();
  if (!list.length && st.status === 'demo' && (S.tab === 'ftk' || S.tab === 'deals')) {
    list = (st.demo || []).filter((g) => (S.tab === 'ftk' ? g.kind === 'ftk' : g.kind === 'deal'));
  }
  const sorters = {
    score: (a, b) => (b.score || 0) - (a.score || 0),
    discount: (a, b) => (b.discount || 0) - (a.discount || 0),
    price: (a, b) => (a.final_cents || 0) - (b.final_cents || 0),
    review: (a, b) => (b.review_pct || 0) - (a.review_pct || 0),
  };
  return list.sort(sorters[S.sort] || sorters.score);
}

function cardHtml(g, kind) {
  const isFtk = kind === 'ftk';
  const isWeekend = kind === 'weekend';
  const cover = g.cover && !g.cover.startsWith('data:') ? g.cover : '';
  const initial = esc((g.name || '?').slice(0, 2));
  const badge = isFtk
    ? '<span class="badge free">限时免费</span>'
    : isWeekend
      ? '<span class="tagweekend">限时畅玩</span>'
      : g.discount > 0
        ? `<span class="badge">-${g.discount}%</span>`
        : '';
  const newTag = g.is_new ? '<span class="tagnew">NEW</span>' : '';
  const left = fmtLeft(g.end_ts);
  const reviewTxt = g.review_pct
    ? `<span class="rev ${g.review_pct < 70 ? 'bad' : ''}">${g.review_desc || '好评'} ${g.review_pct}%</span>`
    : '<span>暂无评价</span>';
  const reviewCount = g.review_count ? `<span>${(g.review_count).toLocaleString()} 人评</span>` : '';
  const year = g.release_year ? `<span>${g.release_year}</span>` : '';
  const priceHtml = isFtk
    ? `<div class="price"><span class="now free">免费领取</span>${g.orig_cents ? `<span class="orig">${yuan(g.orig_cents)}</span>` : ''}</div>`
    : `<div class="price"><span class="now">${yuan(g.final_cents)}</span>` +
      (g.orig_cents && g.orig_cents > g.final_cents ? `<span class="orig">${yuan(g.orig_cents)}</span>` : '') +
      (g.discount > 0 ? `<span class="disc">-${g.discount}%</span>` : '') + '</div>';

  return `
  <article class="card ${g.is_new ? 'fresh' : ''}">
    <div class="cover">
      <div class="ph">${initial}</div>
      ${cover ? `<img src="${esc(cover)}" loading="lazy" alt="" onerror="this.style.display='none'">` : ''}
      ${newTag}${badge}
    </div>
    <div class="body">
      <div class="name" title="${esc(g.name)}">${esc(g.name || 'appid ' + g.appid)}</div>
      <div class="meta">${reviewTxt}${reviewCount}${year}${g.in_wishlist ? '<span style="color:#66c0f4">★ 愿望单</span>' : ''}</div>
      ${priceHtml}
      <div class="foot">
        <span class="${left && left.soon ? 'countdown soon' : 'countdown'}">${left ? left.text : (isWeekend ? '限时活动' : '')}</span>
        <a class="open" href="https://store.steampowered.com/app/${g.appid}/" target="_blank" rel="noopener">在 Steam 打开 →</a>
      </div>
    </div>
  </article>`;
}

function render() {
  const st = S.state;
  if (!st) return;

  // 状态行
  const mode = st.result && st.result.mode === 'deep' ? '深度刷新' : '快刷';
  let statusText;
  if (st.job && st.job.running) statusText = '正在刷新…（可以先去忙别的，好了会自动更新）';
  else if (st.status === 'demo') statusText = '示例数据（还没成功连上 Steam）';
  else statusText = `数据更新于 ${fmtAgo(st.updated_at)} · 上次成功 ${st.last_success_text} · ${mode}`;
  $('#statusLine').textContent = statusText;

  // 数字
  $('#cnt-ftk').textContent = st.counts.ftk;
  $('#cnt-deals').textContent = st.counts.deals;
  $('#cnt-wl').textContent = st.counts.wishlist;
  $('#cnt-weekend').textContent = st.counts.weekend;

  // 提示条
  const alert = $('#alert');
  const msgs = [];
  if (st.status === 'demo') {
    msgs.push('<b>当前显示的是项目自带的示例数据。</b>点「刷新」尝试获取实时数据；如果一直失败，请在设置里打开代理（加速器）。');
  }
  if (st.result && st.result.errors && st.result.errors.length) {
    msgs.push(st.result.errors.join(' '));
  }
  if (st.status === 'live' && st.result && st.result.new_ftk && st.result.new_ftk.length) {
    msgs.push(`本次新发现 <b>${st.result.new_ftk.length}</b> 款可领取的限时免费游戏。`);
  }
  if (st.status === 'live' && st.updated_at && Date.now() / 1000 - st.updated_at > 6 * 3600) {
    msgs.push('缓存已经超过 6 小时，数据可能过期，建议刷新一次。');
  }
  if (msgs.length) {
    alert.innerHTML = msgs.join('<br>');
    alert.classList.remove('hidden');
    alert.classList.toggle('err', st.status !== 'demo' && !(st.result && st.result.new_ftk && st.result.new_ftk.length));
  } else {
    alert.classList.add('hidden');
  }

  // 列表
  const list = currentList();
  const panel = $('#panel');
  if (!list.length) {
    const tips = {
      ftk: '目前没有正在进行的限时免费领取活动。Steam 一般每周四前后上新，工具会自动提醒你。',
      deals: '还没有折扣数据，点一下「刷新」。',
      wishlist: '还没有同步愿望单，或者你的愿望单里暂时没有正在打折的游戏。到「设置」里填 SteamID 同步一下。',
      weekend: '当前没有免费周末活动。',
    };
    const cta = st.counts.deals > 0 && S.tab === 'ftk'
      ? '<div style="margin-top:14px"><button class="btn" onclick="switchTab(\'deals\')">去看折扣精选 →</button></div>'
      : '';
    panel.innerHTML = `<div class="empty"><b>${{ ftk: '暂时没有限免', deals: '暂无折扣数据', wishlist: '愿望单是空的', weekend: '没有免费周末' }[S.tab]}</b>${tips[S.tab]}${cta}</div>`;
  } else {
    panel.innerHTML = list.map((g) => cardHtml(g, S.tab)).join('');
  }

  // 健康状态
  const health = st.health || [];
  $('#healthList').innerHTML = health.length
    ? health.map((h) => `<span class="src ${h.ok ? 'ok' : 'bad'}">${esc(h.label)}${h.ok ? ` · ${h.count} 条` : ' · 失败'}${h.via ? ` · ${h.via === 'proxy' ? '代理' : '直连'}` : ''}</span>`).join('')
    : '<span class="src">暂无记录（还没成功刷新过）</span>';

  // 按钮状态
  const running = st.job && st.job.running;
  $('#btnQuick').disabled = running;
  $('#btnDeep').disabled = running;
  $('#btnQuick').classList.toggle('loading', !!running);
}

/* ---------------------------------------------------------------- 数据 */

async function loadState() {
  const res = await fetch('/api/state', { cache: 'no-store' });
  S.state = await res.json();
  if (!S.tabPicked) {
    S.tabPicked = true;
    const fromHash = (location.hash || '').replace('#', '');
    if (['ftk', 'deals', 'wishlist', 'weekend'].includes(fromHash)) S.tab = fromHash;
    else if (!S.state.counts.ftk && S.state.counts.deals) S.tab = 'deals';
  }
  render();
}

function setTab(name) {
  S.tab = name;
  S.tabPicked = true;
  $$('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  history.replaceState(null, '', '#' + name);
  render();
}
window.switchTab = setTab;

async function doRefresh(mode) {
  const btn = mode === 'deep' ? $('#btnDeep') : $('#btnQuick');
  btn.disabled = true;
  try {
    const res = await fetch('/api/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    });
    const data = await res.json();
    // 已经在跑（比如开机自动刷新）也要接上进度，不能什么都不做
    startPolling();
  } catch (e) {
    alert('刷新请求失败：' + e.message);
  } finally {
    btn.disabled = false;
  }
}

function startPolling() {
  if (S.polling) clearInterval(S.polling);
  let ticks = 0;
  S.polling = setInterval(async () => {
    ticks++;
    await loadState();
    const running = S.state && S.state.job && S.state.job.running;
    if (!running) {
      clearInterval(S.polling);
      S.polling = null;
      if (S.state.result && S.state.result.elapsed) {
        const ok = S.state.result.ok;
        const el = S.state.result.elapsed;
        $('#statusLine').textContent = (ok ? '刷新完成' : '刷新失败，已显示缓存') + ` · 耗时 ${el}s`;
      }
    }
    if (ticks > 240) { clearInterval(S.polling); S.polling = null; }
  }, 1500);
}

/* ---------------------------------------------------------------- 设置 */

function fillSettings(st) {
  const s = st.settings || {};
  $('#setProxyEnabled').checked = !!s.proxy_enabled;
  $('#setProxyUrl').value = s.proxy_url || '';
  $('#setNotify').checked = !!s.notify_enabled;
  $('#setAutostart').checked = !!s.autostart;
  $('#setInterval').value = s.auto_refresh_minutes ?? 30;
  $('#setDeepPages').value = s.deep_pages ?? 20;
  $('#setSteamId').value = s.wishlist_steamid || '';
}

async function saveSettings() {
  const patch = {
    proxy_enabled: $('#setProxyEnabled').checked,
    proxy_url: $('#setProxyUrl').value.trim(),
    notify_enabled: $('#setNotify').checked,
    autostart_enabled: $('#setAutostart').checked,
    auto_refresh_minutes: Number($('#setInterval').value || 0),
    deep_pages: Number($('#setDeepPages').value || 20),
    wishlist_steamid: $('#setSteamId').value.trim(),
  };
  const res = await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  const data = await res.json();
  $('#saveMsg').textContent = data.message || '已保存';
  await loadState();
  setTimeout(() => { $('#saveMsg').textContent = ''; }, 3000);
}

/* ---------------------------------------------------------------- 事件 */

function bind() {
  $$('.tab').forEach((btn) => {
    btn.addEventListener('click', () => setTab(btn.dataset.tab));
  });

  $('#sortBy').addEventListener('change', (e) => {
    S.sort = e.target.value;
    render();
  });

  $('#btnQuick').addEventListener('click', () => doRefresh('quick'));
  $('#btnDeep').addEventListener('click', () => {
    if (confirm('深度刷新会扫描几十页数据，大约需要 1-3 分钟，期间可以继续浏览。开始吗？')) doRefresh('deep');
  });

  $('#btnSettings').addEventListener('click', () => {
    if (S.state) fillSettings(S.state);
    $('#settingsDlg').showModal();
  });
  $('#btnCloseSettings').addEventListener('click', () => $('#settingsDlg').close());
  $('#btnSaveSettings').addEventListener('click', saveSettings);

  $('#btnTestNotify').addEventListener('click', async () => {
    const res = await fetch('/api/notify/test', { method: 'POST' });
    const data = await res.json();
    $('#saveMsg').textContent = data.ok ? '已发送，看看右下角' : '发送失败（系统可能禁用了通知）';
  });

  $('#btnSyncWishlist').addEventListener('click', async () => {
    const steamid = $('#setSteamId').value.trim();
    const msg = $('#wishlistMsg');
    if (!steamid) { msg.textContent = '先填 SteamID 或主页链接'; return; }
    msg.textContent = '同步中…';
    const res = await fetch('/api/wishlist/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ steamid }),
    });
    const data = await res.json();
    msg.textContent = data.ok
      ? `同步成功：${data.count} 个愿望，其中 ${data.discounted} 个正在打折`
      : '失败：' + (data.error || '未知错误');
    await loadState();
  });

  $('#btnExport').addEventListener('click', () => {
    const what = S.tab === 'wishlist' ? 'wishlist' : S.tab === 'ftk' ? 'ftk' : 'deals';
    window.location = '/api/export?what=' + what;
  });
}

/* ---------------------------------------------------------------- 启动 */

async function main() {
  bind();
  await loadState();
  // 每分钟刷新一次倒计时；顺便每 5 分钟重新拉一次状态（别的窗口刷新也能同步）
  S.timer = setInterval(() => {
    if (S.state) render();
  }, 60_000);
  setInterval(loadState, 300_000);
}

main();
