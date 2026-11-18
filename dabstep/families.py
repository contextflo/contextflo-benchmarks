"""Question families: what a DABstep question asks, ignoring which merchant, month, amount or id.

DABstep's hard questions come from a handful of templates filled in with different
merchants, dates, amounts and fee ids. Two questions in the same family need the same
analysis; only the inputs differ. The held-out set is balanced by family, and results are
reported by family, so one template can't dominate.

Labelled by hand from the question texts (every hard question matches exactly one pattern;
an unmatched question is an error, not a new family). Patterns are matched in order.
"""

import re

FAMILIES: list[tuple[str, str, re.Pattern]] = [
    ("applicable_fee_ids", "Which fee rules apply to a merchant in a period",
     re.compile(r"(applicable fee ids for .+ in|fee ids applicable to)", re.I)),
    ("total_fees_paid", "Total fees a merchant paid in a period",
     re.compile(r"(total fees \(in euros\) that .+ (paid|should pay))", re.I)),
    ("relative_fee_delta", "How much more or less a merchant pays if one fee's rate changes",
     re.compile(r"what delta would .+ pay if the relative fee", re.I)),
    ("mcc_change_delta", "How much more or less a merchant pays if its business category changes",
     re.compile(r"had changed its MCC code", re.I)),
    ("fraud_aci_steering", "Cheapest payment method to move a merchant's fraudulent transactions to",
     re.compile(r"move the fraudulent transactions towards a different", re.I)),
    ("card_scheme_steering", "Which card scheme a merchant should steer traffic to, for min or max fees",
     re.compile(r"to which card scheme should the merchant", re.I)),
    ("most_expensive_aci", "Most expensive payment method for a given card payment",
     re.compile(r"what would be the most expensive Authorization Characteristics Indicator", re.I)),
    ("fee_ids_by_account_aci", "Which fee rules apply to an account type and payment method",
     re.compile(r"fee ID or IDs that apply to account_?type", re.I)),
    ("average_fee_for_scheme", "Average fee a card scheme charges for a given amount",
     re.compile(r"what would be the average fee that the card scheme", re.I)),
    ("cheapest_or_priciest_scheme", "Which card scheme is cheapest or most expensive on average",
     re.compile(r"in the average scenario, which card scheme", re.I)),
    ("most_expensive_mcc", "Most expensive business category for a given amount",
     re.compile(r"most expensive MCC for a transaction", re.I)),
    ("fee_restricted_to_account_type", "Which merchants a fee change to one account type would affect",
     re.compile(r"was only applied to account type .+ which merchants", re.I)),
    ("merchants_affected_by_fee", "Which merchants a fee rule applied to in a year",
     re.compile(r"which merchants were affected by the Fee with ID", re.I)),
    ("average_transaction_value", "Average transaction value grouped by a field",
     re.compile(r"average transaction value grouped by", re.I)),
]


def family(*, question: str) -> str:
    for name, _, pattern in FAMILIES:
        if pattern.search(question):
            return name
    raise ValueError(f"question matches no family: {question[:120]!r}")


def describe(*, name: str) -> str:
    for family_name, description, _ in FAMILIES:
        if family_name == name:
            return description
    raise ValueError(f"unknown family {name!r}")
