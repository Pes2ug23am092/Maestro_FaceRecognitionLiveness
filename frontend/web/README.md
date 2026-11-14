# VerifyAura enterprise console

This React, TypeScript, Vite, and Tailwind CSS application is a standalone UI demo
alongside the existing Streamlit application in `frontend/`.

## Run locally

```powershell
cd frontend\web
npm install
npm run dev
```

## Production build

```powershell
npm run build
npm run preview
```

The current interface intentionally runs in demo mode. Audit rows, analytics,
verification outcomes, confidence scores, latency, policy values, and evidence
actions are sample data or browser-local demonstrations. Camera preview is
requested only after an operator selects **Enable camera**; the preview remains
in the browser and is not uploaded. No authentication, decision, AWS Edge,
Rekognition, S3, or KMS service is connected. Connect reviewed backend APIs and
security controls before using this interface for real identity decisions.
