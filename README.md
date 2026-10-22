# Vera — Deterministic Merchant Engagement Engine

## Approach
The bot stores the four context types with version-aware replacement and composes from the latest context at tick time. Routing is deterministic: trigger kind selects a small category-aware handler, while message facts are read from the supplied merchant/category/customer/trigger payloads. This avoids stale examples and hallucinated facts when the judge injects new versions.

Customer-facing sends use `merchant_on_behalf`; merchant-facing sends use `vera`. Every proactive send carries the trigger suppression key and a template shape. Customer outreach is consent-aware. `/v1/reply` has explicit handlers for opt-out, repeated WhatsApp auto-replies, commitment/intent transitions, and off-topic asks.

## Tradeoffs
I chose deterministic templates over an external LLM because the challenge rewards reproducibility, grounding, sub-30-second execution, and adaptation to unseen context. The tradeoff is less linguistic variety. The templates therefore pull concrete numbers, dates, prices, source citations, names, offers, and trigger facts whenever they exist.

## Run
```bash
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8080
```

Run the local simulator from this directory after configuring its LLM key:
```bash
python judge_simulator.py
```

For the full seed dataset:
```bash
python dataset/generate_dataset.py --seed-dir dataset --out dataset/expanded
```
