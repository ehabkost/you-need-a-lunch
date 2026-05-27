# Type Checking and Validation Guide

This project provides multiple levels of type checking for API requests:

## 1. Pydantic Type Validation (Runtime)

**Best for**: Catching real-world data errors at runtime

### lm_api_types.py — Type Models
Defines pydantic models for all request/response types:
- `CreateManualAccountRequest` — validates POST /manual_accounts
- `InsertTransactionsRequest` — validates POST /transactions
- `ManualAccountsResponse` — validates GET /manual_accounts responses
- etc.

### lm_client_typed.py — Type-Safe Client
Wraps LMClient with automatic pydantic validation:

```python
from lm_client_typed import TypedLMClient

client = TypedLMClient(token="...")

# Type-safe creation with validation
try:
    account = client.create_manual_account(
        name="Checking",
        type="cash",
        balance="100.50",
        currency="usd"
    )
except ValidationError as e:
    print(f"Invalid account data: {e}")
```

**Advantages**:
- ✓ Catches type errors at runtime
- ✓ Provides helpful error messages
- ✓ Can coerce/convert types (e.g., "100.50" → Decimal)
- ✓ Validates enums (AccountType, Currency)
- ✓ Enforces string length limits (`name: max_length=45`)

**Usage**:
```bash
from lm_client_typed import TypedLMClient
client = TypedLMClient(token=os.environ["LUNCHMONEY_API_TOKEN"])
```

## 2. Static Type Checking (Development Time)

**Best for**: Catching type errors before runtime, IDE autocomplete

### Option A: mypy

Install:
```bash
.venv/bin/pip install mypy
```

Run:
```bash
.venv/bin/mypy importer/ --strict
```

**Advantages**:
- ✓ Catches type errors before runtime
- ✓ Industry standard
- ✓ Works with type hints in Python code

**Example error caught**:
```python
# mypy error: Argument "balance" to "create_manual_account" 
# has incompatible type "int"; expected "str"
client.create_manual_account(name="X", type="cash", balance=100, currency="usd")
```

### Option B: pyright (Faster)

Install:
```bash
.venv/bin/pip install pyright
```

Run:
```bash
.venv/bin/pyright importer/
```

**Advantages**:
- ✓ 3-5x faster than mypy
- ✓ Stricter by default
- ✓ Better error messages
- ✓ Integrates with VS Code

## 3. Combining Both Approaches

**For maximum safety**:

1. **Development**: Use IDE with pyright for instant feedback
   - Type hints catch errors as you code
   - Autocomplete suggests available fields

2. **CI/CD**: Run mypy in strict mode
   ```bash
   .venv/bin/mypy importer/ --strict
   ```

3. **Runtime**: Use TypedLMClient for validation
   - Catches data errors from untrusted sources
   - Provides validation error details

## Type Models Reference

### Enums

```python
from lm_api_types import AccountType, Currency

# Valid account types (10 total)
AccountType.CASH
AccountType.CREDIT
AccountType.LOAN
AccountType.OTHER_ASSET
# ... etc

# Currency codes
Currency.USD
Currency.CAD
Currency.EUR
```

### Request Models

```python
from lm_api_types import CreateManualAccountRequest

req = CreateManualAccountRequest(
    name="Checking",
    type="cash",
    balance="100.50",
    currency="usd",
    subtype="checking",                          # optional
    external_id="ynab:budget:account",           # optional
    custom_metadata={"source": "ynab"},          # optional
    exclude_from_transactions=False              # optional
)

# Validation happens automatically
# Raises pydantic.ValidationError if invalid
```

### Response Models

```python
from lm_api_types import ManualAccountsResponse
from pydantic import TypeAdapter

# Validate untrusted API response data
resp_data = {"manual_accounts": [...]}
validated = TypeAdapter(ManualAccountsResponse).validate_python(resp_data)

# Access with type safety
for account in validated.manual_accounts:
    print(account.name, account.type, account.balance)
```

## Common Type Errors

### Error: "Invalid value for AccountType"
**Cause**: Using YNAB type instead of LM type

```python
# ❌ Wrong
create_manual_account(type="checking")

# ✓ Correct
create_manual_account(type="cash", subtype="checking")
```

### Error: "balance must be string, not int"
**Cause**: Passing amount as number instead of string

```python
# ❌ Wrong
create_manual_account(balance=100.50)

# ✓ Correct
create_manual_account(balance="100.50")
```

### Error: "name must be at most 45 chars"
**Cause**: Account name exceeds max length

```python
# ❌ Wrong (47 chars)
create_manual_account(name="This is a very long account name that is too long")

# ✓ Correct
create_manual_account(name="My Checking Account")
```

## Validation Best Practices

1. **Always use TypedLMClient for untrusted data**
   ```python
   # JSON from file/API
   account_data = json.loads(...)
   account = TypedLMClient.create_manual_account(**account_data)
   # Validation error will be raised if invalid
   ```

2. **Use type hints in your code**
   ```python
   def process_account(account: dict) -> None:
       # mypy will catch if you pass wrong type
       client.create_manual_account(**account)
   ```

3. **Let pydantic coerce when safe**
   ```python
   # Pydantic auto-converts date string to date object
   TypeAdapter(UpsertBudgetRequest).validate_python({
       "start_date": "2024-01-01",  # string
       # ... becomes date object internally
   })
   ```

## References

- [Pydantic Documentation](https://docs.pydantic.dev/latest/)
- [mypy Documentation](https://mypy.readthedocs.io/)
- [Pyright Documentation](https://github.com/microsoft/pyright)
- [Python Type Hints (PEP 484)](https://peps.python.org/pep-0484/)
