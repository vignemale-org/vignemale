"""Tests de l'orchestrateur apply — via un faux provider (aucun appel cloud)."""

import pytest

from vignemale_deploy import Target, apply_plan, build_runtime_env


class FakeProvider:
    """Provider en mémoire : enregistre les appels, renvoie des DSN factices."""

    def __init__(self):
        self.calls = []

    def existing(self, target):
        return set()

    def ensure_databases(self, target, names):
        self.calls.append(("databases", tuple(names)))
        return {
            n: f"postgresql://principal:secret@host:5432/{n}?sslmode=require"
            for n in names
        }

    def ensure_bucket(self, target, name):
        self.calls.append(("bucket", name))

    def deploy_container(self, target, name, image, env, secret_env):
        self.calls.append(("container", name))
        self.env = env
        self.secret_env = secret_env
        return f"https://{name}.fnc.fr-par.scw.cloud"


META = {
    "services": [{"name": "kb"}, {"name": "rag"}],
    "databases": ["corpus_kb", "corpus_rag"],
    "buckets": ["documents"],
    "secrets": ["OPENAI_API_KEY"],
}


def _target():
    return Target(
        app="corpus", env="prod", region="fr-par", image="rg/corpus@sha256:x",
        scw_access_key="AK", scw_secret_key="SK", scw_project_id="proj",
    )


def test_apply_ordonne_et_complet():
    p = FakeProvider()
    dep = apply_plan(META, _target(), p, secret_values={"OPENAI_API_KEY": "sk-1"})
    kinds = [c[0] for c in p.calls]
    # bases (en un appel), puis bucket, puis container en dernier
    assert kinds[0] == "databases"
    assert ("databases", ("corpus_kb", "corpus_rag")) in p.calls
    assert "bucket" in kinds
    assert kinds[-1] == "container"
    assert dep.url.startswith("https://corpus-prod")


def test_dsn_injecte_en_secret():
    p = FakeProvider()
    apply_plan(META, _target(), p, secret_values={"OPENAI_API_KEY": "sk-1"})
    # DSN + clés S3 + secret applicatif → env CHIFFRÉ, jamais en clair public
    assert "VIGNEMALE_SQLDB_CORPUS_KB" in p.secret_env
    assert p.secret_env["VIGNEMALE_SQLDB_CORPUS_KB"].startswith("postgresql://")
    assert "sslmode=require" in p.secret_env["VIGNEMALE_SQLDB_CORPUS_KB"]
    assert "VIGNEMALE_S3_SECRET_KEY" in p.secret_env
    assert p.secret_env["VIGNEMALE_SECRET_OPENAI_API_KEY"] == "sk-1"
    # public : adresse + endpoint S3 (non sensibles)
    assert p.env["VIGNEMALE_ADDR"] == "0.0.0.0:8080"
    assert p.env["VIGNEMALE_S3_REGION"] == "fr-par"


def test_secret_sans_valeur_signale():
    p = FakeProvider()
    dep = apply_plan(META, _target(), p, secret_values={})  # pas de valeur
    assert "VIGNEMALE_SECRET_OPENAI_API_KEY" not in p.secret_env
    assert any("OPENAI_API_KEY" in s for s in dep.steps)


def test_apply_exige_image():
    p = FakeProvider()
    t = Target(app="x", scw_access_key="a", scw_secret_key="b", scw_project_id="c")
    with pytest.raises(ValueError):
        apply_plan({"services": []}, t, p)


def test_build_runtime_env_sans_db_ni_bucket():
    public, secret = build_runtime_env({"services": []}, _target(), {}, {})
    assert public == {"VIGNEMALE_ADDR": "0.0.0.0:8080"}
    assert secret == {}
