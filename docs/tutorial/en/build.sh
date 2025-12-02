#!/bin/bash

set -e

# Clean old build files
rm -rf build/ doctrees/

# Generate the API rst files
sphinx-apidoc -o source/build_api ../../../src/agentscope -t ../_templates -e

# Build the html
sphinx-build -M html source build

# Remove temporary files (double insurance)
rm -rf build/html/.doctrees
rm -f build/html/.buildinfo
find build/html -name "*.pickle" -delete
find build/html -name "__pycache__" -delete
find build/html -name "*.pyc" -delete

echo "✅ English docs for v0 built successfully, temporary files cleaned"