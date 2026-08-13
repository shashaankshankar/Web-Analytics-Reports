import os


# Importing app.main constructs the production-shaped ASGI application. Keep
# collection deterministic without weakening the runtime's fail-closed config
# validation or depending on a developer's untracked .env file.
os.environ.setdefault("PLATFORM_OPERATOR_EMAIL", "test-operator@example.com")
