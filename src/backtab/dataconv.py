#!/usr/bin/python3

import typing
import beancount.parser.printer
import beancount.core.data as bcdata
import beancount.core.amount as bcamount
import datetime

product_types = dict(
    CIDER="Cider",
    CHIMAY="Chimay",
    CHIP="Chips",
    CM="Club Mate",
    CMRED="Club Mate Red",
    CMKRAFT="Club Mate Kraftstoff",
    CMCOLA="Club Mate Cola",
    COKE="Cola",
    COKE_LIGHT="Cola Light",
    COKE_ZERO="Cola Zero",
    CROQUE="Croque",
    CURRY="Curry",
    DUVEL="Duvel",
    FANTA="Fanta",
    FRITZ="Fritz Cola",
    JUPILER="Jupiler",
    GUINS="Guinness",
    STRKBIER="Heavy Beer",
    HOOD="Hoodie",
    IJS="Ice Cream",
    ICETEA="Ice Tea",
    JNVR="Jenever*",
    JNVRKOT="Jeneverkot",
    JOYLENT="Joylent",
    JOILENTS="Joylent*",
    KITKAT="KitKat",
    KRIEK="Kriek",
    LEFFE="Leffe",
    MNM="M&M",
    MARS="Mars",
    ORVL="Orval",
    PIZZA="Pizza",
    SNACK="Snack",
    SNICK="Snickers",
    SODA="Soda",
    SPRITE="Sprite",
    TSHIRT="T-Shirt",
    TSHIRTS="T-Shirt*",
    TEA="Tea",
    TWX="Twix",
    WATER="Water",
)

DONT_CLOSE={
    # Alex's account gets reused later
    "Liabilities:Bar:Members:Alex",
}

def to_decimal(number: typing.Union[str, float, int, bcdata.Decimal]) -> bcdata.Decimal:
    if type(number) is not str:
        number = "%.02f" % number
    return bcdata.D(number)


def load_json(filename):
    import json
    with open(filename, "rt") as f:
        jsons = "[" + f.read().rstrip()[:-1] + "]"
        return json.loads(jsons)


def member_account(member_name: str) -> str:
    if member_name == "--CASH--":
        return "Assets:Cash:Bar"
    else:
        return "Liabilities:Bar:Members:" + member_name.capitalize()


def new_txn(entry) -> bcdata.Transaction:
    return bcdata.Transaction(
        meta={},
        date=datetime.datetime.fromtimestamp(entry["timestamp"]).date(),
        flag="txn",
        payee="",
        narration=entry["human"],
        tags=set(),
        links=set(),
        postings=[])


class Processor:
    entries: typing.List[bcdata.Transaction]
    initial_balances: typing.Dict[str, bcdata.Decimal]
    last_assertion: typing.Dict[str, datetime.date]
    accounts_by_id: typing.Dict[int, str]
    line: int = 0

    def __init__(self):
        self.entries = []
        self.initial_balances = dict()
        self.last_assertion = dict()
        self.accounts_by_id = {}


    def get_member(self, id, name, date, balance):
        #name = member_account(name)
        name = self.accounts_by_id[id]
        if id in self.accounts_by_id and self.accounts_by_id[id] != name:
            # Generate rename
            txn = bcdata.Transaction(
                meta={},
                date=date,
                flag="txn",
                payee="",
                narration="Rename %(from)s to %(to)s" % {
                    "from": self.accounts_by_id[id],
                    "to": name,
                },
                tags=set("rename"),
                links=set(),
                postings=[])
            bcdata.create_simple_posting(txn, self.accounts_by_id[id], balance, "EUR")
            bcdata.create_simple_posting(txn, name, -balance, "EUR")
            self.entries.append(txn)
            if self.accounts_by_id[id] not in DONT_CLOSE:
                self.entries.append(bcdata.Close(
                    meta={},
                    date=date+datetime.timedelta(days=1),
                    account=self.accounts_by_id[id],
                ))
            # Disable the balance assertion for the next day
            self.last_assertion[name] = date
            self.initial_balances[name] = None
            self.accounts_by_id[id] = name
        elif name not in self.initial_balances:
            self.initial_balances[name] = balance
            self.last_assertion[name] = date
            self.accounts_by_id[id] = name
        elif self.last_assertion.get(name, None) != date and name != "Assets:Cash:Bar":
            self.entries.append(bcdata.Balance(
                {"iline": str(self.line)}, date, name, bcdata.Amount(-balance, "EUR"),
                None, None
            ))
            self.last_assertion[name] = date
        return name

    def process_buy(self, entry):
        txn = new_txn(entry)
        amount = to_decimal(entry["products_totalprice"])
        if amount < 0:
            amount = -amount

        assert len(entry["takefrom"]) == 1, "More than one account in takefrom"

        takefrom = entry["takefrom"][0]
        initial_balance = to_decimal(takefrom["account_money"])
        account_name = self.get_member(takefrom["account_id"], takefrom["account_name"], txn.date, initial_balance)

        bcdata.create_simple_posting(txn, account_name, amount, "EUR")
        bcdata.create_simple_posting(txn, "Income:Bar", -amount, "EUR")
        if "giveto" in entry:
            for giveto in entry["giveto"]:
                giveto_account = self.get_member(giveto["account_id"], giveto["account_name"], txn.date,
                                                 to_decimal(giveto["account_money"]))
                giveto_amount = to_decimal(giveto["account_money_give"])
                bcdata.create_simple_posting(txn, giveto_account, -giveto_amount, "EUR")
                bcdata.create_simple_posting(txn, "Expenses:Bar", giveto_amount, "EUR")
        # TODO: Report products purchased

        self.entries.append(txn)

    def process_deposit(self, entry):
        txn = new_txn(entry)
        assert len(entry["giveto"]) == 1, "More than one giveto action in an entry"
        giveto = entry["giveto"][0]
        amount = to_decimal(giveto["give"])
        initial_balance = to_decimal(giveto['account_money'])
        account = self.get_member(giveto["account_id"], giveto["account_name"], txn.date, initial_balance)

        bcdata.create_simple_posting(txn, account, -amount, "EUR")
        bcdata.create_simple_posting(txn, "Assets:Cash:Bar", amount, "EUR")
        self.entries.append(txn)

    def process_check(self, entry):
        pass

    def process_entry(self, entry):
        self.line += 1
        proc = getattr(self, "process_" + entry["type"], None)
        if proc is None:
            print("Could not process entry type %s\n" % (entry["type"],), file=sys.stderr)
        else:
            return proc(entry)

    def process_json(self, json):
        # Start by computing account IDs
        for entry in json:
            for acct in entry.get("giveto", []) + entry.get("takefrom", []):
                self.accounts_by_id[acct["account_id"]] = member_account(acct["account_name"])
        for entry in json:
            self.process_entry(entry)

    def transfer_opening_balances(self):
        for account, balance in self.initial_balances.items():
            if balance is None:
                continue
            if balance == 0:
                continue
            txn = bcdata.Transaction(
                meta={},
                date=datetime.date(1970,1,1),
                flag="txn",
                payee="",
                narration="Initial balance transfer for " + account,
                tags=set(),
                links=set(),
                postings=[])
            bcdata.create_simple_posting(txn, account, -balance, "EUR")
            bcdata.create_simple_posting(txn, "Assets:InitialBalances", balance, "EUR")
            yield txn

    def print_results(self):
        open_accounts = [
          bcdata.Open({}, datetime.date(1970, 1, 1), acct, None, None)
          for acct in sorted(self.initial_balances.keys())
          ]
        opening_balances = list(self.transfer_opening_balances())
        entries = self.entries

        beancount.parser.printer.print_entries(opening_balances + entries)


def main():
    proc = Processor()
    proc.process_json(load_json("/dev/stdin"))
    proc.print_results()


glbls = globals()

if __name__ == "__main__":
    main()
