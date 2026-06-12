"""CLI Vignemale — l'outillage développeur, séparé du runtime.

Composants : `cli` (commandes run/check/gen/rgpd), `collect` (le parser
statique : code Python → meta.proto), `devinfra` (provisioning local
docker), `gen` (clients typés). Le runtime (`vignemale`) reste le seul
package nécessaire en production.
"""
