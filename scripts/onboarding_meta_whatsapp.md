# Meta WhatsApp Cloud API — Onboarding Guide (A1)

## You need (from Meta Business Manager):
1. A Meta Business Account (business.facebook.com)
2. A WhatsApp Business Account (WABA) — create at business.facebook.com → WhatsApp
3. A phone number added to the WABA (can be a new virtual number)
4. From the Meta Developer Dashboard → your app → WhatsApp → API Setup:
   - **Access Token** (temporary or permanent system user token)
   - **Phone Number ID** (the numeric ID, not the phone number)
   - **App Secret** (Settings → Basic → App Secret)

## Configure in Revenue OS:
1. Go to your app → Settings → قنوات التواصل
2. Click "ربط WhatsApp Business (Meta)"
3. Paste: Access Token + Phone Number ID + App Secret (when prompted)
4. Set the webhook URL in Meta Developer Dashboard → WhatsApp → Webhooks:
   - URL: `https://your-backend.railway.app/api/v1/channels/webhooks/meta-whatsapp`
   - Verify Token: the `META_WEBHOOK_VERIFY_TOKEN` env var value
5. Subscribe to the `messages` field

## Important:
- The system uses **fail-closed** signature verification — the `app_secret` MUST be configured
- Set `TEST_MODE=false` on Railway once you go live (disables the simulator)
- Business-initiated messages require pre-approved templates (create in Settings → Templates)
