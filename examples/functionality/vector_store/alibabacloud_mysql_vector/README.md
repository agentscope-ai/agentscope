# AlibabaCloud MySQL Vector Store Example

This example demonstrates how to use the `AlibabaCloudMySQLStore` class in AgentScope's RAG system for vector storage and similarity search operations using AlibabaCloud MySQL (RDS) with native vector functions.

## Features

AlibabaCloudMySQLStore provides:
- Vector storage using MySQL's native VECTOR data type
- Automatic vector index creation (CREATE VECTOR INDEX) based on distance metric
- Vector functions (VEC_FROMTEXT, VEC_DISTANCE_COSINE, VEC_DISTANCE_EUCLIDEAN)
- Database-level distance calculation and sorting via ORDER BY
- Two distance metrics: COSINE and EUCLIDEAN (supported by AlibabaCloud MySQL)
- Metadata filtering support
- CRUD operations (Create, Read, Update, Delete)
- Support for chunked documents
- Direct access to the underlying MySQL connection for advanced operations
- Full integration with AlibabaCloud RDS MySQL instances

## Prerequisites

### 1. AlibabaCloud RDS MySQL Instance

You need an AlibabaCloud RDS MySQL instance with vector support:

- **Version**: MySQL 8.0+
- **Vector Plugin**: Ensure the vector search plugin is enabled (check `vidx_disabled` parameter is OFF)
- **Network Access**: Configure security group and whitelist to allow access

#### Create RDS MySQL Instance on AlibabaCloud:

1. Go to [AlibabaCloud RDS Console](https://rdsnext.console.aliyun.com/)
2. Click "Create Instance"
3. Select MySQL 8.0 or higher
4. Configure specifications based on your needs
5. Set up network and security settings
6. Note down the connection endpoint (e.g., `rm-xxxxx.mysql.rds.aliyuncs.com`)

#### Configure Database:

```sql
-- Connect to your RDS MySQL instance
mysql -h rm-xxxxx.mysql.rds.aliyuncs.com -P 3306 -u your_username -p

-- Check if vector capability is enabled (vidx_disabled should be OFF)
SHOW VARIABLES LIKE 'vidx_disabled';
-- Expected result: vidx_disabled | OFF
-- If OFF, vector capability is enabled
-- If ON, contact AlibabaCloud support to enable vector search plugin

-- Create database
CREATE DATABASE agentscope_test;

-- Use the database
USE agentscope_test;

-- Verify vector functions are available
SELECT VEC_FROMTEXT('[1,2,3]');
```

### 2. Python Dependencies

```bash
pip install mysql-connector-python agentscope
```

### 3. Network Configuration

Ensure your local machine or server can access the RDS instance:
- Add your IP to the RDS whitelist
- Configure security group rules
- Use SSL connection if required

## Configuration

Update the connection parameters in `main.py`:

```python
store = AlibabaCloudMySQLStore(
    host="rm-xxxxx.mysql.rds.aliyuncs.com",  # Your RDS endpoint
    port=3306,
    user="your_username",        # Your RDS username
    password="your_password",    # Your RDS password
    database="agentscope_test",
    table_name="test_vectors",
    dimensions=768,              # Set to your embedding dimension
    distance="COSINE",
    # Optional: SSL configuration
    # connection_kwargs={
    #     "ssl_ca": "/path/to/ca.pem",
    #     "ssl_verify_cert": True,
    # }
)
```

## Running the Example

```bash
python main.py
```

## Example Tests

The example includes three comprehensive tests:

### 1. Basic CRUD Operations
- Initialize AlibabaCloudMySQLStore
- Add documents with embeddings
- Search for similar documents
- Delete documents
- Get the underlying MySQL connection

### 2. Search with Metadata Filtering
- Add documents with different categories
- Search with and without filters
- Use SQL WHERE clauses for filtering

### 3. Different Distance Metrics
- Test COSINE similarity (best for normalized vectors)
- Test EUCLIDEAN distance (best for absolute distance)

## Key Features Explained

### Distance Metrics

AlibabaCloud MySQL supports two distance metrics:

- **COSINE**: Measures the cosine of the angle between vectors. Values range from 0 (identical) to 2 (opposite). Best for text embeddings and normalized vectors.
- **EUCLIDEAN**: Measures the straight-line Euclidean distance between vectors. Lower values indicate similarity. Best for absolute distance measurements.

**Note**: Unlike some other vector databases, AlibabaCloud MySQL currently only supports COSINE and EUCLIDEAN distance functions. Inner Product (IP) is not supported.

### Metadata Filtering

Use SQL WHERE clauses to filter search results:

```python
results = await store.search(
    query_embedding=embedding,
    limit=10,
    filter='doc_id LIKE "ai%" AND chunk_id > 0',
)
```

### Table Structure

The implementation automatically creates a table with the following structure:

```sql
CREATE TABLE IF NOT EXISTS table_name (
    id VARCHAR(255) PRIMARY KEY,
    embedding VECTOR(dimensions) NOT NULL,
    doc_id VARCHAR(255) NOT NULL,
    chunk_id INT NOT NULL,
    content TEXT NOT NULL,
    total_chunks INT NOT NULL,
    INDEX idx_doc_id (doc_id),
    INDEX idx_chunk_id (chunk_id),
    VECTOR INDEX (embedding) M=16 DISTANCE=cosine  -- or DISTANCE=euclidean
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Note**: The vector index is created directly within the `CREATE TABLE` statement, not as a separate SQL command. The `M` parameter controls the HNSW algorithm's graph connectivity (default: 16).

### Performance Considerations

- **VECTOR Data Type**: Uses MySQL's native VECTOR type for efficient storage
- **Vector Index**: Automatically creates a vector index with the specified distance metric for fast similarity search
- **Database-Level Distance Calculation**: Vector distance calculations are performed at the database level using MySQL's native vector functions (VEC_DISTANCE_COSINE, VEC_DISTANCE_EUCLIDEAN), with sorting done via SQL ORDER BY
- **Native Vector Support**: MySQL 8.0+ has built-in vector functions that are highly optimized for vector operations
- **Supported Distance Metrics**: Only COSINE and EUCLIDEAN are supported
- **Small to Medium Datasets**: AlibabaCloudMySQLStore performs well for datasets up to 100K vectors
- **Large Datasets**: For datasets with millions of vectors, consider using dedicated vector databases (MilvusLite, Qdrant) with specialized indexing (HNSW, IVF, etc.)
- **RDS Performance**: Leverage AlibabaCloud RDS features like read replicas, backup, and monitoring

## Advanced Usage

### Direct Database Access

```python
# Get the MySQL connection for advanced operations
conn = store.get_client()
cursor = conn.cursor()

# Execute custom SQL queries
cursor.execute("SELECT COUNT(*) FROM test_vectors")
count = cursor.fetchone()
print(f"Total vectors: {count[0]}")
```

### Using MySQL Native Vector Functions

MySQL's native vector functions can be used directly in SQL queries:

```python
conn = store.get_client()
cursor = conn.cursor()

# Use MySQL native vector functions directly
query_vector = "[0.1,0.2,0.3,0.4]"
cursor.execute("""
    SELECT
        doc_id,
        VEC_DISTANCE_COSINE(vector, VEC_FROMTEXT(%s)) as distance
    FROM test_vectors
    ORDER BY distance ASC
    LIMIT 10
""", (query_vector,))

results = cursor.fetchall()

# Available MySQL vector functions in AlibabaCloud:
# - VEC_FROMTEXT(text) - Convert text "[1,2,3]" to vector
# - VEC_DISTANCE_COSINE(v1, v2) - Cosine distance
# - VEC_DISTANCE_EUCLIDEAN(v1, v2) - Euclidean distance
```

### SSL Connection

For secure connections to AlibabaCloud RDS:

```python
store = AlibabaCloudMySQLStore(
    host="rm-xxxxx.mysql.rds.aliyuncs.com",
    port=3306,
    user="your_username",
    password="your_password",
    database="agentscope_test",
    table_name="vectors",
    dimensions=768,
    distance="COSINE",
    connection_kwargs={
        "ssl_ca": "/path/to/ca.pem",
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
    },
)
```

### Batch Operations

```python
# Add large batches of documents
batch_size = 1000
for i in range(0, len(all_documents), batch_size):
    batch = all_documents[i:i + batch_size]
    await store.add(batch)
```

### Connection Pooling

```python
store = AlibabaCloudMySQLStore(
    host="rm-xxxxx.mysql.rds.aliyuncs.com",
    port=3306,
    user="your_username",
    password="your_password",
    database="agentscope_test",
    table_name="vectors",
    dimensions=768,
    distance="COSINE",
    connection_kwargs={
        "pool_name": "mypool",
        "pool_size": 10,
        "pool_reset_session": True,
    },
)
```

## Troubleshooting

### MySQL Version Check

Ensure your RDS MySQL version supports vector functions:

```sql
SELECT VERSION();
-- Should be MySQL 8.0 or higher

-- Check if vector capability is enabled (critical check)
SHOW VARIABLES LIKE 'vidx_disabled';
-- Expected result: vidx_disabled | OFF (vector capability enabled)

-- Test vector functions
SELECT VEC_FROMTEXT('[1,2,3]');
```

### Connection Errors

If you get connection errors:

1. **Check Whitelist**: Ensure your IP is in the RDS whitelist
2. **Security Group**: Verify security group rules allow port 3306
3. **Network Type**: Ensure you're using the correct endpoint (public/private)
4. **Credentials**: Double-check username and password

```bash
# Test connection from command line
mysql -h rm-xxxxx.mysql.rds.aliyuncs.com -P 3306 -u your_username -p
```

### Vector Function Errors

If you get errors about VEC_DISTANCE_COSINE or VECTOR type not being recognized:

1. **Check if vector capability is enabled**:

```sql
-- Check vidx_disabled parameter (must be OFF)
SHOW VARIABLES LIKE 'vidx_disabled';
-- Expected result: vidx_disabled | OFF
-- If ON, vector capability is disabled, contact AlibabaCloud support
```

2. Verify MySQL version is 8.0 or higher

```sql
SELECT VERSION();
```

3. Test vector functions availability:

```sql
-- Check if vector functions are available
SELECT VEC_FROMTEXT('[1,2,3]');

-- Check if VECTOR type is supported
CREATE TABLE test_vector (v VECTOR(3));
DROP TABLE test_vector;

-- List vector indexes
SHOW INDEX FROM your_table_name WHERE Index_type = 'VECTOR';
```

If `vidx_disabled` is ON, contact AlibabaCloud support to enable the vector search plugin for your RDS instance.

### Performance Optimization

For large datasets on AlibabaCloud RDS:

1. **Upgrade Instance**: Consider higher specifications (CPU, Memory)
2. **Read Replicas**: Use read replicas for read-heavy workloads
3. **Indexes**: Add indexes on frequently filtered columns
4. **Connection Pool**: Use connection pooling for concurrent operations
5. **Monitor**: Use AlibabaCloud CloudMonitor for performance insights

### Timeout Errors

If you experience timeout errors:

```python
store = AlibabaCloudMySQLStore(
    host="rm-xxxxx.mysql.rds.aliyuncs.com",
    port=3306,
    user="your_username",
    password="your_password",
    database="agentscope_test",
    table_name="vectors",
    dimensions=768,
    distance="COSINE",
    connection_kwargs={
        "connect_timeout": 30,
        "read_timeout": 60,
        "write_timeout": 60,
    },
)
```

## AlibabaCloud RDS Best Practices

1. **Backup**: Enable automatic backups in RDS console
2. **Monitoring**: Set up alerts for CPU, memory, and connection usage
3. **Security**: Use private network connections when possible
4. **Scaling**: Consider read-only instances for read-heavy workloads
5. **Cost Optimization**: Use reserved instances for long-term usage

## Related Resources

- [AlibabaCloud RDS Documentation](https://www.alibabacloud.com/help/en/apsaradb-for-rds)
- [AlibabaCloud MySQL Vector Functions](https://www.alibabacloud.com/help/en/rds/apsaradb-rds-for-mysql/vector-storage-1)
- [AgentScope RAG Tutorial](https://doc.agentscope.io/tutorial/task_rag.html)
- [MySQL Connector Python](https://dev.mysql.com/doc/connector-python/en/)

## Example Use Cases

### RAG System with AlibabaCloud

```python
from agentscope.rag import AlibabaCloudMySQLStore, KnowledgeBase

# Initialize vector store with AlibabaCloud RDS
store = AlibabaCloudMySQLStore(
    host="rm-xxxxx.mysql.rds.aliyuncs.com",
    port=3306,
    user="your_username",
    password="your_password",
    database="rag_system",
    table_name="knowledge_vectors",
    dimensions=768,
    distance="COSINE",
)

# Create knowledge base
kb = KnowledgeBase(store=store)

# Add documents
await kb.add_documents(documents)

# Search
results = await kb.search("What is AI?", top_k=5)
```

## Support

For issues related to:
- **AlibabaCloudMySQLStore**: Open an issue on AgentScope GitHub
- **RDS MySQL**: Contact AlibabaCloud Support
- **Vector Functions**: Check MySQL documentation or AlibabaCloud support

## License

This example is part of the AgentScope project and follows the same license.

