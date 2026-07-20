# Corpus — enterprise RAG with permissions

The full "document platform" case (like a unified enterprise backend):
**users and groups**, **shared knowledge bases**, **document indexing**
(PDF via pypdf, or text) split/embedded into **pgvector**, and a
**RAG whose vector search is filtered by permissions** —
the access filter lives in the SQL `WHERE`, not after the fact.

```
corpus/
├── embedding.py      embeddings (offline hash bag-of-words — plug a real model in here)
├── users/            accounts (PII, GDPR), groups, auth handler
├── kb/               knowledge bases, group sharing, indexing, vector search
└── rag/              /search and /ask (streamed) — delegates to kb, auth propagated
```

## Run (zero config — the local Postgres includes pgvector)

```bash
vignemale run examples/corpus
```

## The scenario

```bash
# accounts
TA=$(curl -s -X POST :8080/signup -d '{"email":"alice@ex.fr","name":"Alice"}' | jq -r .token)
TB=$(curl -s -X POST :8080/signup -d '{"email":"bob@ex.fr","name":"Bob"}' | jq -r .token)

# marketing group (alice), bob joins it
curl -X POST -H "Authorization: Bearer $TA" :8080/groups -d '{"name":"marketing"}'
curl -X POST -H "Authorization: Bearer $TA" :8080/groups/1/members -d '{"email":"bob@ex.fr"}'

# two KBs: one shared with the group, one private
curl -X POST -H "Authorization: Bearer $TA" :8080/kbs -d '{"name":"docs-marketing"}'
curl -X POST -H "Authorization: Bearer $TA" :8080/kbs -d '{"name":"confidential"}'
curl -X POST -H "Authorization: Bearer $TA" :8080/kbs/1/grant -d '{"group_id":1}'

# index a document (text or PDF, base64)
curl -X POST -H "Authorization: Bearer $TA" :8080/kbs/1/documents \
     -d "{\"filename\":\"pricing.txt\",\"content_b64\":\"$(base64 < pricing.txt)\"}"

# the RAG respects permissions:
curl -X POST -H "Authorization: Bearer $TB" :8080/search -d '{"query":"management salaries"}'
#  → bob NEVER touches the "confidential" KB — the filter is in the vector query

# streamed reply, cited sources
curl -N -X POST ":8080/ask?token=$TB" -d '{"query":"how much does the BTS cost?"}'
```

And the usual tooling works on the app: `vignemale check --sql`,
`vignemale gen` (the typed clients are committed), `vignemale gdpr map`
(the emails are tagged PII).
