# Skinstinct drafts bot

Turns Meera's Telegram notes into LinkedIn drafts in her voice, for her to review. It never posts anything. Posting stays manual on purpose (the Cut).

## Settings (Vercel > Settings > Environment Variables)

| Key | What |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from @BotFather |
| `GEMINI_API_KEY` | from aistudio.google.com |
| `TELEGRAM_USER_ID` | Meera's Telegram user ID. Drafts go to her private chat with the bot. |
| `CHAT_ID` | her notes channel, e.g. `-100...`. The bot must be an admin of it. |

There's no database. Each bot message carries the note its buttons act on. The webhook secret is derived from the bot token.

## Flow

| Stage | What happens |
|---|---|
| Trigger | A post in the channel `CHAT_ID`, or a private message from `TELEGRAM_USER_ID`, reaches `/api/telegram`. |
| Input | The note text. Voice notes are transcribed by Gemini. |
| Context | `lib/data/voice_guide.md` plus her 15 published pieces (`lib/data/published.txt`). |
| Processing | The note is screened against Section 8.1 of the guide. Rejected notes get a reason and a "Draft anyway" button. |
| AI | This runs in `/api/work` so the webhook can answer straight away. Gemini with Google Search finds a real, dated current angle. The draft model writes the post. Plain-code lint and a Gemini rubric review check it, and it is revised once if anything fails. |
| Output | Two messages in her private chat: the Section 12 details with Redo and Keep buttons, then the copy-ready post. Replying to the post with comments revises it. |

## Setup

1. Push this repo and import it in Vercel with Framework Preset "Other" and no build command.
2. Add the 4 settings above and deploy.
3. Meera opens the bot in Telegram and taps Start. If she doesn't know her user ID, the bot tells her.
4. Open `https://<your-app>.vercel.app/api/setup` once. It connects the webhook and checks everything. Open it again with `?test=1` to send her a test message.

## Test

- `python3 tests/test_offline.py`: all flows with fake Telegram and Gemini.
- `GEMINI_API_KEY=... python3 scripts/try_note.py "note text"`: one real Gemini run.
