import os
import sys
import json
import sqlite3
import schedule
import time
import random
import atexit
import ctypes
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import asyncio
import threading
from telethon import TelegramClient
from telethon.tl.types import InputPeerChannel, InputPeerChat, Channel, Chat

# Windows terminal: tranh crash khi in tieng Viet
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Load environment variables
load_dotenv()


def log(msg):
    print(msg, flush=True)

LOCK_FILE = 'bot.lock'


def ensure_single_instance():
    """Chi cho phep 1 bot.py chay cung luc (tranh database is locked)."""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, encoding='utf-8') as f:
                old_pid = int(f.read().strip())
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, old_pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                log(f"[ERROR] Bot da chay o PID {old_pid}. Tat bot cu (Ctrl+C) roi chay lai.")
                sys.exit(1)
        except (ValueError, OSError):
            pass
    with open(LOCK_FILE, 'w', encoding='utf-8') as f:
        f.write(str(os.getpid()))


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def configure_sqlite_session(telegram_client):
    session = telegram_client.session
    if hasattr(session, '_cursor'):
        session._cursor()
        if getattr(session, '_conn', None):
            session._conn.execute('PRAGMA busy_timeout=30000')


async def run_session_with_retry(telegram_client, group_entity, max_retries=5):
    for attempt in range(max_retries):
        try:
            await daily_schedule(telegram_client, group_entity)
            return
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < max_retries - 1:
                wait = 2 * (attempt + 1)
                log(f"[WARN] Session bi khoa, thu lai sau {wait}s... ({attempt + 1}/{max_retries})")
                await asyncio.sleep(wait)
            else:
                raise

# Data structure to store posts
POSTS_FILE = 'posts.json'

# Image directories
FIXED_IMAGES_DIR = 'images/fixed'
WINCAI_IMAGES_DIR = 'images/wincai'
LOSECAI_IMAGES_DIR = 'images/losecai'
WINCON_IMAGES_DIR = 'images/wincon'
LOSECON_IMAGES_DIR = 'images/losecon'
TIE_IMAGES_DIR = 'images/tie'

RESULT_IMAGE_DIRS = {
    'wincai': WINCAI_IMAGES_DIR,
    'losecai': LOSECAI_IMAGES_DIR,
    'wincon': WINCON_IMAGES_DIR,
    'losecon': LOSECON_IMAGES_DIR,
    'tie': TIE_IMAGES_DIR,
}
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
RESULT_TIME_SLOT = '11:00'

# Result probabilities
WIN_PROBABILITY = 0.90  # 90%
LOSE_PROBABILITY = 0.10  # 10%

sent_slots = set()
MIN_MESSAGES = 9   # Tin 1-9 (index 0-8), tin 8=CON tin 9=CÁI
BEFORE_BET_ORDER = [0, 1, 2, 3, 4]       # Tin 1, 2, 3, 4, 5
CON_BET_INDEX = 7                        # Tin 8 - CON
CAI_BET_INDEX = 8                        # Tin 9 - CÁI
AFTER_RESULT_ORDER = [5, 6]              # Tin 6, 7

TZ = timezone(timedelta(hours=7))  # GMT+7 (Việt Nam)
SCHEDULE_INTERVAL = 5
SCHEDULE_START_HOUR, SCHEDULE_START_MINUTE = 7, 0
SCHEDULE_END_HOUR, SCHEDULE_END_MINUTE = 21, 50

# Thay bằng thông tin của bạn
api_id = int(os.getenv('API_ID'))
api_hash = os.getenv('API_HASH')
phone = (os.getenv('PHONE') or '').strip().replace(' ', '')

def session_name_from_phone(phone_number):
    digits = ''.join(c for c in (phone_number or '') if c.isdigit())
    return f'user_session_{digits}' if digits else 'user_session'

SESSION_NAME = session_name_from_phone(phone)

# ID hoặc username nhóm (có thể là @tennhom hoặc ID số)
group = os.getenv('GROUP')
log(f"GROUP tu .env: {group}")
log(f"Session: {SESSION_NAME} | PHONE tu .env: {phone}")

client = TelegramClient(SESSION_NAME, api_id, api_hash)


async def login_client():
    if not phone:
        log('[ERROR] PHONE chua cau hinh trong .env')
        sys.exit(1)
    await client.start(phone=phone)

def ensure_directories():
    """Create necessary directories if they don't exist"""
    directories = [
        FIXED_IMAGES_DIR,
        WINCAI_IMAGES_DIR,
        LOSECAI_IMAGES_DIR,
        WINCON_IMAGES_DIR,
        LOSECON_IMAGES_DIR,
        TIE_IMAGES_DIR
    ]
    for directory in directories:
        os.makedirs(directory, exist_ok=True)

def load_posts():
    """Load posts from JSON file"""
    if os.path.exists(POSTS_FILE):
        with open(POSTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        'fixed_posts': {},  # Key: time (HH:MM), Value: list of posts
        'rotating_posts': {
            'wincai': {},  # Key: time (HH:MM), Value: list of posts
            'losecai': {},  # Key: time (HH:MM), Value: list of posts
            'wincon': {},  # Key: time (HH:MM), Value: list of posts
            'losecon': {},  # Key: time (HH:MM), Value: list of posts
            'tie': {}      # Key: time (HH:MM), Value: list of posts
        }
    }

def save_posts(posts):
    """Save posts to JSON file"""
    with open(POSTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(posts, f, ensure_ascii=False, indent=4)

def get_next_rotating_post_index(time_slot, result_type):
    """Get the index of the next rotating post to send for a specific time slot and result type"""
    posts = load_posts()
    if not posts['rotating_posts'][result_type].get(time_slot):
        return 0
    
    rotating_posts = posts['rotating_posts'][result_type][time_slot]
    if not rotating_posts:
        return 0
    
    # Get the last sent post index
    last_index = rotating_posts[-1].get('last_sent_index', -1)
    next_index = (last_index + 1) % len(rotating_posts)
    
    # Update the last sent index
    rotating_posts[-1]['last_sent_index'] = next_index
    posts['rotating_posts'][result_type][time_slot] = rotating_posts
    save_posts(posts)
    
    return next_index

def detect_bet_side(text):
    text_upper = (text or '').upper()
    if 'CÁI' in text_upper or 'CAI' in text_upper or 'NHÀ CÁI' in text_upper:
        return 'cai'
    if 'CON' in text_upper or 'NHÀ CON' in text_upper:
        return 'con'
    return 'con'

def list_images_in_dir(dir_path):
    if not os.path.exists(dir_path):
        return []
    files = []
    for name in sorted(os.listdir(dir_path)):
        if name.startswith('.'):
            continue
        if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS:
            files.append(os.path.join(dir_path, name))
    return files

def get_result_image_path(result_type):
    """Lấy ảnh kết quả từ posts.json, fallback quét thư mục images/."""
    posts = load_posts()
    rotating_posts = posts['rotating_posts'][result_type].get(RESULT_TIME_SLOT, [])
    if rotating_posts:
        next_index = get_next_rotating_post_index(RESULT_TIME_SLOT, result_type)
        path = rotating_posts[next_index]['image_path']
        if os.path.exists(path):
            return path
        print(f"[WARN] Ảnh trong posts.json không tồn tại: {path}")

    images = list_images_in_dir(RESULT_IMAGE_DIRS[result_type])
    if images:
        return images[0]
    return None

async def send_result_image(group, result_type, caption):
    image_path = get_result_image_path(result_type)
    if image_path:
        await client.send_file(group, image_path, caption=caption, parse_mode='markdown')
        print(f"Đã gửi ảnh kết quả: {image_path}")
    else:
        print(f"[WARN] Không có ảnh trong {RESULT_IMAGE_DIRS[result_type]}/")

async def send_message(text):
    """Send a text message to the channel"""
    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=text
        )
        print(f"Sent message: {text} at {datetime.now()}")
    except Exception as e:
        print(f"Error sending message: {e}")

async def send_photo(image_path, caption=None):
    """Send a photo to the channel"""
    try:
        with open(image_path, 'rb') as photo:
            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo,
                caption=caption
            )
        print(f"Posted image {image_path} at {datetime.now()}")
    except Exception as e:
        print(f"Error sending photo: {e}")

async def send_video(video_path):
    """Send a video to the channel"""
    try:
        with open(video_path, 'rb') as video:
            await bot.send_video(
                chat_id=CHANNEL_ID,
                video=video
            )
        print(f"Posted video {video_path} at {datetime.now()}")
    except Exception as e:
        print(f"Error sending video: {e}")

async def send_rotating_post(time_slot, result_type, caption=None):
    """Send rotating post for a specific time slot and result type, with optional caption"""
    posts = load_posts()
    rotating_posts = posts['rotating_posts'][result_type].get(time_slot, [])
    
    if not rotating_posts:
        print(f"No rotating posts available for time slot {time_slot} and result type {result_type}")
        return
    
    next_index = get_next_rotating_post_index(time_slot, result_type)
    post = rotating_posts[next_index]
    
    try:
        await send_photo(post['image_path'], caption)
    except Exception as e:
        print(f"Error sending rotating post: {e}")

def get_result_type(choice):
    """Get result type so that 75% là win đúng với bên được hô, còn lại là lose"""
    rand = random.random()
    if rand < 0.75:
        # Win đúng với bên được hô
        if choice == 'NHÀ CÁI 500K':
            return 'wincai'
        else:
            return 'wincon'
    else:
        # Lose (25%)
        if choice == 'NHÀ CÁI 500K':
            return 'losecai'  # Cái hô nhưng Cái thua
        else:
            return 'losecon'  # Con hô nhưng Con thua

async def get_message_content(username):
    """Lấy nội dung tin nhắn từ cuộc trò chuyện với user"""
    try:
        user = await client.get_entity(username)
        messages = []
        async for message in client.iter_messages(user, limit=20):
            if message.text:
                messages.append(message.text)
        return messages
    except Exception as e:
        print(f"Lỗi khi lấy nội dung tin nhắn: {e}")
        return None

async def daily_schedule(client, group):
    try:
        # Kiểm tra kết nối trước khi thực hiện
        if not client.is_connected():
            print("Mất kết nối, đang thử kết nối lại...")
            await client.connect()
            if not await client.is_user_authorized():
                print("Cần đăng nhập lại...")
                await login_client()
        
        # Lấy thông tin user từ username
        user = await client.get_entity('frezeit')
        print(f"\n=== BẮT ĐẦU GỬI TIN NHẮN THEO LỊCH ===")
        
        # Lấy thông tin tài khoản của bạn
        me = await client.get_me()
        print(f"Tìm tin nhắn từ {me.first_name}")
        
        # Mảng để lưu các tin nhắn
        messages_to_send = []
        
        # Lấy các tin nhắn từ cuộc trò chuyện với user
        async for message in client.iter_messages(user, limit=20):
            try:
                # Chỉ xét tin nhắn do bạn gửi
                if message.sender_id == me.id:
                    print(f"\nĐã tìm thấy tin nhắn ID: {message.id}")
                    print(f"Thời gian gốc: {message.date}")
                    messages_to_send.append(message)
            except Exception as e:
                print(f"Lỗi khi xử lý tin nhắn {message.id}: {e}")
                continue
        
        # Sắp xếp tin nhắn theo ID (ID lớn nhất lên đầu)
        messages_to_send.sort(key=lambda x: x.id)
        
        # In ra thứ tự tin nhắn để debug
        print("\nThứ tự tin nhắn sau khi sắp xếp:")
        for i, msg in enumerate(messages_to_send):
            print(f"Index {i}: ID {msg.id} - Thời gian: {msg.date}")
        
        if len(messages_to_send) < MIN_MESSAGES:
            print(f"Không đủ tin nhắn để gửi (cần ít nhất {MIN_MESSAGES} tin nhắn, hiện có {len(messages_to_send)})")
            return

        async def forward_slot(index, label=None):
            await client.forward_messages(
                group,
                messages_to_send[index],
                silent=True,
                drop_author=True,
            )
            print(label or f"Đã gửi tin nhắn thứ {index + 1}")

        # Tin 1-5 -> random tin 8 (CON) hoặc tin 9 (CÁI) -> ảnh kết quả -> tin 6-7
        delays = [10, 10, 30, 60, 45]
        for i, index in enumerate(BEFORE_BET_ORDER):
            await forward_slot(index, f"Đã gửi tin nhắn thứ {index + 1}")
            await asyncio.sleep(delays[i])

        message_choice = random.choice([CON_BET_INDEX, CAI_BET_INDEX])
        is_cai = message_choice == CAI_BET_INDEX
        side = 'CÁI' if is_cai else 'CON'
        await forward_slot(message_choice, f"Đã gửi lệnh {side} (tin {message_choice + 1})")
        await asyncio.sleep(45)

        # Bước 9: ảnh kết quả
        result = random.random()
        if result < 0.8:  # 80% thắng
            is_win = True
            is_tie = False
        elif result < 0.9:  # 10% thua
            is_win = False
            is_tie = False
        else:  # 10% hòa
            is_win = False
            is_tie = True
        
        # Gửi ảnh kết quả (bước 9)
        if is_tie:
            await send_result_image(group, 'tie', '**〰️ HÒA + 0%**')
        elif is_win:
            await send_result_image(group, 'wincai' if is_cai else 'wincon', '**✔️ H + 10%**')
        else:
            await send_result_image(group, 'losecai' if is_cai else 'losecon', '**❌ GÃY -10%**')
        await asyncio.sleep(10)

        for index in AFTER_RESULT_ORDER:
            await forward_slot(index, f"Đã gửi tin nhắn thứ {index + 1}")
            await asyncio.sleep(10)
        print("=== KẾT THÚC PHIÊN ===\n")
    except Exception as e:
        print(f"Lỗi trong daily_schedule: {e}")
        # Thử kết nối lại nếu bị ngắt kết nối
        if "disconnected" in str(e).lower():
            print("Phát hiện mất kết nối, đang thử kết nối lại...")
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    await login_client()
                print("Đã kết nối lại thành công!")
            except Exception as reconnect_error:
                print(f"Không thể kết nối lại: {reconnect_error}")
        raise  # Ném lại lỗi để xử lý ở cấp cao hơn

def add_fixed_post(time_slot, image_path):
    """Add a new fixed post for a specific time slot"""
    posts = load_posts()
    if time_slot not in posts['fixed_posts']:
        posts['fixed_posts'][time_slot] = []
    
    posts['fixed_posts'][time_slot].append({
        'image_path': image_path
    })
    save_posts(posts)

def add_rotating_post(time_slot, image_path, result_type=None):
    """Add a new rotating post for a specific time slot and result type, optionally explicit result_type"""
    filename = os.path.basename(image_path).lower()
    if result_type is None:
        if filename.startswith('win_'):
            result_type = 'wincai' if 'cai' in filename else 'wincon'
        elif filename.startswith('lose_'):
            result_type = 'losecai' if 'cai' in filename else 'losecon'
        elif filename.startswith('tie_'):
            result_type = 'tie'
        else:
            raise ValueError("Image filename must start with 'win_', 'lose_' or 'tie_'")
    posts = load_posts()
    if time_slot not in posts['rotating_posts'][result_type]:
        posts['rotating_posts'][result_type][time_slot] = []
    posts['rotating_posts'][result_type][time_slot].append({
        'image_path': image_path,
        'last_sent_index': -1
    })
    save_posts(posts)

def add_rotating_posts_from_directory():
    """Automatically add all image files from each result directory, regardless of filename prefix"""
    # Add wincai posts
    wincai_dir = WINCAI_IMAGES_DIR
    if os.path.exists(wincai_dir):
        for filename in os.listdir(wincai_dir):
            if not filename.startswith('.'):
                add_rotating_post('11:00', os.path.join(wincai_dir, filename), result_type='wincai')

    # Add losecai posts
    losecai_dir = LOSECAI_IMAGES_DIR
    if os.path.exists(losecai_dir):
        for filename in os.listdir(losecai_dir):
            if not filename.startswith('.'):
                add_rotating_post('11:00', os.path.join(losecai_dir, filename), result_type='losecai')

    # Add wincon posts
    wincon_dir = WINCON_IMAGES_DIR
    if os.path.exists(wincon_dir):
        for filename in os.listdir(wincon_dir):
            if not filename.startswith('.'):
                add_rotating_post('11:00', os.path.join(wincon_dir, filename), result_type='wincon')

    # Add losecon posts
    losecon_dir = LOSECON_IMAGES_DIR
    if os.path.exists(losecon_dir):
        for filename in os.listdir(losecon_dir):
            if not filename.startswith('.'):
                add_rotating_post('11:00', os.path.join(losecon_dir, filename), result_type='losecon')

    # Add tie posts
    tie_dir = TIE_IMAGES_DIR
    if os.path.exists(tie_dir):
        for filename in os.listdir(tie_dir):
            if not filename.startswith('.'):
                add_rotating_post('11:00', os.path.join(tie_dir, filename), result_type='tie')

def is_schedule_minute(minute):
    return minute % SCHEDULE_INTERVAL == 0


def is_within_schedule(hour, minute):
    """7:00 -> 21:50 (GMT+7)."""
    current = hour * 60 + minute
    start = SCHEDULE_START_HOUR * 60 + SCHEDULE_START_MINUTE
    end = SCHEDULE_END_HOUR * 60 + SCHEDULE_END_MINUTE
    return start <= current <= end


def generate_daily_slots():
    """7:00 -> 21:50, moi 5 phut (GMT+7)."""
    slots = []
    start = SCHEDULE_START_HOUR * 60 + SCHEDULE_START_MINUTE
    end = SCHEDULE_END_HOUR * 60 + SCHEDULE_END_MINUTE
    minutes = start
    while minutes <= end and len(slots) < 200:
        hour, minute = divmod(minutes, 60)
        slots.append(f"{hour:02d}:{minute:02d}")
        if minutes >= end:
            break
        minutes += SCHEDULE_INTERVAL
    return slots


TIME_SLOTS = generate_daily_slots()
log(f"[INFO] Da tao {len(TIME_SLOTS)} ca: {TIME_SLOTS[0]} -> {TIME_SLOTS[-1]}")


def get_next_slot(now):
    """Tim ca tiep theo trong ngay (GMT+7)."""
    current = now.hour * 60 + now.minute
    for slot in TIME_SLOTS:
        h, m = map(int, slot.split(':'))
        if h * 60 + m > current:
            return slot
    return TIME_SLOTS[0]


async def schedule_loop(entity):
    """Moi 5 phut, 7:00 - 21:50 GMT+7."""
    global sent_slots
    log(
        f"[INFO] Lich: moi {SCHEDULE_INTERVAL} phut, "
        f"{SCHEDULE_START_HOUR:02d}:{SCHEDULE_START_MINUTE:02d} - "
        f"{SCHEDULE_END_HOUR:02d}:{SCHEDULE_END_MINUTE:02d} GMT+7 "
        f"({len(TIME_SLOTS)} ca/ngay)"
    )
    while True:
        now = datetime.now(TZ)
        hour = now.hour
        minute = now.minute
        in_window = is_within_schedule(hour, minute)
        on_slot = is_schedule_minute(minute)

        log(
            f"[HEARTBEAT] {now.strftime('%H:%M:%S')} GMT+7 | "
            f"trong khung gio: {'CO' if in_window else 'KHONG'} | "
            f"moc {SCHEDULE_INTERVAL}p: {'CO' if on_slot else 'KHONG'} | "
            f"ca tiep: {get_next_slot(now)}"
        )

        if on_slot and in_window:
            slot_key = now.strftime('%Y-%m-%d %H:%M')
            if slot_key not in sent_slots:
                log(f"[INFO] Bat dau ca luc {slot_key}")
                await run_session_with_retry(client, entity)
                sent_slots.add(slot_key)
            else:
                log(f"[INFO] Ca {slot_key} da chay roi, bo qua")

        if hour == 0 and minute == 1:
            sent_slots = set()
            log("[INFO] Reset sent_slots cho ngay moi")

        await asyncio.sleep(60 - now.second)

async def send_now():
    """Gửi ngay nội dung theo daily_schedule với entity từ .env"""
    group_id = int(os.getenv('GROUP'))
    try:
        entity = await client.get_entity(group_id)
        await daily_schedule(client, entity)
        print('Đã gửi ngay nội dung theo daily_schedule!')
    except Exception as e:
        print(f"Lỗi khi gửi ngay nội dung: {e}")

async def list_dialogs():
    """Lấy danh sách nhóm/channel mà userbot đang tham gia"""
    print("Danh sách nhóm/channel đang tham gia:")
    async for dialog in client.iter_dialogs():
        if isinstance(dialog.entity, (Channel, Chat)):
            print(f"Name: {dialog.name} | ID: {dialog.id} | Type: {type(dialog.entity).__name__} | Username: {getattr(dialog.entity, 'username', None)}")


async def main():
    """Main function to run the bot"""
    ensure_single_instance()
    atexit.register(release_lock)

    ensure_directories()
    add_rotating_posts_from_directory()
    log("Starting bot and waiting for scheduled slots...")

    try:
        log("Dang ket noi Telegram...")
        await login_client()
        configure_sqlite_session(client)
        me = await client.get_me()
        log(f"Dang nhap: {me.first_name} (@{me.username})")

        await list_dialogs()

        group_id = int(os.getenv('GROUP'))
        try:
            entity = await client.get_entity(group_id)
            log('Da lay entity tu .env! Cho lich gui...')
        except Exception as e:
            log(f"Loi khi lay entity tu .env: {e}")
            return

        # await send_now()
        await schedule_loop(entity)
    finally:
        await client.disconnect()
        release_lock()

if __name__ == '__main__':
    asyncio.run(main())
