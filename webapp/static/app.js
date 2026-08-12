// Пацанский Ход — Telegram WebApp клиент

const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
    tg.ready();
    tg.expand();
} else {
    // Открыто вне Telegram — показываем заглушку
    document.addEventListener('DOMContentLoaded', () => {
        document.body.insertAdjacentHTML('afterbegin', `
            <div class="tg-required">
                <h2>⚠️ Открой через Telegram</h2>
                <p>Это приложение работает только внутри Telegram Mini App.</p>
                <a href="https://t.me/kolyan_byrbot" class="open-tg-btn">🤖 Открыть @kolyan_byrbot</a>
            </div>
        `);
    });
}

function getInitData() {
    if (tg && tg.initData) return tg.initData;
    return '';  // для отладки вне Telegram
}

async function api(path, opts = {}) {
    const headers = opts.headers || {};
    const initData = getInitData();
    if (initData) headers['X-Tg-Init-Data'] = initData;
    headers['Content-Type'] = 'application/json';
    const res = await fetch(path, { ...opts, headers });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status}: ${text}`);
    }
    return res.json();
}

function showToast(text, type = '') {
    const el = document.getElementById('toast');
    if (!el) {
        if (tg) tg.showAlert(text);
        else alert(text);
        return;
    }
    el.textContent = text;
    el.className = 'toast ' + type;
    setTimeout(() => { el.classList.add('hidden'); }, 6000);
}

function showReward(text, color = 'var(--ok)') {
    const div = document.createElement('div');
    div.className = 'float-reward';
    div.textContent = text;
    div.style.color = color;
    document.body.appendChild(div);
    setTimeout(() => div.remove(), 2000);
}

function haptic(type = 'light') {
    if (tg && tg.HapticFeedback) {
        try { tg.HapticFeedback.impactOccurred(type); } catch (_) {}
    }
}

const ACHIEVEMENT_NAMES = {
    "first_gop":       "🥊 Первый гоп",
    "ne_terpila":      "💪 Не терпила",
    "turnikmen":       "🤸 Турникмен",
    "bazar_reshaet":   "🗣 Базар решает",
    "shuhershchik":    "🕵 Шухерщик",
    "glavar":          "👑 Главарь",
    "groza_podezda":   "🚪 Гроза подъезда",
    "semki_magnat":    "🌻 Семочный магнат",
};

let _knownAchievements = new Set();

async function initAchievements() {
    try {
        const data = await api('/api/achievements');
        (data.items || []).forEach(i => { if (i.unlocked) _knownAchievements.add(i.code); });
    } catch (_) {}
}

function showAchievementPopup(code) {
    haptic('heavy');
    const name = ACHIEVEMENT_NAMES[code] || code;
    const overlay = document.createElement('div');
    overlay.className = 'ach-popup-overlay';
    overlay.innerHTML = `
        <div class="ach-popup">
            <div class="ach-confetti">🎉🎊🎉🎊🎉</div>
            <div class="ach-popup-icon">🏆</div>
            <div class="ach-popup-label">ДОСТИЖЕНИЕ ОТКРЫТО!</div>
            <div class="ach-popup-name">${escapeHtml(name)}</div>
            <div class="ach-popup-desc">${escapeHtml(ACHIEVEMENT_DESCRIPTIONS[code] || '')}</div>
            <button class="ach-popup-btn" onclick="this.closest('.ach-popup-overlay').remove()">Круто!</button>
        </div>
    `;
    document.body.appendChild(overlay);
    // Auto-remove after 5s
    setTimeout(() => overlay.remove(), 5000);
    // Telegram notification
    if (tg && tg.showAlert) {
        // Не блокируем, но можем попробовать haptic
    }
}

const ACHIEVEMENT_DESCRIPTIONS = {
    "first_gop":       "Гопни кого-нибудь в первый раз.",
    "ne_terpila":      "Выиграй 10 PvP-боёв.",
    "turnikmen":       "Достигни 5 силы.",
    "bazar_reshaet":   "Достигни 5 базара.",
    "shuhershchik":    "Заработай 1000₽.",
    "glavar":          "Достигни 100 рейтинга или авторитета.",
    "groza_podezda":   "Собери много нычек.",
    "semki_magnat":    "Накопи 50 семок.",
};

// TG photo_url с fallback. Приоритет:
//  1) photo_url из /api/me (БД — сохраняется при register через WebAppUser.photo_url)
//  2) initDataUnsafe.user.photo_url (если БД пуста)
//  3) /static/avatar_default.png (gopnik)
const FALLBACK_AVATAR = '/static/avatar_default.png';

function avatarOrGopnik(url) {
    if (url && typeof url === 'string' && url.length > 4) return url;
    return FALLBACK_AVATAR;
}

function tgPhotoUrl() {
    try {
        const u = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) || null;
        return u && u.photo_url ? u.photo_url : '';
    } catch (_) { return ''; }
}

function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

// ---------------------------------------------------------------------------
// Bottom nav: подсветить активный, гопнуть
// ---------------------------------------------------------------------------
(function initBottomNav() {
    const path = window.location.pathname;
    document.querySelectorAll('.bottom-nav .nav-item').forEach(a => {
        const href = a.getAttribute('href');
        if (href === path || (path === '/' && href === '/')) {
            a.classList.add('active');
        }
    });
    document.querySelectorAll('.nav-gop').forEach(a => {
        a.addEventListener('click', async (e) => {
            e.preventDefault();
            await quickGop();
        });
    });
})();

async function quickGop() {
    // Прежде чем гопнуть, проверим энергию
    let me;
    try { me = await api('/api/me'); } catch (_) {}
    if (me && me.energy < 15) {
        showToast(`⚡ Не хватает энергии. Есть ${me.energy}/15⚡. Подожди или загляни в ⚡ Дела.`, 'error');
        return;
    }
    showToast('🥊 Ищу соперника...', '');
    try {
        const r = await api('/api/action', {
            method: 'POST',
            body: JSON.stringify({ action: 'gop' }),
        });
        showBattleResult(r);
    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
    }
}

function showBattleResult(r) {
    if (!r.ok) {
        showToast(r.message, 'error');
        return;
    }
    // Если есть battle_id — редиректим на полноценный экран
    if (r.battle_id) {
        window.location.href = `/battle/${r.battle_id}`;
        return;
    }
    const win = r.message.includes('ПОБЕДА') || r.message.includes('🏆');
    showToast(r.message, win ? 'ok' : 'error');
}

// ---------------------------------------------------------------------------
// /api/me
// ---------------------------------------------------------------------------
async function loadMe() {
    if (!getInitData()) return;
    try {
        const me = await api('/api/me');
        renderMe(me);
        return me;
    } catch (e) {
        console.error('loadMe:', e);
    }
}

function renderMe(p) {
    const card = document.getElementById('me-card');
    if (!card) return;
    card.classList.remove('hidden');
    const stats = document.getElementById('me-stats');
    const energyLine = p.minutes_to_full > 0
        ? `<div class="row"><span>⚡ Энергия</span><span class="v">${p.energy}/${p.energy_max} (+${p.minutes_to_full} мин)</span></div>`
        : `<div class="row"><span>⚡ Энергия</span><span class="v">${p.energy}/${p.energy_max}</span></div>`;
    stats.innerHTML = `
        <div class="row"><span>Имя</span><span class="v">${escapeHtml(p.first_name)}</span></div>
        <div class="row"><span>📍 Район</span><span class="v">${escapeHtml(p.district_name)}</span></div>
        <div class="row"><span>🎖 Статус</span><span class="v">${escapeHtml(p.status_name)}</span></div>
        <div class="row"><span>💰 Деньги</span><span class="v">${p.money} ₽</span></div>
        <div class="row"><span>🌻 Семки</span><span class="v">${p.semki}</span></div>
        ${energyLine}
        <div class="row"><span>🏆 Рейтинг</span><span class="v">${p.rating} (BR ${p.br})</span></div>
        <div class="row"><span>🥊 W/L</span><span class="v">${p.wins} / ${p.losses}</span></div>
    `;
}

// ---------------------------------------------------------------------------
// /api/rating
// ---------------------------------------------------------------------------
async function loadRating() {
    const list = document.getElementById('rating-list');
    try {
        const data = await api('/api/rating?limit=50');
        const entries = data.entries || [];
        if (entries.length === 0) {
            list.innerHTML = '<div class="empty">Тут пока никого. Будь первым — загляни в ⚡ Дела.</div>';
            return;
        }
        list.innerHTML = entries.map((e, i) => {
            const rankClass = i === 0 ? 'top1' : i === 1 ? 'top2' : i === 2 ? 'top3' : '';
            const selfClass = e.is_self ? 'is-self' : '';
            return `
                <div class="rating-item ${selfClass}">
                    <div class="rank ${rankClass}">#${i + 1}</div>
                    <img class="avatar" src="${avatarOrGopnik(e.photo_url)}" alt="" onerror="this.src='/static/gopnik.png'">
                    <div class="player-info">
                        <div class="player-name">${escapeHtml(e.first_name)}${e.is_self ? ' ⬅️' : ''}</div>
                        <div class="player-meta">${escapeHtml(e.status_name)} · ${e.wins}W/${e.losses}L</div>
                    </div>
                    <div class="player-stats">
                        <div class="rating">${e.rating}</div>
                        <div class="br">BR ${e.br}</div>
                    </div>
                </div>
            `;
        }).join('');
    } catch (e) {
        list.innerHTML = '<div class="empty">Не удалось загрузить рейтинг. Открой из Telegram.</div>';
        console.error('loadRating:', e);
    }
}

// ---------------------------------------------------------------------------
// Profile page
// ---------------------------------------------------------------------------
let myProfile = null;

async function loadProfile() {
    const root = document.getElementById('profile');
    if (!root) return;
    try {
        const p = await api('/api/me');
        myProfile = p;
        renderProfile(p);
        loadDistricts();
    } catch (e) {
        root.innerHTML = '<div class="empty">Не удалось загрузить профиль. Открой из Telegram.</div>';
        console.error('loadProfile:', e);
    }
}

async function loadDistricts() {
    const root = document.getElementById('district-list');
    if (!root) return;
    try {
        const data = await api('/api/districts');
        const list = data.districts || [];
        const current = myProfile ? myProfile.district_code : '';
        const money = myProfile ? myProfile.money : 0;
        root.innerHTML = list.map(d => {
            const isCurrent = d.code === current;
            const canMove = !isCurrent && money >= 500;
            const bonuses = [];
            if (d.bonus_strength > 0) bonuses.push(`+${d.bonus_strength}👊`);
            if (d.bonus_bazar > 0) bonuses.push(`+${d.bonus_bazar}🗣`);
            if (d.bonus_stamina > 0) bonuses.push(`+${d.bonus_stamina}🛡`);
            return `
                <div class="district-card ${isCurrent ? 'is-current' : ''}">
                    <div class="dc-info">
                        <div class="dc-name">${escapeHtml(d.name)} ${isCurrent ? '📍' : ''}</div>
                        <div class="dc-desc">${escapeHtml(d.description || '')}</div>
                        <div class="dc-bonuses">${bonuses.join(' · ')}</div>
                    </div>
                    <button class="dc-btn" data-code="${escapeHtml(d.code)}" ${isCurrent || !canMove ? 'disabled' : ''}>
                        ${isCurrent ? 'Тут' : (canMove ? '500₽' : `${500 - money}₽`)}
                    </button>
                </div>
            `;
        }).join('');
        root.querySelectorAll('.dc-btn:not(:disabled)').forEach(btn => {
            btn.addEventListener('click', async () => {
                if (!confirm('Переехать за 500₽?')) return;
                try {
                    const r = await api(`/api/district/select?district_code=${encodeURIComponent(btn.dataset.code)}`, { method: 'POST' });
                    showToast(r.message, r.ok ? 'ok' : 'error');
                    if (r.ok) loadProfile();
                } catch (e) {
                    showToast('Ошибка: ' + e.message, 'error');
                }
            });
        });
    } catch (e) {
        root.innerHTML = '<div class="empty">Не удалось загрузить районы.</div>';
        console.error('loadDistricts:', e);
    }
}

// Обновляет шапку Двора (уровень, прогресс, валюта, статы)
function updateDvorHeader(p) {
    if (!p) return;
    const name = document.getElementById('me-name');
    if (name) name.textContent = (p.first_name || 'ПАЦАН').toUpperCase();
    const status = document.getElementById('me-status');
    if (status) status.textContent = (p.status_name || 'УЛИЧНЫЙ АВТОРИТЕТ').toUpperCase();
    const money = document.getElementById('me-money');
    if (money) money.textContent = formatNum(p.money);
    const rating = document.getElementById('me-rating');
    if (rating) rating.textContent = p.rating;
    const energy = document.getElementById('me-energy');
    if (energy) energy.textContent = `${p.energy}/${p.energy_max}`;
    const lvl = document.getElementById('me-level');
    if (lvl) lvl.textContent = Math.max(1, 1 + Math.floor(p.rating / 100));
    // Статы
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('stat-strength', p.strength);
    set('stat-authority', p.authority);
    set('stat-stamina', p.stamina);
    set('stat-bazar', p.bazar);
    // Эконом
    const bal = document.getElementById('econ-balance');
    if (bal) bal.textContent = formatNum(p.money) + '₽';
    const inc = document.getElementById('econ-income');
    if (inc) inc.textContent = formatNum(p.rating * 12) + '₽';
    // XP прогресс
    const curXp = p.rating % 100;
    const xpFill = document.getElementById('me-xp-fill');
    if (xpFill) xpFill.style.width = curXp + '%';
    const xpText = document.getElementById('me-xp-text');
    if (xpText) xpText.textContent = `${curXp} / 100 XP`;
    // Аватар
    const av = document.getElementById('me-avatar');
    if (av) {
        const url = avatarOrGopnik(p.photo_url) || tgPhotoUrl() || FALLBACK_AVATAR;
        av.src = url;
        av.onerror = () => { av.src = FALLBACK_AVATAR; };
    }
    // Движение — отключаем если мало энергии
    const dv = document.getElementById('btn-dvizhenie');
    if (dv) {
        if (p.energy < 15) {
            dv.style.opacity = '0.5';
            dv.disabled = true;
        } else {
            dv.style.opacity = '';
            dv.disabled = false;
        }
    }
}

function formatNum(n) {
    if (n == null) return '0';
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 10000) return (n / 1000).toFixed(1) + 'K';
    return n.toString();
}

// Главный экран — Двор
async function loadDvor() {
    if (!document.getElementById('me-name')) return;
    try {
        const p = await api('/api/me');
        myProfile = p;
        updateDvorHeader(p);
        setEnergyGate(p.energy);
    } catch (e) {
        console.error('loadDvor:', e);
    }
}

// Кнопка ДВИЖЕНИЕ (PvP)
function bindDvorButton() {
    const btn = document.getElementById('btn-dvizhenie');
    if (!btn) return;
    btn.addEventListener('click', async () => {
        if (btn.disabled) return;
        haptic('heavy');
        await quickGop();
    });
}

async function loadProfileForActions() {
    try {
        const p = await api('/api/me');
        myProfile = p;
        updateDvorHeader(p);
        setEnergyGate(p.energy);
    } catch (e) {
        console.error('loadProfileForActions:', e);
    }
}

function setEnergyGate(energy) {
    // cost per action
    const gates = [
        { sel: '[data-action="work"]', need: 10 },
        { sel: '[data-action="turnik"]', need: 8 },
        { sel: '[data-action="bazar"]', need: 6 },
        { sel: '[data-action="mutka"]', need: 12 },
        { sel: '[data-action="nychka"]', need: 0 },
    ];
    gates.forEach(({ sel, need }) => {
        document.querySelectorAll(sel).forEach(el => {
            if (need > 0 && energy < need) {
                el.classList.add('disabled-lite');
                el.disabled = true;
            } else {
                el.classList.remove('disabled-lite');
                el.disabled = false;
            }
        });
    });
    // Движение
    const dv = document.getElementById('btn-dvizhenie');
    if (dv) {
        if (energy < 15) {
            dv.style.opacity = '0.5';
            dv.disabled = true;
        } else {
            dv.style.opacity = '';
            dv.disabled = false;
        }
    }
}

function renderProfile(p) {
    const root = document.getElementById('profile');
    if (!root) return;
    const energyPct = Math.round((p.energy / p.energy_max) * 100);
    const energyClass = energyPct < 30 ? 'energy-low' : '';
    root.innerHTML = `
        <img class="avatar-big" src="${avatarOrGopnik(p.photo_url)}" alt="" onerror="this.src='/static/gopnik.png'">
        <h2>${escapeHtml(p.first_name)}</h2>
        <div class="district">📍 ${escapeHtml(p.district_name)} · ${escapeHtml(p.status_name)}</div>
        <div class="energy-bar-container ${energyClass}">
            <div class="energy-bar-bg">
                <div class="energy-bar-fill" style="width: ${energyPct}%"></div>
            </div>
            <div class="energy-bar-text">
                <span class="energy-now">⚡ ${p.energy}/${p.energy_max}</span>
                ${p.minutes_to_full > 0 ? `<span class="energy-regen">+1 через ${formatMinutesShort(p.minutes_to_full)}</span>` : '<span class="energy-full">✨ Полная</span>'}
            </div>
        </div>
        <div class="stats-grid">
            <div class="stat"><div class="label">💰 Деньги</div><div class="value" id="stat-money">${p.money} ₽</div></div>
            <div class="stat"><div class="label">🌻 Семки</div><div class="value">${p.semki}</div></div>
            <div class="stat"><div class="label">⭐ Авторитет</div><div class="value">${p.authority}</div></div>
            <div class="stat"><div class="label">👊 Сила</div><div class="value">${p.strength}</div></div>
            <div class="stat"><div class="label">🗣 Базар</div><div class="value">${p.bazar}</div></div>
            <div class="stat"><div class="label">🛡 Выносливость</div><div class="value">${p.stamina}</div></div>
            <div class="stat"><div class="label">🏆 Рейтинг</div><div class="value">${p.rating}</div></div>
            <div class="stat"><div class="label">⚔️ BR</div><div class="value">${p.br}</div></div>
            <div class="stat"><div class="label">🥊 W/L</div><div class="value">${p.wins}/${p.losses}</div></div>
        </div>
    `;
    startEnergyTimer(p);
}

let energyTimer = null;

function startEnergyTimer(p) {
    if (energyTimer) clearInterval(energyTimer);
    let cur = p.energy;
    let curMax = p.energy_max;
    let minutesLeft = p.minutes_to_full;
    energyTimer = setInterval(() => {
        if (minutesLeft <= 0 || cur >= curMax) {
            clearInterval(energyTimer);
            return;
        }
        // Каждые 5 мин в реальности, но для UI: каждую минуту часы -1 мин, +1/5 энергии
        minutesLeft -= 1;
        if (minutesLeft % 5 === 0) {
            cur = Math.min(curMax, cur + 1);
        }
        updateEnergyBar(cur, curMax, minutesLeft);
    }, 60000);  // каждые 60 сек в UI
}

function updateEnergyBar(cur, max, minutesLeft) {
    const pct = Math.round((cur / max) * 100);
    const fill = document.querySelector('.energy-bar-fill');
    const now = document.querySelector('.energy-now');
    const regen = document.querySelector('.energy-regen');
    const full = document.querySelector('.energy-full');
    if (fill) fill.style.width = pct + '%';
    if (now) now.textContent = `⚡ ${cur}/${max}`;
    if (minutesLeft > 0) {
        if (regen) regen.textContent = `+1 через ${formatMinutesShort(minutesLeft)}`;
        if (full) full.classList.add('hidden');
    } else {
        if (regen) regen.classList.add('hidden');
        if (full) full.classList.remove('hidden');
    }
    // Low energy pulse
    const container = document.querySelector('.energy-bar-container');
    if (container) {
        if (pct < 30) container.classList.add('energy-low');
        else container.classList.remove('energy-low');
    }
}

function formatMinutesShort(min) {
    if (min < 60) return `${min}м`;
    const h = Math.floor(min / 60);
    const m = min % 60;
    return m ? `${h}ч${m}м` : `${h}ч`;
}

// -------------------------------------------------------------------
// Action buttons — работает с .action-btn (старые) и .action-tile (новые)
// -------------------------------------------------------------------
function bindActions() {
    document.querySelectorAll('[data-action]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const action = btn.dataset.action;
            if (btn.disabled) return;
            btn.disabled = true;
            haptic('light');
            const orig = btn.innerHTML;
            btn.classList.add('loading');
            const isTile = btn.classList.contains('action-tile');
            const originalName = isTile ? btn.querySelector('.action-tile-name')?.textContent : null;
            if (isTile) {
                const nm = btn.querySelector('.action-tile-name');
                if (nm) nm.textContent = '...';
            } else {
                btn.innerHTML = '<span class="spinner"></span>';
            }

            try {
                const r = await api('/api/action', {
                    method: 'POST',
                    body: JSON.stringify({ action }),
                });
                if (r.ok) {
                    haptic('medium');
                    showToast(r.message, 'ok');
                    if (action === 'nychka') bindNychkaCountdown();
                    if (r.unlocked && r.unlocked.length > 0) {
                        for (const code of r.unlocked) showAchievementPopup(code);
                    }
                    // Если бой — редирект
                    if (action === 'gop' && r.battle_id) {
                        window.location.href = `/battle/${r.battle_id}`;
                        return;
                    }
                } else {
                    haptic('heavy');
                    showToast(r.message, 'error');
                }
                // Обновляем профиль
                const p = await api('/api/me');
                myProfile = p;
                setEnergyGate(p.energy);
                updateDvorHeader(p);
            } catch (e) {
                showToast('Ошибка: ' + e.message, 'error');
            } finally {
                btn.innerHTML = orig;
                if (isTile && originalName) {
                    const nm = btn.querySelector('.action-tile-name');
                    if (nm) nm.textContent = originalName;
                }
                btn.classList.remove('loading');
                setTimeout(() => {
                    if (myProfile) setEnergyGate(myProfile.energy);
                }, 800);
            }
        });
    });
}

// ---------------------------------------------------------------------------
// Нычка countdown (на странице /actions)
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Нычка countdown — periodically poll /api/nychka/status
// ---------------------------------------------------------------------------
let nychkaInterval = null;

async function bindNychkaCountdown() {
    const btn = document.getElementById('btn-nychka');
    const costEl = document.getElementById('nychka-cost');
    if (!btn) return;

    async function update() {
        try {
            const s = await api('/api/nychka/status');
            if (s.available) {
                btn.disabled = false;
                btn.classList.remove('disabled-lite');
                if (costEl) costEl.textContent = 'раз в 6 часов';
            } else {
                btn.disabled = true;
                btn.classList.add('disabled-lite');
                if (costEl) costEl.textContent = `⏳ ${formatTimeLeft(s.minutes_left)}`;
            }
        } catch (e) {
            console.error('nychka status:', e);
        }
    }

    await update();
    if (nychkaInterval) clearInterval(nychkaInterval);
    nychkaInterval = setInterval(update, 30000);  // каждые 30с
}

function formatTimeLeft(minutes) {
    if (minutes < 60) return `${minutes} мин`;
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    if (m === 0) return `${h}ч`;
    return `${h}ч ${m}мин`;
}

// ---------------------------------------------------------------------------
// Quests page
// ---------------------------------------------------------------------------
async function loadQuests() {
    const activeList = document.getElementById('active-list');
    const completedList = document.getElementById('completed-list');
    if (!activeList) return;

    try {
        const data = await api('/api/quest/active');
        const quests = data.quests || [];
        if (quests.length === 0) {
            activeList.innerHTML = '<div class="empty">Нет активных заданий. Иди в ⚡ Дела.</div>';
        } else {
            activeList.innerHTML = quests.map(renderQuestCard).join('');
            bindQuestClaim();
        }
    } catch (e) {
        activeList.innerHTML = '<div class="empty">Не удалось загрузить задания.</div>';
        console.error('loadQuests active:', e);
    }

    try {
        const data = await api('/api/quest/completed');
        const quests = data.quests || [];
        if (quests.length === 0) {
            completedList.innerHTML = '<div class="empty">Пока ничего не сделал.</div>';
        } else {
            completedList.innerHTML = quests.map(r => renderQuestCard({...r, progress_pct: 100, current: r.target || 1, target: r.target || 1, completed: true, claimed: true})).join('');
        }
    } catch (e) {
        completedList.innerHTML = '<div class="empty">Не удалось загрузить историю.</div>';
        console.error('loadQuests completed:', e);
    }
}

function renderQuestCard(q) {
    const typeLabels = { single: 'Разовый', daily: 'Ежедневный', recurring: 'Повторяемый' };
    const typeLabel = typeLabels[q.type] || q.type;
    const pct = q.progress_pct || 0;
    const icons = { 'Крыша района': '🏠', 'Дело века': '💰', 'Свои люди': '👥', 'Чёрная машина': '🚗', 'Турникмен': '💪', 'Базарный': '🗣' };
    const icon = icons[q.title] || '📋';
    const canClaim = q.completed && !q.claimed;
    const claimBtn = canClaim
        ? `<button class="claim-btn" data-code="${escapeHtml(q.code)}">🎁 Забрать</button>`
        : (q.claimed ? '<span class="claimed">✅ Забрано</span>' : '<span class="in-progress">⏳ Идёт</span>');
    return `
        <div class="quest-card ${q.completed ? 'is-done' : ''}">
            <div class="quest-head">
                <div class="quest-icon">${icon}</div>
                <div class="quest-title">${escapeHtml(q.title)}</div>
                <div class="quest-type">${typeLabel}</div>
            </div>
            <div class="quest-desc">${escapeHtml(q.description)}</div>
            <div class="quest-progress">
                <div class="bar"><span class="fill" style="width: ${pct}%"></span></div>
                <div class="bar-text">${q.current || 0}/${q.target || 1}</div>
            </div>
            <div class="quest-reward">
                <span>💰 ${q.money_reward}</span>
                <span>🏆 ${q.xp_reward} XP</span>
                <span>⭐ ${q.authority_reward}</span>
            </div>
            <div class="quest-action">${claimBtn}</div>
        </div>
    `;
}

function bindQuestClaim() {
    document.querySelectorAll('.claim-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            btn.disabled = true;
            btn.textContent = '⏳';
            try {
                const r = await api('/api/quest/claim?quest_code=' + encodeURIComponent(btn.dataset.code), { method: 'POST' });
                if (r.ok) {
                    showToast(`🎉 Задание выполнено! +${r.money}₽ +${r.xp}XP +${r.authority}⭐`, 'ok');
                    loadQuests();
                } else {
                    showToast(r.message || 'Не удалось забрать', 'error');
                    btn.disabled = false;
                    btn.textContent = '🎁 Забрать';
                }
            } catch (e) {
                showToast('Ошибка: ' + e.message, 'error');
                btn.disabled = false;
                btn.textContent = '🎁 Забрать';
            }
        });
    });
}

function bindQuestTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('active-list').classList.toggle('hidden', tab !== 'active');
            document.getElementById('completed-list').classList.toggle('hidden', tab !== 'completed');
        });
    });
}

function loadLockedQuests() {
    const lockedList = document.getElementById('locked-list');
    if (!lockedList) return;
    api('/api/quest/locked').then(data => {
        const quests = data.quests || [];
        if (quests.length === 0) {
            lockedList.innerHTML = '<div class="empty">Скоро появятся новые задания.</div>';
            return;
        }
        const lockedIcons = { 'Свои люди': '👥', 'Чёрная машина': '🚗', 'Турникмен': '💪', 'Базарный': '🗣', 'Крыша района': '🏠', 'Дело века': '💰' };
        lockedList.innerHTML = quests.map(q => {
            const icon = lockedIcons[q.title] || '📋';
            return `
                <div class="quest-card">
                    <div class="quest-head">
                        <div class="quest-icon">${icon}</div>
                        <div class="quest-title">${escapeHtml(q.title)}</div>
                        <div class="quest-type">🔒</div>
                    </div>
                    <div class="quest-desc">${escapeHtml(q.description)}</div>
                    <div class="quest-progress">
                        <div class="bar"><span class="fill" style="width: 0%"></span></div>
                        <div class="bar-text">${q.current || 0}/${q.target || 1}</div>
                    </div>
                    <div class="quest-reward">
                        <span>💰 ${q.money_reward}</span>
                        <span>🏆 ${q.xp_reward} XP</span>
                        <span>⭐ ${q.authority_reward}</span>
                    </div>
                    <div class="lock-overlay">
                        <div class="lock-icon">🔒</div>
                        <div class="lock-text">Закрыто</div>
                    </div>
                </div>
            `;
        }).join('');
    }).catch(e => {
        lockedList.innerHTML = '<div class="empty">Не удалось загрузить.</div>';
        console.error('loadLockedQuests:', e);
    });
}


// ---------------------------------------------------------------------------
// Clan page
// ---------------------------------------------------------------------------
async function loadClan() {
    const root = document.getElementById('clan-root');
    if (!root) return;
    root.innerHTML = '<div class="loader">Тащим братву...</div>';
    try {
        const myData = await api('/api/clan/my');
        const lbData = await api('/api/clan/leaderboard?limit=10');
        renderClan(myData.clan, lbData.clans || []);
    } catch (e) {
        root.innerHTML = '<div class="empty">Не удалось загрузить братву. Открой из Telegram.</div>';
        console.error('loadClan:', e);
    }
}

function renderClan(myClan, leaderboard) {
    const root = document.getElementById('clan-root');
    if (!root) return;

    if (myClan) {
        let html = `
            <section class="clan-card mine">
                <h2>👥 ${escapeHtml(myClan.name)}</h2>
                <div class="clan-meta">
                    <span>👤 ${myClan.member_count}/${myClan.max_members}</span>
                    <span>⭐ ${myClan.rating}</span>
                    <span>💰 ${myClan.treasury}₽</span>
                </div>
                <div class="clan-role">Твоя роль: ${roleLabel(myClan.member_role)}</div>
            </section>
            <section class="clan-actions">
                <button class="clan-btn danger" id="btn-leave-clan">🚪 Выйти</button>
            </section>
        `;
        root.innerHTML = html;
        document.getElementById('btn-leave-clan').addEventListener('click', async () => {
            if (!confirm('Точно выйти из братвы?')) return;
            try {
                const r = await api('/api/clan/leave', { method: 'POST' });
                showToast(r.message, r.ok ? 'ok' : 'error');
                if (r.ok) loadClan();
            } catch (e) {
                showToast('Ошибка: ' + e.message, 'error');
            }
        });
    } else {
        let html = `
            <section class="clan-empty">
                <h2>👥 Ты не в братве</h2>
                <p>Создай свою или вступи в чужую. Стоимость: 5000₽ + 50⭐ авторитета.</p>
            </section>
            <section class="clan-create">
                <input type="text" id="clan-name" placeholder="Название братвы" maxlength="32">
                <button class="clan-btn" id="btn-create-clan">👥 Создать</button>
            </section>
        `;
        root.innerHTML = html;
        document.getElementById('btn-create-clan').addEventListener('click', async () => {
            const name = document.getElementById('clan-name').value.trim();
            if (!name) { showToast('Введи название', 'error'); return; }
            try {
                const r = await api('/api/clan/create', {
                    method: 'POST',
                    body: JSON.stringify({ name }),
                });
                showToast(r.message, r.ok ? 'ok' : 'error');
                if (r.ok) loadClan();
            } catch (e) {
                showToast('Ошибка: ' + e.message, 'error');
            }
        });
    }

    const lbRoot = document.getElementById('clan-leaderboard');
    if (lbRoot) {
        if (leaderboard.length === 0) {
            lbRoot.innerHTML = '<div class="empty">Братв пока нет. Будь первым!</div>';
        } else {
            lbRoot.innerHTML = leaderboard.map((c, i) => `
                <div class="clan-lb-item">
                    <div class="rank">#${i+1}</div>
                    <div class="name">${escapeHtml(c.name)}</div>
                    <div class="stats">
                        <span>⭐ ${c.rating}</span>
                        <span>👤 ${c.member_count}/10</span>
                    </div>
                    ${(!myClan && c.member_count < 10) ? `<button class="join-btn" data-name="${escapeHtml(c.name)}">Вступить</button>` : ''}
                </div>
            `).join('');
            lbRoot.querySelectorAll('.join-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    try {
                        const r = await api('/api/clan/join?clan_name=' + encodeURIComponent(btn.dataset.name), { method: 'POST' });
                        showToast(r.message, r.ok ? 'ok' : 'error');
                        if (r.ok) loadClan();
                    } catch (e) {
                        showToast('Ошибка: ' + e.message, 'error');
                    }
                });
            });
        }
    }
}

function roleLabel(role) {
    return { boss: '👑 Босс', smotryashiy: '🫡 Смотрящий', patsan: '👤 Пацан' }[role] || role;
}

// ---------------------------------------------------------------------------
// Achievements page
// ---------------------------------------------------------------------------
async function loadAchievements() {
    const root = document.getElementById('achievements-list');
    if (!root) return;
    root.innerHTML = '<div class="loader">Тащим ачивки...</div>';
    try {
        const data = await api('/api/achievements');
        const items = data.items || [];
        if (items.length === 0) {
            root.innerHTML = '<div class="empty">Ачивок пока нет.</div>';
            return;
        }
        const unlocked = items.filter(a => a.unlocked);
        const locked = items.filter(a => !a.unlocked);
        root.innerHTML = `
            <div class="ach-summary">🏆 ${unlocked.length} / ${items.length} разблокировано</div>
            <h3 class="ach-section">✅ Открытые</h3>
            ${unlocked.length === 0 ? '<div class="empty">Пока ничего. Выполняй задания!</div>' : unlocked.map(renderAchievement).join('')}
            <h3 class="ach-section">🔒 Закрытые</h3>
            ${locked.map(renderAchievement).join('')}
        `;
    } catch (e) {
        root.innerHTML = '<div class="empty">Не удалось загрузить ачивки. Открой из Telegram.</div>';
        console.error('loadAchievements:', e);
    }
}

function renderAchievement(a) {
    return `
        <div class="ach-card ${a.unlocked ? 'is-unlocked' : 'is-locked'}">
            <div class="ach-icon">${a.unlocked ? '🏆' : '🔒'}</div>
            <div class="ach-body">
                <div class="ach-name">${escapeHtml(a.name)}</div>
                <div class="ach-desc">${escapeHtml(a.description)}</div>
                ${a.unlocked_at ? `<div class="ach-date">📅 ${escapeHtml(a.unlocked_at)}</div>` : ''}
            </div>
        </div>
    `;
}


// ---------------------------------------------------------------------------
// Battle screen
// ---------------------------------------------------------------------------
async function loadBattle() {
    const detail = document.getElementById('battle-detail');
    const versions = document.getElementById('battle-versions');
    if (!detail) return;

    // Достаём battle_id из URL: /battle/{id}
    const m = window.location.pathname.match(/\/battle\/(\d+)/);
    const battleId = m ? m[1] : null;

    let battle;
    try {
        if (battleId) {
            battle = await api(`/api/battle/${battleId}`);
        } else {
            battle = await api('/api/battle/last');
        }
    } catch (e) {
        versions.innerHTML = '<div class="empty">Не удалось загрузить бой. <a href="/profile">В профиль</a></div>';
        return;
    }

    if (!battle) {
        versions.innerHTML = '<div class="empty">Нет боёв. Гопни кого-нибудь!</div>';
        return;
    }

    // Render last battles list
    const myId = (myProfile && myProfile.tg_id) || battle.attacker_id;
    const isWin = battle.winner_id === myId;
    const isAttacker = battle.attacker_id === myId;

    document.getElementById('battle-sub').textContent = isWin ? '🏆 Победа!' : '💀 Поражение';
    document.getElementById('attacker-name').textContent = battle.attacker_name || 'Лох';
    document.getElementById('defender-name').textContent = battle.defender_name || 'NPC';
    document.getElementById('attacker-status').textContent = isAttacker ? 'Ты' : 'Соперник';
    document.getElementById('defender-status').textContent = !isAttacker ? 'Ты' : 'Соперник';

    const banner = document.getElementById('battle-result-banner');
    banner.className = 'battle-result-banner ' + (isWin ? 'is-win' : 'is-lose');
    banner.textContent = isWin ? '🏆 ПОБЕДА' : '💀 ПОВЕЗЁТ В ДРУГОЙ РАЗ';

    // Log
    const log = document.getElementById('battle-log');
    if (battle.log && Array.isArray(battle.log)) {
        log.innerHTML = battle.log.map((line, i) => {
            const isHighlight = line.includes('💥') || line.includes('🏆') || line.includes('💀');
            const cls = isHighlight ? 'log-line highlight' : 'log-line';
            return `<div class="${cls}" style="animation-delay: ${i * 0.1}s">${escapeHtml(line)}</div>`;
        }).join('');
    } else {
        log.innerHTML = '<div class="empty">Лог боя недоступен.</div>';
    }

    document.getElementById('battle-date').textContent = battle.created_at || '';
    document.getElementById('battle-rating-delta').textContent = (battle.rating_delta >= 0 ? '+' : '') + (battle.rating_delta || 0);
    document.getElementById('battle-money-reward').textContent = (battle.money_reward || 0) + '₽';

    versions.classList.add('hidden');
    detail.classList.remove('hidden');

    // Load other battles list
    try {
        const hist = await api('/api/battles?limit=10');
        const other = (hist.battles || []).filter(b => String(b.id) !== String(battle.id));
        if (other.length > 0) {
            const lb = document.getElementById('battle-versions');
            lb.classList.remove('hidden');
            lb.innerHTML = '<h2>📚 Прошлые бои</h2>' + other.slice(0, 5).map(b => `
                <a href="/battle/${b.id}" class="battle-history-item">
                    <span class="bh-result ${b.is_win ? 'win' : 'lose'}">${b.is_win ? '🏆' : '💀'}</span>
                    <span class="bh-vs">${escapeHtml(b.attacker_name)} vs ${escapeHtml(b.defender_name)}</span>
                    <span class="bh-date">${escapeHtml(b.created_at.split(' ')[0])}</span>
                </a>
            `).join('');
        }
    } catch (_) {}
}
