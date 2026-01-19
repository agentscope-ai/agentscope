# PgVectorStore Example

This example demonstrates how to use `PgVectorStore` for vector similarity search with PostgreSQL and pgvector extension.

## Prerequisites

1. **PostgreSQL with pgvector extension**
   ```bash
   # Install PostgreSQL (if not already installed)
   # On macOS:
   brew install postgresql@15
   
   # On Ubuntu/Debian:
   sudo apt-get install postgresql-15
   
   # Install pgvector extension
   # Follow instructions at: https://github.com/pgvector/pgvector
   ```

2. **Python dependencies**
   ```bash
   pip install psycopg2-binary
   ```

3. **Enable pgvector extension in your database**
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

## Configuration

Before running the example, update the connection parameters in `main.py`:

```python
store = PgVectorStore(
    host="localhost",          # Your PostgreSQL host
    port=5432,                 # Your PostgreSQL port
    user="postgres",           # Your PostgreSQL user
    password="your_password",  # Your PostgreSQL password
    database="agentscope_test", # Your database name
    table_name="test_vectors",
    dimensions=4,
    distance="COSINE",
)
```

## Running the Example

```bash
python main.py
```

## Features Demonstrated

1. **Basic CRUD Operations**: Add, search, and delete documents
2. **Metadata Filtering**: Search with SQL WHERE clause filters
3. **Multi-chunk Documents**: Handle documents split into multiple chunks
4. **Distance Metrics**: Compare COSINE, L2, and IP distance metrics

## Distance Metrics

- **COSINE**: Cosine distance (1 - cosine similarity)
- **L2**: Euclidean distance
- **IP**: Inner product (negative dot product)

## Notes

- The pgvector extension must be enabled in your PostgreSQL database
- IVFFlat index is automatically created for efficient similarity search
- Lower distance values indicate higher similarity for COSINE and L2 metrics
- For IP metric, higher (less negative) values indicate higher similarity
