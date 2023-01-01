import bottle
import click
import decimal
import sdnotify
from backtab.config import SERVER_CONFIG
from backtab import data_repo
from backtab.data_repo import REPO_DATA, UpdateFailed
from functools import wraps
import typing
import traceback
import time
api = bottle.Bottle()


@api.get("/ping")
def ping():
    return "ok"

@api.get("/products")
def products():
    time.sleep(SERVER_CONFIG.SLOWDOWN)
    return {
        name: product.to_json()
        for name, product in filter(lambda item: item[1].visible, REPO_DATA.products.items())
    }


@api.get("/accounts")
def accounts():
    time.sleep(SERVER_CONFIG.SLOWDOWN)
    return {
        name: {
            "display_name": member.display_name,
            # The balance is negative in the ledger, because the
            # accounts are seen from the hackerspace's viewpoint
            "balance": str(-member.balance_eur),
            "items": member.item_count,
        }
        for name, member in REPO_DATA.accounts.items()
    }


@api.get("/admin/update")
def update():
    time.sleep(SERVER_CONFIG.SLOWDOWN)
    try:
        REPO_DATA.pull_changes()
        return "Success"
    except UpdateFailed as e:
        raise bottle.HTTPResponse(body=traceback.format_exc())


def json_txn_method(fn: typing.Callable[[typing.Dict], data_repo.Transaction]):
    @wraps(fn)
    def result():
        txn = fn(bottle.request.json)
        member_deltas = REPO_DATA.apply_txn(txn)
        return {
            "members": {
                member.internal_name: {
                    "balance": str(-member.balance_eur),
                    "items": member.item_count,
                }
                for member in member_deltas
            },
            "message": txn.beancount_txn.narration +
                       (" (and now has €%s)" % (-txn.primary_account.balance_eur,)
                        if txn.primary_account is not None
                        else ""),
        }
    return result


@api.post("/txn/deposit")
@json_txn_method
def deposit(json):
    return data_repo.DepositTxn(
        member=REPO_DATA.accounts[json["member"]],
        amount=decimal.Decimal(json["amount"]),
    )


@api.post("/txn/xfer")
@json_txn_method
def transfer(json):
    return data_repo.TransferTxn(
        payer=REPO_DATA.accounts[json["payer"]],
        payee=REPO_DATA.accounts[json["payee"]],
        amount=decimal.Decimal(json["amount"]),
    )


@api.post("/txn/buy")
@json_txn_method
def buy(json):

    return data_repo.BuyTxn(
        buyer=REPO_DATA.accounts[json["member"]],
        products=[
            (REPO_DATA.products[product], count)
            for product, count in json["products"].items()
        ],
    )


@click.command()
@click.option('-c', "--config-file", default="config.yml",
              type=click.Path(dir_okay=False, resolve_path=True, exists=True))
def main(config_file):
    notifier = sdnotify.SystemdNotifier()
    # Load config
    SERVER_CONFIG.load_from_config(config_file)
    REPO_DATA.pull_changes()

    notifier.notify("READY=1")
    root = bottle.Bottle()
    root.mount('/api/v1', api)
    bottle.run(root, host=SERVER_CONFIG.LISTEN_ADDR, port=SERVER_CONFIG.PORT)

if __name__ == "__main__":
    main()