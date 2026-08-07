# 🚬 Колян-бот (@kolyan_byrbot)

Гопник Telegram-бот. Наезжает, доёбывается, уважает достойных. Использует Bot API 10.0 **Guest Mode** — работает в любом чате без добавления бота.

## Что умеет

- `@kolyan_byrbot фраза` — наезд в любом чате (guest mode)
- `/gop` — наезд на тебя (в ЛС или где бот добавлен)
- `/gop @username` — наезд на конкретного юзера
- `/gop` в реплай — наезд на того, кого реплайнул
- 7 уровней эскалации (от подката до ультиматума)
- Клички (Лох, Чушок, Ссыкло, Тормоз...) — сохраняются за юзером
- 10 ачивок
- Inline keyboard под каждым наездом

## Команды

```
/gop           — наехать на себя
/gop @user     — наехать на юзера
/gop (reply)   — наехать на реплай-цель
/gop_stop      — отписаться от наездов
/gop_resume    — снова разрешить наезды
/gop_stats     — твоя статистика в чате
/gop_my_nick   — твоя текущая кличка
/gop_achievements — все ачивки
/gop_style     — выбрать стиль
/gop_reset     — сбросить эскалацию и кличку
/gop_help      — список команд
```

## Стек

- Python 3.10
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) 22.8+ (Bot API 10.0)
- SQLite
- [WormSoft API](https://wormsoft.ru) — LLM (qwen/qwen3.6:35b-a3b, fallback kimi)

## Деплой

```bash
# 1. Клонировать
git clone https://github.com/krsksoc/gopnik.git
cd gopnik

# 2. Создать .env
cat > .env << EOF
BOT_TOKEN=...
WORMSOFT_API_KEY=...
WORMSOFT_BASE_URL=https://ai.wormsoft.ru/api/gpt/v1
WORMSOFT_MODEL=qwen/qwen3.6:35b-a3b
EOF

# 3. Установить зависимости
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Запустить
python main.py
```

## systemd

Скопируйте `gop-bot.service` в `/etc/systemd/system/`:

```bash
sudo cp gop-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gop-bot
```

## BotFather настройки

1. `/setprivacy` → Disable (по умолчанию включён, для guest mode не критично)
2. **BotFather MiniApp → Guest Mode → Enable** (Bot API 10.0)
3. Inline Mode — можно оставить disabled, не используется

## Лицензия

Только для личного пользования. Не для продакшена.