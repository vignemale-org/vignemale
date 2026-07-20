# Copilot — the Vignemale showcase

A mini AI-assistant SaaS, **all of Vignemale in one app**:

- **2 folder-services**: `users/` (accounts, tokens, auth) and `chat/`
  (persisted conversations, streamed assistant)
- **2 Postgres databases** declared in code (`SQLDatabase("users")`,
  `SQLDatabase("chat")`) — provisioned automatically on `run`
- **auth handler wired to the database**: the Bearer token is resolved in SQL
- **inter-service call**: `chat` asks `users` for the profile via a client
- **SSE streaming**, structured errors, JSON logs with request-id/trace-id

## Run (zero config)

```bash
vignemale run examples/copilot
# vignemale: Postgres database "chat" ready (local docker)
# vignemale: Postgres database "users" ready (local docker)
# vignemale: 7 endpoint(s) on http://127.0.0.1:8080
```

## The scenario

```bash
# 1) create an account → token
TOKEN=$(curl -s -X POST 127.0.0.1:8080/signup \
  -d '{"email":"ada@example.com","name":"Ada"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

# 2) who am I?
curl -s -H "Authorization: Bearer $TOKEN" 127.0.0.1:8080/me

# 3) create a conversation
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  127.0.0.1:8080/conversations -d '{"title":"My first chat"}'

# 4) talk to the assistant (streamed token by token, token in query for SSE)
curl -N -X POST "127.0.0.1:8080/conversations/1/chat?token=$TOKEN" \
  -d '{"message":"Introduce yourself!"}'

# 5) everything is persisted
curl -s -H "Authorization: Bearer $TOKEN" 127.0.0.1:8080/conversations/1

# 6) and protected
curl -s 127.0.0.1:8080/conversations          # → 401 unauthenticated
curl -s -X POST 127.0.0.1:8080/signup -d '{"email":"ada@example.com","name":"X"}'
#                                             → 409 already_exists
```

Meanwhile, stderr narrates everything as structured JSON: requests with
status/duration/request-id/trace-id, application logs (`account created`,
`reply generated`)…
