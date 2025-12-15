# 📊 Data Analysis Agent

This example demonstrates a **Data Analysis Agent** capable of writing and executing Python code to analyze data and generate visualizations.

## Features

- **Code Execution**: Uses `ReActAgent` equipped with a local Python code interpreter.
- **Data Analysis**: Can read CSV files (e.g., `titanic.csv`) using `pandas`.
- **Visualization**: Can generate charts using `matplotlib` / `seaborn` and save them as images.
- **Compliance**: Follows AgentScope open-source standards.

## Prerequisites

- Python 3.10+
- AgentScope installed
- Dependencies: `pandas`, `matplotlib`, `seaborn`

```bash
pip install pandas matplotlib seaborn
```

## Setup

Set your DashScope API Key (or other model providers):

```bash
# Windows PowerShell
$env:DASHSCOPE_API_KEY = "your_api_key_here"

# Linux/macOS
export DASHSCOPE_API_KEY="your_api_key_here"
```

## Run

```bash
python main.py
```

The agent will analyize `titanic.csv` and generate `age_distribution.png` in the current directory.
