"""Configuration and environment."""

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WORMSOFT_API_KEY = os.environ.get("WORMSOFT_API_KEY", "")
WORMSOFT_BASE_URL = os.environ.get("WORMSOFT_BASE_URL", "https://ai.wormsoft.ru/api/gpt/v1")
WORMSOFT_MODEL = os.environ.get("WORMSOFT_MODEL", "deepseek-ai/deepseek-v4-flash")

DB_PATH = os.environ.get("DB_PATH", "data/gop.db")

# Escalation levels
LEVEL_POdkAT = 1       # Подкат — присматриваешься
LEVEL_NAEZD = 2        # Наезд — прямой
LEVEL_DOBOR = 3        # Добор — достаёшь
LEVEL_PRORABOTKA = 4   # Проработка — детально разбираешь
LEVEL_ULTIMATUM = 5    # Ультиматум — последний шанс
LEVEL_UVAZHUKHA = 6    # Уважуха — жертва достойна
LEVEL_OTVALIL = 7      # Отвалил — скучно