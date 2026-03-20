# AlibabaCloud AnalyticDB MySQL Vector Store Example

This example demonstrates how to use the `AlibabaCloudAnalyticDBStore` class in AgentScope's RAG system for vector storage and similarity search operations using AlibabaCloud AnalyticDB MySQL with native vector functions.

## Features

AlibabaCloudAnalyticDBStore provides:
- Vector storage using AnalyticDB MySQL's native VECTOR data type
- Vector functions (cosine_similarity, l2_distance)
- Two distance metrics: COSINE and EUCLIDEAN
- Metadata filtering support
- CRUD operations (Create, Read, Update, Delete)
- Support for chunked documents
- Full integration with AnalyticDB MySQL instances

## Prerequisites

### 1. AlibabaCloud AnalyticDB MySQL Instance

You need an AnalyticDB MySQL instance:

- **Version**: `COSINE` with v3.2.7+, `EUCLIDEAN` with v3.1.4+
- **Network Access**: Configure whitelist to allow access

#### Create AnalyticDB MySQL Instance on AlibabaCloud:

1. Go to [AnalyticDB MySQL Console](https://adb.console.aliyun.com/)
2. Click "Create Cluster"
3. Next step by step...

#### Configure Database:

```sql
-- Connect to your AnalyticDB MySQL instance
mysql -h ... -P ... -u ... -p ...

-- Create default database
CREATE DATABASE vectorstore;

-- Use the database
USE vectorstore;
```

### 2. Python Dependencies

```bash
pip install pymysql agentscope
```

### 3. Network Configuration

Ensure your local machine or server can access the AnalyticDB MySQL instance:
- Add your IP to the AnalyticDB MySQL whitelist

## Configuration

```bash
export ADB_HOST=...
export ADB_PORT=...
export ADB_USER=...
export ADB_PASSWORD=...
export ADB_DATABASE=vectorstore
export ADB_TABLE=agentscope
```

## Running the Example

```bash
python main.py
```

## Key Features Explained

### Distance Metrics

AnalyticDB MySQL supports two distance metrics:

- **COSINE**: Measures the cosine of the angle between vectors. Values range [0, 1], higher value indicates similarity.
- **EUCLIDEAN**: Measures the straight-line Euclidean distance between vectors. Values range [0, 1], lower value indicates similarity.

### Table Structure

The implementation automatically creates a table with the following structure:

```sql
CREATE TABLE IF NOT EXISTS vectorstore.agentscope (
    id VARCHAR(255) PRIMARY KEY,
    embedding ARRAY<FLOAT>(<dimensions>) NOT NULL,
    doc_id VARCHAR(255) NOT NULL,
    chunk_id INT NOT NULL,
    content TEXT NOT NULL,
    total_chunks INT NOT NULL,
    ANN INDEX idx_vector_embedding(embedding)
)
```

**Note**: The vector index is created directly within the `CREATE TABLE` statement.

## Related Resources

- [AnalyticDB MySQL Documentation](https://www.alibabacloud.com/help/en/analyticdb/analyticdb-for-mysql/product-overview/)
- [AnalyticDB MySQL Vector Best Practice](https://www.alibabacloud.com/help/en/analyticdb/analyticdb-for-mysql/user-guide/vector-retrieval-1)
- [AgentScope RAG Tutorial](https://doc.agentscope.io/tutorial/task_rag.html)

## Support

For issues related to:
- **AlibabaCloudAnalyticDBStore**: Open an issue on AgentScope GitHub
- **AnalyticDB MySQL**: Contact AlibabaCloud Support

## License

This example is part of the AgentScope project and follows the same license.

