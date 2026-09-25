"""Run one note through the real Gemini pipeline locally (no Telegram).

  GEMINI_API_KEY=... python3 scripts/try_note.py "customer asked if our serum works with her vit c..."
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import pipeline  # noqa: E402

text = " ".join(sys.argv[1:]) or ("customer DM again: 'can I use your niacinamide serum with my vit c'. third time this "
                                  "month. nobody publishes pH. ours is 5.5-5.8. tell them vit c first, wait?")
note = {"text": text, "source": "test", "date": pipeline.today()}
scr = pipeline.screen([{"id": 1, "text": text}]).get(1)
print("SCREEN:", scr, "\n")
res = pipeline.run(note, scr)
meta, post = pipeline.render(note, res)
print(post, "\n\n", meta)
print(f"\n[{res['seconds']}s, revised={res['revised']}]")
