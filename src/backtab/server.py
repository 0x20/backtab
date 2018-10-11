import bottle
import yaml
from backtab.config import SERVER_CONFIG
from backtab.data_repo import REPO_DATA, UpdateFailed
import os.path
import threading
import traceback

api = bottle.Bottle()


@api.get("/products")
def products():
    return {
        name: product.to_json()
        for name, product in REPO_DATA.products.items()
    }


@api.get("/accounts")
def accounts():
    return {
        name: {
            "display_name": member.display_name,
            # The balance is negative in the ledger, because the accounts are seen from the hackerspace's viewpoint
            "balance": str(-member.balance),
        }
        for name, member in REPO_DATA.accounts.items()
    }

@api.get("/admin/update")
def update():
    try:
        REPO_DATA.pull_changes()
        return "Success"
    except UpdateFailed as e:
        raise bottle.HTTPResponse(body=traceback.format_exc())



def main():

    # Load config
    SERVER_CONFIG.load_from_config("config.yml")
    REPO_DATA.pull_changes()
    REPO_DATA.load_data()

    root = bottle.Bottle()
    root.mount('/api/v1', api)
    bottle.run(root, host=SERVER_CONFIG.LISTEN_ADDR, port=SERVER_CONFIG.PORT)
