# MM CostIQ web app

This is the Next.js 16 front end for the MM CostIQ Utilities MVP. For setup, personas and demo stories, see [../README.md](../README.md).

```powershell
$env:COSTIQ_API_URL = "http://127.0.0.1:8080"   # the FastAPI backend (default)
npx next dev                                     # or: npx next build; npx next start
```

- `app/api/[...path]/route.ts` forwards `/api/*` to the backend at `COSTIQ_API_URL` and adds `X-CostIQ-Persona` from the `costiq_persona` cookie. On Vercel the service binding in `../vercel.json` sets `COSTIQ_API_URL`.
- `proxy.ts` sends visitors without a persona to `/sign-in`.
- Views are client components in `components/views/`. Charts use Chart.js through react-chartjs-2.
