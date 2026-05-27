# Python Client Options for Lunch Money v2 API

There are now three options for making Lunch Money API calls from Python:

## Option 1: Use `lunchmoney-clients` (Auto-generated v2)

**Status**: ✓ Auto-generated from v2 OpenAPI spec, minimal

[lunchmoney-clients on GitHub](https://github.com/juftin/lunchmoney-clients/tree/main/clients/python) — officially generated Python v2 client

### Installation
```bash
pip install git+https://github.com/juftin/lunchmoney-clients.git
```

### Usage
```python
import lunchmoney
from lunchmoney.rest import ApiException

configuration = lunchmoney.Configuration(
    access_token=os.environ["LUNCHMONEY_API_TOKEN"]
)

with lunchmoney.ApiClient(configuration) as api_client:
    api_instance = lunchmoney.ManualAccountsApi(api_client)
    accounts = api_instance.get_manual_accounts()
```

### Advantages
- ✓ **Official** — auto-generated from v2.9.4+ OpenAPI spec
- ✓ **Complete coverage** — all endpoints included
- ✓ **Type hints** — generated classes with type information
- ✓ **Minimal** — lightweight, just what you need
- ✓ **Active** — updated as spec changes

### Disadvantages
- ✗ Bare-bones (no validation beyond type hints)
- ✗ Less Pythonic than custom wrapper
- ✗ Manual error handling needed
- ✗ Requires git+https installation

### Version
- Based on: Lunch Money v2 API (open alpha)
- Author: juftin

---

## Option 2: Custom `lm_client.py` + Auto-generated Types

**Status**: ⚠️ Custom implementation, self-maintained

What we've built:

### Components
1. **lm_client.py** — minimal custom HTTP client
2. **lm_api_types_generated.py** — auto-generated pydantic models (2689 lines)
3. **lm_client_typed.py** — validation wrapper
4. **validate_api_calls.py** — schema validator

### Usage
```python
from lm_client_typed import TypedLMClient

client = TypedLMClient(token=os.environ["LUNCHMONEY_API_TOKEN"])

# Type-safe with pydantic validation
new_account = client.create_manual_account(
    name="Checking",
    type="cash",
    balance="100.50",
    currency="usd"
)
```

### Advantages
- ✓ **Lightweight** — minimal custom code (lm_client.py ~113 lines)
- ✓ **Full control** — can customize behavior
- ✓ **Auto-generated types** — regenerate with one command
- ✓ **Type checking** — supports mypy/pyright
- ✓ **Clear dependencies** — only httpx if needed

### Disadvantages
- ✗ **Maintenance burden** — need to update spec when API changes
- ✗ **Feature gaps** — no CLI, plugins, etc.
- ✗ **Error handling** — less sophisticated than lunchable
- ✗ **Duplication** — recreating what lunchable already does well

### Regeneration
```bash
cd lunchmoney
./regenerate_api_types.sh
```

---

## Option 3: lunchmoney-clients

**Status**: ⚠️ Auto-generated, minimal features

[lunchmoney-clients on GitHub](https://github.com/juftin/lunchmoney-clients)

Bare-bones auto-generated client, middle ground between lunchable and custom client.

### Installation
```bash
pip install git+https://github.com/juftin/lunchmoney-clients.git
```

### Advantages
- ✓ Auto-generated from spec
- ✓ Lightweight
- ✓ Minimal dependencies

### Disadvantages
- ✗ Bare-bones (no validation, error handling)
- ✗ Less Pythonic than lunchable
- ✗ Requires more manual validation

---

## Comparison Table

| Feature | lunchmoney-clients | Option 2 (Custom) |
|---------|-------------------|-------------------|
| **v2 API Support** | ✓ Yes | ✓ Yes |
| **Auto-generated** | ✓ From spec | ✓ From spec |
| **Type Safety** | ~ Type hints | ✓ Pydantic models |
| **Validation** | ✗ Manual | ✓ TypedLMClient |
| **Error Handling** | ✗ Basic | ✗ Basic |
| **Maintenance** | ✓ Official | ✗ Manual |
| **Dependency Weight** | Light | Light |
| **Installation** | Git+https | Local only |

**⚠️ NOTE: `lunchable` uses v1 API (`dev.lunchmoney.app`) — NOT compatible with this project which requires v2**

---

## Decision: Option 2 (Custom with Auto-Generated Types)

✓ **Using**: Custom `lm_client.py` + auto-generated `lm_api_types_generated.py`

### Why This Approach

1. **Self-contained** — no external dependencies beyond httpx (if added)
2. **Auto-generated** — types always match the OpenAPI spec
3. **Type-safe** — full pydantic validation with TypedLMClient
4. **Maintainable** — regenerate types in one command when spec updates
5. **Full control** — can customize client behavior as needed

### What You Have

```
lunchmoney/
├── lm_client.py                    # Core HTTP client (113 lines)
├── lm_api_types_generated.py       # Auto-generated pydantic models (2689 lines)
├── lm_client_typed.py              # Validation wrapper
├── validate_api_calls.py           # Schema validation tests
└── regenerate_api_types.sh         # Regenerate models from spec
```

### Using the Client

```python
from lm_client_typed import TypedLMClient

client = TypedLMClient(token=os.environ["LUNCHMONEY_API_TOKEN"])

# Type-safe, pydantic-validated calls
account = client.create_manual_account(
    name="Checking",
    type="cash",
    balance="100.50",
    currency="usd"
)
```

### Updating from Spec

When the Lunch Money API spec updates:

```bash
cd lunchmoney
./regenerate_api_types.sh
```

This regenerates `lm_api_types_generated.py` from the OpenAPI spec.

### Why NOT lunchmoney-clients

- `lunchmoney-clients` also provides auto-generated code
- But our custom client + auto-gen types gives us:
  - Simpler, more focused code
  - Better integration with our validation tooling
  - Easier to customize for YNAB migration specifics

### Why NOT lunchable

- **Uses v1 API** (`dev.lunchmoney.app`)
- This project requires **v2** (`api.lunchmoney.dev/v2`)
- Not compatible
