# 🚀 Quick Start Guide

Your Gemini API key has been configured! Here's how to get started:

## Option 1: Use PowerShell Script (Recommended)

1. **Open PowerShell in this directory**
2. **Run the setup script:**
   ```powershell
   .\setup_api_key.ps1
   ```
3. **Start the system:**
   ```powershell
   python run_discovery_system.py
   ```

## Option 2: Manual Setup

1. **Set API key in current session:**
   ```powershell
   $env:GEMINI_API_KEY = "AIzaSyAIjsJCDEtJ14pdxfRIS4uj9sjriB_ff6I"
   ```

2. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

3. **Test the setup:**
   ```powershell
   python test_gemini.py
   ```

4. **Start the web interface:**
   ```powershell
   python run_discovery_system.py
   ```

## Option 3: Use Configuration File

Your API key is already saved in `config.ini`. The system will automatically use it!

Just run:
```powershell
python run_discovery_system.py
```

## 🌐 Access the Web Interface

Once the server starts, open your browser and go to:
**http://localhost:8000**

## 📝 How to Use

1. **Upload Knowledge Base**: Drag and drop your `.md` files
2. **Set Initial Idea**: Enter your research question or topic
3. **Configure Parameters**: Adjust exploration depth and focus areas
4. **Start Discovery**: Click "开始智能探索" and watch the magic happen!
5. **View Results**: Explore the discoveries, insights, and research questions

## ✅ Your API Key

- **API Key**: `AIzaSyAIjsJCDEtJ14pdxfRIS4uj9sjriB_ff6I`
- **Status**: ✅ Configured and ready to use
- **Model**: Gemini 2.5 Pro (powerful and capable)

## 🆘 Troubleshooting

If you encounter any issues:

1. **Test API connection:**
   ```powershell
   python test_gemini.py
   ```

2. **Check dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

3. **Verify API key:**
   - Check that `config.ini` contains your API key
   - Or set environment variable: `$env:GEMINI_API_KEY = "your-key"`

## 🎯 Next Steps

- Try the demo: `python demo.py`
- Upload your own markdown files
- Experiment with different exploration depths
- Explore the generated insights and hypotheses

**Ready to discover insights from your knowledge base!** 🧠✨