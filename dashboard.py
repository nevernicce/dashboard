import os
import logging
import asyncio
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv
import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
import telegram

# --- Настройки ---
BOT_NAME = "dashboard"

# --- Переменные окружения ---
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY")

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# --- Логирование ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(BOT_NAME)


# --- Функции для сбора данных ---
async def fetch_coinglass_data():
    logger.info("Попытка получить данные Coinglass через API...")
    if not COINGLASS_API_KEY:
        logger.error("COINGLASS_API_KEY не установлен. Автоматическое получение данных Coinglass невозможно.")
        return None

    symbols = ["BTC", "ETH", "XRP"]
    coinglass_data = {}
    headers = {
        "accept": "application/json",
        "coinglassSecret": COINGLASS_API_KEY
    }

    coingecko_prices = await fetch_coingecko_data()
    if not coingecko_prices:
        logger.warning("Не удалось получить данные с CoinGecko. Цены и изменения будут отображаться как N/A.")
        coingecko_prices = {"BTC": {"price": "N/A", "change_24h": "N/A"}, 
                            "ETH": {"price": "N/A", "change_24h": "N/A"}, 
                            "XRP": {"price": "N/A", "change_24h": "N/A"},
                            "btc_dominance": "N/A"}

    async with aiohttp.ClientSession(headers=headers) as session:
        for symbol in symbols:
            try:
                current_price_data = coingecko_prices.get(symbol, {"price": "N/A", "change_24h": "N/A"})
                current_price = current_price_data.get("price", "N/A")
                change_24h = current_price_data.get("change_24h", "N/A")

                overview_url = f"https://open-api.coinglass.com/api/pro/v1/futures/openInterest?symbol={symbol}"
                async with session.get(overview_url) as response:
                    response.raise_for_status()
                    data = await response.json()
                    if data and data.get("success") and data.get("data"):
                        latest_data = data["data"]
                        coinglass_data[symbol] = {
                            "current_price": current_price,
                            "change_24h": change_24h,
                            "volume_24h": latest_data.get("totalVolume", "N/A"),
                            "open_interest": latest_data.get("openInterest", "N/A"),
                            "long_liquidations_24h": "N/A",
                            "short_liquidations_24h": "N/A",
                            "total_liquidations_24h": "N/A"
                        }
                    else:
                        logger.warning(f"Не удалось получить общие данные по фьючерсам для {symbol} с Coinglass API.")
                        coinglass_data[symbol] = {
                            "current_price": current_price, "change_24h": change_24h, "volume_24h": "N/A", "open_interest": "N/A",
                            "long_liquidations_24h": "N/A", "short_liquidations_24h": "N/A", "total_liquidations_24h": "N/A"
                        }

                liquidations_url = f"https://open-api.coinglass.com/api/pro/v1/liquidation/history?symbol={symbol}&interval=h24"
                async with session.get(liquidations_url) as response:
                    response.raise_for_status()
                    data = await response.json()
                    if data and data.get("success") and data.get("data"):
                        if data["data"]:
                            latest_liquidation_data = data["data"][0]
                            coinglass_data[symbol]["long_liquidations_24h"] = latest_liquidation_data.get("longLiquidation", "N/A")
                            coinglass_data[symbol]["short_liquidations_24h"] = latest_liquidation_data.get("shortLiquidation", "N/A")
                            coinglass_data[symbol]["total_liquidations_24h"] = latest_liquidation_data.get("totalLiquidation", "N/A")
                        else:
                            logger.warning(f"Данные по ликвидациям для {symbol} за 24ч отсутствуют.")
                    else:
                        logger.warning(f"Не удалось получить данные по ликвидациям для {symbol} за 24ч с Coinglass API.")

            except aiohttp.ClientError as e:
                logger.error(f"Ошибка HTTP клиента при запросе к Coinglass API для {symbol}: {e}")
                coinglass_data[symbol] = {
                    "current_price": current_price, "change_24h": change_24h, "volume_24h": "N/A", "open_interest": "N/A",
                    "long_liquidations_24h": "N/A", "short_liquidations_24h": "N/A", "total_liquidations_24h": "N/A"
                }
            except Exception as e:
                logger.error(f"Неизвестная ошибка при получении данных Coinglass для {symbol}: {e}")
                coinglass_data[symbol] = {
                    "current_price": current_price, "change_24h": change_24h, "volume_24h": "N/A", "open_interest": "N/A",
                    "long_liquidations_24h": "N/A", "short_liquidations_24h": "N/A", "total_liquidations_24h": "N/A"
                }
        
    logger.info("Данные Coinglass успешно получены через API.")
    return coinglass_data

async def fetch_fear_greed_index():
    logger.info("Запрос индекса страха и жадности с alternative.me...")
    url = "https://api.alternative.me/fng/?limit=1"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.json()
                # logger.info(f"Получены данные индекса страха и жадности: {data}") # Удален подробный лог

                if data and data.get("data"):
                    latest_data = data["data"][0]
                    return {
                        "value": latest_data.get("value"),
                        "value_classification": latest_data.get("value_classification"),
                        "timestamp": latest_data.get("timestamp")
                    }
                return None
    except aiohttp.ClientError as e:
        logger.error(f"Ошибка HTTP клиента при запросе к alternative.me API: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка при запросе к alternative.me API: {e}")
    return None

async def fetch_coingecko_data():
    logger.info("Запрос данных с CoinGecko...")
    base_url_prices = "https://api.coingecko.com/api/v3/simple/price"
    params_prices = {
        "ids": "bitcoin,ethereum,ripple",
        "vs_currencies": "usd",
        "include_24hr_change": "true"
    }

    base_url_global = "https://api.coingecko.com/api/v3/global"

    prices_data = None
    global_data = None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(base_url_prices, params=params_prices) as response:
                response.raise_for_status()
                prices_data = await response.json()

            async with session.get(base_url_global) as response:
                response.raise_for_status()
                global_data = await response.json()

        result = {
            "BTC": {
                "price": prices_data.get("bitcoin", {}).get("usd"),
                "change_24h": prices_data.get("bitcoin", {}).get("usd_24h_change"),
            },
            "ETH": {
                "price": prices_data.get("ethereum", {}).get("usd"),
                "change_24h": prices_data.get("ethereum", {}).get("usd_24h_change"),
            },
            "XRP": {
                "price": prices_data.get("ripple", {}).get("usd"),
                "change_24h": prices_data.get("ripple", {}).get("usd_24h_change"),
            },
            "btc_dominance": global_data.get("data", {}).get("market_cap_percentage", {}).get("btc") if global_data else "N/A"
        }
        return result
    except aiohttp.ClientError as e:
        logger.error(f"Ошибка HTTP клиента при запросе к CoinGecko API: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка при запросе к CoinGecko API: {e}")
    return None

async def generate_dashboard_post(coinglass_data, fear_greed_data=None, coingecko_data=None):
    logger.info("Генерация поста для дашборда...")

    if not coinglass_data and not fear_greed_data and not coingecko_data:
        return "Данные для генерации поста недоступны."

    # Экранируем дату и время, так как они содержат спецсимволы MarkdownV2
    current_datetime_str = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M")
    post_parts = [f"📊 Дашборд — {current_datetime_str} MSK\n"]

    if coinglass_data:
        for symbol, data in coinglass_data.items():
            post_parts.append(f"{symbol}: ")
            price = data.get("current_price", "N/A")
            change_24h = data.get("change_24h", "N/A")
            
            if isinstance(price, (int, float)) and isinstance(change_24h, (int, float)):
                escaped_price_formatted = f"{price:.2f}"
                escaped_change_24h_formatted = f"{change_24h:.2f}"
                post_parts.append(f"Текущая цена: {escaped_price_formatted} {escaped_change_24h_formatted}% ")
            else:
                escaped_price = price
                escaped_change_24h = change_24h
                post_parts.append(f"Текущая цена: {escaped_price} {escaped_change_24h}%" if escaped_change_24h != 'N/A' else '')

            post_parts.append(f"Объем 24ч: {data.get("volume_24h", "N/A")}")
            post_parts.append(f"Ликвидации 24ч (общие): {data.get("total_liquidations_24h", "N/A")}")
            post_parts.append(f"Ликвидации лонг 24ч: {data.get("long_liquidations_24h", "N/A")}")
            post_parts.append(f"Ликвидации шорт 24ч: {data.get("short_liquidations_24h", "N/A")}")
            post_parts.append(f"Открытый интерес (OI): {data.get("open_interest", "N/A")}\n")
    else:
        post_parts.append("Данные Coinglass недоступны.\n")

    if coingecko_data and coingecko_data.get("btc_dominance") is not None and coingecko_data.get("btc_dominance") != "N/A":
        btc_dominance = coingecko_data.get("btc_dominance")
        if isinstance(btc_dominance, (int, float)):
            escaped_btc_dominance_formatted = f"{btc_dominance:.2f}"
            post_parts.append(f"Доминация BTC: {escaped_btc_dominance_formatted}%\n")
        else:
            escaped_btc_dominance = str(btc_dominance)
            post_parts.append(f"Доминация BTC: {escaped_btc_dominance}%\n")
    elif coingecko_data:
        post_parts.append("Доминация BTC недоступна.\n")
    
    if fear_greed_data:
        fear_greed_value = fear_greed_data.get('value', 'N/A')
        fear_greed_classification = fear_greed_data.get('value_classification', 'N/A')
        post_parts.append(f"Индекс страха и жадности: {fear_greed_value} {fear_greed_classification}\n")
    else:
        post_parts.append("Индекс страха и жадности недоступен.\n")

    final_post = "\n".join(post_parts)
    logger.info("Пост для дашборда успешно сгенерирован.")
    return final_post

async def publish_post_to_channel(bot, post_text):
    max_length = 4000
    chunks = []
    current_chunk = ""

    paragraphs = post_text.split('\n\n')

    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 <= max_length:
            current_chunk += (para + '\n\n')
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = para + '\n\n'
    if current_chunk:
        chunks.append(current_chunk)

    if not chunks:
        logger.warning("Попытка отправить пустой пост.")
        return False

    for i, chunk in enumerate(chunks):
        try:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=chunk.strip(),
                disable_web_page_preview=False,
            )
            if i < len(chunks) - 1:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Не удалось опубликовать часть поста в канал: {e}")
            await bot.send_message(chat_id=ADMIN_ID, text="Не удалось отправить в канал")
            return False
    return True

async def autopost_dashboard(app: Application):
    logger.info("Запуск автопостинга дашборда...")
    
    bot = app.bot
    
    if ADMIN_ID not in app.bot_data:
        app.bot_data[ADMIN_ID] = {}
    
    coinglass_data_api = await fetch_coinglass_data()
    fear_greed_data = await fetch_fear_greed_index()
    coingecko_data = await fetch_coingecko_data() # Получаем данные CoinGecko напрямую

    if COINGLASS_API_KEY is None or COINGLASS_API_KEY == "":
        logger.warning("COINGLASS_API_KEY не установлен. Автопостинг Coinglass данных отменен. Сообщаю администратору.")
        if ADMIN_ID:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ Coinglass API не подключен!\n\n"
                    "Пожалуйста, укажите `COINGLASS_API_KEY` в файле `.env`, чтобы бот мог автоматически получать данные. "
                    "Автоматическая публикация данных Coinglass отменена." 
                ),
            )
        return # Прекращаем выполнение автопостинга
        
    if coinglass_data_api is None:
        logger.error("Ошибка при получении данных Coinglass API. Автопостинг Coinglass данных отменен. Сообщаю администратору.")
        if ADMIN_ID:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "❌ Ошибка Coinglass API!\n\n"
                    "При попытке получить данные с Coinglass API произошла ошибка. "
                    "Пожалуйста, проверьте логи бота для получения дополнительной информации. "
                    "Автоматическая публикация данных Coinglass отменена." 
                ),
            )
        return # Прекращаем выполнение автопостинга
    else:
        logger.info("Данные Coinglass успешно получены через API. Автопостинг продолжается.")
        post_to_publish = await generate_dashboard_post(coinglass_data_api, fear_greed_data, coingecko_data)

    if post_to_publish:
        if await publish_post_to_channel(bot, post_to_publish):
            logger.info(f"Пост дашборда опубликован для админа {ADMIN_ID}.")
        else:
            logger.error(f"Ошибка при публикации поста дашборда для админа {ADMIN_ID}.")
    else:
        logger.warning(f"Не удалось сгенерировать пост дашборда для админа {ADMIN_ID}.")
    
    logger.info("Автопостинг дашборда завершен.")

async def on_startup(app: Application):
    logger.info("Dashboard бот запускается...")
    scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
    # scheduler.add_job(
    #     autopost_dashboard,
    #     "cron",
    #     hour=8,
    #     minute=0,
    #     timezone=MOSCOW_TZ,
    #     args=(app,)
    # )
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    if ADMIN_ID not in app.bot_data:
        app.bot_data[ADMIN_ID] = {}
    logger.info("Планировщик дашборда запущен.")

# --- Команды ---
async def handle_non_admin_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text("Это dashboard для канала @nevernicce_trade, по всем вопросам прошу обращаться к @nevernicce.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Это dashboard для канала @nevernicce_trade, по всем вопросам прошу обращаться к @nevernicce.")

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Команда /test получена")
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("У вас недостаточно прав.")
        return

    await update.message.reply_text("Начинаю тестовый сбор статистики и формирование отчета...")
    
    # Имитируем логику autopost_dashboard для тестового отчета
    coinglass_data_api = await fetch_coinglass_data()
    fear_greed_data = await fetch_fear_greed_index()
    coingecko_data = await fetch_coingecko_data() # Получаем данные CoinGecko напрямую

    if COINGLASS_API_KEY is None or COINGLASS_API_KEY == "":
        logger.warning("COINGLASS_API_KEY не установлен при выполнении /test. Отправка тестового поста Coinglass данных отменена. Сообщаю администратору.")
        await update.message.reply_text(
            "⚠️ Coinglass API не подключен!\n\n"
            "Пожалуйста, укажите `COINGLASS_API_KEY` в файле `.env`, чтобы бот мог автоматически получать данные. "
            "Отправка тестового поста Coinglass данных отменена." 
        )
        return # Прекращаем выполнение команды /test
    elif coinglass_data_api is None:
        logger.error("Ошибка при получении данных Coinglass API при выполнении /test. Отправка тестового поста Coinglass данных отменена. Сообщаю администратору.")
        await update.message.reply_text(
            "❌ Ошибка Coinglass API!\n\n"
            "При попытке получить данные с Coinglass API произошла ошибка. "
            "Пожалуйста, проверьте логи бота для получения дополнительной информации. "
            "Отправка тестового поста Coinglass данных отменена." 
        )
        return # Прекращаем выполнение команды /test
    else:
        logger.info("Данные Coinglass успешно получены через API для /test. Продолжаю.")
        post_to_send = await generate_dashboard_post(coinglass_data_api, fear_greed_data, coingecko_data)

    if post_to_send:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=post_to_send,
                disable_web_page_preview=False,
            )
            logger.info("Тестовый пост дашборда успешно отправлен админу.")
            await update.message.reply_text("Тестовый пост со статистикой успешно отправлен вам.")
        except Exception as e:
            logger.error(f"Не удалось отправить тестовый пост админу: {e}")
            await update.message.reply_text(f"Ошибка при отправке тестового поста: {e}")
    else:
        logger.warning(f"Не удалось сгенерировать пост дашборда для админа {ADMIN_ID}.")
        await update.message.reply_text("Не удалось подготовить тестовый пост дашборда.")

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Команда /report получена")
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("У вас недостаточно прав.")
        return

    await update.message.reply_text("Начинаю сбор статистики и формирование отчета для немедленной публикации...")

    # Имитируем логику autopost_dashboard для немедленной публикации
    bot = context.bot # Для publish_post_to_channel
    
    coinglass_data_api = await fetch_coinglass_data()
    fear_greed_data = await fetch_fear_greed_index()
    coingecko_data = await fetch_coingecko_data() # Получаем данные CoinGecko напрямую

    post_to_publish = None

    if COINGLASS_API_KEY is None or COINGLASS_API_KEY == "":
        logger.warning("COINGLASS_API_KEY не установлен при выполнении /report. Публикация Coinglass данных отменена. Сообщаю администратору.")
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "⚠️ Coinglass API не подключен!\n\n"
                "Пожалуйста, укажите `COINGLASS_API_KEY` в файле `.env`, чтобы бот мог автоматически получать данные. "
                "Публикация Coinglass данных отменена."
            ),
        )
        return # Прекращаем выполнение команды /report
    elif coinglass_data_api is None:
        logger.error("Ошибка при получении данных Coinglass API при выполнении /report. Публикация Coinglass данных отменена. Сообщаю администратору.")
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "❌ Ошибка Coinglass API!\n\n"
                "При попытке получить данные с Coinglass API произошла ошибка. "
                "Пожалуйста, проверьте логи бота для получения дополнительной информации. "
                "Публикация Coinglass данных отменена."
            ),
        )
        return # Прекращаем выполнение команды /report
    else:
        logger.info("Данные Coinglass успешно получены через API для /report. Продолжаю.")
        post_to_publish = await generate_dashboard_post(coinglass_data_api, fear_greed_data, coingecko_data)

    if post_to_publish:
        if await publish_post_to_channel(bot, post_to_publish):
            logger.info("Пост дашборда успешно опубликован в канал по команде /report.")
            await update.message.reply_text("Отчет по дашборду успешно сгенерирован и опубликован в канал.")
        else:
            logger.error("Ошибка при публикации поста дашборда в канал по команде /report.")
            await update.message.reply_text("Пост по дашборду сгенерирован, но не удалось опубликовать в канал. Смотри логи.")
    else:
        logger.warning(f"Не удалось сгенерировать пост дашборда для канала {CHANNEL_ID}.")
        await update.message.reply_text("Не удалось подготовить пост по дашборду для публикации.")

async def report_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Команда /report_admin получена")
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("У вас недостаточно прав.")
        return
    
    await update.message.reply_text("Пожалуйста, введите актуальные данные по фьючерсам Coinglass для BTC, ETH, XRP. "
                                   "Используйте следующий формат для каждой монеты (данные могут быть неполными, 'N/A' или пропущены, если недоступны):\n\n"
                                   "BTC: TV=X, TL=Y, LL=A, SL=B, OI=C; \n"
                                   "ETH: TV=D, TL=E, LL=F, SL=G, OI=H; \n"
                                   "XRP: TV=I, TL=J, LL=K, SL=L, OI=M\n\n"
                                   "Где TV - Общий объем фьючерсов, TL - Общий объем ликвидаций, LL - Объем ликвидаций лонг, SL - Объем ликвидаций шорт, OI - Открытый интерес.\n\n"
                                   "Например:\nBTC: TV=500M, TL=10M, LL=6M, SL=4M, OI=100K; ETH: TV=200M, TL=5M, LL=3M, SL=2M, OI=50K; XRP: TV=100M, TL=2M, LL=1.5M, SL=0.5M, OI=20K\n\n"
                                   "Если данные недоступны или вы не хотите их добавлять, просто ответьте 'N/A'.")
    context.user_data["waiting_for_manual_coinglass_input_channel"] = True
    logger.info("Запрос на ручной ввод Coinglass данных отправлен администратору через команду /report_admin.")

async def handle_admin_manual_coinglass_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
    # Проверяем флаг ожидания ручного ввода для публикации в канал
    if update.effective_user.id == ADMIN_ID and context.user_data.get("waiting_for_manual_coinglass_input_channel"):
        target_chat_id = CHANNEL_ID
        context.user_data["waiting_for_manual_coinglass_input_channel"] = False
    # Проверяем флаг ожидания ручного ввода для отправки админу (тест)
    elif update.effective_user.id == ADMIN_ID and context.user_data.get("waiting_for_manual_coinglass_input_admin"):
        target_chat_id = ADMIN_ID
        context.user_data["waiting_for_manual_coinglass_input_admin"] = False
    else:
        return # Не админ или не ждали ручного ввода

    await update.message.reply_text("Получил данные Coinglass, начинаю обработку...")
    coinglass_input_text = update.message.text
    
    coinglass_parsed_data = {}
    if coinglass_input_text.strip().lower() != "n/a":
        coins_data_str = coinglass_input_text.split(';')
        for coin_str in coins_data_str:
            if ':' in coin_str:
                coin_name, details_str = coin_str.split(':', 1)
                coin_name = coin_name.strip().upper()
                details = {}
                parts = details_str.split(',')
                for part in parts:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        details[key.strip()] = value.strip()
                if coin_name in ["BTC", "ETH", "XRP"]:
                    coinglass_parsed_data[coin_name] = {
                        "volume_24h": details.get("TV", "N/A"),
                        "total_liquidations_24h": details.get("TL", "N/A"),
                        "long_liquidations_24h": details.get("LL", "N/A"),
                        "short_liquidations_24h": details.get("SL", "N/A"),
                        "open_interest": details.get("OI", "N/A")
                    }
    
    # Получаем цены с CoinGecko и объединяем с ручными данными
    coingecko_prices = await fetch_coingecko_data()
    if not coingecko_prices:
        logger.warning("Не удалось получить текущие цены с CoinGecko. Цены будут отображаться как N/A.")
        coingecko_prices = {"BTC": "N/A", "ETH": "N/A", "XRP": "N/A"}

    # Добавляем цены CoinGecko к данным, введенным вручную
    for symbol in ["BTC", "ETH", "XRP"]:
        if symbol not in coinglass_parsed_data:
            coinglass_parsed_data[symbol] = {}
        coinglass_parsed_data[symbol]["current_price"] = coingecko_prices.get(symbol, "N/A")

    # Получаем индекс страха и жадности
    fear_greed_data = await fetch_fear_greed_index()
    if not fear_greed_data:
        logger.warning("Не удалось получить индекс страха и жадности.")

    # Получаем данные CoinGecko напрямую для ручного ввода
    coingecko_data = await fetch_coingecko_data()
    if not coingecko_data:
        logger.warning("Не удалось получить данные с CoinGecko для ручного ввода. Цены и изменения будут отображаться как N/A.")
        coingecko_data = {"BTC": {"price": "N/A", "change_24h": "N/A"}, 
                            "ETH": {"price": "N/A", "change_24h": "N/A"}, 
                            "XRP": {"price": "N/A", "change_24h": "N/A"},
                            "btc_dominance": "N/A"}

    post_to_publish = await generate_dashboard_post(coinglass_parsed_data, fear_greed_data, coingecko_data)

    if post_to_publish:
        try:
            if target_chat_id == CHANNEL_ID:
                if await publish_post_to_channel(context.bot, post_to_publish):
                    logger.info("Пост дашборда с ручными данными успешно опубликован в канал.")
                    await update.message.reply_text("Отчет по дашборду с ручными данными Coinglass сгенерирован и опубликован в канал.")
                else:
                    logger.error("Ошибка при публикации поста дашборда с ручными данными в канал.")
                    await update.message.reply_text("Пост по дашборду с ручными данными сгенерирован, но не удалось опубликовать в канал. Смотри логи.")
            elif target_chat_id == ADMIN_ID:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=post_to_publish,
                    disable_web_page_preview=False,
                )
                logger.info("Тестовый пост дашборда с ручными данными успешно отправлен админу.")
                await update.message.reply_text("Тестовый пост со статистикой с ручными данными Coinglass успешно отправлен вам.")
        except Exception as e:
            logger.error(f"Не удалось отправить пост с ручными данными: {e}")
            await update.message.reply_text(f"Ошибка при отправке поста с ручными данными: {e}")
    else:
        logger.warning("Не удалось сгенерировать пост дашборда с учетом ручных данных.")
        await update.message.reply_text("Не удалось подготовить пост по дашборду с учетом ручных данных.")

async def report_admin_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Команда /report_admin_test получена")
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("У вас недостаточно прав.")
        return

    await update.message.reply_text("Пожалуйста, введите актуальные данные по фьючерсам Coinglass для BTC, ETH, XRP для тестовой публикации. "
                                   "Используйте следующий формат для каждой монеты (данные могут быть неполными, 'N/A' или пропущены, если недоступны):\n\n"
                                   "BTC: TV=X, TL=Y, LL=A, SL=B, OI=C; \n"
                                   "ETH: TV=D, TL=E, LL=F, SL=G, OI=H; \n"
                                   "XRP: TV=I, TL=J, LL=K, SL=L, OI=M\n\n"
                                   "Где TV - Общий объем фьючерсов, TL - Общий объем ликвидаций, LL - Объем ликвидаций лонг, SL - Объем ликвидаций шорт, OI - Открытый интерес.\n\n"
                                   "Например:\nBTC: TV=500M, TL=10M, LL=6M, SL=4M, OI=100K; ETH: TV=200M, TL=5M, LL=3M, SL=2M, OI=50K; XRP: TV=100M, TL=2M, LL=1.5M, SL=0.5M, OI=20K\n\n"
                                   "Если данные недоступны или вы не хотите их добавлять, просто ответьте 'N/A'.")
    context.user_data["waiting_for_manual_coinglass_input_admin"] = True
    logger.info("Запрос на ручной ввод Coinglass данных отправлен администратору через команду /report_admin_test.")

def main():
    if not TELEGRAM_BOT_TOKEN or not CHANNEL_ID or not ADMIN_ID:
        logger.error("Необходимо указать TELEGRAM_BOT_TOKEN, CHANNEL_ID и ADMIN_ID в .env")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(on_startup).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("report_admin", report_admin_command))
    application.add_handler(CommandHandler("report_admin_test", report_admin_test_command))

    # Обработчик для ручного ввода Coinglass данных администратором
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND) & filters.User(ADMIN_ID), handle_admin_manual_coinglass_input))
    # Обработчик для всех остальных сообщений
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_non_admin_messages))
    # Обработчик для всех остальных сообщений, включая медиа, для не-админов
    application.add_handler(MessageHandler(~filters.User(ADMIN_ID) & filters.ALL, handle_non_admin_messages))


    logger.info(f"Версия Python-Telegram-Bot: {telegram.__version__}")
    logger.info(f"{BOT_NAME} запущен...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
