# RefundOps

An AI investigator for PayPal disputes. A web agent gathers evidence across disconnected merchant sites, recommends a response PayPal allows for that dispute, and carries it out through PayPal only after the merchant approves.

Built for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/). Work in progress: the full README (setup, architecture, PayPal APIs, AI tools, safety, reuse disclosure) lands before submission.

## Quick start (development)

```bash
uv sync
uv run playwright install chromium
cp .env.example .env        # then fill in your keys
uv run python manage.py migrate
uv run uvicorn RefundOpsAI.asgi:application --host 127.0.0.1 --port 8000
```

Open http://localhost:8000/.

## License

MIT, see [LICENSE](LICENSE). The browser-agent core in `navgent/` is adapted from the author's earlier project; see `navgent/VENDORED.md`.
