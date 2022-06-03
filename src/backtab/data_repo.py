from backtab.config import SERVER_CONFIG
import contextlib
import datetime
import decimal
import os.path
import subprocess
import threading
import typing
import beancount.core.account as bcacct
import beancount.core.data as bcdata
import beancount.core.inventory as bcinv
import beancount.core.interpolate as bcinterp
import beancount.loader
import beancount.parser.printer
import beancount.query.query
import collections
import io

repo_lock = threading.RLock()

CASH_ACCT = "Assets:Cash:Bar"


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
    balance: bcinv.Inventory
    item_currencies: typing.Set[str]
    is_paying_member: bool

    def __init__(self, account, item_curencies):
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
        self.item_currencies = item_curencies
        self.is_paying_member = False

    @property
    def balance_eur(self):
        return self.balance.get_currency_units("EUR").number.quantize(
            decimal.Decimal("0.00"), decimal.ROUND_HALF_EVEN)

    @property
    def item_count(self):
        return sum(
            int(self.balance.get_currency_units(currency).number.quantize(
                decimal.Decimal("0"), decimal.ROUND_HALF_EVEN))
            for currency in self.item_currencies
        )


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
    paying_member_price: decimal.Decimal

    payback: typing.Optional[Payback]

    def __init__(self, definition):
        self.name = definition["name"]
        self.localized_name = definition.get("localized_name", {})
        self.currency = definition["currency"]
        self.price = parse_price(definition["event_price" if SERVER_CONFIG.EVENT_MODE else "price"])
        self.paying_member_price = parse_price(definition["paying_member_price"]) if "paying_member_price" in definition else self.price
        self.category = definition.get("category", "misc")
        self.sort_key = definition.get("sort_key", "%s_%s" % (self.category, self.name))
        if "payback" in definition:
            self.payback = Payback(
                account=definition["payback"]["account"],
                amount=parse_price(definition["payback"]["amount"]),
            )
        else:
            self.payback = None

    def to_json(self) -> typing.Dict:
        """Return the JSON form for clients. This does not include payback
         information; that only appears in the input and logs"""
        return {
            "name": self.name,
            "localized_name": self.localized_name,
            "currency": self.currency,
            "price": str(self.price),
            "category": self.category,
            "sort_key": self.sort_key,
        }


class Transaction:
    txn: bcdata.Transaction
    primary_account: typing.Optional[Member]

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
                timestamp=str(datetime.datetime.utcnow()),
            )
            date = date.date()
        self.primary_account = None
        if title is None:
            raise TypeError("Title must be provided for a transaction")
        self.txn = bcdata.Transaction(
            meta, date,
            flag="txn",
            payee=None,
            narration=title,
            tags=set(),
            links=set(),
            postings=[],
        )

    @property
    def beancount_txn(self):
        return self.txn


class BuyTxn(Transaction):
    def __init__(self,
                 buyer: Member,
                 products: typing.List[typing.Tuple[Product, int]],
                 date: typing.Optional[datetime.datetime]=None):
        total_cost = decimal.Decimal("0.00")
        total_count = 0
        for product, count in products:
            total_count += count
            price = product.paying_member_price if buyer.is_paying_member else product.price
            total_cost += count * price

        super(BuyTxn, self).__init__(
            title="%s bought %d items for €%s" % (
                buyer.display_name, total_count, total_cost,
            ),
            date=date,
            meta={
                "type": "purchase",
            })
        self.primary_account = buyer
        charge = decimal.Decimal("0.00")
        paybacks = collections.defaultdict(lambda: decimal.Decimal("0.00"))

        for product, qty in products:
            price = product.paying_member_price if buyer.is_paying_member else product.price
            charge += price * qty
            if product.payback is not None:
                paybacks[product.payback.account] += \
                    product.payback.amount * qty
            bcdata.create_simple_posting(
                self.txn, "Assets:Inventory:Bar",
                -qty, product.currency)
            bcdata.create_simple_posting(
                self.txn, buyer.account,
                qty, product.currency
            )
        bcdata.create_simple_posting(
            self.txn, buyer.account,
            charge, "EUR"
        )
        for payee, amt in paybacks.items():
            bcdata.create_simple_posting(
                self.txn, payee,
                -amt, "EUR"
            )
            charge -= amt
        bcdata.create_simple_posting(
            self.txn, "Income:Bar",
            -charge, "EUR",
        )


class TransferTxn(Transaction):
    def __init__(self,
                 payer: Member,
                 payee: Member,
                 amount: decimal.Decimal,
                 date: typing.Optional[datetime.datetime]=None):
        super(TransferTxn, self).__init__(
            title="%s gave %s a gift of €%s" % (
                payer.display_name,
                payee.display_name,
                amount),
            date=date,
            meta={
                 "type": "transfer",
            })
        self.primary_account = payer
        bcdata.create_simple_posting(
            self.txn, payer.account,  amount, "EUR")
        bcdata.create_simple_posting(
            self.txn, payee.account, -amount, "EUR")


class DepositTxn(Transaction):
    def __init__(self,
                 member: Member,
                 amount: decimal.Decimal,
                 date: typing.Optional[datetime.datetime]=None):
        super(DepositTxn, self).__init__(
            title="%s deposited €%s" % (member.display_name, amount),
            date=date,
            meta={
                 "type": "deposit",
            })

        self.primary_account = member
        bcdata.create_simple_posting(
            self.txn, member.account, -amount, "EUR")
        bcdata.create_simple_posting(
            self.txn, CASH_ACCT,  amount, "EUR")


class RepoData:
    accounts: typing.Dict[str, Member]
    accounts_raw: typing.Dict[str, Member]
    products: typing.Dict[str, Product]

    # Invariants:
    # instance_ledger_name: the relative path from the data root to the active
    #    ledger file. Does not change once created
    # instance_ledger: The actual ledger file. Closed and set to None whenever
    #    the underlying file may have changed; opened when needed
    instance_ledger_name: typing.Optional[str]
    instance_ledger_uncommitted: bool

    synchronized: bool
    _repo_path: str

    def __init__(self, repo_path=None):
        self.instance_ledger_name = None
        self.instance_ledger_uncommitted = True
        self.synchronized = False
        self._repo_path = repo_path or None

    @property
    def repo_path(self):
        return self._repo_path or SERVER_CONFIG.DATA_DIR

    @transaction()
    def pull_changes(self):
        """Pull the latest changes from the upstream git repo"""
        self.synchronized = False
        try:
            subprocess.run("git pull --no-edit "
                           "|| ( git merge --abort; false; )",
                           shell=True,
                           cwd=self.repo_path,
                           stderr=subprocess.PIPE,
                           check=True)
        except subprocess.CalledProcessError as e:
            raise UpdateFailed(e.stderr)

        try:
            self.load_data()
        except Exception as e:
            # Rollback
            self.git_cmd("git", "checkout", "@{-1}")
            if isinstance(e, UpdateFailed):
                raise
            else:
                raise UpdateFailed("Failed to reload data") from e
        self.synchronized = True

    def git_cmd(self, *args):
        print("\x1b[1;31mGit command: \x1b[0m" + " ".join(args))
        subprocess.run(list(args),
                       cwd=self.repo_path,
                       check=True)

    def add_file(self, filename: str):
        self.git_cmd("git", "add", filename)

    @contextlib.contextmanager
    def git_transaction(self):
        head = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=self.repo_path)
        head = head.decode("utf-8").strip()
        try:
            yield
            self.git_cmd("git", "commit", "-m", "Automatic commit by backtab")
        except Exception:
            self.git_cmd("git", "reset", "--hard", head)
            raise

        self.synchronized = False
        try:
            self.git_cmd("git", "push")
        except subprocess.SubprocessError:
            # Try pulling first
            self.pull_changes()
            self.git_cmd("git", "push")
        self.synchronized = True

    @property
    def instance_ledger(self) -> typing.TextIO:
        while self.instance_ledger_name is None:
            import datetime
            import socket
            trial_name = "%(hostname)s_%(date)s.beancount" % {
                "hostname": socket.gethostname(),
                "date": datetime.datetime.now(datetime.timezone.utc),
            }
            try:
                path = os.path.join(self.repo_path, "ledger", trial_name)
                with open(path, "xt"):
                    pass
                print("Got instance ledger " + path)
                self.instance_ledger_name = path
            except FileExistsError:
                import time
                time.sleep(1)
                continue
        while self.instance_ledger_uncommitted:
            try:
                # We have an instance ledger; add it to git and push
                with self.git_transaction():
                    dynamic_filename = os.path.join(self.repo_path, "ledger", "dynamic.beancount")
                    include_line = 'include "%s"\n' % os.path.basename(self.instance_ledger_name)
                    found_include = False
                    with open(dynamic_filename, "rt") as dynamic:
                        for line in dynamic:
                            if line == include_line:
                                found_include = True
                    if not found_include:
                        with open(dynamic_filename, "at") as dynamic:
                            dynamic.write(include_line)
                    with open(self.instance_ledger_name, "at"):
                        # Make sure the file exists; it might have gotten destroyed by a failed push
                        pass
                    self.add_file(self.instance_ledger_name)
                    self.add_file(os.path.join("ledger", "dynamic.beancount"))
                self.instance_ledger_uncommitted = False
                break
            except subprocess.SubprocessError:
                self.pull_changes()
                continue

        return open(self.instance_ledger_name, "at")

    @transaction()
    def apply_txn(self, txn: Transaction) -> typing.List[Member]:
        bc_txn = txn.beancount_txn

        # Ensure that the transaction balances
        residual = bcinterp.compute_residual(bc_txn.postings)
        tolerances = bcinterp.infer_tolerances(bc_txn.postings, self.bc_options_map)
        assert residual.is_small(tolerances), "Imbalanced transaction generated"

        # add the transaction to the ledger
        while True:
            try:
                with self.git_transaction():
                    beancount.parser.printer.print_entry(bc_txn, file=self.instance_ledger)
                    self.instance_ledger.flush()
                    self.add_file(self.instance_ledger_name)
            except subprocess.SubprocessError:
                self.pull_changes()
            else:
                break

        changed_members = {}
        # Once it's durable, apply it to the live state
        for posting in bc_txn.postings:
            if posting.account in self.accounts_raw:
                member = self.accounts_raw[posting.account]
                member.balance.add_amount(posting.units)
                changed_members[member.internal_name] = member
        return list(changed_members.values())

    @transaction()
    def load_data(self):
        import yaml

        products = {}
        with open(os.path.join(self.repo_path, "static", "products.yml"), "rt") as f:
            raw_products = yaml.load(f)
        if type(raw_products) != list:
            raise TypeError("Products should be a list")
        for raw_product in raw_products:
            product = Product(raw_product)
            if product.currency in products:
                raise UpdateFailed("Duplicate product %s" % (product.name,))
            products[product.currency] = product

        product_currencies = {product.currency for product in products.values()}

        # Load ledger
        ledger_data, errors, options = beancount.loader.load_file(
            os.path.join(self.repo_path, "bartab.beancount")
        )
        if errors:
            error_stream = io.StringIO("Failed to load ledger\n")
            beancount.parser.printer.print_errors(errors, error_stream)
            raise UpdateFailed(error_stream.getvalue())

        accounts = {}
        accounts_raw = {}
        # TODO: Handle this using a realization
        balances = {
            row.account: row.balance
            for row in beancount.query.query.run_query(ledger_data, options, """
                select account, sum(position) as balance
                where PARENT(account) = "Liabilities:Bar:Members"
                   OR account = "Assets:Cash:Bar" 
                group by account
                """)[1]
        }
        for entry in ledger_data:
            if not isinstance(entry, bcdata.Open):
                continue
            if not bcacct.parent(entry.account) == "Liabilities:Bar:Members" and entry.account != "Assets:Cash:Bar":
                print("Didn't load %s as it's no bar account" % (entry.account,))
                continue
            acct = Member(entry.account, item_curencies=product_currencies)
            if "display_name" in entry.meta:
                acct.display_name = entry.meta["display_name"]
            if "is_paying_member" in entry.meta:
                acct.is_paying_member = bool(entry.meta["is_paying_member"])
                if acct.is_paying_member:
                    acct.display_name += "*"
            acct.balance = balances.get(acct.account, bcinv.Inventory())
            accounts[acct.internal_name] = acct
            accounts_raw[acct.account] = acct

        # That's all the data loaded; now we update this class's fields
        self.accounts_raw = accounts_raw
        self.accounts = accounts
        self.products = products
        self.bc_options_map = options

    def close_instance_ledger(self):
        if self.instance_ledger is not None:
            self.instance_ledger.close()
            self.instance_ledger = None


REPO_DATA = RepoData()
