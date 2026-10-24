# Aura Skincare AI Voice Agent

A minimal starter project with a React + Vite frontend and a FastAPI backend. AI, voice processing, and business logic are not implemented yet.

## Prerequisites

- Node.js and npm
- Python 3.9 or newer

## Run the frontend

In a terminal, from the project folder:

```powershell
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite (usually `http://localhost:5173`).

## Run the backend

In a separate terminal, from the project folder:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

The health check is available at `http://127.0.0.1:8000/health`; interactive API documentation is at `http://127.0.0.1:8000/docs`.

The backend also provides `GET /orders/{order_id}` for mock order lookups and `GET /policies` for the store policies. Run the backend tests from the `backend` folder:

```powershell
python -m unittest discover -s tests -v
```

## Project structure

```text
aura-voice-agent/
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.js
│   └── src/
│       ├── App.jsx
│       ├── App.css
│       └── main.jsx
├── backend/
│   ├── main.py
│   ├── orders.py
│   ├── policies.py
│   ├── requirements.txt
│   └── tests/
│       └── test_backend.py
├── .gitignore
└── README.md
```
