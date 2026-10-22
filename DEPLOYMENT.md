# Deployment checklist

## Local
```bash
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8080
curl http://localhost:8080/v1/healthz
```

## Docker
```bash
docker build -t vera-bot .
docker run --rm -p 8080:8080 vera-bot
```

## Public hosting
Deploy the folder to any service that supports a Python/Docker web service. The process must bind to `0.0.0.0` and the service should expose the assigned public HTTPS base URL.

Set the start command to:
```bash
uvicorn server:app --host 0.0.0.0 --port $PORT
```

Before submitting, verify these URLs on the public base URL:
- `GET /v1/healthz`
- `GET /v1/metadata`
- `POST /v1/context`
- `POST /v1/tick`
- `POST /v1/reply`

The challenge judge will push all 255 base contexts before the scoring window, so the service must keep state in memory without restarting during evaluation. For a production host, disable aggressive sleeping/scale-to-zero if the platform offers that option.

## Final edits before submission
1. Change `TEAM` and metadata contact details in `server.py`.
2. Run `python smoke_test.py`.
3. Start the server and run the provided `judge_simulator.py` after adding your LLM API key to the simulator configuration.
4. Keep the public URL live for the full judging window.
