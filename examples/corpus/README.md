# Corpus — RAG d'entreprise avec permissions

Le cas « plateforme documentaire » complet (façon back unifié d'entreprise) :
**utilisateurs et groupes**, **knowledge bases partagées**, **indexation de
documents** (PDF via pypdf, ou texte) découpés/embeddés dans **pgvector**, et
un **RAG dont la recherche vectorielle est filtrée par les permissions** —
le filtre d'accès est dans le `WHERE` SQL, pas après coup.

```
corpus/
├── embedding.py      embeddings (hash bag-of-words hors-ligne — brancher un vrai modèle ici)
├── users/            comptes (PII, RGPD), groupes, auth handler
├── kb/               knowledge bases, partage par groupe, indexation, recherche vectorielle
└── rag/              /search et /ask (streamé) — délègue à kb, auth propagée
```

## Lancer (zéro config — le Postgres local inclut pgvector)

```bash
vignemale run examples/corpus
```

## Le scénario

```bash
# comptes
TA=$(curl -s -X POST :8080/signup -d '{"email":"alice@ex.fr","name":"Alice"}' | jq -r .token)
TB=$(curl -s -X POST :8080/signup -d '{"email":"bob@ex.fr","name":"Bob"}' | jq -r .token)

# groupe marketing (alice), bob y entre
curl -X POST -H "Authorization: Bearer $TA" :8080/groups -d '{"name":"marketing"}'
curl -X POST -H "Authorization: Bearer $TA" :8080/groups/1/members -d '{"email":"bob@ex.fr"}'

# deux KB : une partagée au groupe, une privée
curl -X POST -H "Authorization: Bearer $TA" :8080/kbs -d '{"name":"docs-marketing"}'
curl -X POST -H "Authorization: Bearer $TA" :8080/kbs -d '{"name":"confidentiel"}'
curl -X POST -H "Authorization: Bearer $TA" :8080/kbs/1/grant -d '{"group_id":1}'

# indexer un document (texte ou PDF, base64)
curl -X POST -H "Authorization: Bearer $TA" :8080/kbs/1/documents \
     -d "{\"filename\":\"tarifs.txt\",\"content_b64\":\"$(base64 < tarifs.txt)\"}"

# le RAG respecte les permissions :
curl -X POST -H "Authorization: Bearer $TB" :8080/search -d '{"query":"salaires direction"}'
#  → bob ne touche JAMAIS la KB « confidentiel » — le filtre est dans la requête vectorielle

# réponse streamée, sources citées
curl -N -X POST ":8080/ask?token=$TB" -d '{"query":"combien coûte le BTS ?"}'
```

Et l'outillage habituel marche sur l'app : `vignemale check --sql`,
`vignemale gen` (les clients typés sont committés), `vignemale rgpd map`
(les emails sont tagués PII).
