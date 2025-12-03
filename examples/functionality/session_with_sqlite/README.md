# Session Management with Sqlite DB

This example demonstrates how to implement session management with a database backend. We use SQLite for simplicity,
but the approach can be adapted for other databases.

Specifically, we implement a ``SqliteSession`` class that persists and retrieves session data from a SQLite table.
The table schema includes fields for session ID, session data (stored as JSON), and timestamps for creation and last
update.

We will create a simple agent and chat with it, then store the session data in the SQLite database. Then in the
``test_load_session`` function, we will load the session data from the database and continue the chat.

## Quick Start

Install agentscope from Pypi or source code.

```bash
pip install agentscope
```

Run the example by the following command

```bash
python main.py
```