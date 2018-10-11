from backtab.config import SERVER_CONFIG
import contextlib
import datetime
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


class Transaction:
    txn: beancount.core.data.Transaction

    def __init__(self,
                 title: str=None,
                 date: typing.Optional[datetime.datetime]=None,
                 meta: typing.Optional[typing.Dict[str, str]]=None):
        if meta is None:
            meta = {}
        if date is None:
            date = datetime.datetime.utcnow()
        if isinstance(date, datetime.datetime):
            meta.update(
                timestamp = str(datetime.datetime.utcnow()),
            )
            date = date.date()
        if title is None:
            raise TypeError("Title must be provided for a transaction")
        self.txn = beancount.core.data.Transaction(
            meta, date,
            flag = "txn",
            payee=None,
            narration=title
        )

class BuyTxn(Transaction):
    buyer: Member

    def __init__(self, buyer: Member, date: typing.Optional[datetime.datetime]):
        super(BuyTxn, self).__init__(title = "%s bought some stuff" % buyer.display_name,
                                     date=date,
                                     meta={
                                         "type": "purchase",
                                     })
        self.buyer = buyer

    def add_product(self, product, quantity=1):
        beancount.core.data.create_simple_posting(
            self.txn, "Assets:"
        )

class RepoData:
    accounts: typing.Dict[str, Member]
    products: typing.Dict[str, Product]

    # Invariants:
    # instance_ledger_name: the relative path from the data root to the active
    #    ledger file. Does not change once created
    # instance_ledger: The actual ledger file. Closed and set to None whenever
    #    the underlying file may have changed; opened when needed
    instance_ledger_name: typing.Optional[str]
    instance_ledger: io.FileIO

    def __init__(self):
        self.instance_ledger_name = None
        self.instance_ledger = None

    @transaction()
    def pull_changes(self):
        """Pull the latest changes from the upstream git repo"""
        if self.instance_ledger is not None:
            self.instance_ledger.close()
            self.instance_ledger = None

        try:
            subprocess.run("git pull --no-edit "
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

    def add_file(self, filename: str):
        subprocess.run(["git", "add", filename],
                       cwd=SERVER_CONFIG.DATA_DIR,
                       check=True)

    @contextlib.contextmanager
    def git_transaction(self):
        head = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=SERVER_CONFIG.DATA_DIR)
        try:
            # TODO: fetch first?
            yield
            subprocess.run(["git", "push"],
                           cwd=SERVER_CONFIG.DATA_DIR,
                           check=True)
        except Exception:
            # Rollback
            subprocess.run(["git", "reset", "--hard", head],
                           cwd=SERVER_CONFIG.DATA_DIR,
                           check=True)
            raise

    def open_instance_ledger(self):
        if self.instance_ledger is not None:
            return self.instance_ledger
        while self.instance_ledger_name is not None:
            import datetime
            import socket
            trial_name = "%(hostname)s_%(date)s.beancount" % {
                "hostname": socket.gethostname(),
                "date": datetime.datetime.now(datetime.timezone.utc),
            }
            with self.git_transaction():
                try:
                    path = os.path.join(SERVER_CONFIG.DATA_DIR, "ledger", trial_name)
                    with open(path, "xt"):
                        pass
                    self.instance_ledger_name = path
                except FileExistsError:
                    import time
                    time.sleep(1)
                    continue
                else:
                    # We have an instance ledger; add it to git and push
                    with open(os.path.join(SERVER_CONFIG.DATA_DIR, "ledger", "dynamic.beancount"), "at") as dynamic:
                        dynamic.write('include "%s"\n' % trial_name)
                    self.add_file(os.path.join("ledger", trial_name))
                    self.add_file(os.path.join("ledger", "dynamic.beancount"))
        self.instance_ledger = open(self.instance_ledger_name, "at")
        return self.instance_ledger

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

    def close_instance_ledger(self):
        if self.instance_ledger is not None:
            self.instance_ledger.close()
            self.instance_ledger = None


REPO_DATA = RepoData()
