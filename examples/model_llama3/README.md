# Llama3 in AgentScope

AgentScope supports Llama3 now! You can

- 🚀 Set up Llama3 model service in AgentScope! Both CPU and GPU inference are supported!
- 🔧 Test Llama3 in AgentScope built-in examples!
- 🖋 Use Llama3 to build your own multi-agent applications!

Follow the guidance below to use Llama3 in AgentScope!

## Contents

- [CPU Inference](#cpu-inference)
  - [Setup Llama3 Service](#setup-llama3-service)
  - [Use Llama3 in AgentScope](#use-llama3-in-agentscope)
- [GPU Inference](#gpu-inference)
  - [Setup Llama3 Service](#setup-llama3-service-1)
  - [Use Llama3 in AgentScope](#use-llama3-in-agentscope-1)

## CPU Inference

### Setup Llama3 Service

AgentScope supports Llama3 CPU inference with the help of ollama. Note the llama3 models in ollama are quantized into 4 bits.

1. Download ollama from [here](https://ollama.com/).

2. Start ollama software, or execute the following command in terminal

   ```bash
   ollama serve
   ```

3. Pull llama3 model by the following command

 ```bash
 # llama3 8b model
 ollama pull llama3

 # llama3 70b model
 ollama pull llama3:70b
 ```

### Use Llama3 in AgentScope

Use llama3 model with the following model configuration in AgentScope

```python
llama3_8b_ollama_model_configuration = {
   "config_name": "ollama_llama3_8b",
   "model_type": "ollama_chat",
   "model_name": "llama3",
   "options": {
       "temperature": 0.5,
       "seed": 123
   },
   "keep_alive": "5m"
}

llama3_70b_ollama_model_configuration = {
   "config_name": "ollama_llama3_70b",
   "model_type": "ollama_chat",
   "model_name": "llama3:70b",
   "options": {
       "temperature": 0.5,
       "seed": 123
   },
   "keep_alive": "5m"
}
```

After that, you can experience llama3 with our built-in examples! For example, start a conversation with llama3-8b model by the following code:

```python
import agentscope
from agentscope.agent import UserAgent, DialogAgent

agentscope.init(model_configs=llama3_8b_ollama_model_configuration)

user = UserAgent("user")
agent = DialogAgent("assistant", sys_prompt="You're a helpful assistant.", model_config_name="ollama_llama3_8b")

x = None
while True:
    x = agent(x)
    x = user(x)
    if x.content == "exit":
        break
```

## GPU Inference

### Setup Llama3 Service

If you have a GPU, you can set up llama3 model service with the help of Flask and Transformers quickly.

Note you need to apply for permission to download the llama3 model from [Hugging Face model hub](https://huggingface.co/unsloth/llama-3-8b-Instruct).

1. Install Flask and Transformers

```bash
pip install flask transformers torch
```

2. Apply for model permission, and log in your huggingface account in terminal

```bash
huggingface-cli login
```

3. Then run flask server by the following command in scripts directory:

```bash
# 8B model
python flask_transformers/setup_hf_service.py --model_name_or_path meta-llama/Meta-Llama-3-8B-Instruct --port 8000

# 70B model
python flask_transformers/setup_hf_service.py --model_name_or_path meta-llama/Meta-Llama-3-70B-Instruct --port 8000
```

### Use Llama3 in AgentScope

In AgentScope, use the following model configurations

```python
llama3_flask_model_configuration = {
  "model_type": "post_api_chat",
  "config_name": "llama-3",
  "api_url": "http://127.0.0.1:8000/llm/",
  "json_args": {
    "max_length": 4096,
    "temperature": 0.5,
    "eos_token_id": [128001, 128009] # currently the model configuration in huggingface misses eos_token_id
  }
}
```
