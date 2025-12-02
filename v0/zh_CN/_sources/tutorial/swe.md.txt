# SWE-Bench

We are pleased to share our recent progress on the SWE-Bench (Verified) benchmark, where we successfully resolved 63.4% of issues with the model `claude-3-5-sonnet-20241022`, a trained `Qwen2.5-Coder-Instruct`, and our multi-agent framework AgentScope. In this blog, we'll give a brief overview of our solution.

## Methodology
Our solution is a multi-agent approach that involves two stages: issue-solving and voting. In the first stage, we independently resolve the issue four separate times to generate distinct patches. Then, we vote to select the final patch from these candidates. We elaborate on these two stages as follows.

### Issue-solving Stage
The solving stage consists of three sequential sections: reproducing, fixing, and testing. Each section is handled by a specialized agent.

+ Reproducing section: Given the PR description, reproduce the issue by creating a `reproduction_test.py`file.
+ Fixing section: Fix the issue in the given repository, and run `reproduction_test.py`to ensure the original issue has been properly addressed.
+ Testing section: Identify and execute relevant unit tests in the repository to ensure the fixing doesn't break the original functionality. If any single test fails, the agent continues refining the fix until successful.



In these sections, agents work in a reasoning-acting manner,  equipped with tools like Bash execution, file editor, Git difference viewer, and Git resetor. Specifically, we allow agents to restart their sections by invoking the `section_restart`function when they consider it necessary. To conserve tokens and prevent agents from attempting the same solution repeatedly, at the start of the new section, we will provide a brief summary of the previous attempt. Once the agent completes the tasks within the section, it will call `section_complete` and summarize its trajectory in this section.



To facilitate agents in evaluating their current actions and aligning them with the intended objectives, we require them to conduct a post Chain-of-Thought (CoT) analysis after receiving the tool execution results. This analysis summarizes how the execution outcomes advance the current goal and outlines the subsequent steps that need to be undertaken. We believe this structured reasoning enables agents to confirm the outcomes of the tool's execution and gain awareness of the progress made on the current task.

### Voting Stage
In the voting stage, we train a reward model to evaluate the candidate patches. We utilize Qwen2.5-Coder-Instruct as our base model and fine-tune it on datasets like [SWE-bench-extra](https://huggingface.co/datasets/nebius/SWE-bench-extra) and [SWE-Gym](https://github.com/SWE-Gym/SWE-Gym). This reward model takes a patch alongside the summarized trajectory as input, and assigns a score. The patch with the highest score is ultimately selected.

To address potential biases arising from different solution trajectories and enhance our training data, we define a unified format for the trajectory, which contains the relative code span spotted and the final generated model patch.

In the voting stage, we also try LLM-as-Judge and the generative reward model. However, the performance of LLM-as-Judge is bounded by the base model capability, and it's very difficult to collect training datasets for the generative reward model. The trained score reward model performs better so we use it instead.

### Further Discussions
Here we provide several insights gained from our hands-on experience with this project.

**While LLMs are powerful, we still have great room to explore their potentials.** We observe in most cases, LLM can quickly identify problems and suggest fixes. However, LLMs struggle to understand and follow the implicit conventions in the target repository, e.g. message format and symmetry of the code. As a result, their fixes often break the original functionalities or fail to pass certain assertions.

**High variance poses a significant challenge in development.** During development, we observe that performance can fluctuate substantially across parallel runs. As a result, it can be rather challenging and costly to precisely assess the effects of added features and enhancement, especially in such a complex decision-making process where minor variations in each step can yield markedly different outcomes. On the other hand, such a high-variance nature provides an alternative rationale for voting-based approaches. By aggregating outputs from multiple runs or models, one can mitigate the impact of individual high-variance instances and achieve more stable and robust results.

## Conclusions
The above solution represents a preliminary milestone in our ongoing exploration of software engineering tasks with multi-agent systems. We hope that our submission and the methods shared can be helpful to other researchers in the field. We remain committed to pushing technological boundaries and are excited to share our future progress with the research community.

## Acknowledge
We would like to thank the [Nebius](https://nebius.com/blog/posts/training-and-search-for-software-engineering-agents) and [SWE-Gym](https://github.com/SWE-Gym/SWE-Gym) teams for open sourcing their datasets, and thank the [SWE-Bench](https://github.com/swe-bench/SWE-bench) team for their outstanding contribution in advancing LLM-empowered software engineering.

## Main Contributors
Wenhao Zhang*, Yuexiang Xie*, Dawei Gao*, Xuchen Pan, Yaliang Li, Bolin Ding, Jingren Zhou

*Equal contribution. Author ordering is determined by coin flip.
