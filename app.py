import streamlit as st
import asyncio
import threading
import time
import os
import re
from datetime import datetime, timezone, timedelta
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
import aiohttp
from telethon.errors import FloodWaitError
import queue
x
# Streamlit page configuration
st.set_page_config(
    page_title="1ux4u4nt-t124d9",
    page_icon="📱",
    layout="wide"
)

# Define WIB timezone (GMT+7)
wib_tz = timezone(timedelta(hours=7))

# Global variables for thread communication
message_queue = queue.Queue()
stats_queue = queue.Queue()

# Custom formatter with WIB timezone
class WIBFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, wib_tz)
        if datefmt:
            s = dt.strftime(datefmt)
        else:
            s = dt.strftime("%Y-%m-%d %H:%M:%S WIB")
        return s

# Configure logging with WIB formatter
wib_formatter = WIBFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler = logging.FileHandler("telegram_forwarder.log")
file_handler.setFormatter(wib_formatter)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(wib_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler]
)
logger = logging.getLogger(__name__)

# Load secrets
API_ID = st.secrets["telegram"]["api_id"]
API_HASH = st.secrets["telegram"]["api_hash"]
PHONE_NUMBER = st.secrets["telegram"]["phone_number"]
SESSION_STRING = st.secrets["telegram"]["session_string"]

SOURCE_CHANNEL_ID = st.secrets["channels"]["source_channel_id"]

# Source Channels untuk berita crypto
SOURCE_CHANNEL_ID_2 = st.secrets["channels"]["crypto_news_1"]  
SOURCE_CHANNEL_ID_3 = st.secrets["channels"]["crypto_news_2"]   
SOURCE_CHANNEL_ID_4 = st.secrets["channels"]["crypto_news_3"]  
SOURCE_CHANNEL_ID_5 = st.secrets["channels"]["crypto_news_4"]  
SOURCE_CHANNEL_ID_6 = st.secrets["channels"]["crypto_news_5"]  

NEWS_TOPIC_ID = st.secrets["topics"]["news_topic_id"]          
NEW_TOPIC_ID = st.secrets["topics"]["new_topic_id"]
SIGNAL_CALL_TOPIC_ID = st.secrets["topics"]["signal_call_topic_id"] 
SIGNAL_UPDATE_TOPIC_ID = st.secrets["topics"]["signal_update_topic_id"] 

GROUP_CHANNEL_ID = st.secrets["channels"]["group_channel_id"]  

# Files to store verification code
VERIFICATION_CODE_FILE = "verification_code.txt"
LOG_FILE = "bot_logs.txt"

# Thread-safe stats counter
class ThreadSafeStats:
    def __init__(self):
        self._total_forwarded = 0
        self._lock = threading.Lock()
    
    def increment(self):
        with self._lock:
            self._total_forwarded += 1
            return self._total_forwarded
    
    def get_count(self):
        with self._lock:
            return self._total_forwarded

# Global stats instance
bot_stats = ThreadSafeStats()

# Session state initialization with proper defaults
if 'running' not in st.session_state:
    st.session_state['running'] = False
if 'total_forwarded' not in st.session_state:
    st.session_state['total_forwarded'] = 0
if 'log_messages' not in st.session_state:
    st.session_state['log_messages'] = []

# Function to save log to file
def write_log(message, is_error=False):
    try:
        with open(LOG_FILE, "a") as f:
            timestamp = datetime.now().strftime("%H:%M:%S")
            f.write(f"{timestamp} WIB - {'ERROR' if is_error else 'INFO'} - {message}\n")
        
        # Add to queue for UI updates
        try:
            message_queue.put_nowait({
                'time': timestamp,
                'message': message,
                'error': is_error
            })
        except queue.Full:
            pass  # Ignore if queue is full
            
    except Exception as e:
        logger.error(f"Failed to write log to file: {str(e)}")

# Function to read logs from file
def read_logs():
    logs = []
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r") as f:
                for line in f.readlines():
                    parts = line.strip().split(" - ", 2)
                    if len(parts) >= 3:
                        timestamp = parts[0]
                        level = parts[1]
                        message = parts[2]
                        logs.append({
                            'time': timestamp,
                            'message': message,
                            'error': level == 'ERROR'
                        })
    except Exception as e:
        logger.error(f"Failed to read logs from file: {str(e)}")
    return logs

# Function to get verification code
def code_callback():
    logger.info("Waiting for verification code...")
    if os.path.exists(VERIFICATION_CODE_FILE):
        os.remove(VERIFICATION_CODE_FILE)
    
    write_log("Bot needs verification code. Please enter the verification code in Telegram.")
    
    while not os.path.exists(VERIFICATION_CODE_FILE):
        time.sleep(1)
    
    with open(VERIFICATION_CODE_FILE, "r") as f:
        code = f.read().strip()
    
    os.remove(VERIFICATION_CODE_FILE)
    write_log(f"Verification code received: {code}")
    return code

# Function to calculate percentage change
def calculate_percentage_change(entry_price, target_price):
    try:
        entry = float(entry_price)
        target = float(target_price)
        
        if entry < 0.0001:
            logger.warning(f"Entry price too small: {entry}, using default")
            return 0.0
            
        percentage = ((target - entry) / entry) * 100
        
        if abs(percentage) > 1000:
            logger.warning(f"Percentage too large: {percentage}, limited to ±1000%")
            percentage = 1000.0 if percentage > 0 else -1000.0
            
        return percentage
    except (ValueError, ZeroDivisionError):
        logger.error(f"Error calculating percentage: {entry_price}, {target_price}")
        return 0.0

# Function to get current cryptocurrency price
async def get_current_price(coin_symbol):
    try:
        base_symbol = coin_symbol.replace('USDT', '')
        
        binance_url = f"https://api.binance.com/api/v3/ticker/price?symbol={coin_symbol}"
        async with aiohttp.ClientSession() as session:
            async with session.get(binance_url) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'price' in data:
                        return float(data['price'])
                
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={base_symbol.lower()}&vs_currencies=usd"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if base_symbol.lower() in data:
                        return data[base_symbol.lower()]['usd']
                
        return None
    except Exception as e:
        logger.error(f"Error getting price: {str(e)}")
        return None

# Rate limiting handler
async def send_message_with_retry(client, entity, message, reply_to=None, retries=3):
    """Send message with automatic retry and rate limiting handling"""
    for attempt in range(retries):
        try:
            if reply_to:
                await client.send_message(entity=entity, message=message, reply_to=reply_to)
            else:
                await client.send_message(entity=entity, message=message)
            return True
            
        except FloodWaitError as e:
            wait_time = e.seconds
            logger.warning(f"Rate limited. Waiting {wait_time} seconds...")
            write_log(f"Rate limited. Waiting {wait_time} seconds...", True)
            
            # If wait time is too long, skip this message
            if wait_time > 300:  # 5 minutes
                logger.error(f"Wait time too long ({wait_time}s), skipping message")
                write_log(f"Wait time too long ({wait_time}s), skipping message", True)
                return False
                
            await asyncio.sleep(wait_time + 1)
            
        except Exception as e:
            logger.error(f"Error sending message (attempt {attempt + 1}): {str(e)}")
            write_log(f"Error sending message (attempt {attempt + 1}): {str(e)}", True)
            
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
            
    return False

# Rate limiting for media
async def send_file_with_retry(client, entity, file, caption=None, reply_to=None, retries=3):
    """Send file with automatic retry and rate limiting handling"""
    for attempt in range(retries):
        try:
            await client.send_file(entity=entity, file=file, caption=caption, reply_to=reply_to)
            return True
            
        except FloodWaitError as e:
            wait_time = e.seconds
            logger.warning(f"Rate limited on file send. Waiting {wait_time} seconds...")
            write_log(f"Rate limited on file send. Waiting {wait_time} seconds...", True)
            
            if wait_time > 300:
                logger.error(f"Wait time too long ({wait_time}s), skipping file")
                write_log(f"Wait time too long ({wait_time}s), skipping file", True)
                return False
                
            await asyncio.sleep(wait_time + 1)
            
        except Exception as e:
            logger.error(f"Error sending file (attempt {attempt + 1}): {str(e)}")
            write_log(f"Error sending file (attempt {attempt + 1}): {str(e)}", True)
            
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
                
    return False

# Function to extract risk information
def extract_risk_info(message_text):
    risk_info = ""
    
    volume_match = re.search(r'Volume\([^)]+\) Ranked: ([^R\n]+)', message_text)
    if volume_match:
        risk_info += f"Volume(24H) Ranked: {volume_match.group(1).strip()}\n"
    
    risk_match = re.search(r'Risk Level:\s*([^\n]+)', message_text)
    if risk_match:
        risk_info += f"Risk Level: {risk_match.group(1).strip()}"
    
    return risk_info

# Function to create percentage change table
def create_percentage_table(entry_price, targets, stop_losses):
    try:
        table = "📝 Targets & Stop Loss\n"
        table += "---------------------------------------\n"
        table += "Level         Price       % Change from Entry\n"
        table += "---------------------------------------\n"
        
        for i, target in enumerate(targets, 1):
            percentage = calculate_percentage_change(entry_price, target)
            table += f"Target {i}         {target}      +{percentage:.2f}%\n"
        
        for i, sl in enumerate(stop_losses, 1):
            percentage = calculate_percentage_change(entry_price, sl)
            sign = "+" if percentage >= 0 else ""
            table += f"Stop Loss {i}    {sl}      {sign}{percentage:.2f}%\n"
        
        table += "---------------------------------------"
        return table
    except Exception as e:
        logger.error(f"Error creating percentage table: {str(e)}")
        return "Error creating percentage table."

# IMPROVED: Function to detect message type with better pattern matching
def detect_message_type(text):
    if re.search(r'Daily\s+Results|每日結算統計|Results', text, re.IGNORECASE):
        return "DAILY_RECAP"
    
    target_checkmarks = re.findall(r'Target\s+\d+.*?[✅🟢]', text, re.IGNORECASE)
    if len(target_checkmarks) > 1:
        return "MULTI_TARGET_HIT"
    
    if (re.search(r'Hitted\s+target|Reached\s+target', text, re.IGNORECASE) or 
        re.search(r'Target\s+\d+.*?[✅🟢]', text, re.IGNORECASE) or
        re.search(r'Target\s+\d+\s*[:]\s*\d+.*?[✅🟢]', text, re.IGNORECASE)):
        return "TARGET_HIT"
    
    if (re.search(r'Hitted\s+stop\s+loss|Stop\s+loss\s+triggered', text, re.IGNORECASE) or
        re.search(r'Stop\s+loss\s+\d+.*?[🛑🔴]', text, re.IGNORECASE) or
        re.search(r'Stop\s+loss\s+\d+\s*[:]\s*\d+.*?[🛑🔴]', text, re.IGNORECASE)):
        return "STOP_LOSS_HIT"
    
    if len(text.strip().split('\n')) <= 2 and ('USDT' in text or 'BTC' in text):
        if '✅' in text or '🟢' in text:
            return "TARGET_HIT"
        elif '🛑' in text or '🔴' in text:
            return "STOP_LOSS_HIT"
    
    return "NEW_SIGNAL"

# Function to extract data from message
def extract_trading_data(message_text):
    try:
        lines = message_text.split('\n')
        
        coin_name = None
        entry_price = None
        targets = []
        stop_losses = []
        
        for line in lines[:3]:
            line = line.strip()
            if not line:
                continue
                
            coin_patterns = [
                r'^([A-Za-z0-9]+)[^A-Za-z0-9]',
                r'([A-Za-z0-9]+USDT)',
                r'([A-Za-z0-9]+) NEW'
            ]
            
            for pattern in coin_patterns:
                coin_match = re.search(pattern, line)
                if coin_match:
                    coin_name = coin_match.group(1)
                    break
            
            if coin_name:
                break
        
        for line in lines:
            line = line.strip()
            
            entry_match = re.search(r'Entry:?\s*([0-9.]+)', line)
            if entry_match:
                entry_price = entry_match.group(1)
            
            target_match = re.search(r'Target\s+(\d+):?\s*([0-9.]+)', line)
            if target_match:
                target_num = int(target_match.group(1))
                target_price = target_match.group(2)
                
                while len(targets) < target_num:
                    targets.append(None)
                
                targets[target_num-1] = target_price
            
            sl_match = re.search(r'Stop\s+loss\s+(\d+):?\s*([0-9.]+)', line, re.IGNORECASE)
            if sl_match:
                sl_num = int(sl_match.group(1))
                sl_price = sl_match.group(2)
                
                while len(stop_losses) < sl_num:
                    stop_losses.append(None)
                
                stop_losses[sl_num-1] = sl_price
        
        targets = [t for t in targets if t is not None]
        stop_losses = [sl for sl in stop_losses if sl is not None]
        
        return {
            'coin_name': coin_name,
            'entry_price': entry_price,
            'targets': targets,
            'stop_losses': stop_losses
        }
    except Exception as e:
        logger.error(f"Error extracting trading data: {str(e)}")
        return {
            'coin_name': None,
            'entry_price': None,
            'targets': [],
            'stop_losses': []
        }

# IMPROVED: Function to extract data from target hit/stop loss message
def extract_hit_data(message_text):
    data = {'coin': None, 'targets': [], 'stop_losses': []}
    
    coin_match = re.search(r'([A-Za-z0-9]+)(USDT|BTC|ETH|BNB)', message_text)
    if coin_match:
        data['coin'] = coin_match.group(0)
    
    target_matches = re.findall(r'Target\s+(\d+)[:\s]+([0-9.]+)\s*[✅🟢]', message_text, re.IGNORECASE)
    for target_num, target_price in target_matches:
        data['targets'].append({
            'level': f"Target {target_num}",
            'price': target_price
        })
    
    sl_matches = re.findall(r'Stop\s+loss\s+(\d+)[:\s]+([0-9.]+)\s*[🛑🔴]', message_text, re.IGNORECASE)
    for sl_num, sl_price in sl_matches:
        data['stop_losses'].append({
            'level': f"Stop Loss {sl_num}",
            'price': sl_price
        })
    
    return data

# Function to extract data from daily recap
def extract_daily_recap_data(text):
    data = {
        'date': None,
        'hitted_targets': [],
        'running': [],
        'stop_losses': [],
        'total_signals': 0,
        'hitted_take_profits': 0,
        'hitted_stop_losses': 0
    }
    
    date_match = re.search(r'(\d{2}/\d{2}-\d{2}/\d{2})', text)
    if date_match:
        data['date'] = date_match.group(1)
    
    for i in range(1, 5):
        target_match = re.search(rf'Hitted\s+target\s+{i}:\s*(.*?)(?:\n|$)', text)
        if target_match:
            coins = [coin.strip() for coin in target_match.group(1).split(',')]
            data['hitted_targets'].append({'level': i, 'coins': coins})
    
    running_match = re.search(r'Running:\s*(.*?)(?:\n|$)', text)
    if running_match:
        data['running'] = [coin.strip() for coin in running_match.group(1).split(',')]
    
    sl_match = re.search(r'Hitted\s+stop\s+loss:\s*(.*?)(?:\n|$)', text)
    if sl_match:
        data['stop_losses'] = [coin.strip() for coin in sl_match.group(1).split(',')]
    
    total_match = re.search(r'Total\s+Calls:\s*(\d+)', text)
    if total_match:
        data['total_signals'] = int(total_match.group(1))
    
    tp_match = re.search(r'Hitted\s+Take-Profits:\s*(\d+)', text)
    if tp_match:
        data['hitted_take_profits'] = int(tp_match.group(1))
    
    sl_count_match = re.search(r'Hitted\s+Stop-Losses:\s*(\d+)', text)
    if sl_count_match:
        data['hitted_stop_losses'] = int(sl_count_match.group(1))
    
    return data

# Function to create win rate table - FIXED VERSION
def create_win_rate_table(recap_data):
    total_signals = recap_data['total_signals']
    take_profits = recap_data['hitted_take_profits']
    stop_losses = recap_data['hitted_stop_losses']
    running_signals = len(recap_data['running'])
    
    closed_positions = take_profits + stop_losses
    
    if closed_positions == 0:
        win_rate = 0
    else:
        win_rate = (take_profits / closed_positions) * 100
    
    table = "📊 Trading Performance Analysis 📊\n\n"
    table += "Metric                  Value       Percentage\n"
    table += "--------------------------------------------\n"
    
    table += f"Win Rate               {take_profits}/{closed_positions}     {win_rate:.2f}%\n"
    
    if total_signals > 0:
        running_percentage = (running_signals / total_signals) * 100
        table += f"Running Signals        {running_signals}         {running_percentage:.2f}%\n"
    
    if total_signals > 0:
        closed_percentage = (closed_positions / total_signals) * 100
        table += f"Closed Rate            {closed_positions}/{total_signals}     {closed_percentage:.2f}%\n"
    
    return table

# Function to run Telethon client
async def run_client():
    try:
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
        
        # Event handler for new messages
        @client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID))
        async def handler(event):
            try:
                message = event.message
                
                if not message.text:
                    if message.media:
                        success = await send_file_with_retry(
                            client=client,
                            entity=GROUP_CHANNEL_ID,
                            file=message.media,
                            caption=f"🆕 NEW CALL 🆕\n\n",
                            reply_to=SIGNAL_CALL_TOPIC_ID
                        )
                        if success:
                            log_msg = f"Media forwarded to Call topic"
                            logger.info(log_msg)
                            write_log(log_msg)
                            bot_stats.increment()
                    return
                
                logger.info(f"Received message: {message.text[:100]}...")
                
                message_type = detect_message_type(message.text)
                logger.info(f"Detected message type: {message_type}")
                
                if message_type == "DAILY_RECAP":
                    recap_data = extract_daily_recap_data(message.text)
                    
                    custom_text = f"📅 DAILY RECAP: {recap_data['date'] if recap_data['date'] else 'Today'} 📅\n\n"
                    custom_text += message.text + "\n\n"
                    custom_text += create_win_rate_table(recap_data)
                    custom_text += "\n\n"
                    
                    success = await send_message_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        message=custom_text,
                        reply_to=SIGNAL_CALL_TOPIC_ID
                    )
                    
                    if success:
                        log_msg = f"Daily recap forwarded to Call topic"
                        logger.info(log_msg)
                        write_log(log_msg)
                        bot_stats.increment()
                
                elif message_type in ["MULTI_TARGET_HIT", "TARGET_HIT", "STOP_LOSS_HIT"]:
                    
                    if message_type == "MULTI_TARGET_HIT":
                        hit_data = extract_hit_data(message.text)
                        
                        if hit_data['coin'] and hit_data['targets']:
                            custom_text = f"✅ CALL UPDATE: {hit_data['coin']} ✅\n\n"
                            
                            for target in hit_data['targets']:
                                custom_text += f"🎯 {target['level']} ({target['price']}) HIT!\n"
                            
                            custom_text += "\n"
                        else:
                            custom_text = f"✅ CALL UPDATE ✅\n\n"
                            custom_text += message.text + "\n\n"
                    
                    elif message_type == "TARGET_HIT":
                        hit_data = extract_hit_data(message.text)
                        
                        if hit_data['coin'] and len(hit_data['targets']) > 0:
                            custom_text = f"✅ CALL UPDATE: {hit_data['coin']} ✅\n\n"
                            custom_text += f"🎯 {hit_data['targets'][0]['level']} ({hit_data['targets'][0]['price']}) HIT!\n\n"
                        else:
                            custom_text = f"✅ CALL UPDATE ✅\n\n"
                            custom_text += message.text + "\n\n"
                    
                    elif message_type == "STOP_LOSS_HIT":
                        hit_data = extract_hit_data(message.text)
                        
                        if hit_data['coin'] and len(hit_data['stop_losses']) > 0:
                            custom_text = f"🔴 CALL UPDATE: {hit_data['coin']} 🔴\n\n"
                            custom_text += f"⚠️ {hit_data['stop_losses'][0]['level']} ({hit_data['stop_losses'][0]['price']}) TRIGGERED!\n\n"
                        else:
                            custom_text = f"🔴 CALL UPDATE 🔴\n\n"
                            custom_text += message.text + "\n\n"
                    
                    success = await send_message_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        message=custom_text,
                        reply_to=SIGNAL_UPDATE_TOPIC_ID
                    )
                    
                    if success:
                        message_preview = message.text[:50] + "..." if message.text and len(message.text) > 50 else "Media or message without text"
                        log_msg = f"Call update forwarded to Call Update topic: {message_preview}"
                        logger.info(log_msg)
                        write_log(log_msg)
                        bot_stats.increment()
                    
                else:  # NEW_SIGNAL
                    trading_data = extract_trading_data(message.text)
                    coin_name = trading_data['coin_name']
                    entry_price = trading_data['entry_price']
                    targets = trading_data['targets']
                    stop_losses = trading_data['stop_losses']
                    
                    if coin_name and not entry_price and (targets or stop_losses):
                        current_price = await get_current_price(coin_name)
                        if current_price:
                            entry_price = str(current_price)
                            logger.info(f"Using current price for {coin_name}: {entry_price}")
                    
                    if coin_name and entry_price and (targets or stop_losses):
                        custom_text = f"🆕 NEW CALL: [{coin_name}](https://www.tradingview.com/symbols/{coin_name}.P/) 🆕\n\n"
                        
                        risk_info = extract_risk_info(message.text)
                        if risk_info:
                            custom_text += "📊 Risk Analysis 📊\n"
                            custom_text += risk_info + "\n\n"
                        
                        custom_text += f"**Entry: {entry_price}**\n\n"
                        
                        if targets or stop_losses:
                            custom_text += create_percentage_table(entry_price, targets, stop_losses)
                            
                            if coin_name:
                                # Fixed regex pattern - removed unterminated group
                                pure_coin_name = re.sub(r'(USDT|BTC|ETH|BNB|USD|BUSD)$', '', coin_name)
                                custom_text += f"\n\n[🔍Sentimen ${pure_coin_name}](https://x.com/search?q=%24{pure_coin_name}&src=typed_query)"
                                custom_text += f"\n[📊 Data Coinglass ${pure_coin_name}](https://www.coinglass.com/currencies/{pure_coin_name})"
                        
                    else:
                        custom_text = f"🆕 NEW CALL 🆕\n\n{message.text}\n\n"
                    
                    success = await send_message_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        message=custom_text,
                        reply_to=SIGNAL_CALL_TOPIC_ID
                    )
                    
                    if success:
                        message_preview = message.text[:50] + "..." if message.text and len(message.text) > 50 else "Media or message without text"
                        log_msg = f"New signal forwarded to Signal Call topic: {message_preview}"
                        logger.info(log_msg)
                        write_log(log_msg)
                        bot_stats.increment()
                    
            except Exception as e:
                error_msg = f"Error processing main signal: {str(e)}"
                logger.error(error_msg)
                write_log(error_msg, True)
        
        # Crypto news handlers with rate limiting
        @client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID_2))
        async def crypto_news_handler2(event):
            try:
                message = event.message
                
                logger.info(f"Menerima berita crypto dari SOURCE_CHANNEL_ID_2: {message.text[:100] if message.text else 'Konten media'}...")
                
                is_new_announcement = False
                if message.text and message.text.strip().upper().startswith("NEW"):
                    is_new_announcement = True
                
                if message.text:
                    if is_new_announcement:
                        custom_text = message.text + "\n\n**Source: Crypto News**"
                    else:
                        coin_match = re.search(r'#([A-Z]{3,})', message.text)
                        if coin_match:
                            coin = coin_match.group(1)
                        custom_text = message.text + "\n\n**Source: Crypto News**"
                else:
                    custom_text = "📰 CRYPTO NEWS UPDATE 📰\n\n**Source: Crypto News**"
                
                success = False
                if message.media:
                    success = await send_file_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        file=message.media,
                        caption=custom_text,
                        reply_to=NEWS_TOPIC_ID
                    )
                else:
                    success = await send_message_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        message=custom_text,
                        reply_to=NEWS_TOPIC_ID
                    )
                
                if success:
                    log_msg = f"Berita crypto diteruskan ke topik Berita"
                    logger.info(log_msg)
                    write_log(log_msg)
                    bot_stats.increment()
                
            except Exception as e:
                error_msg = f"Error meneruskan berita crypto: {str(e)}"
                logger.error(error_msg)
                write_log(error_msg, True)
        
        @client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID_3))
        async def crypto_news_handler3(event):
            try:
                message = event.message
                logger.info(f"Menerima berita crypto dari TU Crypto News: {message.text[:100] if message.text else 'Konten media'}...")
                
                is_new_announcement = False
                if message.text and message.text.strip().upper().startswith("NEW"):
                    is_new_announcement = True
                
                if message.text:
                    if is_new_announcement:
                        custom_text = message.text + "\n\n**Source: TU Crypto News**"
                    else:
                        coin_match = re.search(r'#([A-Z]{3,})', message.text)
                        if coin_match:
                            coin = coin_match.group(1)
                        custom_text = message.text + "\n\n**Source: TU Crypto News**"
                else:
                    custom_text = "📰 CRYPTO NEWS UPDATE 📰\n\n**Source: TU Crypto News**"
                
                success = False
                if message.media:
                    success = await send_file_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        file=message.media,
                        caption=custom_text,
                        reply_to=NEWS_TOPIC_ID
                    )
                else:
                    success = await send_message_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        message=custom_text,
                        reply_to=NEWS_TOPIC_ID
                    )
                
                if success:
                    log_msg = f"Berita crypto diteruskan ke topik Berita"
                    logger.info(log_msg)
                    write_log(log_msg)
                    bot_stats.increment()
                    
            except Exception as e:
                error_msg = f"Error meneruskan berita crypto: {str(e)}"
                logger.error(error_msg)
                write_log(error_msg, True)
        
        @client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID_4))
        async def crypto_news_handler4(event):
            try:
                message = event.message
                logger.info(f"Menerima berita crypto dari Crypto Insider: {message.text[:100] if message.text else 'Konten media'}...")
                
                is_new_announcement = False
                if message.text and message.text.strip().upper().startswith("NEW"):
                    is_new_announcement = True
                
                if message.text:
                    if is_new_announcement:
                        custom_text = message.text + "\n\n**Source: Crypto Insider**"
                    else:
                        coin_match = re.search(r'#([A-Z]{3,})', message.text)
                        if coin_match:
                            coin = coin_match.group(1)
                        custom_text = message.text + "\n\n**Source: Crypto Insider**"
                else:
                    custom_text = "📰 CRYPTO NEWS UPDATE 📰\n\n**Source: Crypto Insider**"
                
                success = False
                if message.media:
                    success = await send_file_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        file=message.media,
                        caption=custom_text,
                        reply_to=NEWS_TOPIC_ID
                    )
                else:
                    success = await send_message_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        message=custom_text,
                        reply_to=NEWS_TOPIC_ID
                    )
                
                if success:
                    log_msg = f"Berita crypto diteruskan ke topik Berita"
                    logger.info(log_msg)
                    write_log(log_msg)
                    bot_stats.increment()
                    
            except Exception as e:
                error_msg = f"Error meneruskan berita crypto: {str(e)}"
                logger.error(error_msg)
                write_log(error_msg, True)
        
        @client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID_5))
        async def new_source_handler(event):
            try:
                message = event.message
                logger.info(f"Menerima berita crypto dari Water Bloomberg: {message.text[:100] if message.text else 'Konten media'}...")
                
                is_new_announcement = False
                if message.text and message.text.strip().upper().startswith("NEW"):
                    is_new_announcement = True
                
                if message.text:
                    if is_new_announcement:
                        custom_text = message.text + ""
                    else:
                        coin_match = re.search(r'#([A-Z]{3,})', message.text)
                        if coin_match:
                            coin = coin_match.group(1)
                        custom_text = message.text + ""
                else:
                    custom_text = ""
                
                success = False
                if message.media:
                    success = await send_file_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        file=message.media,
                        caption=custom_text,
                        reply_to=NEW_TOPIC_ID
                    )
                else:
                    success = await send_message_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        message=custom_text,
                        reply_to=NEW_TOPIC_ID
                    )
                
                if success:
                    log_msg = f"Berita crypto diteruskan ke topik Berita"
                    logger.info(log_msg)
                    write_log(log_msg)
                    bot_stats.increment()
                    
            except Exception as e:
                error_msg = f"Error meneruskan berita crypto: {str(e)}"
                logger.error(error_msg)
                write_log(error_msg, True)
        
        @client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID_6))
        async def crypto_news_handler6(event):
            try:
                message = event.message
                logger.info(f"Menerima berita crypto dari Financial World Updates: {message.text[:100] if message.text else 'Konten media'}...")
                
                is_new_announcement = False
                if message.text and message.text.strip().upper().startswith("NEW"):
                    is_new_announcement = True
                
                if message.text:
                    if is_new_announcement:
                        custom_text = message.text + "\n\n**Source: Financial World Updates**"
                    else:
                        coin_match = re.search(r'#([A-Z]{3,})', message.text)
                        if coin_match:
                            coin = coin_match.group(1)
                        custom_text = message.text + "\n\n**Source: Financial World Updates**"
                else:
                    custom_text = "📰 CRYPTO NEWS UPDATE 📰\n\n**Source: Financial World Updates**"
                
                success = False
                if message.media:
                    success = await send_file_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        file=message.media,
                        caption=custom_text,
                        reply_to=NEWS_TOPIC_ID
                    )
                else:
                    success = await send_message_with_retry(
                        client=client,
                        entity=GROUP_CHANNEL_ID,
                        message=custom_text,
                        reply_to=NEWS_TOPIC_ID
                    )
                
                if success:
                    log_msg = f"Berita crypto diteruskan ke topik Berita"
                    logger.info(log_msg)
                    write_log(log_msg)
                    bot_stats.increment()
                    
            except Exception as e:
                error_msg = f"Error meneruskan berita crypto: {str(e)}"
                logger.error(error_msg)
                write_log(error_msg, True)
        
        # Run client
        write_log("Starting Telegram client...")
        await client.start()
        
        log_msg = f"Bot successfully activated. Monitoring channel: {SOURCE_CHANNEL_ID}"
        logger.info(log_msg)
        write_log(log_msg)
        
        await client.run_until_disconnected()
        
    except Exception as e:
        error_msg = f"Error running client: {str(e)}"
        logger.error(error_msg)
        write_log(error_msg, True)

# Function to run client in separate thread
def start_client_thread():
    try:
        write_log("Starting client in separate thread...")
        asyncio.set_event_loop(asyncio.new_event_loop())
        loop = asyncio.get_event_loop()
        loop.run_until_complete(run_client())
    except Exception as e:
        error_msg = f"Error in thread: {str(e)}"
        logger.error(error_msg)
        write_log(error_msg, True)

# Function to save verification code
def save_verification_code():
    if st.session_state.code_input:
        try:
            with open(VERIFICATION_CODE_FILE, "w") as f:
                f.write(st.session_state.code_input)
            st.success("Verification code sent!")
        except Exception as e:
            st.error(f"Failed to save verification code: {str(e)}")

# Function to update UI stats from thread
def update_ui_stats():
    """Update UI with latest stats from thread"""
    try:
        # Update from queue without blocking
        while True:
            try:
                msg = message_queue.get_nowait()
                if 'log_messages' not in st.session_state:
                    st.session_state['log_messages'] = []
                st.session_state['log_messages'].append(msg)
                # Keep only last 100 messages
                if len(st.session_state['log_messages']) > 100:
                    st.session_state['log_messages'] = st.session_state['log_messages'][-100:]
            except queue.Empty:
                break
        
        # Update stats
        current_count = bot_stats.get_count()
        st.session_state['total_forwarded'] = current_count
        
    except Exception as e:
        logger.error(f"Error updating UI stats: {str(e)}")

# Streamlit UI
st.title("Telegram Channel Forwarder")
st.markdown("Application to forward messages from source channel to your target channel.")

# Update stats from thread
update_ui_stats()

# Column for verification code
if st.session_state['running']:
    st.text_input("Enter Verification Code from Telegram (if requested):", 
                  key="code_input", 
                  on_change=save_verification_code)

# Display status and statistics
st.subheader("Status & Statistics")
col1, col2 = st.columns(2)
with col1:
    status = "🟢 **Running**" if st.session_state['running'] else "🔴 **Stopped**"
    st.markdown(f"**Bot Status:** {status}")
with col2:
    st.markdown(f"**Total Messages Sent:** {st.session_state['total_forwarded']}")

# Start/stop buttons
col1, col2 = st.columns(2)
with col1:
    if not st.session_state['running']:
        if st.button("Start Forwarding", use_container_width=True):
            # Create log file if it doesn't exist
            if not os.path.exists(LOG_FILE):
                with open(LOG_FILE, "w") as f:
                    f.write("")
            
            # Run client in separate thread
            thread = threading.Thread(target=start_client_thread, daemon=True)
            thread.start()
            
            st.session_state['running'] = True
            write_log("Bot starting...")
            st.rerun()

with col2:
    if st.session_state['running']:
        if st.button("Stop Forwarding", use_container_width=True):
            st.session_state['running'] = False
            write_log("Bot stopped!")
            st.rerun()

# Real-time logs section
st.subheader("Recent Activity")
if st.button("🔄 Refresh Logs"):
    update_ui_stats()
    st.rerun()

# Display recent logs
if 'log_messages' in st.session_state and st.session_state['log_messages']:
    # Show last 10 messages
    recent_logs = st.session_state['log_messages'][-10:]
    for log in reversed(recent_logs):
        if log.get('error', False):
            st.error(f"{log['time']} - {log['message']}")
        else:
            st.success(f"{log['time']} - {log['message']}")
else:
    st.info("No recent activity. Start the bot to see logs here.")

# Additional monitoring section
if st.session_state['running']:
    with st.expander("📊 Performance Monitoring"):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric("Total Forwarded", st.session_state['total_forwarded'])
        
        with col2:
            # Calculate success rate from logs
            if 'log_messages' in st.session_state:
                total_attempts = len([log for log in st.session_state['log_messages'] if 'forwarded' in log['message']])
                error_count = len([log for log in st.session_state['log_messages'] if log.get('error', False)])
                success_rate = ((total_attempts - error_count) / max(total_attempts, 1)) * 100
                st.metric("Success Rate", f"{success_rate:.1f}%")
            else:
                st.metric("Success Rate", "0%")
        
        with col3:
            # Show last activity time
            if 'log_messages' in st.session_state and st.session_state['log_messages']:
                last_activity = st.session_state['log_messages'][-1]['time']
                st.metric("Last Activity", last_activity)
            else:
                st.metric("Last Activity", "None")

# Auto-refresh every 30 seconds when running
if st.session_state['running']:
    time.sleep(30)
    st.rerun()
