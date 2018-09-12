import bottle
import yaml
from backtab.config import SERVER_CONFIG
from backtab.data_repo import REPO_DATA
import os.path
import threading


api = bottle.Bottle()


@api.get("/products")
def products():
    return {
        name: product.to_json()
        for name, product in REPO_DATA.products.items()
    }


def main():

    # Load config
    SERVER_CONFIG.load_from_config("config.yml")
    REPO_DATA.load_data()

    root = bottle.Bottle()
    root.mount('/api/v1', api)
    bottle.run(root, host=SERVER_CONFIG.LISTEN_ADDR, port=SERVER_CONFIG.PORT)
