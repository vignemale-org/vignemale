# Copilote — le showcase Vignemale

Un mini-SaaS d'assistant IA, **tout Vignemale en une app** :

- **2 services-dossiers** : `users/` (comptes, tokens, auth) et `chat/`
  (conversations persistées, assistant streamé)
- **2 bases Postgres** déclarées dans le code (`SQLDatabase("users")`,
  `SQLDatabase("chat")`) — provisionnées automatiquement au `run`
- **auth handler branché sur la base** : le Bearer token est résolu en SQL
- **appel inter-services** : `chat` demande le profil à `users` via client
- **streaming SSE**, erreurs structurées, logs JSON avec request-id/trace-id

## Lancer (zéro config)

```bash
vignemale run examples/copilote
# vignemale: base Postgres « chat » prête (docker local)
# vignemale: base Postgres « users » prête (docker local)
# vignemale: 7 endpoint(s) sur http://127.0.0.1:8080
```

## Le scénario

```bash
# 1) créer un compte → token
TOKEN=$(curl -s -X POST 127.0.0.1:8080/signup \
  -d '{"email":"ada@example.com","name":"Ada"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

# 2) qui suis-je ?
curl -s -H "Authorization: Bearer $TOKEN" 127.0.0.1:8080/me

# 3) créer une conversation
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  127.0.0.1:8080/conversations -d '{"title":"Mon premier chat"}'

# 4) parler à l'assistant (streamé token par token, token en query pour SSE)
curl -N -X POST "127.0.0.1:8080/conversations/1/chat?token=$TOKEN" \
  -d '{"message":"Présente-toi !"}'

# 5) tout est persisté
curl -s -H "Authorization: Bearer $TOKEN" 127.0.0.1:8080/conversations/1

# 6) et protégé
curl -s 127.0.0.1:8080/conversations          # → 401 unauthenticated
curl -s -X POST 127.0.0.1:8080/signup -d '{"email":"ada@example.com","name":"X"}'
#                                             → 409 already_exists
```

Pendant ce temps, stderr raconte tout en JSON structuré : requêtes avec
statut/durée/request-id/trace-id, logs applicatifs (`compte créé`,
`réponse générée`)…
