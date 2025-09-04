# ACEBench Example

This is an example of agent-oriented evaluation in AgentScope.

We take [ACEBench](https://github.com/ACEBench/ACEBench) as an example benchmark, and run
a ReAct agent with [Ray](https://github.com/ray-project/ray)-based evaluator, which supports
**distributed** and **parallel** evaluation.

To run the example, you need to install AgentScope first, and then run the evaluation with the following command:

```bash
python main.py --data_dir {data_dir} --result_dir {result_dir}
```

## Further Reading

- [ACEBench](https://github.com/ACEBench/ACEBench)
- [Ray](https://github.com/ray-project/ray)
