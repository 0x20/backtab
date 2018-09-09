#!/usr/bin/python3

import typing
import beancount.parser.printer
import beancount.core.data as bcdata
import beancount.core.amount as bcamount
import datetime

product_types = dict(
    JUPILER="Jupiler",
    CM="Club Mate",
    CMRED="Club Mate Red",
    CMKRAFT="Club Mate Kraftstoff",
    CMCOLA="Club Mate Cola",
    CHIMAY="Chimay",
    CHIP="Chips",
    CIDER="Cider",
    COLA="Cola",
    COLALT="Cola Light",
    COLAZ="Cola Zero",
    CROQUE="Croque",
    CURRY="Curry",
    DUVEL="Duvel",
    FANTA="Fanta",
    FRITZ="Fritz Cola",
    GUINS="Guinness",
    STRKBIER="Heavy Beer",
    HOOD="Hoodie",
    IJS="Ice Cream",
    ICETEA="Ice Tea",
    JNVR="Jenever*",
    JNVRKOT="Jeneverkot",
    JOYLENT="Joylent",
    JOILETS="Joylent*",
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
    balance_assertions: typing.List[bcdata.Directive]
    line: int = 0

    def __init__(self):
        self.entries = []
        self.initial_balances = dict()
        self.last_assertion = dict()
        self.balance_assertions = []

    def get_member(self, name, date, balance):
        name = member_account(name)
        if name not in self.initial_balances:
            self.initial_balances[name] = balance
            self.last_assertion[name] = date
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

        initial_balance = to_decimal(entry["takefrom"][0]["account_money"])
        account_name = self.get_member(entry["takefrom"][0]["account_name"], txn.date, initial_balance)

        bcdata.create_simple_posting(txn, account_name, amount, "EUR")
        bcdata.create_simple_posting(txn, "Income:Bar", -amount, "EUR")
        if "giveto" in entry:
            for giveto in entry["giveto"]:
                giveto_account = self.get_member(giveto["account_name"], txn.date,
                                                 to_decimal(giveto["account_money"]))
                giveto_amount = to_decimal(giveto["account_money_give"])
                bcdata.create_simple_posting(txn, giveto_account, -giveto_amount, "EUR")
                bcdata.create_simple_posting(txn, "Expenses:Bar", giveto_amount, "EUR")
        # TODO: Report products purchased

        self.entries.append(txn)

    def process_deposit(self, entry):
        txn = new_txn(entry)
        assert len(entry["giveto"]) == 1, "More than one giveto action in an entry"
        amount = to_decimal(entry["giveto"][0]["give"])
        initial_balance = to_decimal(entry["giveto"][0]['account_money'])
        account = self.get_member(entry["giveto"][0]["account_name"], txn.date, initial_balance)

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
        for entry in json:
            self.process_entry(entry)

    def transfer_opening_balances(self):
        for account, balance in self.initial_balances.items():
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

        print('include "../static/includes.beancount"')
        beancount.parser.printer.print_entries(opening_balances + entries)


def main():
    proc = Processor()
    proc.process_json(load_json("/dev/stdin"))
    proc.print_results()


glbls = globals()

if __name__ == "__main__":
    main()
