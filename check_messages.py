"""Kiểm tra số tin nhắn nguồn trên tài khoản bot."""
import asyncio
import os
import sys
from dotenv import load_dotenv
from telethon import TelegramClient

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

SOURCES = ['frezeit']
phone = (os.getenv('PHONE') or '').strip().replace(' ', '')
phone_digits = ''.join(c for c in phone if c.isdigit())
SESSION_NAME = f'user_session_{phone_digits}' if phone_digits else 'user_session'
client = TelegramClient(SESSION_NAME, int(os.getenv('API_ID')), os.getenv('API_HASH'))


async def count_messages(username):
    me = await client.get_me()
    try:
        user = await client.get_entity(username)
    except Exception as e:
        print(f"  @{username}: KHONG TIM THAY - {e}")
        return

    messages = []
    async for message in client.iter_messages(user, limit=100):
        if message.sender_id == me.id:
            messages.append(message)
    messages.sort(key=lambda x: x.id)

    print(f"  @{username}: {len(messages)} tin (can toi thieu 13)")
    for i, msg in enumerate(messages[:15]):
        preview = (msg.text or msg.message or '[media]').replace('\n', ' ')[:60]
        print(f"    [{i}] id={msg.id} | {preview}")
    if len(messages) > 15:
        print(f"    ... va {len(messages) - 15} tin nua")


async def main():
    if not phone:
        print('[ERROR] PHONE chua cau hinh trong .env')
        return
    await client.start(phone=phone)
    me = await client.get_me()
    print(f"Tài khoản: {me.first_name} (@{me.username})")
    print(f"Session: {SESSION_NAME}")
    print("Dem tin do ban gui cho:")
    for src in SOURCES:
        await count_messages(src)


if __name__ == '__main__':
    asyncio.run(main())
