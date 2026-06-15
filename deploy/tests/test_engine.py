"""Tests du moteur d'orchestration (plan), hors-ligne et déterministes."""

from vignemale_deploy import Target, build_plan, render


META_RICHE = {
    "services": [{"name": "kb"}, {"name": "rag"}],
    "databases": ["corpus_kb", "corpus_rag"],
    "buckets": ["documents"],
    "secrets": ["OPENAI_API_KEY"],
}


def _kinds(plan):
    return [a.resource.kind for a in plan.actions]


def test_plan_mappe_chaque_primitive():
    # défaut = serverless : pas d'instance, 1 Serverless SQL DB par base déclarée
    plan = build_plan(META_RICHE, Target(app="corpus", image="img@sha256:x"))
    kinds = _kinds(plan)
    assert kinds.count("db_instance") == 0
    assert kinds.count("database") == 2
    assert kinds.count("bucket") == 1
    assert kinds.count("secret") == 1
    assert kinds.count("container") == 1


def test_managed_ajoute_une_instance():
    plan = build_plan(META_RICHE, Target(app="corpus", image="i", db_backend="managed"))
    kinds = _kinds(plan)
    assert kinds.count("db_instance") == 1  # instance partagée
    assert kinds.count("database") == 2     # bases logiques


def test_tout_a_creer_hors_ligne():
    # serverless : 2 bases + 1 bucket + 1 secret + 1 container = 5
    plan = build_plan(META_RICHE, Target(app="corpus", image="img@sha256:x"))
    assert all(a.op == "create" for a in plan.actions)
    assert plan.counts()["create"] == 5


def test_env_vars_provider_switch():
    plan = build_plan(META_RICHE, Target(app="corpus", region="nl-ams", image="i"))
    env = plan.env_vars
    assert env["VIGNEMALE_SQLDB_CORPUS_KB"]
    assert env["VIGNEMALE_SQLDB_CORPUS_RAG"]
    assert env["VIGNEMALE_S3_REGION"] == "nl-ams"
    assert "VIGNEMALE_S3_ENDPOINT" in env
    assert env["VIGNEMALE_SECRET_OPENAI_API_KEY"]


def test_sans_base_pas_d_instance():
    plan = build_plan({"services": [{"name": "api"}]}, Target(app="x", image="i"))
    kinds = _kinds(plan)
    assert "db_instance" not in kinds
    assert "database" not in kinds
    assert kinds == ["container"]


def test_avertissement_image_absente():
    plan = build_plan({"services": [{"name": "api"}]}, Target(app="x"))
    assert any("image" in w for w in plan.warnings)


def test_avertissement_valeur_secret():
    plan = build_plan({"secrets": ["TOKEN"], "services": []}, Target(app="x", image="i"))
    assert any("TOKEN" in w for w in plan.warnings)


def test_render_lisible():
    txt = render(build_plan(META_RICHE, Target(app="corpus", env="staging", image="i")))
    assert "app « corpus » · env « staging »" in txt
    assert "Managed Database" in txt
    assert "Object Storage" in txt
    assert "Résumé" in txt
