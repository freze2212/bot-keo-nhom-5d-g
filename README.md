# bot-keo-nhom-5d-g

Telegram userbot (Telethon) — forward tin từ `@frezeit` sang nhóm theo lịch, kèm ảnh kết quả ngẫu nhiên.

## Cấu hình `.env`

```env
API_ID=
API_HASH=
PHONE=+84xxxxxxxxx
GROUP=-100xxxxxxxxxx
```

Lấy `API_ID` / `API_HASH` tại https://my.telegram.org

## Chạy local

```bash
python -m venv venv
# Windows: .\venv\Scripts\activate
# Linux:   source venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Deploy VPS (PM2)

```bash
git clone https://github.com/freze2212/bot-keo-nhom-5d-g.git
cd bot-keo-nhom-5d-g
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
nano .env   # điền API_ID, API_HASH, PHONE, GROUP
python bot.py   # nhập OTP lần đầu
pm2 start ecosystem.config.js
pm2 save
pm2 logs bot-keo-nhom-5d-g
```

## Lưu ý

- Gửi **9 tin mẫu** vào `@frezeit` (tin 8 = CON, tin 9 = CÁI).
- Account phải **join nhóm** trong `GROUP`.
- Không commit `.env` và `user_session*.session`.
