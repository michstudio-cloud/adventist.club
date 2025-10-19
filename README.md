<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://github.com/user-attachments/assets/0aa67016-6eaf-458a-adb2-6e31a0763ed6" />
</div>

# Run and deploy your AI Studio app

This contains everything you need to run your app locally.

View your app in AI Studio: https://ai.studio/apps/drive/1qOSRVaV7V452UE2PmevXqB2eOK0RABze

## Run Locally

**Prerequisites:**  Node.js


1. Install dependencies:
   `npm install`
2. Set the `GEMINI_API_KEY` in [.env.local](.env.local) to your Gemini API key
3. Run the app:
   `npm run dev`

## Deployment notes (Vercel) and server-side AI proxy

This project includes a serverless endpoint at `server/api/ai-feedback.ts` which proxies requests to the Gemini API. This keeps your Gemini API key out of the browser bundle. To deploy on Vercel:

1. Add `GEMINI_API_KEY` as a Project Environment Variable on Vercel (do NOT commit it to source).
2. Deploy the project to Vercel (it will auto-detect the frontend and serverless function).
3. The frontend calls `/api/ai-feedback` (relative path) to request AI feedback.

Security note: Never expose your Gemini API key on the client. Always proxy from a server-side environment.

## Supabase (suggested)

If you plan to use Supabase for auth, storage and DB:

1. Create a Supabase project and add `SUPABASE_URL` and `SUPABASE_ANON_KEY` (for client) and `SUPABASE_SERVICE_KEY` (for server) as env vars.
2. Use Supabase on the frontend for auth and storage; use service key on serverless functions for privileged operations (uploading certificates, signing, etc.).

Frontend env vars (add to `.env.local`):

- VITE_SUPABASE_URL=https://xyzcompany.supabase.co
- VITE_SUPABASE_ANON_KEY=your-anon-key

Storage:
- Create a bucket named `evidence` (public or set public read policy for uploaded files).
- For private buckets, serve images through signed URLs from server-side.
