# Open Voice + GitHub Copilot Proxy Configuration

This configuration routes Open Voice traffic through the GitHub Copilot Proxy running locally at `localhost:3000`, utilizing multiple accounts for automatic load balancing and rate limit handling.

## 🏗️ Architecture

```
Open Voice Frontend → Open Voice Backend → OpenCode → GitHub Copilot Proxy (localhost:3000) → GitHub Copilot API
                                           ↑
                                    Multi-Account Rotation
                                    (5 accounts, automatic)
```

## 📁 Configuration Files

### 1. `.opencode/opencode.json` (Provider Config)
Defines the `copilot-proxy` provider with free models only:
- **gpt-4.1** - Trivial tasks (greetings, confirmations)
- **gpt-4o** - Simple tasks (basic questions)
- **gpt-4o-mini** - Moderate tasks (standard operations)
- **claude-sonnet-4** - Complex tasks (reasoning, analysis)
- **gpt-5-mini** - Expert tasks (code generation, expert queries)

### 2. `demos/.env.local` (Environment Config)
Routes different complexity tiers to different models:
```
trivial_route  → gpt-4.1
simple_route   → gpt-4o
moderate_route → gpt-4o-mini
complex_route  → claude-sonnet-4
expert_route   → gpt-5-mini
```

## 🚀 Quick Start

### Prerequisites
1. GitHub Copilot Proxy running on `localhost:3000`
2. At least 1 active account in the proxy

### Start the Proxy
```bash
cd ~/Documents/fun/github-copilot-proxy
bun run dev
```

### Start Open Voice
```bash
cd ~/Documents/fun/open-voice/demos
bun run dev
```

Or separately:
```bash
# Terminal 1: Backend
python3 demos/backend/run.py

# Terminal 2: Frontend
cd demos/frontend && bun run dev
```

## 📊 Multi-Account Benefits

The proxy automatically:
- ✅ Rotates requests across accounts
- ✅ Handles rate limits gracefully
- ✅ Retries with next account on failure
- ✅ Tracks quota per account
- ✅ Shows all accounts in status endpoint

**Current Accounts:**
- sahilchouksey (1 req)
- akshadakaleghacc (0 req)
- khushboovishwakarmaghacc (0 req)
- khushibenghacc (4 req)
- **vandanapatelgh (DISABLED)** - Hidden from routing

## 🔧 Managing Accounts

### Check Status
```bash
cd ~/Documents/fun/github-copilot-proxy
bun run quota          # View quota across all accounts
bun run auth:list      # List all accounts with status
```

### Disable/Enable Accounts
```bash
# Disable an account (will be ignored)
bun run auth:disable -- --id account-1770910651825-1

# Enable an account (will be used again)
bun run auth:enable -- --id account-1770910651825-1
```

### Add New Accounts
```bash
# Authenticate new GitHub account
bun run auth:add

# Follow prompts to authenticate via device flow
```

## 🎯 Route Tiers

Open Voice automatically selects routes based on query complexity:

| Tier | Use Case | Model | Max Tokens |
|------|----------|-------|------------|
| **Trivial** | Greetings, confirmations | gpt-4.1 | 500 |
| **Simple** | Basic questions | gpt-4o | 1000 |
| **Moderate** | Standard tasks | gpt-4o-mini | 2000 |
| **Complex** | Multi-step reasoning | claude-sonnet-4 | 4000 |
| **Expert** | Code, analysis | gpt-5-mini | 8000 |

## 💡 Usage Tips

1. **Monitor Quota**: Run `bun run quota` regularly to check usage
2. **Add Accounts**: More accounts = higher rate limits
3. **Free Models**: All configured models are FREE with unlimited requests
4. **Disabled Accounts**: Completely invisible to the proxy (not counted in quota)
5. **Rate Limits**: If you hit 429 errors, wait 1 hour or add more accounts

## 🐛 Troubleshooting

### "All accounts are currently unavailable"
- Rate limits hit: Wait 1 hour or add more accounts
- Check: `curl http://localhost:3000/status`

### "Proxy not running"
- Start proxy: `cd ~/Documents/fun/github-copilot-proxy && bun run dev`
- Check port: `lsof -i :3000`

### Low quota warnings
- Check quota: `bun run quota`
- Add accounts: `bun run auth:add`
- Disable exhausted accounts: `bun run auth:disable -- --id <id>`

## 📈 Monitoring

### Proxy Status
```bash
curl http://localhost:3000/status | jq
```

### Quota Status
```bash
curl http://localhost:3000/quota | jq
```

### Test Proxy
```bash
curl -X POST http://localhost:3000/v1/chat/completions \
  -H "Authorization: Bearer copilot-proxy" \
  -d '{"model": "gpt-4.1", "messages": [{"role": "user", "content": "Hi"}]}'
```

## 🔒 Security Notes

- Tokens stored securely in OS keychain
- API key is "copilot-proxy" (no actual key needed)
- Proxy handles authentication automatically
- Disabled accounts remain in storage but are ignored
