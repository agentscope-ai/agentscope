#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Startup script for the Agent Discovery System.

This script helps you run the integrated Agent Discovery System with
Gemini API integration and web interface.
"""

import os
import sys
import subprocess
import webbrowser
import time
from pathlib import Path


def check_dependencies():
    """Check if required dependencies are installed."""
    required_packages = [
        'fastapi',
        'uvicorn',
        'websockets',
        'pydantic',
        'google-generativeai'
    ]
    
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
        except ImportError:
            missing_packages.append(package)
    
    if missing_packages:
        print("❌ Missing required packages:")
        for package in missing_packages:
            print(f"   - {package}")
        print("\n💡 Install missing packages with:")
        print(f"   pip install {' '.join(missing_packages)}")
        return False
    
    return True


def check_gemini_api_key():
    """Check if Gemini API key is set."""
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        print("❌ GEMINI_API_KEY environment variable not set")
        print("\n🔧 To set up your Gemini API key:")
        print("1. Get your API key from: https://makersuite.google.com/app/apikey")
        print("2. Set environment variable:")
        print("   Windows: set GEMINI_API_KEY=your-api-key")
        print("   Linux/macOS: export GEMINI_API_KEY=your-api-key")
        print("\n⚠️  The system will run in demo mode without API key")
        return False
    
    print(f"✅ Gemini API key found: {api_key[:8]}...")
    return True


def start_server():
    """Start the FastAPI server."""
    print("🚀 Starting Agent Discovery System server...")
    
    # Get the directory of this script
    script_dir = Path(__file__).parent
    server_file = script_dir / "discovery_server.py"
    
    if not server_file.exists():
        print(f"❌ Server file not found: {server_file}")
        return False
    
    try:
        # Start the server
        process = subprocess.Popen([
            sys.executable, str(server_file)
        ], cwd=str(script_dir))
        
        # Wait a moment for server to start
        print("⏳ Waiting for server to start...")
        time.sleep(3)
        
        # Open browser
        print("🌐 Opening browser...")
        webbrowser.open("http://localhost:8000")
        
        print("\n" + "="*60)
        print("🎉 Agent Discovery System is running!")
        print("📍 URL: http://localhost:8000")
        print("📚 Upload MD files and start exploring!")
        print("⏹️  Press Ctrl+C to stop the server")
        print("="*60)
        
        # Wait for the process
        process.wait()
        
    except KeyboardInterrupt:
        print("\n🛑 Shutting down server...")
        process.terminate()
        return True
    except Exception as e:
        print(f"❌ Failed to start server: {e}")
        return False


def main():
    """Main function."""
    print("🔍 Agent Discovery System - Startup")
    print("="*50)
    
    # Check dependencies
    print("📦 Checking dependencies...")
    if not check_dependencies():
        return 1
    
    # Check API key
    print("\n🔑 Checking Gemini API key...")
    has_api_key = check_gemini_api_key()
    
    if not has_api_key:
        response = input("\n❓ Continue without API key? (y/N): ").strip().lower()
        if response != 'y':
            print("👋 Setup your API key and try again!")
            return 1
    
    # Start server
    print("\n🚀 Starting system...")
    if start_server():
        print("✅ Server stopped successfully")
        return 0
    else:
        print("❌ Server failed to start")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)