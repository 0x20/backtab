from backtab.config import SERVER_CONFIG
import contextlib
import decimal
import os.path
import subprocess
import threading
import typing
import beancount.core.data
import beancount.loader
import beancount.parser.printer
import beancount.query.query
import collections
import io

repo_lock = threading.RLock()


@contextlib.contextmanager
def transaction():
    """Get the repo lock. Designed to be used with a with statement"""
    with repo_lock:
        yield


def parse_price(price_str: typing.Union[float, str, decimal.Decimal, int]) -> decimal.Decimal:
    return decimal.Decimal(price_str).quantize(decimal.Decimal('0.00'), decimal.ROUND_HALF_EVEN)


class UpdateFailed(Exception):
    pass


class Member:
    internal_name: str
    display_name: str
    account: str
    balance: decimal.Decimal

    def __init__(self, account):
        account_parts = account.split(":")
        if account == "Assets:Cash:Bar":
            self.display_name = "--CASH--"
            self.internal_name = "--cash--"
        elif len(account_parts) != 4:
            raise ValueError("Member account should have four components", account)
        else:
            self.display_name = self.internal_name = account_parts[-1]
        self.account = account
        self.balance = decimal.Decimal("0.00")


Payback = collections.namedtuple("Payback", {
    "account": str,
    "amount": decimal.Decimal,
})


class Product:
    # The display name of the product
    name: str
    # Localized names of the product; the key should be the two-letter
    #  language identifier (e.g., "en", "fr", and "nl")
    localized_name: typing.Dict[str, str]
    # The name of a the currency that should be used for
    # inventory tracking. Should be short and all caps
    currency: str
    price: decimal.Decimal

    # These parameters must either both be set
    payback: typing.Optional[Payback]

    def __init__(self, definition):
        self.name = definition["name"]
        self.localized_name = definition.get("localized_name", {})
        self.currency = definition["currency"]
        self.price = parse_price(definition["price"])
        if "payback" in definition:
            self.payback = Payback(
                account=definition["payback"]["account"],
                amount=parse_price(definition["payback"]["amount"]),
            )

    def to_json(self) -> typing.Dict:
        """Return the JSON form for clients. This does not include payback
         information; that only appears in the input and logs"""
        return {
            "name": self.name,
            "localized_name": self.localized_name,
            "currency": self.currency,
            "price": str(self.price),
        }


class RepoData:
    accounts: typing.Dict[str, Member]
    products: typing.Dict[str, Product]

    @transaction()
    def pull_changes(self):
        """Pull the latest changes from the upstream git repo"""
        subprocess.run(["git", "fetch", "origin"], cwd=SERVER_CONFIG.DATA_DIR)
        try:
            subprocess.run("git merge --no-edit origin/master "
                           "|| ( git merge --abort; false; )",
                           shell=True,
                           cwd=SERVER_CONFIG.DATA_DIR,
                           stderr=subprocess.PIPE,
                           check=True)
        except subprocess.CalledProcessError as e:
            raise UpdateFailed(e.stderr)

        try:
            self.load_data()
        except UpdateFailed:
            # Don't wrap an UpdateFailed
            raise
        except Exception as e:
            # Rollback
            subprocess.run(["git", "checkout", "@{-1}"], check=True)
            raise UpdateFailed("Failed to reload data") from e

    @transaction()
    def load_data(self):
        import yaml

        products = {}
        with open(os.path.join(SERVER_CONFIG.DATA_DIR, "static", "products.yml"), "rt") as f:
            raw_products = yaml.load(f)
        if type(raw_products) != list:
            raise TypeError("Products should be a list")
        for raw_product in raw_products:
            product = Product(raw_product)
            if product.name in products:
                raise UpdateFailed("Duplicate product %s" % (product.name,))
            products[product.name] = product

        # Load ledger
        ledger_data, errors, options = beancount.loader.load_file(
            os.path.join(SERVER_CONFIG.DATA_DIR, "bartab.beancount")
        )
        if errors:
            error_stream = io.StringIO("Failed to load ledger\n")
            beancount.parser.printer.print_errors(errors, error_stream)
            raise UpdateFailed(error_stream.getvalue())

        accounts = {}
        balances = {
            row.account: (row.balance.get_currency_units("EUR").number
                          .quantize(decimal.Decimal("0.00")))
            for row in beancount.query.query.run_query(ledger_data, options, """
                select account, sum(position) as balance
                where PARENT(account) = "Liabilities:Bar:Members"
                   OR account = "Assets:Cash:Bar" 
                group by account
                """)[1]
        }
        for entry in ledger_data:
            if not isinstance(entry, beancount.core.data.Open):
                continue
            if entry.account not in balances:
                print("Didn't load %s as no balance found" % (entry.account,))
                continue
            acct = Member(entry.account)
            if "display_name" in entry.meta:
                acct.display_name = entry.meta["display_name"]
            acct.balance = balances[acct.account]
            accounts[acct.internal_name] = acct

        # That's all the data loaded; now we update this class's fields
        self.accounts = accounts
        self.products = products


REPO_DATA = RepoData()
