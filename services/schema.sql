-- ============================================================================
-- Пацанский Ход — игровая схема (миграция поверх существующего gop.db)
-- ============================================================================
-- Идемпотентен. Применяется через services.migrate.ensure_schema().
-- ============================================================================

-- ---------------------------------------------------------------------------
-- districts — справочник районов (чтобы потом добавлять бонусы)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS districts (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    bonus_strength INTEGER DEFAULT 0,
    bonus_bazar INTEGER DEFAULT 0,
    bonus_stamina INTEGER DEFAULT 0
);

INSERT OR IGNORE INTO districts(code, name, description) VALUES
    ('severniy',        'Северный',       'Суровый, продувается ветром. Здесь зима длиннее, а пацаны крепче.'),
    ('solnechny',       'Солнечный',      'Ламповый, бабушки на лавках, районные сплетни за чаем.'),
    ('cheremushki',     'Черёмушки',      'Классика панелек. Каждый второй подъезд — потенциальная нычка.'),
    ('vzletka',         'Взлётка',        'Новостройки, амбициозные, пахнут штукатуркой и понтами.'),
    ('zelenaya_roshcha','Зелёная Роща',   'Тихий, но опасный. Самые громкие разборки — у школы.'),
    ('pyatak',          'Пятак',          'Рыночный, торговый. Тут базар решает всё, даже уважуху.'),
    ('zavodskoy',       'Заводской',      'Пролетарский, грубый. Здесь мужики закалённые и слова не подбирают.');

-- ---------------------------------------------------------------------------
-- users (только если таблицы вообще нет — миграция колонок в services.migrate)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    last_name TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- clans — братвы
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    owner_id INTEGER NOT NULL,
    level INTEGER DEFAULT 1,
    max_members INTEGER DEFAULT 10,
    treasury INTEGER DEFAULT 0,
    rating INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_clans_rating ON clans(rating DESC);

-- ---------------------------------------------------------------------------
-- clan_members — участники (с soft-delete через left_at)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clan_members (
    clan_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'patsan',  -- boss | smotryashiy | patsan
    joined_at TEXT DEFAULT (datetime('now')),
    left_at TEXT DEFAULT NULL,
    PRIMARY KEY (clan_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_clan_members_user ON clan_members(user_id, left_at);

-- ---------------------------------------------------------------------------
-- battles — PvP бои (числа отдельно + JSON для реплея)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS battles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attacker_id INTEGER NOT NULL,
    defender_id INTEGER NOT NULL,         -- 0 = NPC
    winner_id INTEGER NOT NULL,           -- 0 = NPC победил
    attacker_br INTEGER NOT NULL,
    defender_br INTEGER NOT NULL,
    attacker_hp INTEGER NOT NULL,
    defender_hp INTEGER NOT NULL,
    turns_count INTEGER NOT NULL,
    log_json TEXT NOT NULL,               -- реплей: список ударов
    rating_delta INTEGER DEFAULT 0,
    money_stolen INTEGER DEFAULT 0,
    energy_lost INTEGER DEFAULT 0,
    is_npc INTEGER DEFAULT 0,             -- 1 если defender_id=0
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_battles_attacker ON battles(attacker_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_battles_defender ON battles(defender_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- game_achievements — игровые достижения
--   first_gop, ne_terpila, turnikmen, bazar_reshaet, shuhershchik,
--   glavar, groza_podezda, semki_magnat
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    achievement_code TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    unlocked_at TEXT DEFAULT (datetime('now')),
    UNIQUE(achievement_code, user_id)
);

CREATE INDEX IF NOT EXISTS idx_game_ach_user ON game_achievements(user_id);

-- ---------------------------------------------------------------------------
-- status_history — трекинг переходов по статусам
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_status_hist_user ON status_history(user_id, changed_at DESC);

-- ---------------------------------------------------------------------------
-- quests — ежедневные/разовые задания от Шрупа
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'single',  -- single | daily | recurring
    xp_reward INTEGER DEFAULT 0,
    money_reward INTEGER DEFAULT 0,
    authority_reward INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT DEFAULT NULL         -- NULL = не истекает
);

CREATE INDEX IF NOT EXISTS idx_quests_code ON quests(code);

-- ---------------------------------------------------------------------------
-- user_quests — прогресс каждого юзера по квестам
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_quests (
    quest_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',  -- active | completed | expired
    completed_at TEXT DEFAULT NULL,
    PRIMARY KEY (quest_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_quests_user ON user_quests(user_id);

-- ---------------------------------------------------------------------------
-- user_quest_progress — детальная прогрессия по квестам (сколько сделал)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_quest_progress (
    quest_code TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    progress INTEGER DEFAULT 0,
    target INTEGER DEFAULT 1,
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(quest_code, user_id)
);

CREATE INDEX IF NOT EXISTS idx_uqp_user ON user_quest_progress(user_id);

-- ---------------------------------------------------------------------------
-- user_active_quests — текущие активные квесты юзера
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_active_quests (
    quest_code TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_active_quests_user ON user_active_quests(user_id);

-- ---------------------------------------------------------------------------
-- Добавляем clan_id в users (если ещё нет)
-- ---------------------------------------------------------------------------
-- NOTE: это нужно делать через migrate.py, а не напрямую здесь,
-- потому что ALTER TABLE ADD COLUMN может не работать поверх существующей DB.
