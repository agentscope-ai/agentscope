# -*- coding: utf-8 -*-
"""
.. _structured-output:

Structured Output
==========================

AgentScope supports two structured output methods, as shown in the following
figure:

- Tool API: Construct a function with input parameters as the fields of the required structured data, and then ask the LLM to call the function to obtain the structured data.
- Text Parsing: Call the LLM API to obtain plain text data, and then parse the structured data from the plain text locally.

.. image:: https://img.alicdn.com/imgextra/i4/O1CN01TLx5qg1tcmx3cKCNN_!!6000000005923-55-tps-661-391.svg
   :width: 100%
   :alt: 两种不同的结构化输出方式

The advantages and disadvantages of the two methods are as follows:

.. list-table::
    :header-rows: 1

    * - Method
      - Advantages
      - Disadvantages
    * - Tool API
      - 1. The model **autonomously** decides when to call the function/generate structured output, which can be well combined with the ReAct algorithm.
        2. Data parsing occurs at the LLM API provider, making local development easier.
        3. Supports complex constraints based on JSON Schema.
      - Requires an LLM API that supports tool invocation.
    * - Text Parsing
      - 1. Simple and easy to use.
        2. Can adjust the format and parsing method based on model capabilities and required structured data.
      - 1. Relies on model capabilities, which may never produce structured data that meets the requirements.
        2. The model **passively** generates structured data, and the developer decides when to prompt the LLM to generate structured data.


Next we will introduce how AgentScope supports these two different parsing methods.

Tool API
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The Tool API method combines tool invocation and structured output. For example, if we need the fields `"thought"`, `"choice"`, and `"number"`, we can construct a function as follows:
"""

from typing import Literal
from pydantic import BaseModel, Field
import json


def generate_response(
    thought: str,
    choice: Literal["apple", "banana"],
    number: int,
) -> None:
    pass


# %%
# The function signature serves as a constraint, and when the model correctly
# invokes the function, we can obtain the corresponding structured data.
#
# Considering that some complex constraints cannot be expressed using Python's
# type annotations, AgentScope supports defining complex constraints using
# Pydantic's `BaseModel`
# Taking the following two model as an example:


class Model1(BaseModel):
    name: str = Field(min_length=0, max_length=20, description="The name")
    description: str = Field(
        min_length=0,
        max_length=200,
        description="The brief description",
    )
    age: int = Field(ge=0, le=100, description="The age")


class Model2(BaseModel):
    choice: Literal["apple", "banana"] = Field(description="Your choice")


# %%
#
# The `ReActAgentV2` class in AgentScope will combine the JSON Schema of the
# `BaseModel` subclass with the schema of a function named `generate_response`.
# This will generate a new schema that can be used to constrain the model's
# output when calling the function.
#
# For example, the following code demonstrates how to use `ReActAgentV2` to
# combine the ReAct algorithm with structured output.

from agentscope.agent import ReActAgentV2
from agentscope.service import ServiceToolkit
from agentscope.message import Msg
import agentscope

agentscope.init(
    model_configs={
        "config_name": "my_config",
        "model_type": "dashscope_chat",
        "model_name": "qwen-max",
    },
)

toolkit = ServiceToolkit()

agent = ReActAgentV2(
    name="Friday",
    model_config_name="my_config",
    service_toolkit=toolkit,
)

msg1 = Msg("user", "Introduce Einstein", "user")
res_msg = agent(msg1, structured_model=Model1)

print("The structured output: ", res_msg.metadata)

# %%
#
# With different `structured_model`, we can achieve different structured output

msg2 = Msg("user", "Pick a fruit", "user")
res_msg = agent(msg2, structured_model=Model2)

print("The structured output: ", res_msg.metadata)

# %%
# To observe how `ReActAgentV2` dynamically constructs the schema of the
# function, we remove a hook function that cleans up the structured output,
# allowing us to print the processed function schema.

# Clear the memory
agent.memory.clear()
# Remove the hook function that cleans up the structured output
agent.remove_hook("post_reply", "as_clear_structured_output")

# Observe the current schema of the target function
print(
    json.dumps(
        toolkit.json_schemas[agent._finish_function],
        indent=4,
        ensure_ascii=False,
    ),
)


# %%
# Now we call the agent once and observe the changes in the schema of the target function

res_msg = agent(msg1, structured_model=Model1)

print(
    json.dumps(
        toolkit.json_schemas[agent._finish_function],
        indent=4,
        ensure_ascii=False,
    ),
)

# %%
# We can see that the schema of the `generate_response` function has been combined with the schema of the `Model1` class.
# Therefore, when the model calls this function, it will generate the corresponding structured data.
#
# .. tip:: More implementation details can be found in the `ReActAgentV2` `source code <https://github.com/modelscope/agentscope/blob/main/src/agentscope/agents/_react_agent_v2.py>`_
#
# Text Parsing
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# AgentScope's `parsers` module provides various parser classes that developers can choose from based on the required structured data.
#
# Here's an example of using `MarkdownJsonDictParser` to parse structured data from Markdown-formatted text.
#
# Defining the Parser
# -------------------

from agentscope.model import ModelResponse
from agentscope.parsers import MarkdownJsonDictParser


parser = MarkdownJsonDictParser(
    content_hint='{"thought": "What you thought", "speak": "What you speak to the user"}',
    required_keys=["thought", "speak"],
)


# %%
# The parser will generate a format instruction according to your input. You
# can use the `format_instruction` property to in your prompt to guide LLM to
# generate the desired output.

print(parser.format_instruction)

# %%
# Parsing the Output
# -------------------
# When receiving output from LLM, use `parse` method to extract the
# structured data.
# It takes an object of `agentscope.model.ModelResponse` as input, parses
# the value of the `text` field, and returns a parsed dictionary in the
# `parsed` field.

dummy_response = ModelResponse(
    text="""```json
{
    "thought": "I should greet the user",
    "speak": "Hi! How can I help you?"
}
```""",
)

print(f"parsed field before parsing: {dummy_response.parsed}")

parsed_response = parser.parse(dummy_response)

print(f"parsed field after parsing: {parsed_response.parsed}")
print(type(parsed_response.parsed))

# %%
# Error Handling
# -------------------
# If the LLM output does not match the expected format, the parser will raise
# an error with a detailed message.
# So developers can present the error message to LLM to guide it to correct
# the output.

error_response = ModelResponse(
    text="""```json
{
    "thought": "I should greet the user"
}
```""",
)

try:
    parsed_response = parser.parse(error_response)
except Exception as e:
    print(e)

# %%
# Advanced Usage
# -------------------
# More Complex Content
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
# Asking LLM to directly generate a JSON dictionary can be challenging,
# especially when the JSON content is complex (e.g. code snippets, nested
# structures).
# You can utilize advanced parsers to structure the LLM output.
# Here is an example of a more complex parser that handle code snippets.

from agentscope.parsers import RegexTaggedContentParser

parser = RegexTaggedContentParser(
    format_instruction="""Response in the following format:
<thought>what you thought</thought>
<number>A random number here</number>
<code>your python code here</code>
""",
    try_parse_json=True,  # Try to parse each value as a JSON object
    required_keys=[
        "thought",
        "number",
        "code",
    ],  # Required keys in the parsed dictionary
)

print(parser.format_instruction)

# %%
# The `RegexTaggedContentParser` uses regular expressions to match the tagged
# content in the text and return the parsed dictionary.
#
# .. note:: The parsed output of `RegexTaggedContentParser` is a dictionary, which means the required keys should be unique.
# You can also change the regular expression pattern by settings the `tagged_content_pattern` parameter when initializing the parser.

import json

dummy_response = ModelResponse(
    text="""<thought>Print the current date</thought>
<number>42</number>
<code>import datetime
print(datetime.datetime.now())
</code>
""",
)

parsed_response = parser.parse(dummy_response)

print("The type of parsed response: ", type(parsed_response.parsed))
print("The type of the number: ", type(parsed_response.parsed["number"]))
print(json.dumps(parsed_response.parsed, indent=4))

# %%
# Auto Post-Processing
# ^^^^^^^^^^^^^^^^^^^^
#
# Within the parsed dictionary, different keys may require different
# post-processing steps.
# For example, in a werewolf game, the LLM is playing the role of a seer, and
# the output should contain the following keys:
#
# - `thought`: The seer's thoughts
# - `speak`: The seer's speech
# - `use_ability`: A boolean value indicating whether the seer should use its ability
#
# In this case, the `thought` and `speak` contents should be stored in the
# agent's memory to ensure the consistency of the agent's behavior.
# The `speak` content should be spoken out to the user.
# The `use_ability` key should be accessed outside the agent easily to
# determine the game flow.
#
# AgentScope supports automatic post-processing of the parsed dictionary by
# providing the following parameters when initializing the parser.
#
# - `keys_to_memory`: key(s) that should be stored in the agent's memory
# - `keys_to_content`: key(s) that should be spoken out
# - `keys_to_metadata`: key(s) that should be stored in the metadata field of the agent's response message
#
# .. note:: If a string is provided, the parser will extract the value of the given key from the parsed dictionary. If a list of strings is provided, a sub-dictionary will be created with the given keys.
#
# Here is an example of using the `MarkdownJsonDictParser` to automatically
# post-process the parsed dictionary.

parser = MarkdownJsonDictParser(
    content_hint='{"thought": "what you thought", "speak": "what you speak", "use_ability": "whether to use the ability"}',
    keys_to_memory=["thought", "speak"],
    keys_to_content="speak",
    keys_to_metadata="use_ability",
)

dummy_response = ModelResponse(
    text="""```json
{
    "thought": "I should ...",
    "speak": "I will not use my ability",
    "use_ability": false
}```
""",
)

parsed_response = parser.parse(dummy_response)

print("The parsed response: ", parsed_response.parsed)
print("To memory", parser.to_memory(parsed_response.parsed))
print("To message content: ", parser.to_content(parsed_response.parsed))
print("To message metadata: ", parser.to_metadata(parsed_response.parsed))

# %%
# Here we show how to create an agent that can automatically post-process the
# parsed dictionary by the following core steps in the `reply` method.
#
# 1. Put the format instruction in prompt to guide LLM to generate the desired output
# 2. Parse the LLM response
# 3. Post-process the parsed dictionary using relevant methods
#
# .. tip:: By changing different parsers, the agent can adapt to different scenarios and generate structured output in various formats.

from agentscope.model import DashScopeChatWrapper
from agentscope.agent import AgentBase
from agentscope.message import Msg


class Agent(AgentBase):
    def __init__(self):
        self.name = "Alice"
        super().__init__(name=self.name)

        self.sys_prompt = f"You're a helpful assistant named {self.name}."

        self.model = DashScopeChatWrapper(
            config_name="_",
            model_name="qwen-max",
        )

        self.parser = MarkdownJsonDictParser(
            content_hint='{"thought": "what you thought", "speak": "what you speak", "use_ability": "whether to use the ability"}',
            keys_to_memory=["thought", "speak"],
            keys_to_content="speak",
            keys_to_metadata="use_ability",
        )

        self.memory.add(Msg("system", self.sys_prompt, "system"))

    def reply(self, msg):
        self.memory.add(msg)

        prompt = self.model.format(
            self.memory.get_memory(),
            # Instruct the model to respond in the required format
            Msg("system", self.parser.format_instruction, "system"),
        )

        response = self.model(prompt)

        parsed_response = self.parser.parse(response)

        self.memory.add(
            Msg(
                name=self.name,
                content=self.parser.to_memory(parsed_response.parsed),
                role="assistant",
            ),
        )

        return Msg(
            name=self.name,
            content=self.parser.to_content(parsed_response.parsed),
            role="assistant",
            metadata=self.parser.to_metadata(parsed_response.parsed),
        )
