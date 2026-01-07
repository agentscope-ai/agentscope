# the script to run the restaurant finder example:

step 1: install the python environment

```bash
conda create -n a2ui_demo python=3.12
```

activate conda environment:
```bash
conda activate a2ui_demo
```

install agentscope and the modified agentscope-runtime

```bash
pip install agentscope
cd agentscope-runtime
# pip install -e.
pip install -e ".[ext]"
```

run the restaurant finder example

```bash
export DASHSCOPE_API_KEY=your_api_key
cd a2ui-as-demo/samples/client/lit
npm run demo:restaurant
```