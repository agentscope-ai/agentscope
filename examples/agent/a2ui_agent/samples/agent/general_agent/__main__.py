# -*- coding: utf-8 -*-
"""Main entry point for running the restaurant finder A2UI server."""
import logging
import os
import uvicorn
from starlette.staticfiles import StaticFiles
from setup_a2ui_server import app

if __name__ == "__main__":
    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    images_dir = os.path.join(script_dir, "images")

    # Mount static files if images directory exists
    if os.path.exists(images_dir):
        app.mount("/static", StaticFiles(directory=images_dir), name="static")
        logging.info(
            "Mounted static files from %s at /static",
            images_dir,
        )
    else:
        logging.warning(
            "Images directory not found at %s, "
            "static files will not be served",
            images_dir,
        )

    # Run the app manually with uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=10002,
        log_level="info",
        access_log=True,
    )
