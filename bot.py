import os
import time
import base64
import random
from io import BytesIO

import discord
from discord import app_commands, Interaction
from discord.ext import commands
from dotenv import load_dotenv

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore


# Отладочная информация
print("🔍 Загружаю переменные окружения...")
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GUILD_ID_RAW = os.getenv("GUILD_ID", "").strip()

print(f"🔑 DISCORD_TOKEN: {'✅ Установлен' if DISCORD_TOKEN else '❌ Не найден'}")
print(f"🔑 OPENAI_API_KEY: {'✅ Установлен' if OPENAI_API_KEY else '❌ Не найден'}")
print(f"🔑 GUILD_ID: {'✅ Установлен' if GUILD_ID_RAW else '❌ Не указан'}")

if not DISCORD_TOKEN:
    raise RuntimeError("Не задан DISCORD_TOKEN в .env")

# Инициализация клиента OpenAI (если ключ указан)
openai_client = None
if OPENAI_API_KEY and OpenAI is not None:
    print("🤖 Инициализирую OpenAI клиент...")
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    print("✅ OpenAI клиент готов!")
else:
    print("❌ OpenAI клиент не инициализирован")
    if not OPENAI_API_KEY:
        print("   Причина: OPENAI_API_KEY не найден в .env")
    if OpenAI is None:
        print("   Причина: Пакет openai не установлен")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Общая перезарядка для /image (глобально для всей команды)
IMAGE_COOLDOWN_SECONDS = 180
_last_image_command_ts: float = 0.0


def _seconds_left_for_image() -> int:
    if _last_image_command_ts <= 0:
        return 0
    remain = int(_last_image_command_ts + IMAGE_COOLDOWN_SECONDS - time.time())
    return remain if remain > 0 else 0


@bot.event
async def on_ready():
    guild_for_sync = None
    if GUILD_ID_RAW:
        try:
            guild_for_sync = discord.Object(id=int(GUILD_ID_RAW))
        except ValueError:
            print("GUILD_ID задан неверно, пропускаю локальную синхронизацию.")

    try:
        if guild_for_sync is not None:
            await tree.sync(guild=guild_for_sync)
            print(f"Слэш-команды синхронизированы для гильдии {GUILD_ID_RAW}")
        else:
            await tree.sync()
            print("Слэш-команды синхронизированы глобально (может занять до часа видимости)")
    except Exception as e:
        print(f"Ошибка синхронизации слэш-команд: {e}")

    print(f"Вошёл как {bot.user} (ID: {bot.user.id})")


@app_commands.describe(prompt="Кратко опишите желаемое изображение")
@tree.command(name="image", description="Сгенерировать изображение по запросу")
async def image_cmd(interaction: Interaction, prompt: str):
    global _last_image_command_ts

    # Проверка перезарядки (глобальная)
    remain = _seconds_left_for_image()
    if remain > 0:
        await interaction.response.send_message(
            f"Эта команда на перезарядке. Подождите ещё {remain} сек.", ephemeral=True
        )
        return

    if openai_client is None:
        await interaction.response.send_message(
            "OpenAI API не настроен. Укажите OPENAI_API_KEY в .env", ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        # Генерация изображения через OpenAI Images API
        result = openai_client.images.generate(
            model="dall-e-3",  # Используем более новую модель
            prompt=prompt,
            size="1024x1024",
        )
        image_url = result.data[0].url
        
        # Создаем embed с изображением
        embed = discord.Embed(title="🖼️ Сгенерированное изображение", description=f"**Запрос:** {prompt}")
        embed.set_image(url=image_url)
        embed.set_footer(text="Создано с помощью OpenAI DALL-E 3")
        
        await interaction.followup.send(embed=embed)
        _last_image_command_ts = time.time()
        
    except Exception as e:
        error_msg = str(e)
        
        # Обработка конкретных ошибок OpenAI
        if "billing_hard_limit_reached" in error_msg:
            await interaction.followup.send(
                "❌ **Ошибка биллинга OpenAI:** Достигнут лимит по оплате.\n\n"
                "Данная команда временно недоступна.",
                ephemeral=True
            )
        elif "insufficient_quota" in error_msg:
            await interaction.followup.send(
                "❌ **Недостаточно квоты:** Исчерпан лимит API запросов.\n\n"
                "Попробуйте позже или проверьте настройки биллинга.", 
                ephemeral=True
            )
        elif "rate_limit" in error_msg:
            await interaction.followup.send(
                "⚠️ **Превышен лимит запросов:** Слишком много запросов к API.\n\n"
                "Подождите немного и попробуйте снова.", 
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ Не удалось сгенерировать изображение:\n```{error_msg}```", 
                ephemeral=True
            )


@app_commands.describe(query="Ваш вопрос к нейросети")
@tree.command(name="ask", description="Ответ нейросети на вопрос")
async def ask_cmd(interaction: Interaction, query: str):
    if openai_client is None:
        await interaction.response.send_message(
            "OpenAI API не настроен. Укажите OPENAI_API_KEY", ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        # Текстовый ответ (чат-модель)
        resp = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты кратко и по делу отвечающий помощник на русском языке."},
                {"role": "user", "content": query},
            ],
            temperature=0.7,
        )
        answer = resp.choices[0].message.content or "(пустой ответ)"
        
        # Создаем embed для ответа
        embed = discord.Embed(title="🤖 Ответ нейросети", description=answer)
        embed.add_field(name="Вопрос", value=query, inline=False)
        embed.set_footer(text="Ответ сгенерирован с помощью OpenAI GPT-4o-mini")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        error_msg = str(e)
        
        # Обработка конкретных ошибок OpenAI
        if "billing_hard_limit_reached" in error_msg:
            await interaction.followup.send(
                "❌ **Ошибка биллинга OpenAI:** Достигнут лимит по оплате.\n\n"
                "Команда `/ask` временно недоступна.", 
                ephemeral=True
            )
        elif "insufficient_quota" in error_msg:
            await interaction.followup.send(
                "❌ **Недостаточно квоты:** Исчерпан лимит запросов.\n\n"
                "Попробуйте позже или проверьте настройки биллинга.", 
                ephemeral=True
            )
        elif "rate_limit" in error_msg:
            await interaction.followup.send(
                "⚠️ **Превышен лимит запросов:** Слишком много запросов.\n\n"
                "Подождите немного и попробуйте снова.", 
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ Ошибка при запросе к нейросети:\n```{error_msg}```", 
                ephemeral=True
            )


@app_commands.describe(question="О чём спросить шар?")
@tree.command(name="ball", description="Шар судьбы: да/нет/незнаю/возможно")
async def ball_cmd(interaction: Interaction, question: str):
    answers = ["да", "нет", "незнаю", "возможно"]
    choice = random.choice(answers)
    
    # Создаем embed для ответа шара
    embed = discord.Embed(title="🔮 Шар судьбы", color=0x9b59b6)
    embed.add_field(name="Вопрос", value=question, inline=False)
    embed.add_field(name="Ответ", value=f"**{choice.upper()}**", inline=False)
    
    # Добавляем эмодзи в зависимости от ответа
    if choice == "да":
        embed.add_field(name="", value="✅", inline=False)
    elif choice == "нет":
        embed.add_field(name="", value="❌", inline=False)
    elif choice == "незнаю":
        embed.add_field(name="", value="🤷‍♂️", inline=False)
    else:
        embed.add_field(name="", value="🤔", inline=False)
    
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
