# TRADEIQ — START HERE

এই ZIP-টি আগের ApexQuant v5.2.0 source-এর সঙ্গে তুলনা করে আপডেট করা হয়েছে। আগের Render environment variable names সমর্থিত। তবে এটিকে এখনো production-ready বা সম্পূর্ণ Supabase migration বলা যাবে না।

## 1. আগের Render credentials পুনরায় ব্যবহার

পুরোনো ApexQuant ZIP-এ `.env` secret values ছিল না। এই ZIP-এও কোনো API key/password নেই। Render Dashboard → তোমার Web Service → Environment-এ আগের সেট করা values থাকলে সেগুলো সেখানেই রাখবে। নতুন service করলে নিজে আবার configure করবে।

এই পুরোনো variable names TRADEIQ চিনতে পারে:

- `ANGEL_API_KEY`
- `ANGEL_CLIENT_CODE`
- `ANGEL_PIN`
- `ANGEL_TOTP_SECRET`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `GEMINI_API_KEYS`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

কোনো secret chat-এ পাঠাবে না এবং GitHub-এ commit করবে না।

## 2. Render deployment

Repository root-এ `render.yaml` রাখা আছে। Render Blueprint ব্যবহার করলে:

- Root directory: repository root
- Build command: `pip install -r backend/requirements.txt && cd frontend && npm install --no-audit --no-fund && npm run build`
- Start command: `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`

প্রথম deployment-এ `DEMO_MODE=true` রাখবে। এই সেটিং নিরাপদভাবে Demo mode চালু রাখে।

## 3. Integration পরীক্ষা

Deploy হলে:

1. `/health` — backend health
2. `/api/v1/integrations/status` — configured integrations-এর অবস্থা
3. `/api/v1/providers/diagnostics` — Angel One session/WebSocket diagnostics
4. `POST /api/v1/integrations/supabase/verify` — Supabase REST connectivity পরীক্ষা
5. `POST /api/v1/notifications/telegram/test` body `{"confirm_send": true}` — Telegram-এ test message পাঠায়

Angel One real connection পরীক্ষা করতে credentials configure করে private test deployment-এ `DEMO_MODE=false` করতে হবে। তখন backend login, instrument master refresh ও WebSocket start করার চেষ্টা করবে। সাম্প্রতিক WebSocket tick না এলে Market Status `LIVE` দেখাবে না।

## 4. গুরুত্বপূর্ণ অসম্পূর্ণতা

- পুরোনো Supabase `paper_trades` table-এর জন্য compatibility adapter আছে; পুরোনো NSE paper positions restore এবং নতুন paper positions insert/close করার চেষ্টা করে।
- নতুন normalized TRADEIQ schema-তে user settings, watchlists, orders এবং journal-এর পূর্ণ persistence এখনো শেষ হয়নি।
- API authentication সব private endpoint-এ enforce করা হয়নি। এই অবস্থায় public production URL-এ ব্যক্তিগত data রেখো না।
- Frontend feature modules-এর কিছু অংশ এখনো placeholder।
- Real Angel One, Telegram, Supabase ও Gemini credentials দিয়ে live external verification করা হয়নি।
- Frontend production build এই environment-এ verify করা যায়নি; npm dependency installation timeout হয়েছে।

সম্পূর্ণ migration scope এবং বাকি কাজ `docs/LEGACY_APEXQUANT_MIGRATION.md`-এ আছে।

## 5. Local run

Backend:

```bash
cd backend
pip install -r requirements.txt
pytest -q
uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

এই project paper trading ও read-only market data-এর জন্য। কোনো real broker order placement নেই।
