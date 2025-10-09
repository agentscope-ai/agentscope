# -*- coding: utf-8 -*-
"""A module that optimize agent system prompt given dialog history."""
from typing import Union, List

from agentscope.manager import ModelManager
from agentscope.message import Msg
from agentscope.model import ModelWrapperBase

_DEFAULT_META_PROMPT_TEMPLATE = """
You are an excellent Prompt Engineer. Your task is to optimize an Agent's system prompt by adding notes.

The original system prompt provided by the user is:
```
{system_prompt}
```

The dialog history of user interaction with the agent is:
```
{dialog_history}
```

Now, you need to:
1. Determine if the user-agent interaction in the dialog history contains any explicit errors (such as function call errors, failure to adhere to input-output formats), misunderstandings of user intentions, etc.
2. Conduct a detailed analysis of the reasons for the errors and find solutions corresponding to the errors.
3. Based on the causes of the errors and user intentions, write one or several notes that can be added after the user’s system prompt in the form of attention notes or example notes to prevent the same mistakes from happening again in the future.

If the notes to be added include examples, be extremely cautious. If unsure whether the example to add is correct, you may refrain from adding.

The language of the notes you add should be consistent with the original system prompt provided by the user. For example, if the original system prompt provided by the user is written in Chinese, the notes you add should also be in Chinese; if the original system prompt provided by the user is written in English, the notes you add should also be in English.

The notes you add should be included within the tag [prompt_note], for example:
[prompt_note] Please note that the output should only include JSON format [/prompt_note].

If there are no obvious issues in the dialog history, then no notes need to be added.
"""  # noqa

OPT_PROMPT_TEMPLATE_ZH = """你是一个优秀的Prompt Engineer，现在你要通过添加note的方式对一个Agent的system prompt进行优化。

用户提供的原始system prompt是：
```
{system_prompt}
```

用户与之交互的dialog history是：
```
{dialog_history}
```

现在，你要
1. 判断用户与agent交互的dialog history中，是否包含显式的错误（如函数调用错误、没有遵循输入输出格式），对用户意图的误解等。
2. 对发生错误的原因进行详细分析，并且寻找对于对应错误的解决方案。
3. 根据错误原因和用户意图，写一条或几条可以添加在用户system prompt后面的注意事项note，或者example形式的note，使之不要再犯同样的错误。
如果要添加的note包含example，需要格外小心，如果不确定添加的example是否正确，可以先不添加。
你添加的note语言与用户提供的原始system prompt一致，即用户提供的原始system prompt是使用中文写的，你添加的note也必须是中文; 如果用户提供的原始system prompt是使用english写的，你添加的note也必须是english。
你添加的note应该包含在tag [prompt_note]中，例如 [prompt_note] 请注意输出仅包含json格式 [/prompt_note]。如果dialog history没有明显问题，则不需要添加任何note。
"""  # noqa


class SystemPromptOptimizer:
    """A system prompt optimizer class. For now (2024-06-13), the optimizer can
    optimize system prompt by extracting notes from the dialog history. It's
    more like reflection on the dialog history."""

    def __init__(
        self,
        model_or_model_config_name: Union[ModelWrapperBase, str],
        meta_prompt_template: str = _DEFAULT_META_PROMPT_TEMPLATE,
    ) -> None:
        """Initialize the system prompt optimizer.

        Args:
            model_or_model_config_name (`Union[ModelWrapperBase, str]`):
                The model or model config name to be used for generating notes.
            meta_prompt_template (`str`,
            defaults to `_DEFAULT_META_PROMPT_TEMPLATE`):
                The meta prompt to guide the LLM to extract notes from the
                system prompt and dialog history. Must contain placeholders
                `{system_prompt}` and `{dialog_history}`.
        """

        if isinstance(model_or_model_config_name, ModelWrapperBase):
            self.model = model_or_model_config_name
        elif isinstance(model_or_model_config_name, str):
            model_manager = ModelManager.get_instance()
            self.model = model_manager.get_model_by_config_name(
                model_or_model_config_name,
            )
        else:
            raise TypeError(
                "model_or_model_config_name must be ModelWrapperBase or str",
            )

        self.meta_prompt = meta_prompt_template

    def _get_all_tagged_notes(self, response_text: str) -> List[str]:
        """Get all the notes in the response text."""
        # TODO: Use a parser to extract the notes
        notes = []
        start_tag = "[prompt_note]"
        end_tag = "[/prompt_note]"
        start_index = response_text.find(start_tag)
        while start_index != -1:
            end_index = response_text.find(
                end_tag,
                start_index + len(start_tag),
            )
            if end_index != -1:
                note = response_text[start_index + len(start_tag) : end_index]
                notes.append(note)
                start_index = response_text.find(
                    start_tag,
                    end_index + len(end_tag),
                )
            else:
                break
        return notes

    def generate_notes(
        self,
        system_prompt: str,
        dialog_history: List[Msg],
    ) -> List[str]:
        """Given the system prompt and conversation history, generate notes to
        optimize the system prompt.

        Args:
            system_prompt (`str`):
                The system prompt provided by the user.
            dialog_history (`List[Msg]`):
                The conversation history of user interaction with the agent.

        Returns:
            List[str]: The notes added to the system prompt.
        """

        dialog_history_str = "\n".join(
            [f"{msg.name}: {msg.content}" for msg in dialog_history],
        )

        prompt = self.model.format(
            Msg(
                "user",
                self.meta_prompt.format(
                    system_prompt=system_prompt,
                    dialog_history=dialog_history_str,
                ),
                role="user",
            ),
        )

        response = self.model(prompt).text

        # Extract all the notes from the response text
        notes = self._get_all_tagged_notes(response)

        return notes
