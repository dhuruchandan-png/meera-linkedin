# Skinstinct drafts bot

Turns Meera's Telegram notes into LinkedIn drafts in her voice, for her to review. It never posts anything. Posting stays manual on purpose (the Cut).

## Flow

| Stage | What happens |
|---|---|
| Trigger | Meera messages the bot (text or voice), or posts in her linked channel. Telegram calls `/api/telegram`. A Vercel cron calls `/api/cron` on Mon, Wed and Fri at 09:00 IST. |
| Input | The note text. Voice notes are transcribed by Gemini. |
| Context | `lib/data/voice_guide.md` plus her 15 published pieces (`lib/data/published.txt`). |
| Processing | The note is screened against the guide's Section 8.1, and the strongest notes are queued in Upstash Redis. |
| AI | Gemini with Google Search finds a real, dated current angle. The draft model writes the post. Plain-code lint and a Gemini rubric review check it, and it is revised once if anything fails. |
| Output | The Section 12 structure in three Telegram messages, with Keep, Redo and Reject buttons. Replying to a draft with comments revises it. |

## Commands

- `/draft` or `/draft 12`: draft now
- `/queue`: notes waiting to be drafted
- `/notes`: recent notes and their status
- `/import`: bulk-add old notes, separated by `---` lines, or as a .txt file with the caption `/import`
- `/skip 12`: take a note out of the queue
- `/channel`: how to link her notes channel
- `/help`: how the bot works

## Deploy

1. Fill in `.env.local`.
2. Deploy (GitHub import in Vercel, Framework Preset "Other", or `python3 scripts/deploy.py`), then open `https://<your-app>.vercel.app/api/setup?code=<SETUP_CODE>` once. It registers the Telegram webhook from the deployed env vars.
3. Open the printed `t.me/...?start=...` link on Meera's phone and tap Start. That locks the bot to her.
4. Optional: add the bot as an admin of her notes channel, then post `/connect` there.

## Test

- `python3 tests/test_offline.py`: all flows with fake Telegram, Gemini and Redis.
- `python3 scripts/try_note.py "note text"`: one real Gemini run printed to the terminal.
