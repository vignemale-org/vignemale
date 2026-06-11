"""`vignemale gen` : clients de services typés, générés depuis le graphe."""

import importlib
import shutil
import subprocess
import sys

import pytest

from conftest import EXAMPLES


@pytest.fixture()
def shop_genere(tmp_path):
    app = tmp_path / "shop"
    shutil.copytree(f"{EXAMPLES}/shop", app)
    r = subprocess.run(
        [sys.executable, "-m", "vignemale.cli", "gen", str(app)],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    return app


def test_clients_generes_types(shop_genere):
    catalog = (shop_genere / "vignemale_clients" / "catalog.py").read_text()
    # signature typée + import des modèles sous TYPE_CHECKING (jamais exécuté)
    assert "def get_item(*, id: Any) -> Item:" in catalog
    assert "if TYPE_CHECKING:\n    from catalog.items import Item" in catalog
    assert 'validate_model("catalog.items", "Item"' in catalog

    orders = (shop_genere / "vignemale_clients" / "orders.py").read_text()
    assert "def create_order(*, body: Order | dict) -> dict:" in orders

    init = (shop_genere / "vignemale_clients" / "__init__.py").read_text()
    assert "from . import catalog as catalog" in init


def test_client_genere_appel_local_retype(shop_genere):
    """Le client généré appelle en local (direct) et RE-TYPE la réponse."""
    sys.path.insert(0, str(shop_genere))
    try:
        for mod in ("catalog", "catalog.items", "orders", "orders.create"):
            importlib.import_module(mod)  # charge l'app (registre des endpoints)
        client = importlib.import_module("vignemale_clients.catalog")

        item = client.get_item(id=7)
        assert type(item).__name__ == "Item"  # une vraie instance du modèle
        assert item.id == 7 and item.name == "widget"
    finally:
        sys.path.remove(str(shop_genere))
        for mod in list(sys.modules):
            if mod.split(".")[0] in ("catalog", "orders", "vignemale_clients"):
                del sys.modules[mod]
