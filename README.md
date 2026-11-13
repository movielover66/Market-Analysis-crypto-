# ApexQuant INR — Spot Swing & Macro Crypto Panel

Full-stack **Spot Swing (INR ₹)** crypto scanner and **US Macro / News Intelligence** dashboard built with **Python FastAPI**, **CoinGecko**, **CoinDCX INR**, **Alternative.me Fear & Greed**, and **Gemini AI**.

## Project Structure
```text
apexquant-inr-panel/
├── .gitignore
├── README.md
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    └── index.html
```

## Quick Start (Local)
```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate | Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Add your GEMINI_API_KEY inside .env
uvicorn main:app --reload --port 8000
```
Open `http://127.0.0.1:8000` for the dashboard or `http://127.0.0.1:8000/docs` for the API explorer.

## Publish to GitHub
```bash
git init
git add .
git commit -m "Initial commit: ApexQuant INR Spot Swing & News Panel"
git branch -M main
git remote add origin https://github.com/movielover66/apexquant-inr-panel.git
git push -u origin main
```
