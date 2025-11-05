# Gemini API Setup Guide

This guide will help you set up the Agent Discovery System to use Google's Gemini API instead of OpenAI.

## Prerequisites

1. **Python Environment**: Ensure you have Python 3.10+ installed
2. **AgentScope**: The AgentScope framework should be installed
3. **Google Generative AI Library**: Install the required dependency

```bash
pip install google-generativeai
```

## Getting Your Gemini API Key

1. Go to [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Sign in with your Google account
3. Click "Create API Key"
4. Copy the generated API key

## Setting Up Environment Variables

### On Windows (PowerShell)
```powershell
$env:GEMINI_API_KEY="your-gemini-api-key-here"
```

### On Windows (Command Prompt)
```cmd
set GEMINI_API_KEY=your-gemini-api-key-here
```

### On Linux/macOS
```bash
export GEMINI_API_KEY="your-gemini-api-key-here"
```

### Persistent Setup (Recommended)

For permanent setup, add the environment variable to your shell configuration file:

**Windows**: Add to your PowerShell profile or set as a system environment variable
**Linux/macOS**: Add to your `~/.bashrc`, `~/.zshrc`, or similar file:

```bash
echo 'export GEMINI_API_KEY="your-gemini-api-key-here"' >> ~/.bashrc
source ~/.bashrc
```

## Testing Your Setup

Run the test script to verify everything is working:

```bash
python test_gemini.py
```

This will:
- Check if your API key is set correctly
- Test the model initialization
- Make a simple API call to verify connectivity

## Available Gemini Models

The following Gemini models are available:

| Model Name | Description | Best For |
|------------|-------------|----------|
| `gemini-2.5-flash-lite` | Fast, efficient model | Quick responses, simple tasks |
| `gemini-2.5-pro` | More capable model | Complex reasoning, detailed analysis |
| `gemini-2.5-flash` | Latest fast model | Balanced performance and speed |

## Configuration Examples

### Basic Configuration
```python
from agentscope.model import GeminiChatModel
from agentscope.formatter import GeminiChatFormatter

model = GeminiChatModel(
    model_name="gemini-2.5-pro",
    api_key=os.getenv("GEMINI_API_KEY"),
    stream=True,
    generate_kwargs={
        "temperature": 0.7,
    }
)

formatter = GeminiChatFormatter()
```

### Advanced Configuration
```python
# For creative tasks
creative_model = GeminiChatModel(
    model_name="gemini-2.5-flash-lite",
    api_key=os.getenv("GEMINI_API_KEY"),
    stream=True,
    generate_kwargs={
        "temperature": 0.9,  # Higher creativity
        "top_p": 0.9,
        "top_k": 40,
    }
)

# For analytical tasks
analytical_model = GeminiChatModel(
    model_name="gemini-2.5-pro",
    api_key=os.getenv("GEMINI_API_KEY"),
    stream=False,
    generate_kwargs={
        "temperature": 0.3,  # More focused
        "top_p": 0.7,
    }
)
```

## Running the Examples

After setting up your API key, you can run the examples:

```bash
# Run the main Agent Discovery System example
python main.py

# Run the HiVA MBTI Dynamic Agent Generation demo
python hiva_demo.py
```

## Troubleshooting

### Common Issues

1. **Import Error**: If you get an import error for `google.generativeai`:
   ```bash
   pip install google-generativeai
   ```

2. **API Key Not Found**: Ensure your environment variable is set correctly:
   ```bash
   echo $GEMINI_API_KEY  # Linux/macOS
   echo $env:GEMINI_API_KEY  # Windows PowerShell
   ```

3. **Authentication Error**: Verify your API key is valid and active

4. **Rate Limiting**: Gemini has rate limits. If you encounter rate limiting:
   - Reduce the `token_budget` in your configuration
   - Add delays between API calls
   - Consider using a lower tier model for testing

### Debug Mode

To enable debug logging, add this to your script:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Cost Considerations

Gemini offers generous free tier limits:
- Gemini 2.5 Flash Lite: 15 requests per minute, 1 million tokens per day
- Gemini 2.5 Pro: 2 requests per minute, 50,000 tokens per day

For production use, consider:
- Using Gemini Flash for simpler tasks
- Implementing token counting to track usage
- Setting appropriate budget limits in your workflows

## Support

If you encounter issues:
1. Check the [Gemini API Documentation](https://ai.google.dev/gemini-api/docs)
2. Verify your API key and quotas in [Google AI Studio](https://makersuite.google.com/)
3. Run the test script to isolate the issue
4. Check AgentScope documentation for model integration details