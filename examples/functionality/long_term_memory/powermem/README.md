# PowerMem Long-Term Memory in AgentScope

This example shows how to integrate PowerMem as a long-term memory backend
in AgentScope, aligned with the Mem0/ReMe five-interface pattern.

## Environment Variables

- `DASHSCOPE_API_KEY`: DashScope API key
- `DASHSCOPE_LLM_MODEL`: optional, default `qwen-max-latest`
- `DASHSCOPE_EMBEDDING_MODEL`: optional, default `text-embedding-v4`
- `DASHSCOPE_EMBEDDING_DIMS`: optional, default `1536`
- `DASHSCOPE_BASE_URL`: optional
- `OCEANBASE_HOST`: default `127.0.0.1`
- `OCEANBASE_PORT`: default `2881`
- `OCEANBASE_USER`: default `root`
- `OCEANBASE_PASSWORD`: default empty
- `OCEANBASE_DATABASE`: default `powermem`
- `OCEANBASE_COLLECTION`: default `memories`

## Start seekdb

```bash
docker run -d --name seekdb -p 2881:2881 -p 2883:2883 docker.io/oceanbase/seekdb:latest
docker exec seekdb mysql -h127.0.0.1 -P2881 -uroot -e "CREATE DATABASE IF NOT EXISTS powermem"
```

## Run the Example

```bash
python examples/functionality/long_term_memory/powermem/memory_example.py
```

## Notes

- `infer=True` enables LLM extraction and consolidation. To validate only the
  storage pipeline, set `infer` to `False` and set
  `intelligent_memory.enabled=False`.
