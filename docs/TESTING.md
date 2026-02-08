# Testing Guide

## Requirements

- **Python 3.11+** (required for `hashlib.scrypt` used by Werkzeug)
- pytest, pytest-flask, pytest-cov (installed via `requirements.txt`)

## Running Tests

```bash
# Run all tests with verbose output
pytest tests/ -v

# Run with coverage report
pytest tests/ -v --cov=. --cov-report=term-missing

# Run specific test file
pytest tests/test_auth.py -v

# Run specific test class
pytest tests/test_auth.py::TestLogin -v
```

## Test Structure

```
tests/
├── conftest.py              # Fixtures (app, client, authenticated_client)
├── test_auth.py             # Authentication flow tests (28 tests)
├── test_routes.py           # Core route tests (23 tests)
├── test_bullhorn.py         # Bullhorn integration tests (29 tests)
├── test_schedules.py        # Schedule CRUD tests (14 tests)
├── test_settings.py         # Settings tests (7 tests)
├── test_triggers.py         # Trigger endpoint tests (12 tests)
├── test_vetting.py          # Vetting tests (27 tests)
├── test_service_bullhorn.py # BullhornService unit tests (19 tests)
├── test_service_email.py    # EmailService unit tests (15 tests)
└── test_service_vetting.py  # CandidateVettingService unit tests (15 tests)
```

**Total: 189 tests**

## CI

Tests run automatically on push via GitHub Actions (see `.github/workflows/test.yml`).
