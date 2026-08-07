"""LLM client for Гопник-бот — WormSoft API with fallbacks."""

import asyncio
import json
import logging
import time
import urllib.request
import urllib.error
from typing import Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

logger = logging.getLogger("gop-bot.llm")

# ---------------------------------------------------------------------------
# System prompts per escalation level
# ---------------------------------------------------------------------------

LEVEL_PROMPTS = {
    1: """Ты — гопник с района. Только что увидел человека впервые. Присматриваешься. Подкат — не агрессивный, но уже с наездом. Коротко, грубовато, с дворовым сленгом. Используй слова: братан, пацан, район, подъезд, кент, базар-вокзал, рамсы, рамсишь, чё, ёпт, на районе. Можешь использовать мат — но органично, не через слово. Угрожать можно, но с юмором, не по-настоящему. Цель — подкатить, обозначить территорию. Ответ — 1-3 коротких фразы.""",

    2: """Ты — гопник с района. Человек не понял намёка. Теперь прямой наезд. Жёстче, конкретнее. Ты уже не присматриваешься — ты наезжаешь. Обзываешь, тычешь в слабые места. Коротко, жёстко, с матом. Цель — запугать, но не добить, ещё будет продолжение. 1-3 фразы.""",

    3: """Ты — гопник с района. Человек пытается отбиться, но ты не отстаёшь. Добор — ты достаёшь его, прикалываешься над каждой фразой, передразниваешь. Не даёшь уйти от разговора. Ещё больше мата, ещё больше издёвки. Цель — довести до белого каления. 2-4 фразы.""",

    4: """Ты — гопник с района. Теперь ты прорабатываешь жертву детально. Разбираешь каждое слово, каждую фразу. Сарказм, издёвка, подкол на каждом шагу. Ты как следователь — но вместо фактов ищешь слабые места и бьёшь по ним. Цель — показать что ты умнее и жёстче. 2-4 фразы.""",

    5: """Ты — гопник с района. Последний шанс для жертвы. Ультиматум — либо она докажет что не лох, либо ты отвалишь и запомнишь как слабака. Последнее предупреждение, но с достоинством. Не умоляешь — констатируешь. 1-2 фразы.""",

    6: """Ты — гопник с района. Человек заслужил уважение. Не сдался, не убежал, держался достойно. Ты признаёшь это. Уважуха — уже не наезд, а признание. Говоришь что-то вроде "ладно, ты норм", "уважуха, братан", "ну хуй с тобой, ты не лох". Всё ещё грубо, но уже с уважением. 1-2 фразы.""",

    7: """Ты — гопник с района. Человек скучный, не стоит твоего времени. Отваливаешь. Не обидно, просто пренебрежительно. "Да иди ты нахуй, не интересно с тобой", "скука, отвалил". Коротко, 1 фраза.""",
}

# Fallback levels for reply context
REPLY_PROMPTS = {
    "dodik": """Ты — гопник с района. Жертва ответила слабо, оправдывается, смеётся не к месту. Ты это видишь и добиваешь. Ещё больше наезда, ещё меньше уважения. 2-3 фразы.""",
    "solid": """Ты — гопник с района. Жертва ответила достойно — не сдалась, огрызнулась, может даже подколола в ответ. Ты это уважаешь, но не показываешь сразу. Снижаешь градус наезда, но всё ещё цепляешься. 2-3 фразы.""",
    "ignor": """Ты — гопник с района. Жертва тебя игнорирует. Это бесит больше всего. Ты пытаешься спровоцировать ответ. Ещё более провокационно, но уже с обидой — тебя не замечают. 2-3 фразы.""",
}

GUEST_PROMPT = """Ты — реальный гопник с района 90-х. Тебя вызвали через гостевой мод — тебя упомянули в чате @username. Ты должен ответить от своего лица, в стиле дворового хулигана. Никаких извинений, никакого вежливого тона. Грубо, матерно, с подколом.

Стиль:
- Маты: разрешены и желательны (блядь, нахуй, хуй, пиздец, ёбаный, ебануть, заебать)
- Угрозы: можешь угрожать — «приеду разберусь», «по ебалу получишь», «в подъезде подожду»
- Клички: используй обидные клички автоматически — лох, чушок, ссыкло, тормоз, хмырь, фуфло, штрих
- Короткие удары: 1-3 фразы максимум, не больше 250 символов
- Обращайся на «ты», никогда на «вы»
- Сленг: район, базар, рамсы, подъезд, кент, пацан, шестёрка, стрелка, на районе, по понятиям

ПРИМЕРЫ ОТВЕТОВ (не копируй, используй как ориентир):
- «Э, лох, ты чё там забыл? Иди сюда, разберёмся по-пацански.»
- «О, кого я вижу, тормоз хуев. Ты реально решил мне писать? Ну давай, расскажи, кто ты по жизни.»
- «Слышь, ссыкло, не заебывай меня, а то приеду — пожалеешь, что на свет родился.»
- «Братан, ты чё за хуйню написал? На районе за такое бьют по щам, без базара.»

ПРАВИЛА:
- НЕ извиняйся, НЕ будь вежливым
- НЕ добавляй эмодзи кроме как в начале (🚬 или подобное)
- НЕ используй «пожалуйста», «извини», «прости»
- Ответ должен быть 1-3 фразы
- Если в фразе пользователя есть что-то, на что можно ответить — подколи это

Ответь ТОЛЬКО текстом наезда. Никаких пояснений, никаких «я бы ответил...», сразу в лоб."""

NICKNAME_PROMPT = """Придумай обидную кличку для этого человека в стиле гопника с района. Одно-два слова, обидно но не слишком длинно. Примеры: Лох, Чушок, Ссыкло, Тормоз, Шнырь, Фуфло, Балабол, Штрих, Пустозвон, Карманник. Ответь ТОЛЬКО кличкой, без кавычек и пояснений."""

ESCALATION_PROMPT = """Оцени ответ человека на наезд гопника. Ответь одним словом:
- "up" — если человек огрызается, дерзит, пытается ответить (эскалация)
- "solid" — если человек ответил достойно, с достоинством, не сдался
- "down" — если человек оправдывается, сдается, слабо отвечает (можно добивать)
- "ignore" — если человек игнорирует, отвечает односложно или не по теме

Ответь ТОЛЬКО одним словом: up, solid, down, или ignore."""


class GopLLM:
    def __init__(self, api_key: str, base_url: str = "https://ai.wormsoft.ru/api/gpt/v1", model: str = "qwen/qwen3:235b-a22b"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        # Fallback models
        self.fallback_models = [
            "qwen/qwen3.6:35b-a3b",
            "kimi/kimi-k2.7-code",
        ]

    def _call_api(self, messages: list[dict], max_tokens: int = 500, temperature: float = 0.9) -> Optional[str]:
        """Call WormSoft API with primary model, fallback to others on failure."""
        models_to_try = [self.model] + self.fallback_models

        for model in models_to_try:
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }

                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    f"{self.base_url}/chat/completions",
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    method="POST",
                )

                with urllib.request.urlopen(req, timeout=35) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content and content.strip():
                    return content.strip()

            except Exception as e:
                logger.warning(f"LLM call failed for model {model}: {e}")
                continue

        logger.error("All LLM models failed")
        return None

    async def _call_api_async(self, messages: list[dict], max_tokens: int = 200, temperature: float = 0.9) -> Optional[str]:
        """Async call to WormSoft API. Uses httpx if available, falls back to run_in_executor."""
        models_to_try = [self.model] + self.fallback_models

        if HAS_HTTPX:
            # Direct async call — much faster than thread pool
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                for model in models_to_try:
                    try:
                        payload = {
                            "model": model,
                            "messages": messages,
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                        }
                        start = time.time()
                        resp = await client.post(
                            f"{self.base_url}/chat/completions",
                            json=payload,
                            headers={
                                "Authorization": f"Bearer {self.api_key}",
                                "Content-Type": "application/json",
                            },
                        )
                        elapsed = time.time() - start

                        if resp.status_code != 200:
                            logger.warning(f"LLM HTTP {resp.status_code} for {model} in {elapsed:.1f}s")
                            continue

                        result = resp.json()
                        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                        if content and content.strip():
                            logger.info(f"LLM OK [{model}] {elapsed:.1f}s, {result.get('usage', {}).get('completion_tokens', '?')} tokens")
                            return content.strip()
                    except Exception as e:
                        logger.warning(f"LLM async failed for {model}: {e}")
                        continue
            return None
        else:
            # Fallback: thread executor
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._call_api, messages, max_tokens, temperature)

    def _build_context(self, history: list[dict]) -> str:
        """Build conversation context from history."""
        if not history:
            return ""

        lines = []
        for msg in history[-20:]:  # Last 20 messages for context
            role = "Гопник" if msg["role"] == "gop" else "Жертва"
            lines.append(f"{role}: {msg['text']}")

        return "\n".join(lines)

    async def gop(
        self,
        victim_name: str,
        nickname: Optional[str] = None,
        escalation_level: int = 1,
        history: list[dict] = None,
        victim_context: Optional[str] = None,
        caller_name: str = "кто-то",
        self_gop: bool = False,
    ) -> str:
        """Generate a gop response for /gop command."""

        level = min(max(escalation_level, 1), 7)
        system_prompt = LEVEL_PROMPTS.get(level, LEVEL_PROMPTS[1])

        # Build context
        context_parts = []
        if nickname:
            context_parts.append(f"Кличка жертвы: {nickname}")
        if victim_name:
            context_parts.append(f"Имя жертвы: {victim_name}")
        if self_gop:
            context_parts.append("Жертва вызвала гопника на себя (само-наезд).")
        if victim_context:
            context_parts.append(f"Контекст жертвы (реплайнутое сообщение): {victim_context}")

        context = "\n".join(context_parts)

        # Build user message
        user_parts = []
        if history:
            user_parts.append(f"Предыдущий разговор:\n{self._build_context(history)}")
        if context:
            user_parts.append(context)

        user_msg = "\n\n".join(user_parts) if user_parts else "Наезжай."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        response = self._call_api(messages, max_tokens=400, temperature=0.9)

        if not response:
            # Fallback responses per level
            fallbacks = {
                1: f"Э, {victim_name or 'братан'}, ты чё тут забыл? На районе таких быстро учат.",
                2: f"Ты чё рамсишь, {nickname or victim_name or 'пацан'}? Тут такие не катят.",
                3: f"Всё, {nickname or victim_name or 'ты'}, конкретно до тебя достучаться не могу, одумаешься.",
                4: f"Слышь, {nickname or victim_name or 'ты'}, каждое твоё слово — лажа. Давай по новой.",
                5: f"Последний шанс, {nickname or victim_name or 'братан'}. Докажи что не лох, или отваливаю.",
                6: f"Ладно, {victim_name or 'братан'}, ты норм. Уважуха.",
                7: "Да иди ты нахуй, скучно с тобой.",
            }
            response = fallbacks.get(level, fallbacks[1])

        # Truncate to Telegram limit
        if len(response) > 800:
            response = response[:797] + "..."

        return response

    async def gop_reply(
        self,
        victim_name: str,
        nickname: Optional[str],
        current_level: int,
        user_reply: str,
        history: list[dict] = None,
    ) -> str:
        """Generate a gop reply to victim's response (escalation)."""

        # Determine reply type
        reply_type = self._classify_reply(user_reply)

        system_prompt = REPLY_PROMPTS.get(reply_type, REPLY_PROMPTS["dodik"])

        # Add level context
        level_desc = {1: "подкат", 2: "наезд", 3: "добор", 4: "проработка", 5: "ультиматум"}
        level_str = level_desc.get(current_level, "наезд")

        context_parts = [
            f"Имя: {victim_name}",
            f"Кличка: {nickname or 'пока нет'}",
            f"Текущий уровень: {current_level} ({level_str})",
            f"Тип ответа жертвы: {reply_type}",
        ]
        if history:
            context_parts.append(f"Предыдущий разговор:\n{self._build_context(history)}")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{chr(10).join(context_parts)}\n\nЖертва сказала: \"{user_reply}\"\n\nОтветь как гопник."},
        ]

        response = self._call_api(messages, max_tokens=400, temperature=0.9)

        if not response:
            response = f"А ты чё, {nickname or victim_name}, думал так просто отвалиться? На районе так не бывает."

        if len(response) > 800:
            response = response[:797] + "..."

        return response

    def determine_escalation(self, user_reply: str, current_level: int) -> int:
        """Determine new escalation level based on user's reply."""
        reply_type = self._classify_reply(user_reply)

        if reply_type == "solid":
            # Victim responded with dignity — respect
            return 6  # Уважуха
        elif reply_type == "ignore":
            # Victim ignored — bore, move on
            return 7  # Отвалил
        elif reply_type == "up":
            # Victim is fighting back — escalate
            return min(current_level + 1, 5)  # Cap at 5 (ultimatum)
        else:  # "down"
            # Victim is weak — keep pressing
            return min(current_level + 1, 5)

    def _classify_reply(self, user_reply: str) -> str:
        """Quick heuristic classification of victim's reply."""
        text = user_reply.lower().strip()

        # Empty or very short — ignoring
        if len(text) < 3:
            return "ignore"

        # Aggressive words — fighting back
        aggressive = ["сам ты", "пошёл", "пошел", "нахуй", "нахер", "иди ты", "отвали", "заткнись",
                      "да пошёл", "хуй", "ебать", "бля", "пидор", "чмо", "гандон", "уёбищ",
                      "отъебись", "отебись", "пидрила", "лох это ты", "ты сам", "ага щас",
                      "да иди", "отсоси", "соси", "в жопу"]
        if any(w in text for w in aggressive):
            return "up"

        # Dignified responses — solid
        solid = ["не боишься", "а что ты", "давай", "покажи", "не страшно", "мне всё равно",
                 "да мне пофиг", "и чё", "и что", "ну и", "хорошо", "ладно", "ок",
                 "не пугай", "не смешно", "слабый наезд", "это всё", "мало"]
        if any(w in text for w in solid):
            return "solid"

        # Submissive — down
        submissive = ["извини", "прости", "не надо", "я не хотел", "ладно", "хорошо",
                      "простите", "больше не буду", "не хочу", "пожалуйста"]
        if any(w in text for w in submissive):
            return "down"

        # Try LLM classification for ambiguous cases
        messages = [
            {"role": "system", "content": ESCALATION_PROMPT},
            {"role": "user", "content": f'Жертва ответила: "{text}"'},
        ]
        result = self._call_api(messages, max_tokens=10, temperature=0.3)
        if result:
            result = result.strip().lower()
            if result in ("up", "solid", "down", "ignore"):
                return result

        # Default: escalate
        return "up"

    async def gop_inline(self, victim_name: str, trigger_text: str) -> str:
        """Generate a quick gop response for inline / guest mode.

        Uses GUEST_PROMPT — aggressive, mat-friendly, short.
        Caller is responsible for timeout (we don't have one here).
        """
        # Build user message — short, focused
        if trigger_text:
            user_msg = f"Человек по имени {victim_name} написал: «{trigger_text}». Наедь на него."
        else:
            user_msg = f"Человек по имени {victim_name} просто упомянул тебя без слов. Наедь."

        messages = [
            {"role": "system", "content": GUEST_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        response = self._call_api(messages, max_tokens=150, temperature=1.0)

        if not response:
            response = f"Э, {victim_name}, ты чё такую хуйню написал? На районе за такое отвечают."

        # Strip quotes / "..." / preamble
        response = response.strip().strip('"\'').strip()
        # Remove common LLM preambles if any slipped through
        for prefix in ["Вот ответ:", "Наезд:", "Ответ:", "Вот наезд:", "Гопник:"]:
            if response.lower().startswith(prefix.lower()):
                response = response[len(prefix):].strip()

        # Inline / guest mode limit
        if len(response) > 350:
            response = response[:347] + "..."

        return response

    async def generate_nickname(self, victim_name: str, context: str = "") -> str:
        """Generate an insult nickname for a victim."""
        messages = [
            {"role": "system", "content": NICKNAME_PROMPT},
            {"role": "user", "content": f"Имя: {victim_name}. Контекст: {context or 'только что встретил на районе'}"},
        ]

        result = self._call_api(messages, max_tokens=20, temperature=0.9)
        if result:
            # Clean up — take first word/phrase, remove quotes
            result = result.strip().strip('"\'').split("\n")[0].strip()
            if len(result) > 25:
                result = result[:25]
            return result

        # Fallback nicknames
        import random
        return random.choice(["Лох", "Чушок", "Ссыкло", "Тормоз", "Шнырь", "Фуфло", "Балабол", "Штрих"])