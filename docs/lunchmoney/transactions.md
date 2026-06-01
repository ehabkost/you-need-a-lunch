# FAQ

### Frequently Asked Questions

1. [How do I track transfers between my own accounts?](#how-do-i-track-transfers-between-my-own-account)
2. [How do I handle transfers between accounts of different currencies?](#how-do-i-handle-transfers-between-accounts-of-different-currencies)
3. [How do I handle credit card payments?](#how-do-i-handle-credit-card-payments)
4. [How do I handle mortgage payments?](#how-do-i-handle-mortgage-payments)
5. [How do I handle the case where others pay me back for a large bill that I paid for?](/finances/transactions/transactions.md#how-do-i-handle-the-case-where-others-pay-me-back-for-a-large-bill-that-i-paid-for)
6. [Is there a limit on how many transactions I can add?](/finances/transactions/transactions.md#is-there-a-limit-on-how-many-transactions-i-can-add)
7. [What are some ways I can stay on top of manually adding transactions?](/finances/transactions/transactions.md#what-are-some-ways-i-can-stay-on-top-of-manually-adding-transactions)
8. [Why can't I update the category for my recurring transactions?](#why-cant-i-update-the-category-for-my-recurring-transactions)
9. [How do I deal with duplicate transactions?](#how-do-i-deal-with-duplicate-transactions)
10. [How do I unsubscribe from the automatic imported transactions emails?](#how-do-i-unsubscribe-from-the-automatic-imported-transactions-emails)

***

## How do I track transfers between my own accounts?

In Lunch Money, internal transfers between your own accounts (including credit card payments, mortgage payments, etc.) would consist of one debit transaction and one credit transaction, categorized in a dedicated transfer category.\
\
In Lunch Money, we recommend using the default '*Payment, Transfer*' category for any transfers between your own accounts. This category has the '[Exclude from totals](/setup/categories/category-properties.md#exclude-from-totals)' and '[Exclude from budget](/setup/categories/category-properties.md#exclude-from-budget)' category properties enabled, to ensure that the transactions in the category won't affect your total income and expenses.

{% hint style="info" %}
If you don't have the 'Payment, Transfer' category set up on your account, you can set it up yourself via the Categories page. Just make sure to enable the 'Exclude from totals' and 'Exclude from budget' [category properties](/setup/categories/category-properties.md).
{% endhint %}

Let's say you transfer $500 from your checking account to pay off your mortgage. You will log this by creating two transactions: A debit of <mark style="color:$danger;">-$500</mark> in Account A, and a credit of <mark style="color:$success;">$500</mark> in Account B. Both categorized in the '*Payment, Transfer*' category:

<figure><img src="/files/8Hx4MzaFBrxZ6bXsms3a" alt=""><figcaption></figcaption></figure>

In the 'Account' column for each transaction, you will indicate the affected account. When paying towards the mortgage, the credit transaction should have the account set to the Mortgage account (as that is the account receiving the money). By setting the accounts associated with the debit and credit transactions, the account balances will automatically update.

## How do I handle transfers between accounts of different currencies?

When you track transfers between two accounts of different currencies, then the debit and credit transactions of the transfer should each be tracked in a different currency. You will need to enter the transactions in the currency that matches the account they are associated with.

Let's say you are moving money from Account A (**USD**) to Account B (**EUR**). You will have two transactions as follows:

* <mark style="color:$danger;">Debit transaction</mark> in **USD** from Account A
* <mark style="color:green;">Credit transaction</mark> in **EUR** to Account B

As long as you record the transactions in the same currency as the accounts they are in, the balances of the accounts will also be reflected correctly.

Both transactions should be categorized in the default 'Payment, Transfer' category which has the '[Exclude from totals](/setup/categories/category-properties.md#exclude-from-totals)' and '[Exclude from budget](/setup/categories/category-properties.md#exclude-from-budget)' category properties enabled, to ensure that the transactions in the category won't affect your total income and expenses.

{% hint style="warning" %}
**Note:** If you group the two transactions, you won't end up with a total of 0.00 as you typically would with transfers transactions of the same currency. That is because the conversion rate used may differ from the conversion rate at your bank. We suggest leaving the two transfer transactions ungrouped in this case.
{% endhint %}

## How do I handle credit card payments?

For credit card payments, usually made up of one debit to your cash account and one credit to your credit card, we recommend using the default [category](/setup/categories.md) "**Payment, Transfer"**. This category is set to be [excluded from totals](/setup/categories.md#exclude-from-totals) and [excluded from budgets](/setup/categories.md#exclude-from-budget), so these transactions should not affect your overall numbers or budget. Transactions in this category should also total up to $0 at the end of the month.

For instance, let's say you spend $10 at the grocery store and $50 on a few other purchases, all on the same credit card. You'll have expense transactions like the following:

| Category    | Amount | Amount Type | Account     |
| ----------- | ------ | ----------- | ----------- |
| Groceries   | $10    | Debit       | Credit card |
| Shopping    | $20    | Debit       | Credit card |
| Restaurants | $30    | Debit       | Credit card |

You will end up making a credit card payment of $60 to cover all those expenses. This will be represented by two transactions:

1. Debit of <mark style="color:$danger;">$60</mark> on your cash account denoting an amount paid from your cash account
2. Credit of <mark style="color:$success;">$60</mark> on your credit card denoting an amount received from your cash account

The two transactions should be categorized as "**Payment, Transfer"** (or any other category marked with the '[exclude from budget](/setup/categories/category-properties.md#exclude-from-budget)' and '[exclude from totals](/setup/categories/category-properties.md#exclude-from-totals)' properties):

| Category          | Amount                                | Amount Type | Account      |
| ----------------- | ------------------------------------- | ----------- | ------------ |
| Payment, Transfer | $60                                   | Debit       | Cash account |
| Payment, Transfer | <mark style="color:green;">$60</mark> | Credit      | Credit card  |

{% hint style="info" %}
To keep finances organized, you can group the two transactions. Grouped transactions will appear as a single entry on the transactions table, and in the case of credit card payments, will display with a total of zero as the debit and credit transactions cancel each other out.
{% endhint %}

## How do I handle mortgage payments?

Similar to credit card payments and other transfers between your own accounts, mortgage payments should be made up of one debit transaction and one credit transaction, categorized in a dedicated transfer category.\
\
In Lunch Money, there's a default 'Payment, Transfer' category, which is what we recommend to use for any transfers between your own accounts. This category has the '[Exclude from totals](/setup/categories/category-properties.md#exclude-from-totals)' and '[Exclude from budget](/setup/categories/category-properties.md#exclude-from-budget)' category properties enabled, to ensure that the transactions in this category won't affect your total income and expenses.

{% hint style="info" %}
If you don't have the 'Payment, Transfer' category set up on your account, you can set it up yourself via the Categories page. Just make sure to enable the 'Exclude from totals' and 'Exclude from budget' [category properties](/setup/categories/category-properties.md).
{% endhint %}

Let's say you move $500 from your checking account to pay off the mortgage. You will log this by creating two transactions: A debit of <mark style="color:$danger;">-$500</mark> from Account A, and a credit of <mark style="color:$success;">$500</mark> to Account B. Both categorized in the 'Payment, Transfer' category:

<figure><img src="/files/8Hx4MzaFBrxZ6bXsms3a" alt=""><figcaption></figcaption></figure>

In the 'Account' column for each transaction, you will indicate the affected account. When paying towards the mortgage, the credit transaction should have the account set to the Mortgage account. By setting the accounts associated with the debit and credit transactions, the account balances will automatically update.

## How do I handle the case where others pay me back for a large bill that I paid for?

We generally recommend using our grouped transactions feature for this use case. Assuming you footed the bill and your roommates end up paying you back their portion, group together all of these transactions and you should be left with a transaction that represents how much you paid. You can then treat this as a high-level transaction and assign a category to it.

![](/files/-M4qrjb1p1A-NDEXiWb7)

If you don't want to bother recording your friends' payment, you can **split** the transaction and simply categorize the portion paid by your friends to a new category called "Reimbursed" which is excluded from totals and excluded from budgets. This ensures that amount is not counted towards your own expenses.

## Is there a limit on how many transactions I can add?

There are limits to how many transactions you can upload on a single request but there isn't a limit on total transactions you add. This limit differs depending on the upload method (CSV or API).

For analytics and whatnot there's currently a hardcoded limit of 20,000 when fetching transactions, so if you have transactions over 10+ years, you may not be able to see the full picture if you do an "all-time" analysis.

## What are some ways I can stay on top of manually adding transactions?

Here are some suggestions from our users on how to stay on top of manually adding transactions!

1. A recurring task in Things (my task manager of choice) that forces me to spend 5-10 minutes at the end of each week rounding things up.
2. My partner and I live in a primarily cash-based society. We keep a little pile of receipts in the office and every few days when it stacks too high, one of us will take it and input transactions manually. Takes about 10 minutes each time!
3. A Lunch Money user, Derek Reiff created [Milk Money](https://milkmoney.club/), a mobile-friendly solution for quick add on-the-go. ([Github source](https://github.com/dareiff/quick-add))
4. Create a Google form for inputting transactions, and every week or so, export the data as CSV and import it into Lunch Money

Finally, there is a secret, undocumented feature which we should really take time to improve upon– the [Lunch Money Quick Add screen](https://my.lunchmoney.app/transactions/new).

## Why can't I update the category for my recurring transactions?

The issue is that recurring items all share the same category. So all transactions linked to the same recurring item will inherit that recurring item's category and merchant name. As such, updating the category of a transaction is a non-action as it gets overridden by the recurring item's category anyway.

If you want to change the category of that transaction, you will need to update the category of a recurring item from the [Recurring Items](https://my.lunchmoney.app/recurring) page, thereby updating the category of all linked transactions. You also have the option on the [Settings](https://my.lunchmoney.app/settings) page of foregoing categories completely for recurring items and having them all be categorized as "Recurring".

## How do I deal with duplicate transactions?

In some cases, you may find duplicate transactions appearing on the Transactions page. To easily go through duplicates and remove them, we recommend using the Deduplication tool.

The Deduplication Tool will search through the active transaction list view to locate similar transactions based on your choice of 2 or more criteria.

For a step-by-step tutorial on how to use the Deduplication Tool, please [see here](/finances/transactions/other-features.md#deduplication-tool).

## How do I unsubscribe from the automatic imported transactions emails?

The emails you receive for newly imported transactions are sent out by Email Rules that are triggered when  new transactions sync into Lunch Money. These Rules are managed on the [Rules page](https://my.lunchmoney.app/rules) (Setup > Rules). On the Rules page, you can click on "[Email Rules](https://my.lunchmoney.app/rules?filter=email)" on the left side of the screen to filter the view to only display Email Rules. Finally, select all undesired rules and click on "Delete selected" to remove them.
