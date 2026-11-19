# TRADEIQ v2.0 — START HERE (Live Render Setup)

এই ভার্সনটি **১০০% লাইভ মার্কেট ডেটা + ৩-ইঞ্জিন অটো পেপার-ট্রেডিং + মাল্টি-সোর্স রিয়েল মার্কেট নিউজ + Gemini Sentiment Shield + নতুন মেনুবার UI**-সহ সম্পূর্ণ আপগ্রেডেড।

## 1. Render Environment Variables (আগের সব ৯টি ভেরিয়েবল সাপোর্টেড)
Render Dashboard → Web Service → Environment-এ নিচের ভেরিয়েবলগুলো সেট করুন:
- `DEMO_MODE=false` (১০০% লাইভ মোড — কোনো ডেমো ফলব্যাক নয়)
- `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET`
- `SUPABASE_URL`, `SUPABASE_KEY`
- `GEMINI_API_KEYS` (একাধিক কী কমা দিয়ে দেওয়া যাবে)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- **Owner Login Security (বাধ্যতামূলক):**
  - `OWNER_PASSWORD_HASH` (তৈরি করতে রান করুন: `python -m app.core.password_hash`)
  - `AUTH_JWT_SECRET` (কমপক্ষে ৩২ অক্ষরের র‍্যান্ডম স্ট্রিং)

## 2. Build & Start Command (`render.yaml`)
- **Build:** `pip install -r backend/requirements.txt && cd frontend && npm install --no-audit --no-fund && npm run build`
- **Start:** `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`

## 3. ডিপ্লয়ের পর যা করবেন
1. অ্যাপ খুলে আপনার **Owner Password** দিয়ে লগইন করুন।
2. উপরের মেনুবার থেকে **Auto-Trader** ট্যাবে গিয়ে নিজের **Account Balance (₹)** এবং **Per-Trade Amount (₹)** সেট করে **Save Config** চাপুন।
3. **Market News** ট্যাবে **Fetch Live News** চেপে ET Markets, Livemint ও Moneycontrol-এর লাইভ নিউজ ও Gemini Sentiment Shield দেখুন।
4. **System & Settings** ট্যাবে **Test Ping** দিয়ে Telegram ও Angel One কানেকশন যাচাই করুন।

> **Paper Trading Only:** এই সিস্টেম কোনো রিয়েল-মানি ব্রোকার অর্ডার প্লেস করে না।
